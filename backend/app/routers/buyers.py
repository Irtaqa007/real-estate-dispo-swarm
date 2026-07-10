"""Buyer CRUD API endpoints."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.models import ActivityLog, Buyer, BuyerEmail, Campaign, EmailVerification
from app.schemas import BuyerCreate, BuyerResponse, BuyerUpdate
from app.services.buyer_merge import find_duplicate_buyer, merge_new_into_existing_buyer
from app.services.email_verification import verify_email
from app.services.embeddings import generate_embedding
from app.services.matching_service import invalidate_queued_matches_for_buyer
from app.services.opt_out import validate_token
from app.services.parse_buy_box import parse_buy_box

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/buyers", tags=["buyers"])


@router.post("", response_model=BuyerResponse, status_code=status.HTTP_201_CREATED)
async def create_buyer(
    buyer_in: BuyerCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create a new buyer with duplicate detection.

    If a buyer with the same name + company (affiliation) already exists,
    the new data is merged into the existing buyer instead:
    - New email is added as an additional email
    - Buy box is intelligently merged (never removes old criteria)
    - Embedding is regenerated

    Email verification and buy-box embedding run in background tasks
    so the API returns immediately without waiting for external API calls.
    """
    # Check for duplicate name + company match
    existing_buyer, match_reason = await find_duplicate_buyer(
        db,
        full_name=buyer_in.full_name,
        affiliation=buyer_in.affiliation,
        email=buyer_in.email,
    )

    if existing_buyer:
        if match_reason == "exact_duplicate_email":
            # Same email — just merge buy box, don't add duplicate email
            await merge_new_into_existing_buyer(
                db=db,
                existing_buyer=existing_buyer,
                new_buy_box=buyer_in.buy_box,
                new_email=None,
                log_action="buyer_merge_same_email",
            )
        else:
            # Name + company match — add new email and merge buy box
            await merge_new_into_existing_buyer(
                db=db,
                existing_buyer=existing_buyer,
                new_buy_box=buyer_in.buy_box,
                new_email=buyer_in.email,
                log_action="buyer_merge_dedup",
            )

        # Apply explicit structured fields from the create payload,
        # or re-parse from the merged buy_box if none were provided
        update_data = buyer_in.model_dump(exclude_unset=True)
        explicit_fields = [f for f in ("price_min", "price_max", "pref_property_type", "pref_cities") if f in update_data]
        if explicit_fields:
            for field in explicit_fields:
                setattr(existing_buyer, field, update_data[field])
        else:
            parsed = await parse_buy_box(existing_buyer.buy_box)
            existing_buyer.price_min = parsed.get("price_min")
            existing_buyer.price_max = parsed.get("price_max")
            existing_buyer.pref_property_type = parsed.get("pref_property_type")
            existing_buyer.pref_cities = parsed.get("pref_cities")

        # Invalidate queued matches since buy_box changed
        await invalidate_queued_matches_for_buyer(db, existing_buyer.id)

        await db.commit()
        result2 = await db.execute(
            select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == existing_buyer.id)
        )
        existing_buyer = result2.scalar_one()

        logger.info(
            "Buyer %s (%s) updated via dedup merge (reason=%s)",
            existing_buyer.id, existing_buyer.full_name, match_reason,
        )
        return existing_buyer

    # Parse structured fields from buy_box
    parsed = await parse_buy_box(buyer_in.buy_box)

    # No duplicate — create new buyer as normal
    buyer = Buyer(
        id=uuid.uuid4(),
        full_name=buyer_in.full_name,
        email=buyer_in.email,
        affiliation=buyer_in.affiliation,
        buy_box=buyer_in.buy_box,
        buyer_tier=buyer_in.buyer_tier or "C-List",
        status=buyer_in.status or "Active",
        notes=buyer_in.notes,
        price_min=buyer_in.price_min or parsed.get("price_min"),
        price_max=buyer_in.price_max or parsed.get("price_max"),
        pref_property_type=buyer_in.pref_property_type or parsed.get("pref_property_type"),
        pref_cities=buyer_in.pref_cities or parsed.get("pref_cities"),
    )

    db.add(buyer)
    await db.commit()
    result2 = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer.id)
    )
    buyer = result2.scalar_one()

    # Run email verification in background.
    # Embedding is auto-triggered AFTER verification succeeds (chained in _verify_email_background).
    background_tasks.add_task(_verify_email_background, buyer.id, buyer.email)

    logger.info("Buyer %s created — verification queued in background (embedding will follow if verified)", buyer.id)
    return buyer


@router.get("", response_model=List[BuyerResponse])
async def list_buyers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all buyers with pagination."""
    result = await db.execute(
        select(Buyer)
        .options(selectinload(Buyer.buyer_emails))
        .offset(skip).limit(limit)
        .order_by(Buyer.created_at.desc())
    )
    buyers = result.scalars().all()
    return buyers


@router.get("/opted-out")
async def list_opted_out_buyers(
    db: AsyncSession = Depends(get_db),
):
    """Return list of buyers who have opted out (status = 'Do Not Contact' or unsubscribed_at IS NOT NULL)."""
    result = await db.execute(
        select(Buyer)
        .where(
            (Buyer.unsubscribed_at.isnot(None))
            | (Buyer.status == "Do Not Contact")
        )
        .order_by(Buyer.unsubscribed_at.desc().nullslast())
    )
    buyers = result.scalars().all()
    return [
        {
            "id": str(b.id),
            "full_name": b.full_name,
            "email": b.email,
            "unsubscribed_at": b.unsubscribed_at.isoformat() if b.unsubscribed_at else None,
            "status": b.status,
        }
        for b in buyers
    ]


@router.get("/{buyer_id}", response_model=BuyerResponse)
async def get_buyer(buyer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get a single buyer by UUID."""
    result = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer_id)
    )
    buyer = result.scalar_one_or_none()
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Buyer with id '{buyer_id}' not found",
        )
    return buyer


@router.put("/{buyer_id}", response_model=BuyerResponse)
async def update_buyer(
    buyer_id: uuid.UUID,
    buyer_in: BuyerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a buyer. Only provided fields are updated."""
    result = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer_id)
    )
    buyer = result.scalar_one_or_none()
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Buyer with id '{buyer_id}' not found",
        )

    # Update only the fields that were provided
    update_data = buyer_in.model_dump(exclude_unset=True)

    # If buy_box changed (and no explicit structured fields given), re-parse
    if "buy_box" in update_data and not any(f in update_data for f in ("price_min", "price_max", "pref_property_type", "pref_cities")):
        parsed = await parse_buy_box(buyer_in.buy_box)
        buyer.price_min = parsed.get("price_min")
        buyer.price_max = parsed.get("price_max")
        buyer.pref_property_type = parsed.get("pref_property_type")
        buyer.pref_cities = parsed.get("pref_cities")

    for field, value in update_data.items():
        setattr(buyer, field, value)

    # Invalidate queued matches if buy_box or structured fields changed
    if any(f in update_data for f in ("buy_box", "price_min", "price_max", "pref_property_type", "pref_cities")):
        await invalidate_queued_matches_for_buyer(db, buyer.id)

    await db.commit()
    result2 = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer.id)
    )
    buyer = result2.scalar_one()
    return buyer


# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------


async def _verify_email_background(buyer_id: uuid.UUID, email: str) -> None:
    """Verify buyer email in the background after creation.

    If verification succeeds (result == "valid"), automatically triggers
    buy-box embedding generation so only verified buyers get embedded.
    """
    try:
        from app.database import async_session_factory

        verification = await verify_email(email)
        buy_box = None
        async with async_session_factory() as db:
            buyer = await db.get(Buyer, buyer_id)
            if buyer:
                buyer.email_verified = verification["result"] == "valid"
                buyer.email_verification_status = verification["result"]

                verification_log = EmailVerification(
                    id=uuid.uuid4(),
                    buyer_id=buyer_id,
                    email=email,
                    result=verification["result"],
                    score=verification["score"],
                )
                db.add(verification_log)
                await db.commit()
                logger.info("Background email verification for %s: %s", email, verification["result"])

                # Capture buy_box before the session closes — buyer becomes
                # detached after the async with block exits
                buy_box = buyer.buy_box

        # Auto-trigger embedding if verification passed and buyer has a buy box
        if verification["result"] == "valid" and buy_box:
            await _generate_buyer_embedding_background(buyer_id, buy_box)
            logger.info(
                "Auto-triggered buy-box embedding for verified buyer %s (%s)",
                buyer_id, email,
            )
    except Exception as e:
        logger.warning("Background email verification failed for %s: %s", email, e, exc_info=True)


async def _generate_buyer_embedding_background(buyer_id: uuid.UUID, buy_box: str) -> None:
    """Generate buy-box embedding in the background after buyer creation."""
    try:
        from app.database import async_session_factory

        embedding = await generate_embedding(buy_box, input_type="search_query")
        async with async_session_factory() as db:
            buyer = await db.get(Buyer, buyer_id)
            if buyer:
                buyer.buy_box_embedding = embedding
                await db.commit()
                logger.info("Background buy-box embedding generated for buyer %s", buyer_id)
    except Exception as e:
        logger.warning("Background buy-box embedding failed for buyer %s: %s", buyer_id, e, exc_info=True)




@router.post("/{buyer_id}/embed", response_model=BuyerResponse)
async def generate_buyer_embedding(
    buyer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger buy-box embedding generation for a buyer."""
    result = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer_id)
    )
    buyer = result.scalar_one_or_none()
    if not buyer:
        raise HTTPException(status_code=404, detail=f"Buyer {buyer_id} not found")
    if not buyer.buy_box:
        raise HTTPException(status_code=400, detail="Buyer has no buy_box to embed")
    try:
        from app.services.embeddings import generate_embedding
        embedding = await generate_embedding(buyer.buy_box, input_type="search_query")
        buyer.buy_box_embedding = embedding
        await db.commit()
        logger.info("Embedding generated for buyer %s", buyer_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")
    result = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer_id)
    )
    buyer = result.scalar_one()
    return buyer

@router.get("/unsubscribe/{token}")
async def unsubscribe_from_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Unsubscribe a buyer via a signed token from an email link.

    Validates the HMAC-signed token, marks the buyer as unsubscribed
    (sets unsubscribed_at and status = "Do Not Contact"), pauses any
    queued campaigns, and redirects to a confirmation page.

    Args:
        token: Signed token in format {buyer_id_hex}.{hmac_sig}.

    Returns:
        Redirect to the frontend with a confirmation message.
    """
    buyer_id = validate_token(token)
    if not buyer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired unsubscribe link.",
        )

    result = await db.execute(select(Buyer).where(Buyer.id == buyer_id))
    buyer = result.scalar_one_or_none()
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Buyer not found.",
        )

    now = datetime.now(timezone.utc)

    # Mark as unsubscribed
    buyer.unsubscribed_at = now
    buyer.status = "Do Not Contact"
    db.add(buyer)

    # Pause all queued campaigns for this buyer
    queued_result = await db.execute(
        select(Campaign).where(
            Campaign.buyer_id == buyer_id,
            Campaign.status == "Queued",
        )
    )
    for qc in queued_result.scalars().all():
        qc.status = "Paused"
        db.add(qc)

    # Log to activity log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="buyer",
        entity_id=buyer_id,
        action="unsubscribed",
        metadata_json={
            "email": buyer.email,
            "source": "unsubscribe_link",
            "full_name": buyer.full_name,
        },
    )
    db.add(log_entry)

    await db.commit()

    logger.info(
        "Buyer %s (%s) unsubscribed via link",
        buyer_id, buyer.email,
    )

    # Redirect to frontend with confirmation
    redirect_url = f"{settings.frontend_url.rstrip('/')}/buyers/{buyer_id}?unsubscribed=1"
    return RedirectResponse(url=redirect_url)


@router.post("/{buyer_id}/opt-out")
async def opt_out_buyer(
    buyer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Manually mark a buyer as opted out (e.g., from Unsubscribe reply intent).

    This endpoint is called programmatically by the campaign reply processor.
    It can also be called from the UI to manually opt out a buyer.

    Sets unsubscribed_at, changes status to "Do Not Contact", and
    pauses all queued campaigns for this buyer.
    """
    result = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer_id)
    )
    buyer = result.scalar_one_or_none()
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Buyer with id '{buyer_id}' not found",
        )

    now = datetime.now(timezone.utc)
    buyer.unsubscribed_at = now
    buyer.status = "Do Not Contact"
    db.add(buyer)

    # Pause all queued campaigns
    queued_result = await db.execute(
        select(Campaign).where(
            Campaign.buyer_id == buyer_id,
            Campaign.status == "Queued",
        )
    )
    for qc in queued_result.scalars().all():
        qc.status = "Paused"
        db.add(qc)

    # Log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="buyer",
        entity_id=buyer_id,
        action="unsubscribed",
        metadata_json={
            "email": buyer.email,
            "source": "manual_opt_out",
        },
    )
    db.add(log_entry)

    await db.commit()

    logger.info(
        "Buyer %s (%s) opted out manually",
        buyer_id, buyer.email,
    )

    return {"status": "opted_out", "buyer_id": str(buyer_id), "email": buyer.email}


@router.delete("/{buyer_id}/opt-out")
async def resubscribe_buyer(
    buyer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Re-subscribe an opted-out buyer — clear unsubscribed_at and set status to Active.

    This reverses a previous opt-out action, allowing the buyer to receive
    campaign emails again.

    Args:
        buyer_id: UUID of the buyer to re-subscribe.

    Returns:
        dict with success status.
    """
    result = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer_id)
    )
    buyer = result.scalar_one_or_none()
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Buyer with id '{buyer_id}' not found",
        )

    buyer.unsubscribed_at = None
    buyer.status = "Active"
    db.add(buyer)

    # Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="buyer",
        entity_id=buyer_id,
        action="resubscribed",
        metadata_json={
            "email": buyer.email,
            "full_name": buyer.full_name,
        },
    )
    db.add(log_entry)

    await db.commit()

    logger.info(
        "Buyer %s (%s) re-subscribed",
        buyer_id, buyer.email,
    )

    return {"success": True}


@router.delete("/{buyer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_buyer(buyer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete a buyer."""
    result = await db.execute(
        select(Buyer).options(selectinload(Buyer.buyer_emails)).where(Buyer.id == buyer_id)
    )
    buyer = result.scalar_one_or_none()
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Buyer with id '{buyer_id}' not found",
        )

    await db.delete(buyer)
    await db.commit()
    return None