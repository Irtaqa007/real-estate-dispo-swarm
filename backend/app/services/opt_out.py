"""Email opt-out / unsubscribe management service.

Provides unsubscribe link generation for email compliance and
opt-out tracking for the Unsubscribe reply intent classifier.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unsubscribe token helpers
# ---------------------------------------------------------------------------

# Cached HMAC secret — set from settings.unsubscribe_secret on first use.
# If settings.unsubscribe_secret is not set, it is derived from database_url.
_UNSUBSCRIBE_SECRET: Optional[str] = None


def _get_secret() -> str:
    """Get or derive the unsubscribe HMAC secret."""
    global _UNSUBSCRIBE_SECRET
    if _UNSUBSCRIBE_SECRET is None:
        raw = settings.unsubscribe_secret or settings.database_url or "default-unsubscribe-secret"
        _UNSUBSCRIBE_SECRET = hashlib.sha256(raw.encode()).hexdigest()
    return _UNSUBSCRIBE_SECRET


def _generate_token(buyer_id: UUID) -> str:
    """Generate an HMAC token for a buyer's unsubscribe link.

    Token format: {buyer_id_hex}.{hmac_signature}
    """
    secret = _get_secret()
    buyer_str = str(buyer_id)
    signature = hmac.new(
        secret.encode(),
        buyer_str.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]  # 16 chars is sufficient for tamper detection
    return f"{buyer_id.hex}.{signature}"


def validate_token(token: str) -> Optional[UUID]:
    """Validate an unsubscribe token and return the buyer_id if valid.

    Args:
        token: Token string in format {buyer_id_hex}.{hmac_signature}.

    Returns:
        Buyer UUID if the token is valid, None if tampered or malformed.
    """
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        buyer_hex, signature = parts
        buyer_id = UUID(hex=buyer_hex)

        # Verify signature
        secret = _get_secret()
        expected = hmac.new(
            secret.encode(),
            str(buyer_id).encode(),
            hashlib.sha256,
        ).hexdigest()[:16]

        if not hmac.compare_digest(signature, expected):
            logger.warning("Invalid unsubscribe token signature for buyer %s", buyer_id)
            return None

        return buyer_id
    except (ValueError, AttributeError):
        logger.warning("Malformed unsubscribe token: %.50s", str(token)[:50])
        return None


def build_unsubscribe_url(buyer_id: UUID) -> str:
    """Build the full unsubscribe URL for a given buyer.

    The link points to the API endpoint that processes the opt-out.
    The buyer ID is HMAC-signed to prevent tampering.

    Args:
        buyer_id: The buyer's UUID.

    Returns:
        Full unsubscribe URL string.
    """
    token = _generate_token(buyer_id)
    base = settings.base_url.rstrip("/")
    return f"{base}/api/buyers/unsubscribe/{token}"


# ---------------------------------------------------------------------------
# Unsubscribe footer for emails
# ---------------------------------------------------------------------------

UNSUBSCRIBE_FOOTER = (
    "\n\n---\n"
    "To stop receiving emails about off-market deals, "
    "unsubscribe here: {unsubscribe_url}"
)


def append_unsubscribe_footer(body: str, buyer_id: UUID) -> str:
    """Append a CAN-SPAM compliant unsubscribe footer to an email body.

    Args:
        body: The email body text.
        buyer_id: The buyer's UUID for generating the unique link.

    Returns:
        Body with unsubscribe footer appended.
    """
    url = build_unsubscribe_url(buyer_id)
    return body + UNSUBSCRIBE_FOOTER.format(unsubscribe_url=url)
