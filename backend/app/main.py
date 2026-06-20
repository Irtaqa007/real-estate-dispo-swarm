import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.middleware import (
    http_exception_handler,
    response_validation_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)

from app.config import settings

logger = logging.getLogger(__name__)
from app.routers import activity, buyers, campaigns, deals, failed_campaigns, health, jv_partners, matching, title
from app.database import register_pgvector_extension, test_connection, initialize_db
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.state_persistence import load_all_state
from app.services.circuit_breaker import gmail_circuit_breaker
from app.services.resilience import restore_metrics, restore_idempotency_store
from app.services.groq_client import restore_daily_counter


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize resources on startup, clean up on shutdown."""
    # Startup: initialize database engine with retries
    await initialize_db()
    
    # Test connection (non-fatal)
    try:
        await test_connection()
    except Exception as e:
        logger.warning(
            "Database connection test failed on startup. Full exception:\n%s. "
            "App will start but DB-dependent features may fail.",
            e, exc_info=True,
        )
    
    # Register pgvector extension (non-fatal)
    try:
        await register_pgvector_extension()
    except Exception as e:
        logger.warning("Failed to register pgvector extension: %s. Vector features may not be available.", e)

    # Restore persistent in-memory state from the database
    await _restore_persisted_state()

    # Wire up circuit breaker persistence callback
    from app.services.state_persistence import save_circuit_breaker_queue
    from app.services.circuit_breaker import gmail_circuit_breaker

    async def _persist_cb_queue() -> None:
        """Persist the circuit breaker queue after each mutation."""
        try:
            queue = [
                {"campaign_id": item.campaign_id, "to_email": item.to_email, "subject": item.subject}
                for item in gmail_circuit_breaker._queue
            ]
            await save_circuit_breaker_queue(queue)
        except Exception as e:
            logger.warning("Failed to persist circuit breaker queue: %s", e, exc_info=True)

    gmail_circuit_breaker.set_persistence_callback(_persist_cb_queue)

    # Start background campaign scheduler (runs every hour)
    start_scheduler()
    yield
    # Shutdown: stop scheduler and cleanup
    await stop_scheduler()


async def _restore_persisted_state() -> None:
    """Load all previously persisted subsystem state from the DB.

    Called once on application startup to restore:
    - Circuit breaker queued emails
    - Metrics counters
    - Idempotency cache
    - Groq daily call counter

    If the DB is unreachable, state is gracefully skipped (logged warning).
    """
    try:
        state = await load_all_state()

        # 1. Circuit breaker queue
        cb_queue = state.get("circuit_breaker_queue", [])
        if cb_queue:
            await gmail_circuit_breaker.restore_queue(cb_queue)

        # 2. Metrics counters
        metrics = state.get("metrics", {})
        if metrics:
            restore_metrics(metrics)

        # 3. Idempotency cache
        idem_store = state.get("idempotency_store", {})
        if idem_store:
            restore_idempotency_store(idem_store)

        # 4. Groq daily call counter
        groq_state = state.get("groq_daily_counter", {})
        if groq_state and "count" in groq_state:
            restore_daily_counter(groq_state["count"], groq_state["date"])

        logger.info(
            "Persisted state restored: %d CB queue items, %d metrics, %d idem entries, "
            "Groq daily=%s",
            len(cb_queue),
            len(metrics),
            len(idem_store),
            groq_state.get("count", "N/A"),
        )
    except Exception as e:
        logger.warning(
            "Could not restore persisted state (DB not ready yet?): %s",
            e, exc_info=True,
        )


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    debug=settings.debug,
    lifespan=lifespan,
)

# Exception handlers — consistent error serialization
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(ResponseValidationError, response_validation_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(health.router)
app.include_router(buyers.router)
app.include_router(jv_partners.router)
app.include_router(deals.router)
app.include_router(matching.router)
app.include_router(campaigns.router)
app.include_router(failed_campaigns.router)
app.include_router(activity.router)
app.include_router(title.router)


@app.get("/")
async def root():
    """Redirect root to Swagger docs."""
    return RedirectResponse(url="/docs")
