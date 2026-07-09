"""Title company email coordination service.

Polls Gmail for emails from known title company domains or with title-related
subject keywords, classifies each email via Groq AI, and updates deal statuses
based on the classified intent (Title_Clear, Docs_Needed, Scheduled, Funded, Closed).
"""

import asyncio
import email
import imaplib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

import tenacity
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
import app.database as _db
from app.models.models import ActivityLog, Deal, JVPartner
from app.services.gmail_service import send_email
from app.services.groq_client import groq_chat_completion, extract_json_block
from app.services.matching_service import trigger_release_for_deal_async
from app.services.resilience import log_retry_attempt, record_metric

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known title company domains
# ---------------------------------------------------------------------------

TITLE_DOMAINS: List[str] = [
    "@stewart.com",
    "@fidelity.com",
    "@oldrepublic.com",
    "@firstam.com",
    "@ctic.com",
    "@chicagotitle.com",
    "@equitytitle.com",
    "@anywheretitle.com",
    "@nationwidetitle.com",
    "@libertytitle.com",
    "@landam.com",
    "@entitlestl.com",
    "@transnationtitle.com",
    "@wtgtitle.com",
]

TITLE_SUBJECT_KEYWORDS: List[str] = [
    "title", "escrow", "closing", "funding", "settlement",
    "clear to close", "docs needed", "hud", "cd", "closing disclosure",
    "lien", "lien found", "tax lien", "mechanics lien", "title issue",
]

# ---------------------------------------------------------------------------
# Title email classification prompt
# ---------------------------------------------------------------------------

_TITLE_SYSTEM_PROMPT = (
    "You are a real estate closing coordinator. "
    "Analyze this email from a title company and extract structured data."
)

_TITLE_USER_PROMPT_TEMPLATE = """TITLE EMAIL:
Subject: {subject}
From: {from_email}
Body: {body}

CLASSIFY INTO ONE:
- Title_Clear: Title has cleared, no liens or issues
- Docs_Needed: Additional documents required (tax returns, proof of funds, etc.)
- Scheduled: Closing date has been set or rescheduled
- Funded: Funds have been disbursed, deal is funded
- Closed: Deal has officially closed
- Lien_Found: Lien, encumbrance, title defect, or IRS/tax issue found
- Other: Doesn't fit above, or informational

Also extract:
1. deal_address: The property address mentioned (or null)
2. closing_date: Extract closing date in YYYY-MM-DD format if mentioned (or null)
3. summary: One-sentence summary of what this email says
4. action_items: Brief list of any action needed

Return ONLY JSON:
{{
    "intent": "...",
    "deal_address": "...",
    "closing_date": "...",
    "summary": "...",
    "action_items": "..."
}}
"""

_INTENT_MAP: Dict[str, str] = {
    "Title_Clear": "Title_Clear",
    "Docs_Needed": "Docs_Needed",
    "Scheduled": "Scheduled",
    "Funded": "Funded",
    "Closed": "Closed",
    "Lien_Found": "Lien_Found",
    "Other": "Other",
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


async def classify_title_email(email_data: dict) -> dict:
    """Use Groq AI to classify a title company email.

    Args:
        email_data: dict with keys ``subject``, ``body``, ``from_email``.

    Returns:
        dict with keys: intent, deal_address, closing_date, summary, action_items.
    """
    subject = (email_data.get("subject") or "").strip()
    body = (email_data.get("body") or "").strip()
    from_email = (email_data.get("from_email") or "unknown").strip()

    user_prompt = _TITLE_USER_PROMPT_TEMPLATE.format(
        subject=subject,
        from_email=from_email,
        body=body,
    )

    messages = [
        {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.2,
            max_tokens=400,
        )

        content = response.choices[0].message.content.strip()
        logger.debug("Title email classification: %.200s", content)

        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            )

        parsed: dict = json.loads(extract_json_block(content))

        raw_intent = (parsed.get("intent") or "").strip()
        intent = _INTENT_MAP.get(raw_intent, "Other")

        result = {
            "intent": intent,
            "deal_address": (parsed.get("deal_address") or "").strip() or None,
            "closing_date": (parsed.get("closing_date") or "").strip() or None,
            "summary": (parsed.get("summary") or "").strip() or "",
            "action_items": (parsed.get("action_items") or "").strip() or "",
        }

        logger.info(
            "Classified title email from %s as '%s' — %s",
            from_email, intent, result["summary"],
        )

        return result

    except (json.JSONDecodeError, Exception) as e:
        logger.error(
            "Failed to classify title email from %s: %s", from_email, e, exc_info=True,
        )
        return {
            "intent": "Other",
            "deal_address": None,
            "closing_date": None,
            "summary": f"Classification error: {e}",
            "action_items": "",
        }


async def fetch_title_emails() -> List[dict]:
    """Poll Gmail inbox for emails from title companies or with title keywords.

    Connects via IMAP, fetches UNSEEN messages, filters by known title company
    domains or subject keywords, marks processed messages as read.

    Returns:
        List of dicts with keys: message_id, from_email, subject, body, received_at.
    """
    gmail_addr = settings.gmail_address
    gmail_pass = settings.gmail_app_password

    if not gmail_addr or not gmail_pass:
        logger.error("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        return []

    def _fetch() -> List[dict]:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_addr, gmail_pass)
        mail.select("INBOX")

        # Search for unseen (unread) emails
        status, raw_ids = mail.search(None, "UNSEEN")
        if status != "OK" or not raw_ids[0]:
            mail.logout()
            return []

        emails: List[dict] = []
        message_ids = raw_ids[0].split()

        for msg_id in message_ids:
            status, msg_data = mail.fetch(msg_id, "(RFC822 FLAGS)")
            if status != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            from_field = _decode_header_value(msg.get("From", ""))
            from_addr = _extract_email(from_field)
            subject = _decode_header_value(msg.get("Subject", "No Subject"))
            body = _get_email_body(msg)

            # Filter: check if from a title domain or subject has keywords
            if not _is_title_related(from_addr, subject):
                continue

            message_id = (msg.get("Message-ID", "") or "").strip()

            date_str = msg.get("Date", "")
            try:
                received_at = parsedate_to_datetime(date_str).isoformat()
            except Exception:
                received_at = datetime.now(timezone.utc).isoformat()

            emails.append({
                "message_id": message_id,
                "from_email": from_addr,
                "subject": subject,
                "body": body,
                "received_at": received_at,
            })

            # Mark as read
            mail.store(msg_id, "+FLAGS", "\\Seen")

        mail.logout()
        logger.info("Fetched %d title-related email(s) from inbox", len(emails))
        return emails

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
        """Call _fetch with retry wrapping around the sync IMAP operations."""
        return _retryer(_fetch)

    try:
        result = await asyncio.to_thread(_fetch_with_retry)
        return result or []
    except Exception as e:
        record_metric("imap_fetch_failures")
        logger.error("Title IMAP fetch failed after retries: %s", e, exc_info=True)
        return []


def _is_title_related(from_addr: str, subject: str) -> bool:
    """Check if an email is title-related based on sender domain or subject keywords."""
    from_lower = from_addr.lower()
    for domain in TITLE_DOMAINS:
        if domain in from_lower:
            return True

    subject_lower = subject.lower()
    for keyword in TITLE_SUBJECT_KEYWORDS:
        if keyword in subject_lower:
            return True

    return False


async def process_title_emails(db: Optional[AsyncSession] = None) -> Dict[str, Any]:
    """Main orchestrator: fetch title emails, classify, update deals/log.

    This is called by the /api/title/check-emails endpoint and can also
    be called by a background scheduler.

    Args:
        db: Optional existing database session. If None, a new one is created.

    Returns:
        dict with: total_found, processed, results (list of per-email outcomes).
    """
    emails = await fetch_title_emails()
    if not emails:
        return {"total_found": 0, "processed": 0, "results": []}

    # Create own session if one wasn't provided
    close_session = False
    if db is None:
        db = _db.async_session_factory()
        close_session = True

    results: List[dict] = []
    processed = 0
    # Track deals auto-closed (Funded/Closed) to trigger queued match release
    sold_deal_ids: List[uuid.UUID] = []

    try:
        # Pre-fetch all deals for address matching (address -> deal_id)
        deal_result = await db.execute(select(Deal))
        all_deals = deal_result.scalars().all()

        # Pre-fetch JV partners for title company email lookup
        jv_result = await db.execute(select(JVPartner))
        all_jv_partners = jv_result.scalars().all()

        for email_data in emails:
            outcome = await _process_single_title_email(
                db, email_data, all_deals, all_jv_partners,
            )
            results.append(outcome)
            if outcome.get("action_taken"):
                processed += 1
            # Track deals auto-closed (Funded/Closed intents) for release
            if outcome.get("intent") in ("Funded", "Closed") and outcome.get("matched_deal_id"):
                sold_deal_ids.append(outcome["matched_deal_id"])

        await db.commit()

        # FEATURE 2: Event-driven queued match release for auto-closed deals
        for deal_id in sold_deal_ids:
            try:
                await trigger_release_for_deal_async(deal_id)
            except Exception as release_err:
                logger.warning(
                    "Failed to trigger release for auto-closed deal %s: %s",
                    deal_id, release_err, exc_info=True,
                )

    finally:
        if close_session:
            await db.close()

    return {
        "total_found": len(emails),
        "processed": processed,
        "results": results,
    }


async def _process_single_title_email(
    db: AsyncSession,
    email_data: dict,
    all_deals: List[Deal],
    all_jv_partners: List[JVPartner],
) -> dict:
    """Process a single title email: classify, match to deal, update status/log."""
    classification = await classify_title_email(email_data)
    intent = classification["intent"]

    # Try to match the email to a deal by address mention
    matched_deal_id = None
    matched_address = None

    if classification.get("deal_address"):
        addr_lower = classification["deal_address"].lower().strip()
        for deal in all_deals:
            if deal.address and deal.address.lower().strip() == addr_lower:
                matched_deal_id = deal.id
                matched_address = deal.address
                break

    action_taken = False

    # Build metadata for activity log
    metadata = {
        "from_email": email_data["from_email"],
        "subject": email_data["subject"],
        "classified_intent": intent,
        "summary": classification["summary"],
        "action_items": classification["action_items"],
        "closing_date": classification.get("closing_date"),
        "matched_deal_id": str(matched_deal_id) if matched_deal_id else None,
        "matched_address": matched_address,
    }

    # Take action based on intent
    if matched_deal_id:
        deal_result = await db.execute(select(Deal).where(Deal.id == matched_deal_id))
        deal = deal_result.scalar_one_or_none()

        if deal:
            if intent == "Title_Clear":
                deal.title_status = "Clear"
                # Auto move to Under Contract if currently Available/Campaign Launched
                if deal.status in ("Available", "Campaign Launched"):
                    deal.status = "Under Contract"
                    metadata["deal_status_updated"] = "title_status → Clear + status → Under Contract"
                else:
                    metadata["deal_status_updated"] = "title_status → Clear"
                db.add(deal)
                action_taken = True

            elif intent == "Funded":
                # Auto-close the deal: funds disbursed means deal is done
                deal.status = "Sold"
                deal.closed_at = datetime.now(timezone.utc)
                deal.closed_price = float(deal.asking_price)  # Default to asking
                # Calculate payouts
                net_spread = float(deal.closed_price) - float(deal.contract_price)
                split_pct = float(deal.jv_split_percentage or 50) / 100
                deal.net_spread = net_spread
                deal.jv_payout = net_spread * split_pct
                deal.my_payout = net_spread - deal.jv_payout
                db.add(deal)
                action_taken = True
                metadata["deal_status_updated"] = "status → Sold (auto-closed via funding notification)"
                metadata["auto_closed"] = True
                metadata["net_spread"] = net_spread
                metadata["jv_payout"] = net_spread * split_pct

            elif intent == "Closed":
                # Title company confirmed closing — ensure deal is marked Sold
                if deal.status != "Sold":
                    deal.status = "Sold"
                    deal.closed_at = datetime.now(timezone.utc)
                    if not deal.closed_price:
                        deal.closed_price = float(deal.asking_price)
                    db.add(deal)
                    action_taken = True
                    metadata["deal_status_updated"] = "status → Sold (auto-closed via title confirmation)"
                    metadata["auto_closed"] = True
                else:
                    action_taken = True
                    metadata["deal_status_updated"] = "Already Sold — logged confirmation"

            elif intent == "Scheduled" and classification.get("closing_date"):
                action_taken = True
                metadata["closing_date_set"] = classification["closing_date"]

            elif intent == "Lien_Found":
                deal.title_status = "Liens"
                db.add(deal)
                action_taken = True
                metadata["deal_status_updated"] = "title_status → Liens (lien/issue found)"
                metadata["alert_user"] = True
                logger.warning(
                    "AUTO-ALERT: Lien found for deal %s (%s) — title_status set to Liens",
                    deal.id, deal.address,
                )

            elif intent == "Docs_Needed":
                action_taken = True
                metadata["docs_needed"] = classification["action_items"]

            # Any title company response means they've acknowledged
            if action_taken and not deal.title_acknowledged:
                deal.title_acknowledged = True
                db.add(deal)
                metadata["title_acknowledged"] = True
                logger.info(
                    "Title acknowledged for deal %s (%s) via '%s' intent",
                    deal.id, deal.address, intent,
                )

    # Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="deal",
        entity_id=matched_deal_id,
        action=f"title_email_{intent.lower()}",
        metadata_json=metadata,
    )
    db.add(log_entry)

    if action_taken:
        logger.info(
            "Title email action taken: intent=%s, deal=%s, summary=%.100s",
            intent, matched_deal_id, classification["summary"],
        )
    else:
        logger.info(
            "Title email processed (no action): intent=%s, matched=%s",
            intent, bool(matched_deal_id),
        )

    return {
        "from_email": email_data["from_email"],
        "subject": email_data["subject"],
        "intent": intent,
        "deal_matched": bool(matched_deal_id),
        "matched_deal_id": matched_deal_id,
        "deal_address": matched_address,
        "action_taken": action_taken,
        "summary": classification["summary"],
    }


# ---------------------------------------------------------------------------
# Assignment contract email
# ---------------------------------------------------------------------------


async def send_assignment_contract(
    db: AsyncSession,
    deal: Deal,
    buyer_name: str,
    buyer_email: str,
    to_cc: Optional[str] = None,
) -> dict:
    """Send an assignment contract email to the buyer, CC'ing the title company.

    Args:
        db: Database session.
        deal: The deal being assigned.
        buyer_name: Full name of the buyer.
        buyer_email: Email address of the buyer.
        to_cc: Optional CC email (title company). Falls back to deal's JV partner email,
            then to settings.title_company_email.

    Returns:
        dict with keys: sent (bool), message_id (str), cc_email (str).
    """

    # Determine CC email
    cc_email = to_cc
    if not cc_email and deal.jv_partner_id:
        jv = await db.get(JVPartner, deal.jv_partner_id)
        if jv and jv.email:
            cc_email = jv.email
    if not cc_email:
        cc_email = settings.title_company_email

    subject = f"Assignment Contract — {deal.address}"
    body = (
        f"Hi {buyer_name},\n\n"
        f"Congratulations on moving forward with {deal.address}!\n\n"
        f"Here is the assignment contract for this deal:\n\n"
        f"Property: {deal.address}\n"
        f"City: {deal.city or 'N/A'}, {deal.state or 'N/A'}\n"
        f"Asking Price: ${float(deal.asking_price):,.2f}\n"
        f"Contract Price: ${float(deal.contract_price):,.2f}\n"
        f"Spread: ${float(deal.spread) if deal.spread else 0:,.2f}\n\n"
        f"Estimated Assignment Fee: ${float(deal.asking_price) - float(deal.contract_price):,.2f}\n\n"
        f"Please review and sign the attached contract. "
        f"The title company ({cc_email or 'your closing agent'}) has been CC'd "
        f"on this email to begin the closing process.\n\n"
        f"{settings.operator_email_signature}"
    )

    try:
        result = await send_email(
            to=buyer_email,
            subject=subject,
            body=body,
            send_type="reply",
        )

        logger.info(
            "Assignment contract sent to %s (CC: %s) — message_id: %s",
            buyer_email, cc_email or "(none)", result.get("message_id", "unknown"),
        )

        # Set title_opened_at and title_company_email on the deal
        if result.get("message_id"):
            if not deal.title_opened_at:
                deal.title_opened_at = datetime.now(timezone.utc)
                deal.title_company_email = cc_email
                db.add(deal)
                await db.commit()

        return {
            "sent": True,
            "message_id": result.get("message_id", "unknown"),
            "cc_email": cc_email,
        }

    except Exception as e:
        logger.error(
            "Failed to send assignment contract to %s: %s", buyer_email, e,
            exc_info=True,
        )
        return {
            "sent": False,
            "message_id": None,
            "cc_email": cc_email,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Title company chase sequence
# ---------------------------------------------------------------------------


CHASE_SUBJECTS: Dict[int, str] = {
    1: "Following Up — {address}",
    2: "Title Search Status — {address}",
    3: "Update Request — {address}",
    4: "Title Issues? — {address}",
    5: "Closing Date Confirmation — {address}",
    6: "Final Chase — {address}",
}

CHASE_BODIES: Dict[int, str] = {
    1: (
        "Hi,\n\nJust following up to confirm receipt of "
        "the assignment contract for {address}. "
        "Please let us know you have everything needed "
        "to proceed.\n\n"
        "{signature}"
    ),
    2: (
        "Hi,\n\nChecking in on {address} — has the "
        "title search been initiated? Please let us know "
        "the current status.\n\n"
        "{signature}"
    ),
    3: (
        "Hi,\n\nWanted to get a progress update on "
        "{address}. Any issues or additional "
        "documents needed from our side?\n\n"
        "{signature}"
    ),
    4: (
        "Hi,\n\nAre there any title issues or "
        "encumbrances we should be aware of on "
        "{address}? Please advise at your "
        "earliest convenience.\n\n"
        "{signature}"
    ),
    5: (
        "Hi,\n\nCan you confirm the closing date for "
        "{address}? We want to make sure all "
        "parties are aligned on the timeline.\n\n"
        "{signature}"
    ),
    6: (
        "Hi,\n\nThis is our final follow-up on "
        "{address}. Please respond urgently with "
        "a status update — we need to know if closing "
        "is still on track.\n\n"
        "{signature}"
    ),
}

CHASE_SCHEDULE_DAYS: Dict[int, int] = {
    1: 3,
    2: 5,
    3: 7,
    4: 10,
    5: 14,
    6: 21,
}


async def send_title_chase_email(
    db: AsyncSession,
    deal: Deal,
    chase_number: int,
) -> dict:
    """Send a proactive chase email to the title company.

    Args:
        db: Database session.
        deal: The deal to chase on.
        chase_number: 1-6 (maps to Day 3, 5, 7, 10, 14, 21).

    Returns:
        dict with keys: sent (bool), chase_number (int), reason (str on failure).
    """
    title_email = (
        deal.title_company_email
        or settings.title_company_email
    )

    if not title_email:
        logger.warning(
            "No title company email for deal %s — cannot send chase %d",
            deal.id, chase_number,
        )
        return {"sent": False, "chase_number": chase_number, "reason": "no_title_email"}

    subject = CHASE_SUBJECTS.get(chase_number, CHASE_SUBJECTS[6]).format(
        address=deal.address,
    )
    body = CHASE_BODIES.get(chase_number, CHASE_BODIES[6]).format(
        address=deal.address,
        signature=settings.operator_email_signature,
    )

    try:
        result = await send_email(
            to=title_email,
            subject=subject,
            body=body,
            send_type="reply",
        )
        deal.title_last_chase_at = datetime.now(timezone.utc)
        deal.title_chase_count = (deal.title_chase_count or 0) + 1
        db.add(deal)
        await db.commit()
        logger.info(
            "Title chase %d sent for deal %s to %s",
            chase_number, deal.id, title_email,
        )
        return {"sent": True, "chase_number": chase_number}
    except Exception as e:
        logger.error(
            "Failed to send title chase %d for deal %s: %s",
            chase_number, deal.id, e, exc_info=True,
        )
        return {"sent": False, "chase_number": chase_number, "error": str(e)}


async def run_title_chases(db: Optional[AsyncSession] = None) -> int:
    """Scheduler task: find deals needing a title chase and send one.

    Logic per deal:
    1. Deal must be "Under Contract" or "Sold" with title_opened_at set
    2. title_acknowledged must be False (stop chasing once title responds)
    3. Calculate days since title_opened_at
    4. Determine next chase number (title_chase_count + 1)
    5. If next_chase > 6: all chases sent, create dashboard alert
    6. If days_open >= CHASE_SCHEDULE_DAYS[next_chase]: send chase

    Returns:
        Number of chase emails sent.
    """
    close_session = False
    if db is None:
        db = _db.async_session_factory()
        close_session = True

    try:
        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(Deal).where(
                Deal.status.in_(["Under Contract", "Sold"]),
                Deal.title_opened_at.isnot(None),
                Deal.title_acknowledged == False,
            )
        )
        deals_to_chase = result.scalars().all()

        if not deals_to_chase:
            return 0

        sent_count = 0

        for deal in deals_to_chase:
            try:
                days_open = (now - deal.title_opened_at).days
                next_chase = (deal.title_chase_count or 0) + 1

                if next_chase > 6:
                    # All 6 chases sent with no response — create dashboard alert
                    log_entry = ActivityLog(
                        id=uuid.uuid4(),
                        entity_type="deal",
                        entity_id=deal.id,
                        action="title_unresponsive",
                        metadata_json={
                            "deal_id": str(deal.id),
                            "address": deal.address,
                            "days_open": days_open,
                            "chases_sent": 6,
                            "alert_user": True,
                            "action_required": (
                                "Title company unresponsive after 6 chase emails. "
                                "Call them directly or escalate."
                            ),
                        },
                    )
                    db.add(log_entry)
                    await db.commit()
                    continue

                due_day = CHASE_SCHEDULE_DAYS[next_chase]

                if days_open < due_day:
                    continue  # Not due yet

                chase_result = await send_title_chase_email(db, deal, next_chase)
                if chase_result.get("sent"):
                    sent_count += 1

            except Exception as e:
                logger.error(
                    "Failed to process title chase for deal %s: %s",
                    deal.id, e, exc_info=True,
                )
                continue

        if sent_count:
            logger.info("Title chase: %d chase email(s) sent", sent_count)
        return sent_count

    except Exception as e:
        logger.error("Title chase task failed: %s", e, exc_info=True)
        return 0
    finally:
        if close_session:
            await db.close()


# ---------------------------------------------------------------------------
# Helper functions (shared with gmail_monitor)
# ---------------------------------------------------------------------------


def _decode_header_value(value: str) -> str:
    """Decode an email header value that may be MIME-encoded."""
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
