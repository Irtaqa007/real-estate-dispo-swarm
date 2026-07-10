"""Deal CRUD API endpoints."""

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import ActivityLog, Buyer, Campaign, Deal, JVPartner
from app.services.groq_client import groq_chat_completion
from app.schemas import (
    CloseDealRequest,
    CloseDealResponse,
    DealCreate,
    DealResponse,
    DealUpdate,
    MarkPaidRequest,
    MarkPaidResponse,
    RevenueDashboardResponse,
    RevenueDealItem,
    UnderContractRequest,
)
from app.services.embeddings import generate_embedding
from app.services.google_drive import (
    archive_deal_folder,
    get_or_create_archive_folder,
    revoke_shared_links,
    upload_multiple,
)
from app.services.deal_dedup import check_deal_duplicate
from app.services.matching_service import trigger_release_for_deal_async
from app.services.zip_lookup import lookup_zip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deals", tags=["deals"])


@router.post("", response_model=DealResponse, status_code=status.HTTP_201_CREATED)
async def create_deal(
    deal_in: DealCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create a new deal. Spread is computed automatically by the database.

    Deal embedding is generated in a background task so the API returns
    immediately without waiting for the embedding model call.
    """

    # Deal Similarity Deduplication (feature 2): check for duplicates
    is_dup, dup_info = await check_deal_duplicate(
        db=db,
        address=deal_in.address,
        city=deal_in.city,
        state=deal_in.state,
        property_type=deal_in.property_type,
        condition_description=deal_in.condition_description,
        beds=deal_in.beds,
        baths=deal_in.baths,
        sqft=deal_in.sqft,
        lot_size=deal_in.lot_size,
        zoning=deal_in.zoning,
    )
    if is_dup and dup_info:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"This deal is {dup_info['similarity_score']*100:.0f}% similar to an existing deal at {dup_info['address']}.",
                "is_duplicate": True,
                "matched_deal_id": dup_info["matched_deal_id"],
                "matched_address": dup_info["address"],
                "similarity_score": dup_info["similarity_score"],
            },
        )

    deal = Deal(
        id=uuid.uuid4(),
        address=deal_in.address,
        city=deal_in.city,
        state=deal_in.state,
        zip=deal_in.zip,
        county=deal_in.county,
        property_type=deal_in.property_type,
        beds=deal_in.beds,
        baths=deal_in.baths,
        sqft=deal_in.sqft,
        year_built=deal_in.year_built,
        occupancy_status=deal_in.occupancy_status,
        repair_estimate=deal_in.repair_estimate,
        lot_size=deal_in.lot_size,
        zoning=deal_in.zoning,
        utilities_available=deal_in.utilities_available,
        topography_access=deal_in.topography_access,
        condition_description=deal_in.condition_description,
        arv=deal_in.arv,
        asking_price=deal_in.asking_price,
        floor_price=deal_in.floor_price,
        contract_price=deal_in.contract_price,
        title_status=deal_in.title_status,
        photos=deal_in.photos,
        jv_partner_id=deal_in.jv_partner_id,
        jv_split_percentage=deal_in.jv_split_percentage if deal_in.jv_split_percentage is not None else 50,
        status="Available",
    )

    db.add(deal)
    await db.commit()
    await db.refresh(deal)

    # Generate deal embedding in background (don't block the response)
    background_tasks.add_task(
        _generate_deal_embedding_background,
        deal_id=deal.id,
        narrative=_build_deal_narrative(deal),
    )

    # Create Drive deal folder in background
    background_tasks.add_task(
        _create_drive_folder_background,
        deal_id=deal.id,
        address=deal.address,
        city=deal.city or "",
        state=deal.state or "",
    )

    logger.info("Deal %s created — embedding and Drive folder queued in background", deal.id)
    return deal


@router.get("/zip-lookup/{zip_code}")
async def zip_lookup(zip_code: str):
    """Look up city, state, and county for a US ZIP code.

    Uses the free Zippopotam.us API (no API key required).
    Returns city, state, state_full, county.
    """
    result = await lookup_zip(zip_code)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Could not find location for ZIP code '{zip_code}'",
        )
    return result.to_dict()


@router.get("", response_model=List[DealResponse])
async def list_deals(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all deals with pagination."""
    result = await db.execute(
        select(Deal).offset(skip).limit(limit).order_by(Deal.created_at.desc())
    )
    deals = result.scalars().all()
    return deals


@router.get("/{deal_id}", response_model=DealResponse)
async def get_deal(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get a single deal by UUID."""
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )
    return deal


@router.put("/{deal_id}", response_model=DealResponse)
async def update_deal(
    deal_id: uuid.UUID,
    deal_in: DealUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a deal. Only provided fields are updated."""
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    # Update only the fields that were provided
    update_data = deal_in.model_dump(exclude_unset=True)

    # Dedup check: if core address/type fields changed, check for duplicates
    # excluding the current deal itself
    if _dedup_relevant_fields_changed(update_data):
        is_dup, dup_info = await check_deal_duplicate(
            db=db,
            address=update_data.get("address", deal.address),
            city=update_data.get("city", deal.city),
            state=update_data.get("state", deal.state),
            property_type=update_data.get("property_type", deal.property_type),
            condition_description=update_data.get("condition_description", deal.condition_description),
            beds=update_data.get("beds", deal.beds),
            baths=update_data.get("baths", deal.baths),
            sqft=update_data.get("sqft", deal.sqft),
            lot_size=update_data.get("lot_size", deal.lot_size),
            zoning=update_data.get("zoning", deal.zoning),
            deal_id_to_exclude=str(deal.id),
        )
        if is_dup and dup_info:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"This deal is {dup_info['similarity_score']*100:.0f}% similar to an existing deal at {dup_info['address']}.",
                    "is_duplicate": True,
                    "matched_deal_id": dup_info["matched_deal_id"],
                    "matched_address": dup_info["address"],
                    "similarity_score": dup_info["similarity_score"],
                },
            )

    for field, value in update_data.items():
        setattr(deal, field, value)

    await db.commit()
    await db.refresh(deal)

    # Regenerate embedding if semantically meaningful fields changed
    await _regenerate_deal_embedding_if_needed(deal, update_data)

    return deal


@router.delete("/{deal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deal(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete a deal."""
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    await db.delete(deal)
    await db.commit()
    return None


@router.post("/{deal_id}/files", response_model=dict)
async def upload_deal_files(
    deal_id: uuid.UUID,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload files (images, PDFs, etc.) for a deal.

    Each file is uploaded to Google Drive under /DispoSwarm/Deals/{deal_id}/.
    The returned shareable URLs are appended to the deal's photos array.

    Returns:
        dict with keys: uploaded (int), urls (list[str]), filenames (list[str]).
    """
    # Verify deal exists
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    # Read all file contents first
    file_data: list[tuple[bytes, str, str]] = []
    filenames: list[str] = []
    for f in files:
        content = await f.read()
        if not content:
            continue
        mime = f.content_type or "application/octet-stream"
        file_data.append((content, f.filename or "unnamed", mime))
        filenames.append(f.filename or "unnamed")

    if not file_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All files were empty",
        )

    # Upload to Google Drive
    try:
        urls = await upload_multiple(file_data, str(deal_id))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Drive upload failed: {e}",
        )

    if not urls:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="All file uploads to Google Drive failed",
        )

    # Append URLs to the deal's photos array
    existing = deal.photos or []
    deal.photos = existing + urls
    db.add(deal)
    await db.commit()
    await db.refresh(deal)

    logger.info(
        "Uploaded %d file(s) to deal %s: %s",
        len(urls), deal_id, ", ".join(filenames),
    )

    return {
        "uploaded": len(urls),
        "urls": urls,
        "filenames": filenames,
    }


@router.post("/{deal_id}/under-contract", response_model=DealResponse)
async def mark_under_contract(
    deal_id: uuid.UUID,
    body: Optional[UnderContractRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Move a deal to 'Under Contract' status.

    Optionally assigns a buyer to the deal via assigned_buyer_id.
    Allowed from 'Available' or 'Campaign Launched' status.
    """
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    if deal.status not in ("Available", "Campaign Launched"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Deal status is '{deal.status}', must be 'Available' or 'Campaign Launched'",
        )

    deal.status = "Under Contract"

    if body and body.assigned_buyer_id:
        # Verify buyer exists
        buyer = await db.get(Buyer, body.assigned_buyer_id)
        if not buyer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Buyer with id '{body.assigned_buyer_id}' not found",
            )
        deal.assigned_buyer_id = body.assigned_buyer_id

    db.add(deal)

    # Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="deal",
        entity_id=deal.id,
        action="status_change",
        metadata_json={
            "from_status": deal.status,
            "to_status": "Under Contract",
            "assigned_buyer_id": str(body.assigned_buyer_id) if body and body.assigned_buyer_id else None,
        },
    )
    db.add(log_entry)

    await db.commit()
    await db.refresh(deal)

    logger.info(
        "Deal %s (%s) moved to Under Contract — assigned buyer: %s",
        deal.id, deal.address, body.assigned_buyer_id if body else None,
    )

    return deal


@router.post("/{deal_id}/close", response_model=CloseDealResponse)
async def close_deal(
    deal_id: uuid.UUID,
    body: CloseDealRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Close a deal — mark as Sold, calculate payouts, update buyer and JV stats.

    Verifies the deal is in a closable state (Under Contract or Available),
    calculates net_spread, jv_payout, and my_payout based on closed_price and
    the deal's jv_split_percentage. Updates the assigned buyer's stats
    (deals_closed, total_lifetime_spread) and JV partner's stats
    (total_deals_closed, total_split_revenue, total_revenue_generated).

    Args:
        deal_id: UUID of the deal to close.
        body: CloseDealRequest with optional closed_price (defaults to asking_price).

    Returns:
        CloseDealResponse with payout details.
    """
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    if deal.status not in ("Under Contract", "Available", "Campaign Launched"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Deal status is '{deal.status}', must be 'Under Contract', 'Available', or 'Campaign Launched'",
        )

    # Use provided closed_price or default to asking_price
    closed_price = body.closed_price if body.closed_price is not None else float(deal.asking_price)

    # Calculate payouts
    net_spread = closed_price - float(deal.contract_price)
    split_pct = float(deal.jv_split_percentage or 50) / 100
    jv_payout = net_spread * split_pct
    my_payout = net_spread - jv_payout

    now = datetime.now(timezone.utc)

    # Update deal
    deal.status = "Sold"
    deal.closed_at = now
    deal.closed_price = closed_price
    deal.net_spread = net_spread
    deal.jv_payout = jv_payout
    deal.my_payout = my_payout
    db.add(deal)

    buyer_updated = False
    jv_updated = False

    # Update assigned buyer stats
    if deal.assigned_buyer_id:
        buyer = await db.get(Buyer, deal.assigned_buyer_id)
        if buyer:
            buyer.deals_closed = (buyer.deals_closed or 0) + 1
            buyer.total_lifetime_spread = (buyer.total_lifetime_spread or 0) + net_spread
            db.add(buyer)
            buyer_updated = True
            logger.info(
                "Buyer %s deals_closed incremented, total_lifetime_spread += %.2f",
                buyer.id, net_spread,
            )

    # Update JV partner stats
    if deal.jv_partner_id:
        jv = await db.get(JVPartner, deal.jv_partner_id)
        if jv:
            jv.total_deals_closed = (jv.total_deals_closed or 0) + 1
            jv.total_split_revenue = (jv.total_split_revenue or 0) + jv_payout
            jv.total_revenue_generated = (jv.total_revenue_generated or 0) + net_spread
            db.add(jv)
            jv_updated = True
            logger.info(
                "JV partner %s stats updated: deals_closed +1, split_revenue += %.2f",
                jv.id, jv_payout,
            )

    # Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="deal",
        entity_id=deal.id,
        action="closed",
        metadata_json={
            "closed_price": closed_price,
            "net_spread": net_spread,
            "jv_payout": jv_payout,
            "my_payout": my_payout,
            "jv_split_pct": split_pct * 100,
            "buyer_id": str(deal.assigned_buyer_id) if deal.assigned_buyer_id else None,
            "jv_partner_id": str(deal.jv_partner_id) if deal.jv_partner_id else None,
        },
    )
    db.add(log_entry)

    await db.commit()
    await db.refresh(deal)

    # FEATURE 2: Event-driven queued match release
    # When a deal is closed (Sold), buyers' slots may free up — trigger
    # immediate release instead of waiting for the next scheduler tick.
    # This runs as a background task so the API response is not delayed.
    background_tasks.add_task(
        trigger_release_for_deal_async,
        deal_id=deal.id,
    )

    logger.info(
        "Deal %s (%s) closed at $%.2f — net_spread: $%.2f, my_payout: $%.2f, jv_payout: $%.2f",
        deal.id, deal.address, closed_price, net_spread, my_payout, jv_payout,
    )

    return CloseDealResponse(
        id=deal.id,
        status=deal.status,
        closed_at=now,
        closed_price=closed_price,
        net_spread=net_spread,
        jv_payout=jv_payout,
        my_payout=my_payout,
        buyer_updated=buyer_updated,
        jv_updated=jv_updated,
    )




@router.get("/{deal_id}/campaigns")
async def get_deal_campaigns(
    deal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return all campaigns for a deal with conversation stage and reply info."""
    from app.models.models import Campaign
    result = await db.execute(
        select(Campaign)
        .where(Campaign.deal_id == deal_id)
        .order_by(Campaign.touch_number.asc(), Campaign.created_at.asc())
    )
    campaigns = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "buyer_id": str(c.buyer_id),
            "touch_number": c.touch_number,
            "status": c.status,
            "conversation_stage": c.conversation_stage,
            "sent_at": c.sent_at.isoformat() if c.sent_at else None,
            "scheduled_send_at": c.scheduled_send_at.isoformat() if c.scheduled_send_at else None,
            "subject": c.subject,
            "body": c.body,
            "reply_received_at": c.reply_received_at.isoformat() if c.reply_received_at else None,
            "reply_body": c.reply_body,
            "reply_intent": c.reply_intent,
            "ai_extracted_insights": c.ai_extracted_insights,
            "buyer_legal_name": c.buyer_legal_name,
            "buyer_phone": c.buyer_phone,
            "buyer_title_company": c.buyer_title_company,
            "agreed_price": float(c.agreed_price) if c.agreed_price else None,
            "pass_reason_category": c.pass_reason_category,
            "pass_reason_raw": c.pass_reason_raw,
        }
        for c in campaigns
    ]

@router.get("/{deal_id}/pass-intelligence")
async def get_deal_pass_intelligence(
    deal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get pass intelligence for a deal.

    Returns pass reason summary, list of passes with buyer details,
    and an AI-generated recommendation if enough data exists.
    """
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    # Fetch all Passed campaigns for this deal with pass reasons
    campaign_result = await db.execute(
        select(Campaign)
        .where(
            Campaign.deal_id == deal_id,
            Campaign.status == "Passed",
            Campaign.pass_reason_category.isnot(None),
        )
        .order_by(Campaign.passed_at.desc().nullslast())
    )
    passed_campaigns = campaign_result.scalars().all()

    # Build passes list with buyer info
    passes = []
    for c in passed_campaigns:
        buyer = await db.get(Buyer, c.buyer_id)
        passes.append({
            "buyer_name": buyer.full_name if buyer else "Unknown",
            "category": c.pass_reason_category,
            "raw": c.pass_reason_raw or "",
            "confidence": c.pass_reason_confidence or "low",
            "passed_at": c.passed_at.isoformat() if c.passed_at else None,
        })

    # Determine top reason
    summary = deal.pass_reasons_summary or {}
    top_reason = max(summary, key=summary.get) if summary else None

    # Generate AI recommendation if enough data
    recommendation = None
    pass_count = deal.pass_count or 0
    if pass_count >= 3 and summary:
        try:
            summary_text = "; ".join(f"{k}: {v}" for k, v in sorted(summary.items(), key=lambda x: -x[1]))
            messages = [
                {
                    "role": "system",
                    "content": "You are a real estate deal analyst. Provide one sentence of actionable advice.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Deal: {deal.address}, {deal.city}, {deal.state}\n"
                        f"Property Type: {deal.property_type}\n"
                        f"Asking Price: ${float(deal.asking_price):,.0f}\n"
                        f"Pass count: {pass_count}\n"
                        f"Pass reasons summary: {summary_text}\n\n"
                        f"Provide one sentence recommending how to improve this deal based on pass patterns."
                    ),
                },
            ]
            response = await groq_chat_completion(
                messages=messages,
                model="llama-3.1-8b-instant",
                temperature=0.1,
                max_tokens=100,
            )
            recommendation = response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("Failed to generate pass intelligence recommendation: %s", e, exc_info=True)

    return {
        "deal_id": str(deal_id),
        "address": deal.address,
        "pass_count": pass_count,
        "pass_reasons_summary": summary,
        "top_reason": top_reason,
        "passes": passes,
        "recommendation": recommendation,
    }


@router.post("/{deal_id}/mark-paid", response_model=MarkPaidResponse)
async def mark_deal_paid(
    deal_id: uuid.UUID,
    body: MarkPaidRequest,
    db: AsyncSession = Depends(get_db),
):
    """Confirm payment received for a closed deal and archive its Drive folder.

    Validates the deal is in 'Sold' or 'Under Contract' status and payment
    hasn't already been confirmed. Archives the deal's Google Drive folder
    to 'Closed Deals Archive' and revokes public sharing links.

    Drive operations are non-blocking — payment confirmation succeeds even
    if Drive archiving fails.
    """
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    if deal.status not in ("Sold", "Under Contract"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deal must be in Sold or Under Contract status to confirm payment",
        )

    if deal.payment_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment already confirmed for this deal",
        )

    now = datetime.now(timezone.utc)
    payment_amount = body.payment_amount

    # Update deal payment fields
    deal.payment_confirmed = True
    deal.payment_confirmed_at = now
    deal.payment_amount = payment_amount
    if deal.status == "Under Contract":
        deal.status = "Sold"
    db.add(deal)

    # Recalculate payouts using actual payment amount
    split_pct = float(deal.jv_split_percentage or 50) / 100
    actual_net_spread = payment_amount - float(deal.contract_price)
    jv_payout = actual_net_spread * split_pct
    my_payout = actual_net_spread - jv_payout
    deal.jv_payout = jv_payout
    deal.my_payout = my_payout
    deal.net_spread = actual_net_spread
    db.add(deal)

    # ── Drive archive operations (non-blocking) ──
    drive_archived = False
    drive_archived_at = None
    drive_archive_folder_id = None
    shared_links_revoked = 0

    if deal.drive_folder_id:
        try:
            import asyncio
            from googleapiclient.discovery import build
            from app.services.google_drive import _get_credentials

            creds = await asyncio.to_thread(_get_credentials)
            drive_service = build("drive", "v3", credentials=creds)

            archive_result = await archive_deal_folder(
                drive_service=drive_service,
                deal_folder_id=deal.drive_folder_id,
                deal_address=deal.address,
            )

            if archive_result["success"]:
                drive_archived = True
                drive_archived_at = now
                drive_archive_folder_id = archive_result["archive_folder_id"]

                deal.drive_archived = True
                deal.drive_archived_at = now
                deal.drive_archive_folder_id = drive_archive_folder_id
                db.add(deal)

                # Revoke shared links on archived folder
                shared_links_revoked = await revoke_shared_links(
                    drive_service=drive_service,
                    folder_id=deal.drive_folder_id,
                )
            else:
                logger.warning(
                    "Drive archive failed for deal %s: %s",
                    deal_id, archive_result.get("error"),
                )
        except Exception as e:
            logger.warning(
                "Drive archive error for deal %s (payment still confirmed): %s",
                deal_id, e, exc_info=True,
            )
    else:
        logger.warning(
            "Deal %s has no drive_folder_id — skipping Drive archive",
            deal_id,
        )

    # Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="deal",
        entity_id=deal.id,
        action="payment_confirmed",
        metadata_json={
            "deal_id": str(deal_id),
            "address": deal.address,
            "payment_amount": float(payment_amount),
            "my_payout": float(my_payout),
            "drive_archived": drive_archived,
            "shared_links_revoked": shared_links_revoked,
            "alert_user": False,
        },
    )
    db.add(log_entry)

    await db.commit()
    await db.refresh(deal)

    logger.info(
        "Payment confirmed for deal %s ($%.2f) — drive_archived=%s, links_revoked=%d",
        deal_id, payment_amount, drive_archived, shared_links_revoked,
    )

    return MarkPaidResponse(
        deal_id=deal.id,
        address=deal.address,
        payment_confirmed=True,
        payment_confirmed_at=now,
        payment_amount=float(payment_amount),
        drive_archived=drive_archived,
        drive_archived_at=drive_archived_at,
        drive_archive_folder_id=drive_archive_folder_id,
        shared_links_revoked=shared_links_revoked,
        message="Payment confirmed and deal folder archived.",
    )


# ---------------------------------------------------------------------------
# Pipeline endpoint
# ---------------------------------------------------------------------------


@router.get("/pipeline")
async def deal_pipeline(
    db: AsyncSession = Depends(get_db),
):
    """Return all deals grouped by pipeline stage with campaign summaries.

    Each deal includes aggregated campaign counts and a derived pipeline stage
    based on the highest conversation_stage or deal status.

    Returns:
        List of dicts ordered by last_activity_at DESC.
    """
    # Fetch all deals
    deal_result = await db.execute(
        select(Deal).order_by(Deal.created_at.desc())
    )
    deals = deal_result.scalars().all()

    # Fetch all campaign counts grouped by deal_id
    from sqlalchemy import func as sa_func

    campaign_agg = await db.execute(
        select(
            Campaign.deal_id,
            sa_func.count(Campaign.id).label("total"),
            sa_func.count(Campaign.id).filter(Campaign.status == "Sent").label("sent"),
            sa_func.count(Campaign.id).filter(Campaign.status == "Replied").label("replied"),
            sa_func.count(Campaign.id).filter(Campaign.status == "Passed").label("passed"),
            sa_func.count(Campaign.id).filter(Campaign.status == "Contract_Pending").label("contract"),
            sa_func.max(Campaign.sent_at).label("last_sent_at"),
            sa_func.max(Campaign.reply_received_at).label("last_reply_at"),
        )
        .group_by(Campaign.deal_id)
    )
    agg_rows = campaign_agg.all()

    # Fetch conversation_stages per deal
    stage_result = await db.execute(
        select(
            Campaign.deal_id,
            Campaign.conversation_stage,
        )
    )
    stage_rows = stage_result.all()

    # Build lookup: deal_id -> list of conversation stages
    deal_stages: dict[str, list[str]] = defaultdict(list)
    for row in stage_rows:
        if row.conversation_stage:
            deal_stages[str(row.deal_id)].append(row.conversation_stage)

    # Stage priority ordering (highest to lowest)
    STAGE_PRIORITY = [
        "contract_ready",
        "collecting_info",
        "qualifying",
        "engaging",
        "pitching",
    ]

    def _get_highest_stage(stages: list[str]) -> str | None:
        """Return the highest-priority conversation stage from the list."""
        for priority in STAGE_PRIORITY:
            if priority in stages:
                return priority
        return None

    # Build agg lookup
    agg_map: dict[str, dict] = {}
    for row in agg_rows:
        did = str(row.deal_id)
        agg_map[did] = {
            "campaigns_total": row.total,
            "campaigns_sent": row.sent,
            "campaigns_replied": row.replied,
            "campaigns_passed": row.passed,
            "campaigns_contract": row.contract,
            "last_sent_at": row.last_sent_at.isoformat() if row.last_sent_at else None,
            "last_reply_at": row.last_reply_at.isoformat() if row.last_reply_at else None,
        }

    results = []
    for deal in deals:
        did = str(deal.id)
        agg = agg_map.get(did, {})
        stages = deal_stages.get(did, [])
        highest_stage = _get_highest_stage(stages)

        # Determine pipeline stage
        if deal.status in ("Sold", "Under Contract", "Dead"):
            stage = "Closed"
        elif agg.get("campaigns_contract", 0) > 0 or "contract_ready" in stages:
            stage = "Contract Ready"
        elif highest_stage in ("qualifying", "collecting_info"):
            stage = "Negotiating"
        elif agg.get("campaigns_replied", 0) > 0:
            stage = "Replied"
        elif deal.status == "Campaign Launched":
            stage = "Launched"
        else:
            stage = "Available"

        # Determine last activity
        last_activity = deal.created_at.isoformat() if deal.created_at else None
        if agg.get("last_reply_at"):
            last_activity = agg["last_reply_at"]
        elif agg.get("last_sent_at"):
            last_activity = agg["last_sent_at"]

        results.append({
            "deal_id": did,
            "address": deal.address,
            "city": deal.city,
            "state": deal.state,
            "property_type": deal.property_type,
            "asking_price": float(deal.asking_price) if deal.asking_price else 0,
            "arv": float(deal.arv) if deal.arv else 0,
            "status": deal.status,
            "campaigns_total": agg.get("campaigns_total", 0),
            "campaigns_sent": agg.get("campaigns_sent", 0),
            "campaigns_replied": agg.get("campaigns_replied", 0),
            "campaigns_passed": agg.get("campaigns_passed", 0),
            "campaigns_contract": agg.get("campaigns_contract", 0),
            "stage": stage,
            "created_at": deal.created_at.isoformat() if deal.created_at else None,
            "last_activity_at": last_activity,
        })

    # Sort by last_activity_at DESC, nulls last
    results.sort(
        key=lambda r: r["last_activity_at"] or "",
        reverse=True,
    )

    return results


# ---------------------------------------------------------------------------
# Revenue dashboard endpoint
# ---------------------------------------------------------------------------


@router.get("/dashboard/revenue", response_model=RevenueDashboardResponse)
async def revenue_dashboard(
    db: AsyncSession = Depends(get_db),
):
    """Get revenue dashboard data.

    Returns all deals with Sold or Under Contract status (or payment_confirmed=True),
    aggregated with confirmed vs pending payout totals.
    """
    result = await db.execute(
        select(Deal)
        .where(
            (Deal.status.in_(["Sold", "Under Contract"]))
            | (Deal.payment_confirmed == True)
        )
        .order_by(Deal.closed_at.desc().nullslast())
    )
    deals = result.scalars().all()

    items = []
    total_assignment_fees = 0.0
    total_my_payout = 0.0
    total_my_payout_confirmed = 0.0
    total_my_payout_pending = 0.0

    for deal in deals:
        closed_price = float(deal.closed_price) if deal.closed_price else 0.0
        net_spread = float(deal.net_spread) if deal.net_spread else 0.0
        my_payout = float(deal.my_payout) if deal.my_payout else 0.0
        payment_amount = float(deal.payment_amount) if deal.payment_amount else None

        # Get JV partner name
        jv_name = None
        if deal.jv_partner_id:
            jv = await db.get(JVPartner, deal.jv_partner_id)
            if jv:
                jv_name = jv.name

        total_assignment_fees += net_spread
        total_my_payout += my_payout

        if deal.payment_confirmed:
            total_my_payout_confirmed += payment_amount or my_payout
        elif deal.status == "Sold":
            total_my_payout_pending += my_payout

        items.append(RevenueDealItem(
            deal_id=deal.id,
            address=deal.address,
            closed_at=deal.closed_at,
            closed_price=closed_price,
            net_spread=net_spread,
            my_payout=my_payout,
            payment_confirmed=deal.payment_confirmed or False,
            payment_confirmed_at=deal.payment_confirmed_at,
            payment_amount=payment_amount,
            jv_partner_name=jv_name,
            status=deal.status,
        ))

    return RevenueDashboardResponse(
        total_deals_closed=len(items),
        total_assignment_fees=round(total_assignment_fees, 2),
        total_my_payout=round(total_my_payout, 2),
        total_my_payout_confirmed=round(total_my_payout_confirmed, 2),
        total_my_payout_pending=round(total_my_payout_pending, 2),
        deals=items,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEDUP_RELEVANT_FIELDS = frozenset({
    "address", "city", "state", "property_type", "condition_description",
    "beds", "baths", "sqft", "lot_size", "zoning",
})


def _dedup_relevant_fields_changed(update_data: dict) -> bool:
    """Check if the updated fields could affect dedup similarity."""
    return bool(_DEDUP_RELEVANT_FIELDS & update_data.keys())


# Fields that affect the deal embedding (semantic matching relies on these)
_EMBEDDING_AFFECTING_FIELDS = frozenset({
    "property_type", "beds", "baths", "sqft", "year_built",
    "city", "lot_size", "zoning", "condition_description",
    "arv", "asking_price",
})


async def _create_drive_folder_background(
    deal_id: uuid.UUID,
    address: str,
    city: str,
    state: str,
) -> None:
    """Create a Google Drive folder for the deal in the background.

    Runs as a FastAPI BackgroundTask so deal creation is not blocked
    by the Drive API call. Stores the folder ID on the deal record.
    """
    try:
        from app.database import async_session_factory
        from app.services.google_drive import _get_credentials, _ensure_deal_folder
        from googleapiclient.discovery import build
        import asyncio

        folder_name = f"{state} — SFR — {address} — {deal_id.hex[:8]}"

        def _create_folder() -> str:
            """Synchronous: get creds, build service, ensure folder exists."""
            creds = _get_credentials()
            service = build("drive", "v3", credentials=creds)
            return _ensure_deal_folder(service, folder_name)

        folder_id = await asyncio.to_thread(_create_folder)

        if folder_id:
            async with async_session_factory() as db:
                deal = await db.get(Deal, deal_id)
                if deal:
                    deal.drive_folder_id = folder_id
                    await db.commit()
                    logger.info(
                        "Created Drive folder for deal %s: %s (id=%s)",
                        deal_id, folder_name, folder_id,
                    )
    except Exception as e:
        logger.warning(
            "Failed to create Drive folder for deal %s: %s",
            deal_id, e, exc_info=True,
        )


async def _generate_deal_embedding_background(deal_id: uuid.UUID, narrative: str) -> None:
    """Generate deal embedding in the background after creation.

    This runs as a FastAPI BackgroundTask so the API response is not
    blocked by the embedding model call.
    """
    if not narrative:
        return
    try:
        from app.database import async_session_factory
        from sqlalchemy import select

        embedding = await generate_embedding(narrative, input_type="search_document")
        async with async_session_factory() as db:
            deal = await db.get(Deal, deal_id)
            if deal:
                deal.deal_embedding = embedding
                await db.commit()
                logger.info("Background embedding generated for deal %s", deal_id)
    except Exception as e:
        logger.warning("Background embedding failed for deal %s: %s", deal_id, e, exc_info=True)


async def _regenerate_deal_embedding_if_needed(
    deal: Deal,
    update_data: dict,
) -> None:
    """Regenerate the deal embedding if any semantically meaningful fields changed.

    Compares the updated fields against the set of fields that affect
    the embedding narrative. If any of those fields were changed,
    rebuilds the narrative and generates a fresh embedding.

    Args:
        deal: The deal object (already committed and refreshed).
        update_data: Dict of fields that were updated.
    """
    changed_embedding_fields = _EMBEDDING_AFFECTING_FIELDS & update_data.keys()
    if not changed_embedding_fields:
        return

    try:
        narrative = _build_deal_narrative(deal)
        if narrative:
            embedding = await generate_embedding(
                narrative,
                input_type="search_document",
            )
            # We need a new session since the previous one's transaction is committed
            from app.database import async_session_factory
            from sqlalchemy import select

            async with async_session_factory() as db:
                fresh_deal = await db.get(Deal, deal.id)
                if fresh_deal:
                    fresh_deal.deal_embedding = embedding
                    await db.commit()
                    logger.info(
                        "Regenerated deal embedding for %s — fields changed: %s",
                        deal.id, sorted(changed_embedding_fields),
                    )
    except Exception as e:
        logger.warning(
            "Failed to regenerate deal embedding for %s: %s",
            deal.id, e, exc_info=True,
        )


def _build_deal_narrative(deal: Deal) -> str:
    """Build a narrative string from deal data for embedding."""
    if deal.property_type == "House":
        parts = [
            f"Single family {deal.beds}bed/{deal.baths}bath",
        ]
        if deal.city:
            parts.append(f"in {deal.city}")
        if deal.sqft:
            parts.append(f"{deal.sqft}sqft")
        if deal.year_built:
            parts.append(f"built {deal.year_built}")
        if deal.condition_description:
            parts.append(f"{deal.condition_description}")
        parts.append(f"ARV ${deal.arv:,.0f}")
        parts.append(f"asking ${deal.asking_price:,.0f}")
        return ". ".join(parts) + "."

    elif deal.property_type == "Land":
        parts = []
        if deal.lot_size:
            parts.append(f"{deal.lot_size}")
        if deal.zoning:
            parts.append(f"{deal.zoning} lot")
        if deal.city:
            parts.append(f"in {deal.city}")
        if deal.condition_description:
            parts.append(f"{deal.condition_description}")
        parts.append(f"ARV ${deal.arv:,.0f}")
        parts.append(f"asking ${deal.asking_price:,.0f}")
        return ". ".join(parts) + "."

    return ""
