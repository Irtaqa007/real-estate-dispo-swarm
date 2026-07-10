"""Tests for scheduler graceful shutdown checks.

Verifies that:
1. The _scheduler_loop checks _running between each subtask
2. Setting _running = False causes a break at the next check point
3. The loop completes the currently executing task before checking
4. is_scheduler_running returns the correct state
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services import scheduler
from app.services.scheduler import runner as scheduler_runner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_scheduler_state():
    """Reset scheduler global state before each test.

    Accesses runner module directly since _running / _scheduler_task
    are module-level globals in runner.py and can't be value-copied
    through package re-exports.
    """
    scheduler_runner._running = False
    scheduler_runner._scheduler_task = None
    yield
    scheduler_runner._running = False
    scheduler_runner._scheduler_task = None


@pytest.fixture
def mock_all_tasks():
    """Mock all scheduler subtasks to do nothing and return quickly.

    Patches on the runner module (where the functions are actually referenced),
    not the package, because runner.py does direct imports like
        from app.services.scheduler.campaign_sender import process_scheduled_campaigns
    which creates a local binding that package-level patches won't affect.
    """
    runner_patches = [
        patch.object(scheduler_runner, "process_scheduled_campaigns", AsyncMock(return_value=0)),
        patch.object(scheduler_runner, "process_buyer_replies", AsyncMock(return_value=0)),
        patch.object(scheduler_runner, "process_title_emails", AsyncMock(return_value={"total_found": 0, "processed": 0})),
        patch.object(scheduler_runner, "run_tier_promotions", AsyncMock(return_value=[])),
        patch.object(scheduler_runner, "reset_pitch_counters", AsyncMock(return_value=0)),
        patch.object(scheduler_runner, "run_aging_monitor", AsyncMock(return_value=[])),
        patch.object(scheduler_runner, "update_all_buyer_insights", AsyncMock(return_value=0)),
        patch.object(scheduler_runner, "save_all_state", AsyncMock()),
        patch.object(scheduler_runner, "get_cb_queue", return_value=[]),
        patch.object(scheduler_runner, "get_metrics", return_value={}),
        patch.object(scheduler_runner, "get_idempotency_store", return_value={}),
        patch.object(scheduler_runner, "get_call_count_today", return_value=0),
        patch.object(scheduler_runner, "get_calls_today_date", return_value="2026-06-13"),
        patch.object(scheduler_runner, "match_all_active_deals", AsyncMock(return_value={"deals_processed": 0, "campaigns_launched": 0, "buyers_queued": 0})),
        patch.object(scheduler_runner, "save_scheduler_heartbeat", AsyncMock()),
        # Also mock _db.async_session_factory to prevent TypeError from real DB calls
        patch.object(scheduler_runner, "_db", MagicMock(
            async_session_factory=MagicMock(side_effect=RuntimeError("Should not be called")),
        )),
    ]
    for p in runner_patches:
        p.start()
    yield
    for p in runner_patches:
        p.stop()


# ---------------------------------------------------------------------------
# _running flag tests
# ---------------------------------------------------------------------------


def test_is_scheduler_running_initially_false():
    """is_scheduler_running should return False before start."""
    scheduler_runner._running = False
    assert scheduler.is_scheduler_running() is False


def test_is_scheduler_running_true_after_start():
    """is_scheduler_running should return True after _scheduler_loop sets it."""
    scheduler_runner._running = True
    assert scheduler.is_scheduler_running() is True


# ---------------------------------------------------------------------------
# Graceful shutdown via _running checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_breaks_when_running_false_after_task1(mock_all_tasks):
    """Setting _running=False should cause a break after the current task finishes."""
    scheduler_runner._running = True

    # Patch TICK_INTERVAL_SECONDS to 0.01s so the loop doesn't sleep 60s between ticks
    original_tick = scheduler.TICK_INTERVAL_SECONDS
    scheduler.TICK_INTERVAL_SECONDS = 0.01
    original_reply = scheduler.REPLY_INTERVAL_SECONDS
    scheduler.REPLY_INTERVAL_SECONDS = 0.01
    original_daily = scheduler.DAILY_INTERVAL_SECONDS
    scheduler.DAILY_INTERVAL_SECONDS = 0.1
    try:
        # Run scheduler loop briefly, then set _running = False
        async def run_and_stop():
            loop_task = asyncio.create_task(scheduler._scheduler_loop())
            await asyncio.sleep(0.05)  # Let it start and run reply tasks
            scheduler_runner._running = False  # Request shutdown
            await asyncio.sleep(0.2)   # Give it time to react
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

        await run_and_stop()
    finally:
        scheduler.TICK_INTERVAL_SECONDS = original_tick
        scheduler.REPLY_INTERVAL_SECONDS = original_reply
        scheduler.DAILY_INTERVAL_SECONDS = original_daily

    # Task 1 (process_scheduled_campaigns) should have been called
    scheduler_runner.process_scheduled_campaigns.assert_awaited()


@pytest.mark.asyncio
async def test_loop_does_not_run_tasks_after_break(mock_all_tasks):
    """After _running becomes False, remaining tasks should not execute."""
    scheduler_runner._running = True

    # Patch intervals to very small values so the loop doesn't hang
    original_tick = scheduler.TICK_INTERVAL_SECONDS
    scheduler.TICK_INTERVAL_SECONDS = 0.01
    original_reply = scheduler.REPLY_INTERVAL_SECONDS
    scheduler.REPLY_INTERVAL_SECONDS = 0.01
    original_daily = scheduler.DAILY_INTERVAL_SECONDS
    scheduler.DAILY_INTERVAL_SECONDS = 0.1
    try:
        # Make Task 1 trigger shutdown
        original_task1 = scheduler_runner.process_scheduled_campaigns

        async def task1_and_stop():
            await original_task1()
            scheduler_runner._running = False  # Request shutdown during Task 1
            return 0

        scheduler_runner.process_scheduled_campaigns = AsyncMock(side_effect=task1_and_stop)

        loop_task = asyncio.create_task(scheduler._scheduler_loop())
        await asyncio.sleep(0.2)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        # Task 1 was called
        scheduler_runner.process_scheduled_campaigns.assert_awaited()
        # Task 2 should NOT have been called (break happened)
        scheduler_runner.process_buyer_replies.assert_not_awaited()
    finally:
        scheduler.TICK_INTERVAL_SECONDS = original_tick
        scheduler.REPLY_INTERVAL_SECONDS = original_reply
        scheduler.DAILY_INTERVAL_SECONDS = original_daily


# ---------------------------------------------------------------------------
# start_scheduler / stop_scheduler integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_scheduler_sets_running_false(mock_all_tasks):
    """stop_scheduler should set _running to False."""
    scheduler_runner._running = True
    await scheduler.stop_scheduler()
    assert scheduler_runner._running is False


@pytest.mark.asyncio
async def test_start_scheduler_does_not_start_twice(mock_all_tasks):
    """Calling start_scheduler twice should not create two tasks."""
    scheduler_runner._scheduler_task = None
    scheduler.start_scheduler()
    task1 = scheduler_runner._scheduler_task
    scheduler.start_scheduler()  # Should be a no-op
    task2 = scheduler_runner._scheduler_task
    assert task1 is task2  # Same task reference


@pytest.mark.asyncio
async def test_stop_scheduler_cancels_task(mock_all_tasks):
    """stop_scheduler should cancel the running task."""
    scheduler_runner._running = True
    scheduler_runner._scheduler_task = asyncio.create_task(asyncio.sleep(999))
    await scheduler.stop_scheduler()
    assert scheduler_runner._running is False
    assert scheduler_runner._scheduler_task is None or scheduler_runner._scheduler_task.done()
