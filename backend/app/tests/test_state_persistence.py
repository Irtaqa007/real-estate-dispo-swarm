"""Tests for batch state persistence in state_persistence.py.

Verifies that:
1. save_all_state batches all 4 keys into a single session
2. _set_states_batch opens exactly one DB session
3. Empty entries are handled gracefully
4. idempotency store cap is respected
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.database as _db
from app.services import state_persistence as sp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Return a mock async database session."""
    db = AsyncMock()
    # Mock db.get to return None (no existing rows)
    db.get = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_session_factory(mock_db):
    """Mock async_session_factory to return the mock db session."""
    with patch.object(_db, "async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_db
        yield mock_factory, mock_db


# ---------------------------------------------------------------------------
# _set_states_batch — single-session guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_states_batch_opens_one_session(mock_session_factory):
    """_set_states_batch should open exactly one DB session."""
    mock_factory, mock_db = mock_session_factory

    await sp._set_states_batch({
        sp.KEY_CIRCUIT_BREAKER_QUEUE: [],
        sp.KEY_METRICS: {"email_send_attempts": 10},
    })

    # async_session_factory should be called exactly once
    mock_factory.assert_called_once()


@pytest.mark.asyncio
async def test_set_states_batch_upserts_multiple_keys(mock_session_factory):
    """_set_states_batch should upsert all provided keys."""
    mock_factory, mock_db = mock_session_factory

    entries = {
        sp.KEY_CIRCUIT_BREAKER_QUEUE: [{"campaign_id": "abc", "to_email": "t@t.com", "subject": "hi"}],
        sp.KEY_METRICS: {"email_send_attempts": 5},
        sp.KEY_GROQ_DAILY_COUNTER: {"count": 42, "date": "2026-06-13"},
    }
    await sp._set_states_batch(entries)

    # db.add should have been called 3 times (one per key, all new)
    assert mock_db.add.call_count == 3
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_states_batch_updates_existing_keys(mock_session_factory):
    """_set_states_batch should update existing rows (not add new)."""
    mock_factory, mock_db = mock_session_factory

    # Simulate existing AppState row by returning a mutable MagicMock
    existing_row = MagicMock()
    existing_row.value = {"old": "data"}
    mock_db.get = AsyncMock(return_value=existing_row)

    await sp._set_states_batch({sp.KEY_METRICS: {"updated": True}})

    # Should NOT call db.add (since row existed)
    mock_db.add.assert_not_called()
    # Should have updated the existing row's value
    assert existing_row.value == {"updated": True}
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_states_batch_empty_entries_does_nothing(mock_session_factory):
    """_set_states_batch with empty dict should not open a session."""
    mock_factory, mock_db = mock_session_factory

    await sp._set_states_batch({})

    # Session factory should NOT have been called
    mock_factory.assert_not_called()


# ---------------------------------------------------------------------------
# save_all_state — batch integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_all_state_batches_all_five_keys(mock_session_factory):
    """save_all_state should call _set_states_batch with all 5 keys."""
    mock_factory, mock_db = mock_session_factory

    cb_queue = [{"campaign_id": "c1", "to_email": "a@b.com", "subject": "Hello"}]
    metrics = {"email_send_attempts": 10}
    idem_store = {"key1": {"result": "ok", "created_at": 1000}}
    groq_count = 15
    groq_date = "2026-06-13"

    await sp.save_all_state(
        cb_queue=cb_queue,
        metrics=metrics,
        idempotency_store=idem_store,
        groq_count=groq_count,
        groq_date=groq_date,
    )

    # _set_states_batch should have been called with 5 keys
    # (circuit_breaker_queue, metrics, idempotency_store, groq_daily_counter, gmail_daily_sends)
    # We can check by looking at db.add calls (each key creates a new AppState row)
    assert mock_db.add.call_count == 5
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_all_state_sanitizes_cb_queue(mock_session_factory):
    """CB queue entries should be sanitized to the expected keys."""
    mock_factory, mock_db = mock_session_factory

    await sp.save_all_state(
        cb_queue=[{"campaign_id": "c1", "to_email": "a@b.com", "subject": "Hi", "extra": "junk"}],
        metrics={},
        idempotency_store={},
        groq_count=0,
        groq_date="2026-06-13",
    )

    # Get the AppState for cb queue and check value
    added_args = [call for call in mock_db.add.call_args_list]
    for call in added_args:
        row = call[0][0]
        if row.key == sp.KEY_CIRCUIT_BREAKER_QUEUE:
            sanitized = row.value
            assert len(sanitized) == 1
            entry = sanitized[0]
            assert "campaign_id" in entry
            assert "to_email" in entry
            assert "subject" in entry
            assert "extra" not in entry  # should be stripped


@pytest.mark.asyncio
async def test_save_all_state_serializes_metrics(mock_session_factory):
    """Non-serializable metric values should be converted to strings."""
    mock_factory, mock_db = mock_session_factory

    metrics = {
        "int_val": 42,
        "float_val": 3.14,
        "str_val": "hello",
        "bool_val": True,
        "none_val": None,
        "complex_val": {"nested": "object"},  # not int/float/str/bool/None
    }

    await sp.save_all_state(
        cb_queue=[], metrics=metrics, idempotency_store={},
        groq_count=0, groq_date="2026-06-13",
    )

    added_args = [call for call in mock_db.add.call_args_list]
    for call in added_args:
        row = call[0][0]
        if row.key == sp.KEY_METRICS:
            serialized = row.value
            assert serialized["int_val"] == 42
            assert serialized["float_val"] == 3.14
            assert serialized["str_val"] == "hello"
            assert serialized["bool_val"] is True
            assert serialized["none_val"] is None
            # complex (dict) values should be str-converted
            assert isinstance(serialized["complex_val"], str)


@pytest.mark.asyncio
async def test_save_all_state_caps_idempotency_store(mock_session_factory):
    """save_all_state should cap the idempotency store at _MAX_IDEMPOTENCY_ENTRIES."""
    mock_factory, mock_db = mock_session_factory
    max_entries = sp._MAX_IDEMPOTENCY_ENTRIES
    oversized = {
        f"key{i}": {"result": "ok", "created_at": i}
        for i in range(max_entries + 100)
    }

    await sp.save_all_state(
        cb_queue=[], metrics={}, idempotency_store=oversized,
        groq_count=0, groq_date="2026-06-13",
    )

    added_args = [call for call in mock_db.add.call_args_list]
    for call in added_args:
        row = call[0][0]
        if row.key == sp.KEY_IDEMPOTENCY_STORE:
            assert len(row.value) <= max_entries


@pytest.mark.asyncio
async def test_save_all_state_handles_exception_gracefully(mock_session_factory):
    """save_all_state should log and swallow exceptions."""
    mock_factory, mock_db = mock_session_factory
    mock_db.commit.side_effect = Exception("DB error")

    # Should not raise; exception is logged and swallowed
    await sp.save_all_state(
        cb_queue=[], metrics={}, idempotency_store={},
        groq_count=0, groq_date="2026-06-13",
    )
    # If it didn't raise, the test passes
