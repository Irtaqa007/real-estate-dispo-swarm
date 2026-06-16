"""Persistent state management for in-memory subsystem state.

Each subsystem's in-memory state is serialized to the app_state table
so it survives server restarts.

Subsystems persisted:
- circuit_breaker_queue: GmailCircuitBreaker queued emails
- metrics: Resilience metrics counters
- idempotency_store: Idempotency cache entries
- groq_daily_counter: Groq API daily call count

Usage:
    from app.services.state_persistence import (
        load_all_state, save_all_state,
        load_circuit_breaker_queue, save_circuit_breaker_queue,
    )
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

import app.database as _db
from app.models.schemas import AppState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State keys
# ---------------------------------------------------------------------------

KEY_CIRCUIT_BREAKER_QUEUE = "circuit_breaker_queue"
KEY_METRICS = "metrics"
KEY_IDEMPOTENCY_STORE = "idempotency_store"
KEY_GROQ_DAILY_COUNTER = "groq_daily_counter"

# ---------------------------------------------------------------------------
# Generic helpers — single-key ops still open their own session
# ---------------------------------------------------------------------------


async def _get_state(key: str) -> Optional[Any]:
    """Load a single state blob by key."""
    async with _db.async_session_factory() as db:
        row = await db.get(AppState, key)
        if row is not None:
            return row.value
        return None


async def _set_state(key: str, value: Any) -> None:
    """Upsert a single state blob by key."""
    async with _db.async_session_factory() as db:
        existing = await db.get(AppState, key)
        if existing is not None:
            existing.value = value
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(AppState(key=key, value=value, updated_at=datetime.now(timezone.utc)))
        await db.commit()


async def _delete_state(key: str) -> None:
    """Delete a single state blob by key."""
    async with _db.async_session_factory() as db:
        existing = await db.get(AppState, key)
        if existing is not None:
            await db.delete(existing)
            await db.commit()


# ---------------------------------------------------------------------------
# Internal: batch upsert multiple keys in a single session
# ---------------------------------------------------------------------------


async def _set_states_batch(entries: Dict[str, Any]) -> None:
    """Upsert multiple state blobs in a single DB transaction.

    Args:
        entries: Dict mapping state key -> value.
    """
    if not entries:
        return
    async with _db.async_session_factory() as db:
        now = datetime.now(timezone.utc)
        for key, value in entries.items():
            existing = await db.get(AppState, key)
            if existing is not None:
                existing.value = value
                existing.updated_at = now
            else:
                db.add(AppState(key=key, value=value, updated_at=now))
        await db.commit()


# ---------------------------------------------------------------------------
# Circuit breaker queue
# ---------------------------------------------------------------------------

CB_QUEUE_ENTRY_KEYS = {"campaign_id", "to_email", "subject"}


async def load_circuit_breaker_queue() -> List[Dict[str, str]]:
    """Load the circuit breaker queued emails from the DB.

    Returns:
        List of dicts with keys: campaign_id, to_email, subject.
    """
    raw = await _get_state(KEY_CIRCUIT_BREAKER_QUEUE)
    if not raw or not isinstance(raw, list):
        return []
    return [
        {k: str(entry[k]) for k in CB_QUEUE_ENTRY_KEYS if k in entry}
        for entry in raw
    ]


async def save_circuit_breaker_queue(queue: List[Dict[str, str]]) -> None:
    """Save the circuit breaker queued emails to the DB.

    Args:
        queue: List of dicts with keys: campaign_id, to_email, subject.
    """
    sanitized = [
        {k: str(entry[k]) for k in CB_QUEUE_ENTRY_KEYS if k in entry}
        for entry in queue
    ]
    await _set_state(KEY_CIRCUIT_BREAKER_QUEUE, sanitized)
    logger.debug("Persisted %d circuit breaker queued emails", len(sanitized))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


async def load_metrics() -> Dict[str, Any]:
    """Load resilience metrics counters from the DB.

    Returns:
        Dict of metric key -> value.
    """
    raw = await _get_state(KEY_METRICS)
    if not raw or not isinstance(raw, dict):
        return {}
    return dict(raw)


async def save_metrics(metrics: Dict[str, Any]) -> None:
    """Save resilience metrics counters to the DB.

    Args:
        metrics: Dict of metric key -> value.
    """
    serializable = {}
    for k, v in metrics.items():
        if isinstance(v, (int, float, str, bool)):
            serializable[k] = v
        elif v is None:
            serializable[k] = None
        else:
            serializable[k] = str(v)
    await _set_state(KEY_METRICS, serializable)
    logger.debug("Persisted %d metrics counters", len(serializable))


# ---------------------------------------------------------------------------
# Idempotency store
# ---------------------------------------------------------------------------

# Max entries to persist (safety cap)
_MAX_IDEMPOTENCY_ENTRIES = 5000


async def load_idempotency_store() -> Dict[str, Dict[str, Any]]:
    """Load the idempotency cache from the DB.

    Returns:
        Dict mapping idempotency key -> {"result": ..., "created_at": <epoch-ms>}
        where created_at is a wall-clock timestamp (not monotonic).
    """
    raw = await _get_state(KEY_IDEMPOTENCY_STORE)
    if not raw or not isinstance(raw, dict):
        return {}
    return dict(raw)


async def save_idempotency_store(store: Dict[str, Dict[str, Any]]) -> None:
    """Save the idempotency cache to the DB.

    Args:
        store: Dict mapping idempotency key -> {"result": ..., "created_at": <epoch-ms>}
    """
    # Cap the store to prevent unbounded growth
    if len(store) > _MAX_IDEMPOTENCY_ENTRIES:
        sorted_entries = sorted(
            store.items(),
            key=lambda x: x[1].get("created_at", 0) if isinstance(x[1], dict) else 0,
            reverse=True,
        )
        store = dict(sorted_entries[:_MAX_IDEMPOTENCY_ENTRIES])

    # Ensure all values are JSON-serializable
    serializable = {}
    for k, v in store.items():
        if isinstance(v, dict):
            safe = {}
            for vk, vv in v.items():
                if isinstance(vv, (int, float, str, bool, list, dict)):
                    safe[vk] = vv
                elif vv is None:
                    safe[vk] = None
                else:
                    safe[vk] = str(vv)
            serializable[k] = safe
        else:
            serializable[k] = {"result": str(v), "created_at": 0}

    await _set_state(KEY_IDEMPOTENCY_STORE, serializable)
    logger.debug("Persisted %d idempotency cache entries", len(serializable))


# ---------------------------------------------------------------------------
# Groq daily counter
# ---------------------------------------------------------------------------


async def load_groq_daily_counter() -> Dict[str, Any]:
    """Load the Groq daily call counter from the DB.

    Returns:
        Dict with keys: count (int), date (str YYYY-MM-DD), or empty dict.
    """
    raw = await _get_state(KEY_GROQ_DAILY_COUNTER)
    if not raw or not isinstance(raw, dict):
        return {}
    return {
        "count": int(raw.get("count", 0)),
        "date": str(raw.get("date", "")),
    }


async def save_groq_daily_counter(count: int, date_str: str) -> None:
    """Save the Groq daily call counter to the DB.

    Args:
        count: Number of Groq API calls today.
        date_str: Today's date as YYYY-MM-DD.
    """
    await _set_state(KEY_GROQ_DAILY_COUNTER, {"count": count, "date": date_str})
    logger.debug("Persisted Groq daily counter: %d calls on %s", count, date_str)


# ---------------------------------------------------------------------------
# Bulk load / save (for startup and periodic sync)
# ---------------------------------------------------------------------------


async def load_all_state() -> Dict[str, Any]:
    """Load all persisted subsystem state from the DB.

    Called once on application startup to restore in-memory state.

    Returns:
        Dict with keys: circuit_breaker_queue, metrics, idempotency_store,
        groq_daily_counter.
    """
    return {
        KEY_CIRCUIT_BREAKER_QUEUE: await load_circuit_breaker_queue(),
        KEY_METRICS: await load_metrics(),
        KEY_IDEMPOTENCY_STORE: await load_idempotency_store(),
        KEY_GROQ_DAILY_COUNTER: await load_groq_daily_counter(),
    }


async def save_all_state(
    cb_queue: List[Dict[str, str]],
    metrics: Dict[str, Any],
    idempotency_store: Dict[str, Dict[str, Any]],
    groq_count: int,
    groq_date: str,
) -> None:
    """Save all subsystem state to the DB in a single batch transaction.

    Used by the scheduler for periodic state persistence.
    If any individual save fails, the whole batch is rolled back.
    """
    try:
        # Prepare sanitised values for each key
        # Circuit breaker queue
        cb_sanitized = [
            {k: str(entry[k]) for k in CB_QUEUE_ENTRY_KEYS if k in entry}
            for entry in cb_queue
        ]

        # Metrics
        metrics_serializable = {}
        for k, v in metrics.items():
            if isinstance(v, (int, float, str, bool)):
                metrics_serializable[k] = v
            elif v is None:
                metrics_serializable[k] = None
            else:
                metrics_serializable[k] = str(v)

        # Idempotency store
        idem = idempotency_store
        if len(idem) > _MAX_IDEMPOTENCY_ENTRIES:
            sorted_entries = sorted(
                idem.items(),
                key=lambda x: x[1].get("created_at", 0) if isinstance(x[1], dict) else 0,
                reverse=True,
            )
            idem = dict(sorted_entries[:_MAX_IDEMPOTENCY_ENTRIES])

        idem_serializable = {}
        for k, v in idem.items():
            if isinstance(v, dict):
                safe = {}
                for vk, vv in v.items():
                    if isinstance(vv, (int, float, str, bool, list, dict)):
                        safe[vk] = vv
                    elif vv is None:
                        safe[vk] = None
                    else:
                        safe[vk] = str(vv)
                idem_serializable[k] = safe
            else:
                idem_serializable[k] = {"result": str(v), "created_at": 0}

        # Batch upsert all four keys in a single session
        await _set_states_batch({
            KEY_CIRCUIT_BREAKER_QUEUE: cb_sanitized,
            KEY_METRICS: metrics_serializable,
            KEY_IDEMPOTENCY_STORE: idem_serializable,
            KEY_GROQ_DAILY_COUNTER: {"count": groq_count, "date": groq_date},
        })

        logger.debug(
            "Persisted state batch: %d cb queue, %d metrics, %d idempotency, groq=%d on %s",
            len(cb_sanitized), len(metrics_serializable),
            len(idem_serializable), groq_count, groq_date,
        )
    except Exception as e:
        logger.error("Failed to batch-persist state: %s", e, exc_info=True)
