"""Dedicated Gmail circuit breaker with time-window-based failure tracking.

Provides a sliding-window circuit breaker specifically for Gmail SMTP:
- Tracks failures within a 60-second rolling window
- Opens the circuit after 5 failures in that window
- During open: queues email metadata in memory, logs warnings, fails fast
- After 300s: transitions to half-open, allows a single test email
- On test success: closes the circuit and drains the queue
- On test failure: re-opens the circuit for another 300s

Usage:
    from app.services.circuit_breaker import gmail_circuit_breaker

    # Check before sending
    ctx = gmail_circuit_breaker.check()
    if not ctx.allowed:
        gmail_circuit_breaker.queue(campaign_id, buyer_email, subject)
        raise CircuitBreakerOpenError(...)

    # Report result after sending
    gmail_circuit_breaker.report(success=True)
"""

import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.resilience import record_metric

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FAILURE_WINDOW_SECONDS = 60    # Count failures within this window
FAILURE_THRESHOLD = 5          # Max failures before opening
OPEN_DURATION_SECONDS = 300    # How long the circuit stays open (5 min)
MAX_QUEUED_ITEMS = 1000        # Safety limit for queued messages

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class QueuedEmail:
    """Metadata for an email queued while the circuit is open."""
    campaign_id: str
    to_email: str
    subject: str
    queued_at: float = field(default_factory=time.monotonic)


@dataclass
class CircuitContext:
    """Result of a circuit breaker check, returned to the caller."""
    allowed: bool
    state: str          # closed, open, half_open
    failures_in_window: int
    queued_count: int
    recovery_remaining: Optional[float] = None  # seconds until half-open


# ---------------------------------------------------------------------------
# Gmail Circuit Breaker
# ---------------------------------------------------------------------------


class GmailCircuitBreaker:
    """Sliding-window circuit breaker for Gmail SMTP.

    Tracks failure timestamps in a deque. When the count within the
    rolling window exceeds the threshold, the circuit opens. After the
    recovery timeout, it transitions to half-open and allows a test.
    """

    def __init__(
        self,
        name: str = "gmail-send",
        failure_window: float = FAILURE_WINDOW_SECONDS,
        failure_threshold: int = FAILURE_THRESHOLD,
        open_duration: float = OPEN_DURATION_SECONDS,
        max_queue: int = MAX_QUEUED_ITEMS,
    ):
        self.name = name
        self.failure_window = failure_window
        self.failure_threshold = failure_threshold
        self.open_duration = open_duration
        self.max_queue = max_queue

        # Sliding window of failure timestamps
        self._failure_times: collections.deque = collections.deque()

        # Circuit state
        self._state = "closed"  # closed, open, half_open
        self._state_changed_at: float = time.monotonic()
        self._total_trips = 0

        # Queued emails during open state
        self._queue: List[QueuedEmail] = []

        # Optional async callback invoked after queue/drain state changes
        # so external code can persist queue state to the database.
        # Signature: async callback() -> None
        self._persist_callback = None

    def set_persistence_callback(self, callback):
        """Set an async callback invoked after the queue changes.

        Args:
            callback: An async callable with no arguments that persists
                      the current queue state to the database.
        """
        self._persist_callback = callback

    async def restore_queue(self, items: List[Dict[str, str]]) -> None:
        """Restore queued emails from persistence on startup.

        Args:
            items: List of dicts with keys: campaign_id, to_email, subject.
        """
        for item in items:
            self._queue.append(QueuedEmail(
                campaign_id=item.get("campaign_id", ""),
                to_email=item.get("to_email", ""),
                subject=item.get("subject", ""),
                queued_at=0.0,  # Will be pruned; kept for retry on startup
            ))
        if items:
            logger.info(
                "Circuit breaker '%s' restored %d queued emails from persistent state",
                self.name, len(items),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self) -> CircuitContext:
        """Check if the circuit allows a request.

        Returns a CircuitContext with the decision and diagnostics.
        The caller must call report(success=True/False) after the attempt.
        """
        self._prune_failures()
        failures = len(self._failure_times)
        now = time.monotonic()

        if self._state == "closed":
            if failures >= self.failure_threshold:
                self._open_circuit()
                return self._build_context(allowed=False)

            return self._build_context(allowed=True)

        if self._state == "open":
            elapsed = now - self._state_changed_at
            if elapsed >= self.open_duration:
                self._state = "half_open"
                self._state_changed_at = now
                logger.info(
                    "Circuit breaker '%s' HALF_OPEN — allowing trial request "
                    "(failures=%d, queued=%d)",
                    self.name, failures, len(self._queue),
                )
                return self._build_context(allowed=True)

            recovery_remaining = self.open_duration - elapsed
            return self._build_context(
                allowed=False,
                recovery_remaining=recovery_remaining,
            )

        # half_open — allow one trial request
        return self._build_context(allowed=True)

    def report(self, success: bool) -> None:
        """Report the outcome of an email send attempt.

        Args:
            success: True if the email was sent successfully.
        """
        if success:
            self._record_success()
        else:
            self._record_failure()

    def queue(
        self,
        campaign_id: str,
        to_email: str,
        subject: str,
    ) -> bool:
        """Queue an email for later delivery while the circuit is open.

        Args:
            campaign_id: The campaign UUID string.
            to_email: Recipient email address.
            subject: Email subject line.

        Returns:
            True if the email was queued, False if the queue is full.
        """
        if len(self._queue) >= self.max_queue:
            logger.warning(
                "Circuit breaker '%s' queue full (%d items) — "
                "dropping email for campaign %s",
                self.name, self.max_queue, campaign_id,
            )
            return False

        self._queue.append(QueuedEmail(
            campaign_id=campaign_id,
            to_email=to_email,
            subject=subject,
        ))
        logger.info(
            "Circuit breaker '%s' queued campaign %s (queue size: %d)",
            self.name, campaign_id, len(self._queue),
        )
        # Notify persistence layer (fire-and-forget via task if needed)
        if self._persist_callback:
            try:
                import asyncio
                asyncio.ensure_future(self._persist_callback())
            except Exception as e:
                logger.warning("Persistence callback failed after queue: %s", e, exc_info=True)
        return True

    def drain_queue(self) -> List[QueuedEmail]:
        """Drain and return all queued emails.

        Called after the circuit closes to retry queued items.
        """
        items = list(self._queue)
        self._queue.clear()
        if items:
            logger.info(
                "Circuit breaker '%s' draining %d queued emails",
                self.name, len(items),
            )
            # Notify persistence layer
            if self._persist_callback:
                try:
                    import asyncio
                    asyncio.ensure_future(self._persist_callback())
                except Exception as e:
                    logger.warning("Persistence callback failed after drain: %s", e, exc_info=True)
        return items

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _open_circuit(self) -> None:
        """Transition from closed to open."""
        self._state = "open"
        self._state_changed_at = time.monotonic()
        self._total_trips += 1

        record_metric("circuit_breaker_trips")
        record_metric("gmail_circuit_breaker_open")

        logger.error(
            "Circuit breaker '%s' OPENED — %d failures in %.0fs window. "
            "Queuing emails for %ds. Total trips: %d",
            self.name,
            len(self._failure_times),
            self.failure_window,
            self.open_duration,
            self._total_trips,
        )

    def _record_success(self) -> None:
        """Handle a successful send."""
        if self._state == "half_open":
            logger.info(
                "Circuit breaker '%s' CLOSED — trial request succeeded. "
                "%d queued emails to drain.",
                self.name, len(self._queue),
            )
            self._state = "closed"
            self._state_changed_at = time.monotonic()
            self._failure_times.clear()

            # Drain queue for retry
            queued = self.drain_queue()
            if queued:
                record_metric("gmail_circuit_breaker_drained", len(queued))

        elif self._state == "closed":
            # Normal operation — prune old failures
            self._prune_failures()

    def _record_failure(self) -> None:
        """Handle a failed send."""
        now = time.monotonic()
        self._failure_times.append(now)
        self._prune_failures()

        failures = len(self._failure_times)

        if self._state == "half_open":
            # Trial request failed — re-open the circuit
            self._open_circuit()
            logger.error(
                "Circuit breaker '%s' half-open trial FAILED — "
                "re-opening for %ds",
                self.name, self.open_duration,
            )
        elif self._state == "closed" and failures >= self.failure_threshold:
            self._open_circuit()

    def _prune_failures(self) -> None:
        """Remove failure timestamps outside the rolling window."""
        cutoff = time.monotonic() - self.failure_window
        while self._failure_times and self._failure_times[0] < cutoff:
            self._failure_times.popleft()

    def _build_context(
        self,
        allowed: bool,
        recovery_remaining: Optional[float] = None,
    ) -> CircuitContext:
        """Build a CircuitContext from current state."""
        return CircuitContext(
            allowed=allowed,
            state=self._state,
            failures_in_window=len(self._failure_times),
            queued_count=len(self._queue),
            recovery_remaining=recovery_remaining,
        )

    # ------------------------------------------------------------------
    # Monitoring / diagnostics
    # ------------------------------------------------------------------

    def get_queue_items(self) -> List[Dict[str, str]]:
        """Return the queued email items as serializable dicts."""
        return [
            {
                "campaign_id": item.campaign_id,
                "to_email": item.to_email,
                "subject": item.subject,
            }
            for item in self._queue
        ]

    def get_state(self) -> Dict[str, Any]:
        """Return a snapshot of the current state for monitoring."""
        self._prune_failures()
        now = time.monotonic()

        elapsed_since_change = now - self._state_changed_at
        remaining = None
        if self._state == "open":
            remaining = max(0.0, self.open_duration - elapsed_since_change)
        elif self._state == "half_open":
            remaining = 0.0

        return {
            "name": self.name,
            "state": self._state,
            "failures_in_window": len(self._failure_times),
            "failure_threshold": self.failure_threshold,
            "failure_window_seconds": self.failure_window,
            "open_duration_seconds": self.open_duration,
            "elapsed_since_change_seconds": elapsed_since_change,
            "recovery_remaining_seconds": remaining,
            "queued_count": len(self._queue),
            "total_trips": self._total_trips,
        }


# ---------------------------------------------------------------------------
# Singleton instance (used by gmail_service)
# ---------------------------------------------------------------------------

gmail_circuit_breaker = GmailCircuitBreaker(
    name="gmail-send",
    failure_window=60.0,
    failure_threshold=5,
    open_duration=300.0,
    max_queue=1000,
)


# ---------------------------------------------------------------------------
# Convenience accessor for scheduler
# ---------------------------------------------------------------------------


def get_cb_queue() -> List[Dict[str, str]]:
    """Get the circuit breaker's queued email items as serializable dicts."""
    return gmail_circuit_breaker.get_queue_items()


# ---------------------------------------------------------------------------
# Decorator-based integration (alternative to manual check/report)
# ---------------------------------------------------------------------------


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is open and the call is rejected."""
    pass


def with_gmail_circuit_breaker(func):
    """Decorator that wraps an async function with the Gmail circuit breaker.

    The decorated function is expected to raise on failure. The circuit
    breaker will record successes and failures automatically.

    If the circuit is open, the function is not called and
    CircuitBreakerOpenError is raised instead.
    """
    import functools

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        ctx = gmail_circuit_breaker.check()
        if not ctx.allowed:
            raise CircuitBreakerOpenError(
                f"Gmail circuit breaker is OPEN ({ctx.state}). "
                f"{ctx.failures_in_window} failures in window. "
                f"Recovery in ~{ctx.recovery_remaining:.0f}s. "
                f"{ctx.queued_count} emails queued."
            )
        try:
            result = await func(*args, **kwargs)
            gmail_circuit_breaker.report(success=True)
            return result
        except Exception as e:
            gmail_circuit_breaker.report(success=False)
            raise

    return wrapper
