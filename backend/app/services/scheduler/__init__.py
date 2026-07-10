"""Scheduler sub-modules package — split from the original monolithic scheduler.py.

Re-exports all public functions so existing imports like
    from app.services.scheduler import process_scheduled_campaigns
still work.
"""

from app.services.scheduler.campaign_sender import process_scheduled_campaigns
from app.services.scheduler.reply_pipeline import process_buyer_replies
from app.services.scheduler.ghost_manager import detect_and_flag_ghosts, send_ghost_recovery_emails
from app.services.scheduler.auto_stops import check_deal_auto_stops
from app.services.scheduler.reengagement import fire_buyer_reengagements

# Lifecycle functions from the scheduler runner (loop + start/stop/is_running)
from app.services.scheduler.runner import (
    start_scheduler,
    stop_scheduler,
    is_scheduler_running,
    _scheduler_loop,
    REPLY_INTERVAL_SECONDS,
    DAILY_INTERVAL_SECONDS,
    TICK_INTERVAL_SECONDS,
)

# Re-export helper functions used by the scheduler loop and tests
from app.services.title_coordinator import process_title_emails, run_title_chases, send_assignment_contract
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

__all__ = [
    # Core sub-module functions
    "process_scheduled_campaigns",
    "process_buyer_replies",
    "detect_and_flag_ghosts",
    "send_ghost_recovery_emails",
    "check_deal_auto_stops",
    "fire_buyer_reengagements",
    # Lifecycle
    "start_scheduler",
    "stop_scheduler",
    "is_scheduler_running",
    "_scheduler_loop",
    "REPLY_INTERVAL_SECONDS",
    "DAILY_INTERVAL_SECONDS",
    "TICK_INTERVAL_SECONDS",
    # Helper functions
    "process_title_emails",
    "run_title_chases",
    "send_assignment_contract",
    "run_tier_promotions",
    "reset_pitch_counters",
    "calculate_and_update_engagement",
    "run_aging_monitor",
    "update_all_buyer_insights",
    "save_all_state",
    "load_gmail_daily_sends",
    "save_gmail_daily_sends",
    "save_scheduler_heartbeat",
    "process_queued_matches",
    "match_all_active_deals",
    "get_metrics",
    "get_idempotency_store",
    "get_call_count_today",
    "get_calls_today_date",
    "get_cb_queue",
    "retry_failed_campaign",
]
