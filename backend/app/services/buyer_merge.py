"""Smart buyer merge and buy box merging service.

Handles:
- Duplicate buyer detection (name + affiliation match)
- Smart buy box merging via Groq AI (never remove, always append/refine)
- Adding additional emails to existing buyers
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Buyer, BuyerEmail
from app.services.audit_logger import audit
from app.services.embeddings import generate_embedding
from app.services.groq_client import groq_chat_completion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Duplicate buyer detection
# ---------------------------------------------------------------------------


async def find_duplicate_buyer(
    db: AsyncSession,
    full_name: str,
    affiliation: Optional[str],
    email: str,
) -> Tuple[Optional[Buyer], str]:
    """Find an existing buyer that matches name + company.

    Checks:
    1. Exact match on full_name + affiliation (case-insensitive)
    2. If no affiliation, exact match on full_name + email
    3. If affiliation found, also check if email is already associated

    Args:
        db: Database session.
        full_name: Buyer's full name.
        affiliation: Buyer's company/affiliation.
        email: Buyer's email address.

    Returns:
        Tuple of (existing_buyer or None, match_reason string).
    """
    if not full_name:
        return None, ""

    name_lower = full_name.strip().lower()
    aff_lower = affiliation.strip().lower() if affiliation else ""

    # Strategy 1: Same name + same company
    if aff_lower:
        result = await db.execute(
            select(Buyer).where(
                Buyer.status != "Do Not Contact",
                or_(
                    # Exact case-insensitive match on name
                    Buyer.full_name.ilike(name_lower),
                    # Name contains each other (handle "John Smith" vs "John A. Smith")
                ),
            )
        )
        all_matches = result.scalars().all()  # NOTE: consider .limit() for large datasets

        for buyer in all_matches:
            buyer_name = (buyer.full_name or "").strip().lower()
            buyer_aff = (buyer.affiliation or "").strip().lower()

            # Check name match (exact or one contains the other)
            name_match = (
                buyer_name == name_lower
                or name_lower in buyer_name
                or buyer_name in name_lower
            )
            aff_match = buyer_aff == aff_lower or (buyer_aff and aff_lower in buyer_aff)

            if name_match and aff_match:
                # Already has this email?
                if buyer.email.lower() == email.lower():
                    return buyer, "exact_duplicate_email"
                # Check additional emails
                email_result = await db.execute(
                    select(BuyerEmail).where(
                        BuyerEmail.buyer_id == buyer.id,
                        BuyerEmail.email.ilike(email),
                    )
                )
                if email_result.scalar_one_or_none():
                    return buyer, "exact_duplicate_email"
                return buyer, "name_company_match"

    # Strategy 2: Same name + email already exists (exact email match)
    result = await db.execute(
        select(Buyer).where(Buyer.email.ilike(email.strip().lower()))
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing, "exact_duplicate_email"

    return None, ""


# ---------------------------------------------------------------------------
# Smart buy box merging via Groq
# ---------------------------------------------------------------------------


_MERGE_SYSTEM_PROMPT = (
    "You are a real estate buy box curator. Your job is to merge new buying criteria "
    "into an existing buy box WITHOUT removing or replacing any existing criteria. "
    "Always append, combine, or generalize — never delete. "
    "If the new criteria partially overlaps with existing criteria, merge them intelligently "
    "by combining related requirements (e.g., 'Dallas' + 'Arlington' → 'Dallas/Arlington area'). "
    "Keep the full revised buy box concise but comprehensive."
)

_MERGE_USER_PROMPT_TEMPLATE = """EXISTING BUY BOX:
{existing_buy_box}

NEW CRITERIA TO MERGE:
{new_buy_box}

INSTRUCTIONS:
1. Keep ALL existing criteria — never remove anything
2. Add the new criteria in a way that makes sense together
3. If new criteria overlaps with existing (e.g. both mention price range), combine them sensibly
4. If new criteria adds new dimensions (e.g. property type, location, features), append them
5. Return ONLY the merged buy box text, no JSON, no markdown

MERGED BUY BOX:"""


async def merge_buy_boxes(existing_buy_box: str, new_buy_box: str) -> str:
    """Use Groq AI to intelligently merge new buy box criteria into existing.

    Never removes existing criteria — always appends/refines/combines.

    Args:
        existing_buy_box: The current buy box text.
        new_buy_box: New criteria text to merge in.

    Returns:
        Merged buy box text containing both old and new criteria.
    """
    if not existing_buy_box:
        return new_buy_box
    if not new_buy_box:
        return existing_buy_box

    messages = [
        {"role": "system", "content": _MERGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _MERGE_USER_PROMPT_TEMPLATE.format(
                existing_buy_box=existing_buy_box,
                new_buy_box=new_buy_box,
            ),
        },
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=500,
        )

        merged = response.choices[0].message.content.strip()

        # Strip any markdown code fences
        if merged.startswith("```"):
            lines = merged.split("\n")
            merged = "\n".join(line for line in lines if not line.strip().startswith("```"))

        # Quality check: ensure it's at least as long as either input
        # (safety guard against AI accidentally truncating)
        if len(merged) < max(len(existing_buy_box), len(new_buy_box)) * 0.5:
            logger.warning(
                "AI merge produced unexpectedly short result (%d chars), falling back to append",
                len(merged),
            )
            merged = _append_fallback(existing_buy_box, new_buy_box)

        logger.info(
            "Buy box merged: %d chars → %d chars",
            len(existing_buy_box) + len(new_buy_box),
            len(merged),
        )
        return merged

    except Exception as e:
        logger.warning("AI buy box merge failed: %s. Falling back to append.", e, exc_info=True)
        return _append_fallback(existing_buy_box, new_buy_box)


def _append_fallback(existing_buy_box: str, new_buy_box: str) -> str:
    """Simple fallback: append new criteria to existing with a separator."""
    existing = existing_buy_box.strip().rstrip(".")
    new_text = new_buy_box.strip()
    if not existing:
        return new_text
    if not new_text:
        return existing
    return f"{existing}. Additionally, {new_text[0].lower() if new_text else ''}{new_text[1:] if len(new_text) > 1 else ''}"


# ---------------------------------------------------------------------------
# Add email to existing buyer
# ---------------------------------------------------------------------------


async def add_email_to_buyer(
    db: AsyncSession,
    buyer: Buyer,
    email: str,
    verify_in_background: bool = True,
) -> BuyerEmail:
    """Add an additional email to an existing buyer.

    Args:
        db: Database session.
        buyer: The existing buyer.
        email: The new email address to add.
        verify_in_background: If True, verification runs as a background task.

    Returns:
        The newly created BuyerEmail record.
    """
    # Check if already exists as a BuyerEmail
    existing = await db.execute(
        select(BuyerEmail).where(
            BuyerEmail.buyer_id == buyer.id,
            BuyerEmail.email.ilike(email.strip()),
        )
    )
    be = existing.scalar_one_or_none()
    if be:
        return be

    # Create new BuyerEmail
    buyer_email = BuyerEmail(
        buyer_id=buyer.id,
        email=email.strip().lower(),
    )
    db.add(buyer_email)
    logger.info("Added additional email %s to buyer %s (%s)", email, buyer.id, buyer.full_name)
    return buyer_email


# ---------------------------------------------------------------------------
# Get all emails for a buyer (primary + additional)
# ---------------------------------------------------------------------------


async def get_all_buyer_emails(db: AsyncSession, buyer_id: UUID) -> List[str]:
    """Get all email addresses for a buyer, including additional emails.

    Args:
        db: Database session.
        buyer_id: The buyer's UUID.

    Returns:
        List of email addresses (primary first).
    """
    buyer = await db.get(Buyer, buyer_id)
    if not buyer:
        return []

    emails = [buyer.email]

    result = await db.execute(
        select(BuyerEmail.email).where(BuyerEmail.buyer_id == buyer_id)
    )
    additional = result.scalars().all()  # NOTE: consider .limit() for large datasets
    emails.extend([e for e in additional if e.lower() != buyer.email.lower()])

    return emails


async def find_buyer_by_any_email(
    db: AsyncSession,
    email: str,
) -> Optional[Buyer]:
    """Find a buyer by any of their emails (primary or additional).

    Args:
        db: Database session.
        email: The email address to search for.

    Returns:
        The Buyer if found, None otherwise.
    """
    email_lower = email.strip().lower()

    # Check primary email
    result = await db.execute(
        select(Buyer).where(Buyer.email.ilike(email_lower))
    )
    buyer = result.scalar_one_or_none()
    if buyer:
        return buyer

    # Check additional emails
    result = await db.execute(
        select(BuyerEmail).where(BuyerEmail.email.ilike(email_lower))
    )
    be = result.scalar_one_or_none()
    if be:
        return await db.get(Buyer, be.buyer_id)

    return None


# ---------------------------------------------------------------------------
# Full merge: add buyer + merge buy box
# ---------------------------------------------------------------------------


async def merge_new_into_existing_buyer(
    db: AsyncSession,
    existing_buyer: Buyer,
    new_buy_box: str,
    new_email: Optional[str] = None,
    log_action: str = "duplicate_merged",
) -> Dict[str, Any]:
    """Merge a new buyer's data into an existing buyer record.

    1. Merges buy box (AI-powered, never removes old criteria)
    2. Adds new email as additional email
    3. Regenerates buy_box_embedding
    4. Logs to activity log

    Args:
        db: Database session.
        existing_buyer: The existing buyer to merge into.
        new_buy_box: New buy box criteria to merge.
        new_email: Optional new email to add.
        log_action: Action name for audit log.

    Returns:
        Dict with merge results.
    """
    changes = {}

    # 1. Smart buy box merge
    old_buy_box = existing_buyer.buy_box
    if new_buy_box and new_buy_box != old_buy_box:
        merged_buy_box = await merge_buy_boxes(old_buy_box, new_buy_box)
        if merged_buy_box != old_buy_box:
            existing_buyer.buy_box = merged_buy_box
            changes["buy_box"] = {
                "old": old_buy_box[:200],
                "new": merged_buy_box[:200],
            }

            # Regenerate embedding
            try:
                new_embedding = await generate_embedding(
                    merged_buy_box, input_type="search_query",
                )
                existing_buyer.buy_box_embedding = new_embedding
                changes["embedding_regenerated"] = True
            except Exception as e:
                logger.warning("Failed to regenerate embedding: %s", e, exc_info=True)

    # 2. Add additional email
    if new_email and new_email.lower() != existing_buyer.email.lower():
        buyer_email = await add_email_to_buyer(db, existing_buyer, new_email)
        changes["additional_email_added"] = new_email

    # 3. Log to activity log
    try:
        await audit.log_buyer_updated(
            db,
            existing_buyer.id,
            changes=changes,
            updated_by=log_action,
        )
    except Exception as e:
        logger.warning("Failed to log merge: %s", e, exc_info=True)

    db.add(existing_buyer)
    logger.info(
        "Merged into buyer %s (%s): %s",
        existing_buyer.id, existing_buyer.full_name, changes,
    )

    return changes
