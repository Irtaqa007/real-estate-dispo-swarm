"""Resilience patterns: retry with exponential backoff, idempotency, and monitoring.

Usage:
    from app.services.resilience import with_retry, idempotent

    @with_retry(max_attempts=5)
    @idempotent
    async def send_email(to, subject, body):
        ...
"""

import functools
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, TypeVar

import tenacity
from tenacity import (
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Monitoring / Metrics
# ---------------------------------------------------------------------------

# In-memory metrics store — thread-safe for async via asyncio.Lock if needed
# These are simple enough that locking isn't critical for approximate counts.
_metrics: Dict[str, Any] = {
    "email_send_attempts": 0,
    "email_send_successes": 0,
    "email_send_failures": 0,
    "email_send_retries": 0,
    "email_send_total_duration_ms": 0,
    "email_send_last_failure": None,
    "imap_fetch_attempts": 0,
    "imap_fetch_failures": 0,
    "title_email_checks": 0,
    "idempotency_hits": 0,
}


def restore_metrics(persisted: Dict[str, Any]) -> None:
    """Restore metrics counters from persistent state on startup.

    Args:
        persisted: Dict of metric key → value loaded from the DB.
    """
    for key, value in persisted.items():
        _metrics[key] = value
    logger.info("Restored %d metrics counters from persistent state", len(persisted))


def record_metric(key: str, value: Any = 1) -> None:
    """Increment or set a metric."""
    if key in _metrics and isinstance(_metrics[key], (int, float)):
        _metrics[key] += value if isinstance(value, (int, float)) else 1
    else:
        _metrics[key] = value


def get_metrics() -> Dict[str, Any]:
    """Return a snapshot of current metrics."""
    return dict(_metrics)


def log_retry_attempt(retry_state: tenacity.RetryCallState) -> None:
    """Callback for tenacity's before_sleep to log each retry attempt."""
    attempt = retry_state.attempt_number
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    fn_name = retry_state.fn.__name__ if retry_state.fn else "unknown"
    wait = retry_state.next_action.sleep if retry_state.next_action else 0

    record_metric(f"retry_{fn_name}_attempts")
    record_metric("email_send_retries")

    logger.warning(
        "Retry %d/%d for %s — waiting %.1fs — error: %s",
        attempt,
        retry_state.retry_object.stop_max_attempt_number or 5,
        fn_name,
        wait,
        exception,
    )


# ---------------------------------------------------------------------------
# Retry decorator factory
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])


def with_retry(
    max_attempts: int = 5,
    min_delay: float = 2.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple = (
        ConnectionError,
        TimeoutError,
        OSError,
    ),
) -> Callable[[F], F]:
    """Decorator that applies exponential backoff retry to an async function.

    Args:
        max_attempts: Maximum number of retry attempts.
        min_delay: Initial delay in seconds.
        max_delay: Maximum delay in seconds.
        retryable_exceptions: Tuple of exception types to retry on.

    The retry schedule is:
        Attempt 1: original call
        Attempt 2: after 2s  (2^1)
        Attempt 3: after 4s  (2^2)
        Attempt 4: after 8s  (2^3)
        Attempt 5: after 16s (2^4)
        Total max wait: ~62s (for 5 attempts)
    """
    retryer = tenacity.AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_delay, max=max_delay),
        retry=retry_if_exception_type(retryable_exceptions),
        before_sleep=log_retry_attempt,
        reraise=True,
    )

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            result = await retryer(func, *args, **kwargs)
            duration_ms = (time.monotonic() - start) * 1000
            record_metric(f"{func.__name__}_duration_ms", duration_ms)
            record_metric(f"{func.__name__}_successes")
            return result

        return wrapper  # type: ignore

    return decorator


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

# Simple in-memory idempotency store, persisted to DB for restart survival.
# Entries use wall-clock epoch milliseconds for cross-session TTL checks.
_idempotency_store: Dict[str, Dict[str, Any]] = {}
_idempotency_ttl: float = 3600.0  # 1 hour default


def restore_idempotency_store(persisted: Dict[str, Dict[str, Any]]) -> None:
    """Restore the idempotency cache from persistent state on startup.

    Persisted entries store both monotonic and wall-clock timestamps.
    On restore, wall-clock epoch ms is converted to an approximate
    monotonic offset so in-session TTL checks work correctly.

    Args:
        persisted: Dict mapping key → {"result": ..., "created_at_wall_ms": <epoch-ms>}
    """
    current_wall_ms = int(time.time() * 1000)
    current_mono = time.monotonic()
    restored = 0
    dropped = 0

    for key, entry in persisted.items():
        if not isinstance(entry, dict):
            dropped += 1
            continue

        # Use the wall-clock timestamp to estimate the monotonic equivalent
        stored_wall_ms = entry.get("created_at_wall_ms", 0)
        stored_mono = entry.get("created_at", 0)

        if stored_wall_ms > 0:
            # Convert wall-clock epoch ms → monotonic offset:
            # estimated_mono = current_mono - elapsed_wall_time
            elapsed_s = (current_wall_ms - stored_wall_ms) / 1000.0
            estimated_mono = current_mono - elapsed_s
            if estimated_mono > 0 and elapsed_s <= _idempotency_ttl:
                entry["created_at"] = estimated_mono
                _idempotency_store[key] = entry
                restored += 1
            else:
                dropped += 1
        elif stored_mono > 0:
            # Legacy entry (monotonic only) — keep if not expired
            age = current_mono - stored_mono
            if age <= _idempotency_ttl:
                _idempotency_store[key] = entry
                restored += 1
            else:
                dropped += 1
        else:
            dropped += 1

    logger.info(
        "Restored %d idempotency cache entries (dropped %d expired/invalid)",
        restored, dropped,
    )


async def check_idempotency(key: str) -> Optional[Dict[str, Any]]:
    """Check if an idempotency key has been used.

    Args:
        key: The idempotency key to check.

    Returns:
        The stored result dict if the key exists and is still valid, else None.
    """
    record = _idempotency_store.get(key)
    if not record:
        return None

    # Check TTL
    age = time.monotonic() - record.get("created_at", 0)
    if age > _idempotency_ttl:
        del _idempotency_store[key]
        return None

    record_metric("idempotency_hits")
    return record.get("result")


async def store_idempotency(key: str, result: Dict[str, Any]) -> None:
    """Store a result under an idempotency key.

    Stores both a monotonic timestamp (for in-session TTL checks)
    and a wall-clock epoch ms timestamp (for cross-session TTL checks).
    """
    now_ms = int(time.time() * 1000)
    _idempotency_store[key] = {
        "result": result,
        "created_at": time.monotonic(),
        "created_at_wall_ms": now_ms,
    }


def generate_idempotency_key(*args: Any, **kwargs: Any) -> str:
    """Generate an idempotency key from function arguments.

    Uses a hash of the serialized args/kwargs so the same inputs
    always produce the same key.

    Args:
        *args, **kwargs: The function arguments to derive the key from.

    Returns:
        A SHA-256 hex digest string.
    """
    raw = json.dumps(
        {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def idempotent(func: F = None, *, ttl: float = 3600.0) -> Any:
    """Decorator that makes a function idempotent.

    Calls with the same arguments within the TTL window return the
    cached result instead of executing the function again.

    Args:
        ttl: Time-to-live in seconds for cached results.

    Usage:
        @idempotent
        async def send_email(to, subject, body):
            ...

        @idempotent(ttl=7200)
        async def process_reply(reply_data):
            ...
    """
    if func is not None:
        # Called without arguments: @idempotent
        _ttl_local = 3600.0

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = generate_idempotency_key(*args, **kwargs)
            cached = await check_idempotency(key)
            if cached is not None:
                logger.debug("Idempotency hit for %s — returning cached result", func.__name__)
                return cached

            result = await func(*args, **kwargs)
            if isinstance(result, dict):
                await store_idempotency(key, result)
            return result

        return wrapper  # type: ignore
    else:
        # Called with arguments: @idempotent(ttl=7200)
        _ttl = ttl

        def decorator(fn: F) -> F:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                key = generate_idempotency_key(*args, **kwargs)
                cached = await check_idempotency(key)
                if cached is not None:
                    logger.debug("Idempotency hit for %s — returning cached result", fn.__name__)
                    return cached

                result = await fn(*args, **kwargs)
                if isinstance(result, dict):
                    await store_idempotency(key, result)
                return result

            return wrapper  # type: ignore

        return decorator


# ---------------------------------------------------------------------------
# Health / readiness check helpers
# ---------------------------------------------------------------------------


def get_idempotency_store() -> Dict[str, Dict[str, Any]]:
    """Get the idempotency store dict for external inspection/persistence."""
    return _idempotency_store


async def get_resilience_health() -> Dict[str, Any]:
    """Return a comprehensive health report for the resilience subsystem.

    Note: Gmail circuit breaker state is reported separately via
    the /api/health endpoint under gmail_detail.
    """
    return {
        "metrics": get_metrics(),
        "circuit_breakers": {},  # Gmail circuit breaker state is in gmail_detail
        "idempotency_store_size": len(_idempotency_store),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
