"""Gmail reply monitoring service using IMAP with app password authentication.

Connects to Gmail via IMAP, searches for unread emails from known buyer addresses,
extracts reply content, and returns structured data for processing.

Equipped with enterprise-grade retry logic for IMAP connection resilience.
"""

import asyncio
import email
import imaplib
import logging
import re
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import List, Optional

import tenacity
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.resilience import log_retry_attempt, record_metric

logger = logging.getLogger(__name__)


async def check_for_replies(buyer_emails: List[str]) -> List[dict]:
    """Poll Gmail inbox for new replies from known buyer emails.

    Connects to Gmail via IMAP (SSL), searches for UNSEEN messages,
    filters by known buyer email addresses, extracts metadata and body,
    marks each processed message as read.

    Args:
        buyer_emails: List of buyer email addresses to check for replies from.

    Returns:
        List of dicts with keys:
            message_id, thread_id, from_email, subject, body, received_at
    """
    gmail_addr = settings.gmail_address
    gmail_pass = settings.gmail_app_password

    if not gmail_addr or not gmail_pass:
        logger.error("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        return []

    # Lower-case for case-insensitive comparison
    buyer_set = {e.lower() for e in buyer_emails if e}

    # Build a retry-wrapped version of the sync IMAP fetch
    def _fetch_replies() -> List[dict]:
        """Connect to IMAP and fetch replies (runs in thread pool)."""
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_addr, gmail_pass)
        mail.select("INBOX")

        # Search for unseen (unread) emails
        status, raw_ids = mail.search(None, "UNSEEN")
        if status != "OK" or not raw_ids[0]:
            mail.logout()
            return []

        replies: List[dict] = []
        message_ids = raw_ids[0].split()

        for msg_id in message_ids:
            status, msg_data = mail.fetch(msg_id, "(RFC822 FLAGS)")
            if status != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            from_field = _decode_header_value(msg.get("From", ""))
            from_addr = _extract_email(from_field)

            # Only process if from a known buyer
            logger.info("IMAP: email from '%s' | buyer_set has %d emails | match: %s",
                        from_addr, len(buyer_set), from_addr.lower() in buyer_set)
            if from_addr.lower() not in buyer_set:
                continue

            message_id = (msg.get("Message-ID", "") or "").strip()
            # Extract raw In-Reply-To and References for thread-aware matching
            in_reply_to_raw = (msg.get("In-Reply-To", "") or "").strip()
            references_raw = (msg.get("References", "") or "").strip()
            # Use References or In-Reply-To as thread_id (fallback to Message-ID)
            thread_id = (
                (references_raw or in_reply_to_raw or message_id)
                .strip()
            )
            subject = _decode_header_value(msg.get("Subject", "No Subject"))
            body = _get_email_body(msg)
            date_str = msg.get("Date", "")

            # Parse date to ISO format
            try:
                received_at = parsedate_to_datetime(date_str).isoformat()
            except Exception:
                received_at = datetime.now(timezone.utc).isoformat()

            replies.append({
                "message_id": message_id,
                "thread_id": thread_id,
                "from_email": from_addr,
                "subject": subject,
                "body": body,
                "received_at": received_at,
                "headers": {
                    "In-Reply-To": in_reply_to_raw,
                    "References": references_raw,
                },
            })

            # Mark as read after processing
            mail.store(msg_id, "+FLAGS", "\\Seen")

        mail.logout()
        logger.info("Fetched %d reply/replies from inbox", len(replies))
        return replies

    record_metric("imap_fetch_attempts")

    # Wrap with retry for IMAP connection resilience
    _retryer = tenacity.Retrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((imaplib.IMAP4.error, ConnectionError, OSError)),
        before_sleep=log_retry_attempt,
        reraise=True,
    )

    def _fetch_with_retry() -> List[dict]:
        """Call _fetch_replies with retry wrapping around the sync IMAP operations."""
        return _retryer(_fetch_replies)

    try:
        result = await asyncio.to_thread(_fetch_with_retry)
        return result or []
    except Exception as e:
        record_metric("imap_fetch_failures")
        logger.error("IMAP fetch failed after retries: %s", e, exc_info=True)
        return []


def _decode_header_value(value: str) -> str:
    """Decode an email header value that may be MIME-encoded (e.g. =?UTF-8?B?...)."""
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result: List[str] = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def _extract_email(from_field: str) -> str:
    """Extract the email address portion from a 'Name <email>' header value."""
    match = re.search(r"<([^>]+)>", from_field)
    if match:
        return match.group(1).strip()
    return from_field.strip()


def _get_email_body(msg) -> str:
    """Extract the plain-text body from an email message, handling multipart."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode("utf-8", errors="replace")
        return "(No plain text body found)"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                return payload.decode("utf-8", errors="replace")
        return "(No body content)"
