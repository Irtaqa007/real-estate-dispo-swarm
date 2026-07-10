"""Background task scheduler orchestrator for automated 24/7 operations.

Maintains two independent intervals:
- Reply interval (30s default): time-sensitive tasks (reply processing, ghost detection, campaign sends)
- Hourly interval (60 min): daily/maintenance tasks (auto-match, insights, aging, etc.)

The loop ticks every 15 seconds and dispatches tasks based on elapsed time
since each interval's last run. Each task is individually wrapped in
try/except so one failure never crashes the entire scheduler.

Sub-modules:
    scheduler/campaign_sender.py   — process_scheduled_campaigns
    scheduler/reply_pipeline.py    — process_buyer_replies
    scheduler/ghost_manager.py     — detect_and_flag_ghosts, send_ghost_recovery_emails
    scheduler/auto_stops.py        — check_deal_auto_stops
    scheduler/reengagement.py      — fire_buyer_reengagements
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import app.database as _db
from app.config import settings
from app.models.models import FailedCampaign
from app.services.scheduler.campaign_sender import process_scheduled_campaigns
from app.services.scheduler.reply_pipeline import process_buyer_replies
from app.services.scheduler.ghost_manager import detect_and_flag_ghosts, send_ghost_recovery_emails
from app.services.scheduler.auto_stops import check_deal_auto_stops
from app.services.scheduler.reengagement import fire_buyer_reengagements
from app.services.title_coordinator import process_title_emails, run_title_chases
from app.services.buyer_scoring import run_tier_promotions, reset_pitch_counters, calculate_and_update_engagement
from app.services.aging_monitor import run_aging_monitor
from app.services.buyer_insights import update_all_buyer_insights
from app.services.state_persistence import (
    save_all_state,
    load_gmail_daily_sends,
    save_gmail_daily_sends,
    save_scheduler_heartbeat,
)
from app.services.matching_service import process_queued_matches, match_all_active_deals
from app.services.resilience import get_metrics, get_idempotency_store
from app.services.groq_client import get_call_count_today, get_calls_today_date
from app.services.circuit_breaker import get_cb_queue
from app.services.dead_letter_queue import retry_failed_campaign

logger = logging.getLogger(__name__)

# Scheduler intervals
REPLY_INTERVAL_SECONDS = 30  # 30s for testing (change to 5*60 before go-live)
DAILY_INTERVAL_SECONDS = 60 * 60      # 1 hour: daily/maintenance tasks
TICK_INTERVAL_SECONDS = 15            # Outer loop sleep (15s tick — enables 30s reply intervals)

# ---------------------------------------------------------------------------
# Background task runner
# ---------------------------------------------------------------------------

_scheduler_task: asyncio.Task | None = None
_running = False


async def _scheduler_loop() -> None:
    """Run the scheduler loop with two independent intervals.

    Reply interval (30s): process_buyer_replies, detect_and_flag_ghosts,
                          process_scheduled_campaigns
    Hourly interval (60 min): all other tasks (auto-match, insights, aging,
                              reengagement, ghost recovery, midnight reset,
                              queued matches, state persistence, DLQ retry)

    On each 15-second tick, checks which interval is due and runs the
    corresponding task group. Writes a heartbeat every tick.
    """
    global _running
    _running = True

    logger.info(
        "Scheduler: background task started "
        "(reply_interval=%ds, hourly_interval=%ds, tick=%ds)",
        REPLY_INTERVAL_SECONDS,
        DAILY_INTERVAL_SECONDS,
        TICK_INTERVAL_SECONDS,
    )

    # Track when each task group last ran
    _last_reply_run = 0.0
    _last_hourly_run = 0.0
    _last_daily_run_date = None
    _last_auto_match_time = datetime.min.replace(tzinfo=timezone.utc)
    _tick_count = 0

    # ── Run auto-match once on startup ──
    try:
        if settings.auto_match_enabled:
            result = await match_all_active_deals()
            if result["deals_processed"] > 0:
                logger.info(
                    "Initial auto-match: %d deals, %d campaigns launched, %d queued",
                    result["deals_processed"],
                    result["campaigns_launched"],
                    result["buyers_queued"],
                )
            _last_auto_match_time = datetime.now(timezone.utc)
    except Exception as e:
        logger.error("Initial auto-match failed: %s", e, exc_info=True)

    try:
        while _running:
            _tick_count += 1
            now = time.monotonic()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            is_new_day = (_last_daily_run_date != today)

            # ── Write scheduler heartbeat (never blocks the loop) ──
            try:
                await save_scheduler_heartbeat(_tick_count)
            except Exception as e:
                logger.warning("Scheduler heartbeat save failed: %s", e)

            # ====================================================================
            # REPLY INTERVAL — runs every 30 seconds (time-sensitive tasks)
            # ====================================================================
            if now - _last_reply_run >= REPLY_INTERVAL_SECONDS:
                # --- Task R1: Process scheduled campaigns ---
                try:
                    sent = await process_scheduled_campaigns()
                    if sent > 0:
                        logger.info("Scheduler: sent %d campaigns", sent)
                except Exception as e:
                    logger.error("Scheduler: campaign processing failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Task R2: Check for buyer replies ---
                try:
                    processed = await process_buyer_replies()
                    if processed > 0:
                        logger.info("Scheduler: processed %d buyer replies", processed)
                except Exception as e:
                    logger.error("Scheduler: reply processing failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Task R3: Ghost detection (time-sensitive) ---
                try:
                    ghosts = await detect_and_flag_ghosts()
                    if ghosts > 0:
                        logger.info("Scheduler: detected %d ghost buyer(s)", ghosts)
                except Exception as e:
                    logger.error("Scheduler: ghost detection failed: %s", e, exc_info=True)

                _last_reply_run = now

            if not _running:
                break

            # ====================================================================
            # HOURLY INTERVAL — runs every 60 minutes (maintenance tasks)
            # ====================================================================
            if now - _last_hourly_run >= DAILY_INTERVAL_SECONDS:
                # --- Task H1: Process queued deal matches ---
                try:
                    async with _db.async_session_factory() as db:
                        released = await process_queued_matches(db)
                        if released > 0:
                            await db.commit()
                            logger.info("Scheduler: released %d queued deal matches", released)
                except Exception as e:
                    logger.error("Scheduler: queued match processing failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Task H2: Monitor title company emails ---
                try:
                    result = await process_title_emails()
                    if result.get("total_found", 0) > 0:
                        logger.info(
                            "Scheduler: processed %d title emails (%d actions)",
                            result["total_found"], result["processed"],
                        )
                except Exception as e:
                    logger.error("Scheduler: title email monitoring failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Task H3: Daily tier promotions (new day only) ---
                if is_new_day:
                    try:
                        async with _db.async_session_factory() as db:
                            await calculate_and_update_engagement(db)
                            promotions = await run_tier_promotions(db)
                            if promotions:
                                logger.info(
                                    "Scheduler: %d buyers promoted via auto-tier scoring",
                                    len(promotions),
                                )
                    except Exception as e:
                        logger.error("Scheduler: tier promotions failed: %s", e, exc_info=True)

                    if not _running:
                        break

                    # --- Task H4: Weekly fatigue counter reset ---
                    try:
                        async with _db.async_session_factory() as db:
                            reset_count = await reset_pitch_counters(db)
                            if reset_count > 0:
                                logger.info(
                                    "Scheduler: reset pitch counters for %d buyers",
                                    reset_count,
                                )
                    except Exception as e:
                        logger.error("Scheduler: pitch counter reset failed: %s", e, exc_info=True)

                if not _running:
                    break

                # ── Gmail daily send counter midnight reset ──
                try:
                    from zoneinfo import ZoneInfo
                    now_tz = datetime.now(ZoneInfo(settings.gmail_timezone))
                    counter = await load_gmail_daily_sends()
                    counter_date = counter.get("date", "")
                    today_tz = now_tz.strftime("%Y-%m-%d")
                    if counter_date and counter_date != today_tz:
                        yesterday_count = counter.get("count", 0)
                        next_midnight = now_tz.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                        await save_gmail_daily_sends(0, today_tz, next_midnight.isoformat())
                        logger.info(
                            "Gmail daily counter reset. Yesterday: %d sends.",
                            yesterday_count,
                        )
                except Exception as e:
                    logger.error("Scheduler: gmail daily counter reset failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Run independent hourly tasks concurrently ---
                async def _task_aging() -> None:
                    if is_new_day:
                        try:
                            async with _db.async_session_factory() as db:
                                aging_actions = await run_aging_monitor(db)
                                if aging_actions:
                                    logger.info(
                                        "Scheduler: %d aging escalation actions taken",
                                        len(aging_actions),
                                    )
                        except Exception as e:
                            logger.error("Scheduler: aging monitor failed: %s", e, exc_info=True)

                async def _task_insights() -> None:
                    if is_new_day and datetime.now(timezone.utc).weekday() == 0:
                        try:
                            async with _db.async_session_factory() as db:
                                count = await update_all_buyer_insights(db)
                                if count > 0:
                                    logger.info(
                                        "Scheduler: updated portfolio insights for %d buyers",
                                        count,
                                    )
                        except Exception as e:
                            logger.error("Scheduler: buyer insights update failed: %s", e, exc_info=True)

                async def _task_persist() -> None:
                    try:
                        cb_queue_items = get_cb_queue()
                        metrics = get_metrics()
                        idem_store = get_idempotency_store()
                        groq_count = get_call_count_today()
                        groq_date = get_calls_today_date() or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        await save_all_state(
                            cb_queue=cb_queue_items,
                            metrics=metrics,
                            idempotency_store=idem_store,
                            groq_count=groq_count,
                            groq_date=groq_date,
                        )
                        logger.debug("Scheduler: persisted in-memory state to DB")
                    except Exception as e:
                        logger.error("Scheduler: failed to persist in-memory state: %s", e, exc_info=True)

                async def _task_dlq_retry() -> None:
                    try:
                        async with _db.async_session_factory() as db:
                            result = await db.execute(
                                select(FailedCampaign)
                                .where(FailedCampaign.resolved == False)
                                .order_by(FailedCampaign.last_retry_at.asc().nullsfirst())
                                .limit(5)
                            )
                            failed_campaigns = result.scalars().all()
                            for dlq_entry in failed_campaigns:
                                if not _running:
                                    break
                                retry_result = await retry_failed_campaign(db, dlq_entry)
                                if retry_result.get("success"):
                                    logger.info("DLQ auto-retry succeeded for campaign %s", dlq_entry.campaign_id)
                                elif "Cooldown" in retry_result.get("error", ""):
                                    break
                    except Exception as e:
                        logger.error("Scheduler: DLQ auto-retry failed: %s", e, exc_info=True)

                async def _task_auto_match() -> None:
                    nonlocal _last_auto_match_time
                    hours_since = (
                        datetime.now(timezone.utc) - _last_auto_match_time
                    ).total_seconds() / 3600
                    if (
                        settings.auto_match_enabled
                        and hours_since >= settings.auto_match_interval_hours
                    ):
                        try:
                            result = await match_all_active_deals()
                            if result["deals_processed"] > 0:
                                logger.info(
                                    "Periodic auto-match: %d deals, %d campaigns, %d queued",
                                    result["deals_processed"],
                                    result["campaigns_launched"],
                                    result["buyers_queued"],
                                )
                            _last_auto_match_time = datetime.now(timezone.utc)
                        except Exception as e:
                            logger.error("Periodic auto-match failed: %s", e, exc_info=True)

                async def _task_ghost_recovery() -> None:
                    try:
                        sent = await send_ghost_recovery_emails()
                        if sent > 0:
                            logger.info("Scheduler: sent %d ghost recovery email(s)", sent)
                    except Exception as e:
                        logger.error("Scheduler: ghost recovery send failed: %s", e, exc_info=True)

                async def _task_reengagement() -> None:
                    try:
                        fired = await fire_buyer_reengagements()
                        if fired > 0:
                            logger.info("Scheduler: fired %d buyer re-engagement(s)", fired)
                    except Exception as e:
                        logger.error("Scheduler: buyer re-engagement failed: %s", e, exc_info=True)

                async def _task_auto_stops() -> None:
                    try:
                        affected = await check_deal_auto_stops()
                        if affected > 0:
                            logger.info("Scheduler: auto-stopped %d deal(s)", affected)
                    except Exception as e:
                        logger.error("Scheduler: auto-stop check failed: %s", e, exc_info=True)

                async def _task_title_chases() -> None:
                    try:
                        sent = await run_title_chases()
                        if sent > 0:
                            logger.info("Scheduler: sent %d title chase email(s)", sent)
                    except Exception as e:
                        logger.error("Scheduler: title chase failed: %s", e, exc_info=True)

                await asyncio.gather(
                    _task_aging(),
                    _task_insights(),
                    _task_persist(),
                    _task_dlq_retry(),
                    _task_auto_match(),
                    _task_ghost_recovery(),
                    _task_reengagement(),
                    _task_auto_stops(),
                    _task_title_chases(),
                    return_exceptions=True,
                )

                _last_hourly_run = now

            _last_daily_run_date = today

            if not _running:
                break

            # Sleep between ticks
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Scheduler: background task cancelled")
        _running = False
    except Exception as e:
        logger.error("Scheduler: fatal error: %s", e, exc_info=True)
        _running = False


def is_scheduler_running() -> bool:
    """Check if the scheduler background task is currently running."""
    return _running


def start_scheduler() -> None:
    """Start the background scheduler task.

    Safe to call multiple times — will not start a second instance.
    """
    global _scheduler_task

    if _scheduler_task is not None and not _scheduler_task.done():
        logger.warning("Scheduler: already running, skipping start")
        return

    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("Scheduler: started")


async def stop_scheduler() -> None:
    """Gracefully stop the background scheduler task."""
    global _running, _scheduler_task

    _running = False

    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("Scheduler: stopped")
