"""Expanded health monitoring endpoint.

Provides a comprehensive health check for the entire system:
- Database connectivity
- Gmail circuit breaker state
- Groq API rate limit status
- Cohere embedding API status
- Scheduler running status
- Pending and failed campaign counts
- Resilience subsystem metrics
"""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.config import settings
from app.database import engine, get_db
from app.models.schemas import Campaign, FailedCampaign
from app.services.circuit_breaker import gmail_circuit_breaker
from app.services.embeddings import check_cohere_health
from app.services.groq_client import get_rate_limit_status, groq_chat_completion
from app.services.resilience import get_resilience_health
from app.services.scheduler import is_scheduler_running
from app.services.state_persistence import get_gmail_send_status, get_scheduler_heartbeat

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health_check_fast(db: AsyncSession = Depends(get_db)):
    """Fast, minimal health check for Docker/orchestrator healthchecks.

    Does exactly ONE database round trip and nothing else. This endpoint
    is polled every 10s by Docker's healthcheck -- it must not do
    multiple sequential DB queries or external API calls (Cohere, Groq),
    since each adds real latency (especially under NullPool, where every
    query opens a fresh connection) and can push total response time
    past the healthcheck timeout, causing false-positive "unhealthy"
    states even though the app is actually fine.

    For full system diagnostics (Gmail circuit breaker, Groq/Cohere
    status, campaign counts, resilience metrics), use
    GET /api/health/detailed instead.
    """
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        return {"status": "degraded"}


@router.get("/api/health/detailed")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Comprehensive health check for the entire system.

    Returns:
        dict with: status, timestamp, db, gmail, groq, scheduler,
                  pending_campaigns, failed_campaigns, resilience.

    NOTE: This endpoint is intentionally NOT used by Docker's
    healthcheck -- it does 5+ sequential DB queries plus an external
    Cohere API call, which is too slow/heavy to poll every 10s. Use
    this for manual diagnostics or a dashboard status page instead.
    See /api/health for the fast version used by orchestration.
    """
    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Database check
    # ------------------------------------------------------------------
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "disconnected"

    # ------------------------------------------------------------------
    # Gmail circuit breaker state
    # ------------------------------------------------------------------
    cb_state = gmail_circuit_breaker.get_state()
    gmail_status = cb_state["state"]
    gmail_detail = {
        "state": cb_state["state"],
        "failures_in_window": cb_state["failures_in_window"],
        "recovery_remaining_seconds": cb_state["recovery_remaining_seconds"],
        "queued_count": cb_state["queued_count"],
        "total_trips": cb_state["total_trips"],
    }

    # ------------------------------------------------------------------
    # Groq status — real API connectivity check
    # ------------------------------------------------------------------
    groq_status = "available"
    groq_detail = get_rate_limit_status()

    # Check rate limit first (cheap, no API call)
    if groq_detail["minute_limit_remaining"] <= 0:
        groq_status = "rate_limited"
    else:
        # Make a real minimal API call to verify connectivity
        try:
            await asyncio.wait_for(
                groq_chat_completion(
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    model=settings.groq_fallback_model,
                ),
                timeout=5.0,
            )
            groq_status = "ok"
        except asyncio.TimeoutError:
            groq_status = "timeout"
        except Exception as groq_err:
            groq_status = "error"
            groq_detail["error"] = str(groq_err)[:100]

    # ------------------------------------------------------------------
    # Scheduler status
    # ------------------------------------------------------------------
    scheduler_running = is_scheduler_running()

    # ------------------------------------------------------------------
    # Campaign counts
    # ------------------------------------------------------------------
    pending_result = await db.execute(
        select(func.count(Campaign.id)).where(Campaign.status == "Queued")
    )
    pending_count = pending_result.scalar() or 0

    failed_result = await db.execute(
        select(func.count(FailedCampaign.id)).where(FailedCampaign.resolved == False)
    )
    failed_count = failed_result.scalar() or 0

    # ------------------------------------------------------------------
    # Cohere embedding API status
    # ------------------------------------------------------------------
    cohere_result = await check_cohere_health()
    cohere_status = "ok" if cohere_result["reachable"] else "unavailable"
    cohere_detail = {
        "configured": cohere_result["configured"],
        "reachable": cohere_result["reachable"],
        "latency_ms": cohere_result["latency_ms"],
    }
    if cohere_result.get("error"):
        cohere_detail["error"] = cohere_result["error"]

    # ------------------------------------------------------------------
    # Groq connectivity check — derived from the real API call above
    # to avoid making two Groq calls per health check.
    # ------------------------------------------------------------------
    groq_health = {
        "status": "ok" if groq_status == "ok" else groq_status,
    }
    if groq_detail.get("error"):
        groq_health["detail"] = groq_detail["error"]

    # ------------------------------------------------------------------
    # Scheduler heartbeat check
    # ------------------------------------------------------------------
    async def _check_scheduler_heartbeat() -> dict:
        try:
            heartbeat = await get_scheduler_heartbeat()
            if heartbeat is None:
                return {"status": "stale", "detail": "No heartbeat recorded yet"}
            last_tick = heartbeat.get("last_tick", "")
            tick_count = heartbeat.get("tick_count", 0)
            if last_tick:
                last_tick_dt = datetime.fromisoformat(last_tick)
                hours_since = (now - last_tick_dt).total_seconds() / 3600
                if hours_since > 2:
                    return {
                        "status": "stale",
                        "last_tick": last_tick,
                        "tick_count": tick_count,
                        "detail": f"Last tick was {hours_since:.1f} hours ago",
                    }
                return {"status": "ok", "last_tick": last_tick, "tick_count": tick_count}
            return {"status": "stale", "detail": "Heartbeat has no timestamp"}
        except Exception as e:
            return {"status": "error", "detail": str(e)[:100]}

    scheduler_heartbeat = await _check_scheduler_heartbeat()

    # ------------------------------------------------------------------
    # Resilience subsystem
    # ------------------------------------------------------------------
    resilience = await get_resilience_health()

    # ------------------------------------------------------------------
    # Determine overall status
    # Uses only critical services (DB, Gmail circuit breaker, Groq).
    # Cohere is optional — DNS flakiness in Docker should not degrade the
    # health check result.
    # ------------------------------------------------------------------
    status = "ok"
    if db_status != "connected":
        status = "degraded"
    if cb_state["state"] == "open":
        status = "degraded"
    if groq_health.get("status") == "error":
        status = "degraded"
    if scheduler_heartbeat.get("status") == "stale":
        status = "degraded"

    return {
        "status": status,
        "timestamp": now.isoformat(),
        "version": settings.version,
        "environment": settings.environment,
        "db": db_status,
        "gmail": gmail_status,
        "gmail_detail": gmail_detail,
        "groq": groq_status,
        "groq_detail": groq_detail,
        "groq_health": groq_health,
        "cohere": cohere_status,
        "cohere_detail": cohere_detail,
        "scheduler": "running" if scheduler_running else "stopped",
        "scheduler_heartbeat": scheduler_heartbeat,
        "pending_campaigns": pending_count,
        "failed_campaigns": failed_count,
        "resilience": {
            "metrics": resilience["metrics"],
            "circuit_breakers": resilience["circuit_breakers"],
            "idempotency_store_size": resilience["idempotency_store_size"],
        },
    }


@router.get("/api/sending/status")
async def sending_status():
    """Get the current Gmail send quota status for the dashboard widget.

    Returns a snapshot of today's sends vs daily cap. Fast — single DB read
    from app_state only, no external calls.

    Returns:
        dict: sends_today, daily_cap, remaining, percent_used,
              cap_hit, warning_threshold_hit, resets_at.
    """
    return await get_gmail_send_status()