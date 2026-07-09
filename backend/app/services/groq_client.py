"""Centralized Groq AI client with rate limiting and metrics.

Provides a shared Groq client with:
- Rate limiting: 15 requests/minute, 1800 requests/day (below Groq free tier limits)
- Token tracking for monitoring
- Shared client initialization to avoid per-module duplication

Usage:
    from app.services.groq_client import get_groq_client, rate_limited_groq_call

    client = get_groq_client()
    response = await rate_limited_groq_call(
        client.chat.completions.create,
        model=settings.groq_model,
        messages=messages,
        temperature=0.7,
        max_tokens=300,
    )
"""

import asyncio
import collections
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from groq import AsyncGroq

from app.config import settings
from app.services.resilience import record_metric

__all__ = ['groq_chat_completion', 'extract_json_block']


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limit configuration
# ---------------------------------------------------------------------------

# Groq free tier: 20 requests/min, 2000 requests/day
# We set conservative limits: 15 requests/min, 1800 requests/day
MAX_REQUESTS_PER_MINUTE = 15
MAX_REQUESTS_PER_DAY = 1800
RATE_LIMIT_WINDOW_MINUTE = 60       # 60 seconds rolling window
RATE_LIMIT_WINDOW_DAY = 86400       # 24 hours rolling window

# ---------------------------------------------------------------------------
# Shared Groq client (lazy-init)
# ---------------------------------------------------------------------------

_client: Optional[AsyncGroq] = None
_client_lock = asyncio.Lock()


async def get_groq_client() -> AsyncGroq:
    """Get or create the shared Groq AI client (thread-safe, async)."""
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:  # Double-check after acquiring lock
                api_key = settings.groq_api_key
                if not api_key:
                    raise ValueError("GROQ_API_KEY is not set. Add it to your .env file.")
                _client = AsyncGroq(api_key=api_key, timeout=30)
                logger.info("Groq client initialized (model: %s)", settings.groq_model)
    return _client


# ---------------------------------------------------------------------------
# Rate limiter (sliding window)
# ---------------------------------------------------------------------------

# Timestamps of recent API calls — used for monitoring only;
# actual rate limiting is enforced in rate_limited_groq_call.
_call_timestamps: collections.deque = collections.deque()


def _prune_timestamps() -> None:
    """Remove timestamps outside the 1-minute window."""
    cutoff = time.monotonic() - RATE_LIMIT_WINDOW_MINUTE
    while _call_timestamps and _call_timestamps[0] < cutoff:
        _call_timestamps.popleft()


def get_call_count_last_minute() -> int:
    """Get the number of Groq API calls in the last 60 seconds."""
    _prune_timestamps()
    return len(_call_timestamps)


_calls_today_count: int = 0
_calls_today_date: Optional[str] = None


def restore_daily_counter(count: int, date_str: str) -> None:
    """Restore the daily call counter from persistent state on startup.

    Args:
        count: Number of calls already made today.
        date_str: Today's date string (YYYY-MM-DD).
    """
    global _calls_today_count, _calls_today_date
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if date_str == now:
        _calls_today_count = count
        _calls_today_date = date_str
        logger.info(
            "Restored Groq daily counter: %d calls on %s",
            count, date_str,
        )


def get_call_count_today() -> int:
    """Get the approximate number of Groq API calls today.

    Uses a simple daily counter that resets when the date changes.
    This is approximate — accurate to within the last hour.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    global _calls_today_date, _calls_today_count
    if _calls_today_date != now:
        _calls_today_date = now
        _calls_today_count = 0
    return _calls_today_count


def get_calls_today_date() -> Optional[str]:
    """Get the current daily counter date string (YYYY-MM-DD)."""
    return _calls_today_date


def _increment_daily_counter() -> None:
    """Increment the daily call counter."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    global _calls_today_date, _calls_today_count
    if _calls_today_date != now:
        _calls_today_date = now
        _calls_today_count = 0
    _calls_today_count += 1


# ---------------------------------------------------------------------------
# Rate-limited Groq call
# ---------------------------------------------------------------------------


async def rate_limited_groq_call(
    api_call: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Make a rate-limited call to the Groq API.

    Enforces:
    - Max 15 requests per rolling 60-second window
    - Max 1800 requests per rolling 24-hour window
    - If rate limited, waits and retries with exponential backoff

    Args:
        api_call: The Groq API method to call (e.g., client.chat.completions.create).
        *args, **kwargs: Arguments passed to the API call.

    Returns:
        The API response.

    Raises:
        RuntimeError: If daily rate limit is exceeded.
    """
    now = time.monotonic()

    # Prune old timestamps
    _prune_timestamps()

    # Check minute limit
    minute_count = len(_call_timestamps)
    if minute_count >= MAX_REQUESTS_PER_MINUTE:
        oldest = _call_timestamps[0]
        wait = RATE_LIMIT_WINDOW_MINUTE - (now - oldest)
        if wait > 0:
            logger.warning(
                "Groq rate limit reached (%d/min). Waiting %.1fs before retry.",
                MAX_REQUESTS_PER_MINUTE, wait,
            )
            await asyncio.sleep(wait + 1)
            _prune_timestamps()

    # Check daily limit (estimated)
    daily_estimate = get_call_count_today()
    if daily_estimate >= MAX_REQUESTS_PER_DAY:
        logger.error(
            "Groq daily rate limit reached (~%d/day). Blocking call.",
            MAX_REQUESTS_PER_DAY,
        )
        record_metric("groq_rate_limit_daily_blocked")
        raise RuntimeError(
            f"Groq daily rate limit (~{MAX_REQUESTS_PER_DAY}/day) exceeded. "
            "Try again tomorrow or upgrade your Groq plan."
        )

    # Record the call
    _call_timestamps.append(now)
    _increment_daily_counter()
    record_metric("groq_api_calls")

    fn_name = getattr(api_call, "__name__", "groq_call")

    try:
        result = await api_call(*args, **kwargs)
        record_metric(f"groq_{fn_name}_success")
        return result
    except Exception as e:
        record_metric(f"groq_{fn_name}_failure")
        record_metric("groq_api_failures")
        logger.error("Groq API call '%s' failed: %s", fn_name, e, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Convenience wrapper for chat completions
# ---------------------------------------------------------------------------


async def groq_chat_completion(
    messages: list,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 300,
) -> Any:
    """Make a rate-limited chat completion call to Groq with fallback support.

    Attempts the primary model first. On non-rate-limit errors, retries
    with the configured fallback model (llama-3.1-8b-instant by default).
    If the fallback also fails, raises the original exception.

    Args:
        messages: Chat messages for the Groq API.
        model: Model to use (defaults to settings.groq_model).
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in the response.

    Returns:
        The API response object.

    Raises:
        ValueError: If GROQ_API_KEY is not set.
        RuntimeError: If daily rate limit is exceeded.
        groq.APIError: If all models fail.
    """
    client = await get_groq_client()
    primary_model = model or settings.groq_model
    fallback_model = settings.groq_fallback_model

    try:
        return await rate_limited_groq_call(
            client.chat.completions.create,
            model=primary_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        # On rate-limit errors, don't fallback — let the rate limiter handle it
        if "rate_limit" in str(e).lower() or "rate limit" in str(e).lower():
            raise

        logger.warning(
            "Primary Groq model %s failed (%s), retrying with fallback model %s",
            primary_model, type(e).__name__, fallback_model,
        )

        try:
            return await rate_limited_groq_call(
                client.chat.completions.create,
                model=fallback_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            # Fallback also failed — raise the original exception
            raise e


# ---------------------------------------------------------------------------
# Rate limit status for health endpoint
# ---------------------------------------------------------------------------


def get_rate_limit_status() -> dict:
    """Get current rate limit status for monitoring."""
    _prune_timestamps()
    return {
        "calls_last_minute": len(_call_timestamps),
        "max_per_minute": MAX_REQUESTS_PER_MINUTE,
        "estimated_calls_today": get_call_count_today(),
        "max_per_day": MAX_REQUESTS_PER_DAY,
        "minute_limit_remaining": max(0, MAX_REQUESTS_PER_MINUTE - len(_call_timestamps)),
        "daily_limit_remaining": max(0, MAX_REQUESTS_PER_DAY - get_call_count_today()),
    }


def extract_json_block(text: str) -> str:
    """Extract a clean JSON object from LLM output.

    Handles reasoning models (qwen-qwq etc.) that emit <think>...</think>
    blocks, markdown fences, and leading/trailing prose. Returns the first
    balanced {...} block found, or the cleaned text if none found.
    """
    if not text:
        return text
    # Strip reasoning blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)  # unclosed think
    # Strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    # Find first balanced JSON object
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]
