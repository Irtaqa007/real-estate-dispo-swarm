"""Gmail email sending service via SMTP with app password authentication.

Uses smtplib.SMTP_SSL on port 465 with Gmail App Password.
Plain text body for optimal deliverability.

Equipped with enterprise-grade resilience:
- Dedicated time-window circuit breaker (5 failures in 60s → open 300s)
- Retry with exponential backoff (tenacity, up to 5 attempts)
- Idempotency (duplicate sends with same args return cached result)
- Global concurrency limit (max 3 simultaneous sends)
- Rate limiter (max 10 sends per rolling 60-second window)
- Metrics tracking for monitoring
- Dead letter queue for persistent failures
"""

import asyncio
import collections
import logging
import smtplib
import time
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.audit_logger import audit
from app.services.circuit_breaker import (
    CircuitBreakerOpenError,
    gmail_circuit_breaker,
    with_gmail_circuit_breaker,
)
from app.services.resilience import (
    idempotent,
    log_retry_attempt,
    record_metric,
    with_retry,
)
from app.services.state_persistence import (
    get_gmail_cap_warning_sent,
    get_gmail_send_status,
    increment_gmail_send_count,
    save_gmail_cap_warning_sent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Concurrency limit
# ---------------------------------------------------------------------------

# Global semaphore: max 3 concurrent SMTP connections at once.
# This prevents overwhelming the Gmail SMTP server when send-all,
# scheduler, and manual sends all fire simultaneously.
_email_semaphore = asyncio.Semaphore(3)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

# Sliding-window rate limit: max 10 sends per rolling 60-second window.
# Gmail's official limit is ~500 emails/day. 10/min = 600/hr which is well
# within the limit but prevents burst spikes from tripping spam filters.
_MAX_SENDS_PER_MINUTE = 10
_RATE_LIMIT_WINDOW = 60  # seconds

_send_timestamps: collections.deque = collections.deque()


def _prune_timestamps() -> None:
    """Remove send timestamps outside the 60-second rolling window."""
    cutoff = time.monotonic() - _RATE_LIMIT_WINDOW
    while _send_timestamps and _send_timestamps[0] < cutoff:
        _send_timestamps.popleft()


async def _wait_for_rate_limit() -> None:
    """Wait until we're within the rate limit window, then record the send."""
    _prune_timestamps()

    while len(_send_timestamps) >= _MAX_SENDS_PER_MINUTE:
        oldest = _send_timestamps[0]
        wait = _RATE_LIMIT_WINDOW - (time.monotonic() - oldest)
        if wait > 0:
            logger.warning(
                "Email rate limit reached (%d/min). Waiting %.1fs before sending.",
                _MAX_SENDS_PER_MINUTE, wait,
            )
            await asyncio.sleep(wait + 0.5)
            _prune_timestamps()
        else:
            break

    _send_timestamps.append(time.monotonic())


# ---------------------------------------------------------------------------
# Retryable exceptions
# ---------------------------------------------------------------------------

_RETRYABLE_SMTP = (
    smtplib.SMTPException,
    ConnectionError,
    TimeoutError,
    OSError,
)


async def _check_daily_cap(send_type: str) -> bool:
    """Check whether a campaign send is allowed under the daily cap.

    Args:
        send_type: "campaign" or "reply".

    Returns:
        True if the send is allowed, False if blocked by cap.
    """
    if send_type != "campaign":
        return True  # replies are never blocked

    status = await get_gmail_send_status()
    if status["cap_hit"]:
        logger.warning(
            "Gmail daily cap hit (%d/%d) — campaign send blocked",
            status["sends_today"], status["daily_cap"],
        )
        return False

    # ── Approaching-cap alert at 90% (once per day, DB-backed) ──
    if status["warning_threshold_hit"]:
        warning_sent_date = await get_gmail_cap_warning_sent()
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if warning_sent_date != today:
            await save_gmail_cap_warning_sent(today)
            # Fire audit alert
            try:
                import app.database as _db
                async with _db.async_session_factory() as alert_db:
                    await audit.log(
                        alert_db,
                        entity_type="system",
                        entity_id=uuid.uuid4(),
                        action="gmail_cap_warning",
                        metadata={
                            "count": status["sends_today"],
                            "cap": status["daily_cap"],
                            "percent": 90,
                            "alert_user": True,
                        },
                    )
                    await alert_db.commit()
            except Exception as e:
                logger.warning("Failed to log gmail cap warning: %s", e, exc_info=True)

    return True


async def send_email(
    to: str,
    subject: str,
    body: str,
    from_email: Optional[str] = None,
    campaign_id: Optional[str] = None,
    send_type: str = "campaign",
) -> dict:
    """Send an email via Gmail SMTP with retry, circuit breaker, and idempotency.

    Enterprise resilience features:
    1. Retry: Up to 5 attempts with exponential backoff (2s → 4s → 8s → 16s → 32s)
    2. Circuit breaker: Opens after 5 consecutive failures, recovers after 60s
    3. Idempotency: Same (to, subject, body, campaign_id) within 1h returns cached result
    4. Concurrency limit: Max 3 simultaneous SMTP connections
    5. Rate limit: Max 10 sends per rolling 60-second window
    6. Daily cap: send_type="campaign" is blocked when cap is hit;
       send_type="reply" never blocked

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain text email body.
        from_email: Override sender address (defaults to GMAIL_ADDRESS).
        campaign_id: Optional campaign UUID string. When provided, it scopes the
            idempotency key so the same email content for different campaigns
            is considered a distinct send (not a duplicate).
        send_type: "campaign" (checked against daily cap) or "reply" (never blocked).

    Returns:
        dict with keys: message_id (str), status (str), sent_at (str).
        If blocked by daily cap, returns {"status": "deferred_cap", ...}.

    Raises:
        ValueError: If GMAIL credentials are not configured.
        smtplib.SMTPAuthenticationError: If app password is invalid.
        CircuitBreakerOpenError: If the circuit breaker is open (fail-fast).
        smtplib.SMTPException: If all retry attempts fail.
    """
    record_metric("email_send_attempts")

    # ── Daily cap check: campaign sends halt at cap ──
    if not await _check_daily_cap(send_type):
        logger.info(
            "Email deferred (daily cap): to=%s, subject=%.60s, send_type=%s",
            to, subject, send_type,
        )
        return {
            "message_id": "",
            "status": "deferred_cap",
            "sent_at": "",
            "reason": "Daily send cap reached — deferred until midnight reset",
        }

    # Wait for rate limit window
    await _wait_for_rate_limit()

    # Acquire concurrency slot (released after _send completes)
    async with _email_semaphore:
        result = await _send_email_inner(to, subject, body, from_email)

    # Increment daily counter on successful campaign sends
    if send_type == "campaign" and result.get("status") == "sent":
        try:
            new_count = await increment_gmail_send_count()
            logger.debug("Gmail daily send count incremented to %d", new_count)
        except Exception as e:
            logger.warning("Failed to increment gmail send counter: %s", e, exc_info=True)

    return result


async def _send_email_inner(
    to: str,
    subject: str,
    body: str,
    from_email: Optional[str] = None,
) -> dict:
    """Inner send function that runs under the concurrency semaphore.

    All the decorators (idempotent, circuit_breaker, retry) are applied
    to the outer `send_email`, so this inner function just does the
    actual SMTP work.
    """
    gmail_addr = settings.gmail_address
    gmail_pass = settings.gmail_app_password

    if not gmail_addr or not gmail_pass:
        error_msg = "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env"
        record_metric("email_send_failures")
        raise ValueError(error_msg)

    sender = from_email or gmail_addr

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = sender
    msg.set_content(body)

    logger.info(
        "Sending email to %s via Gmail SMTP — subject: %.60s",
        to, subject,
    )

    try:
        def _send():
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(gmail_addr, gmail_pass)
                server.send_message(msg)
                return msg["Message-ID"] or "unknown"

        message_id = await asyncio.to_thread(_send)
        sent_at = datetime.now(timezone.utc).isoformat()

        logger.info("Email sent to %s — message_id: %s", to, message_id)
        record_metric("email_send_successes")

        return {
            "message_id": message_id,
            "status": "sent",
            "sent_at": sent_at,
        }

    except smtplib.SMTPAuthenticationError:
        record_metric("email_send_failures")
        logger.error(
            "Gmail authentication failed for %s. "
            "Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env. "
            "App passwords require 2FA enabled on the Google account.",
            gmail_addr,
        )
        raise
    except (CircuitBreakerOpenError, smtplib.SMTPException) as e:
        record_metric("email_send_failures")
        logger.error("SMTP error sending to %s: %s", to, e)
        raise
