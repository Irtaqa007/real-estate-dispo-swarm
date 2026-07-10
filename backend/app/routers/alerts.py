"""Contract alerts API router.

Provides endpoints for the dashboard Contracts page:
- GET /api/alerts/contract-ready — lists unresolved contract-ready alerts
- POST /api/alerts/contract-ready/{id}/resolve — marks an alert as resolved
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import ActivityLog, Buyer, Campaign, Deal
from app.schemas import ContractAlertItem, ContractAlertResolveRequest
from app.services.gmail_service import send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("/contract-ready", response_model=List[ContractAlertItem])
async def get_contract_alerts(
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Fetch all unresolved contract_ready alerts, ordered by created_at DESC.

    Queries the activity_log for entries with action='contract_ready',
    filtering for unresolved ones (where metadata->>'resolved' is not true).
    Returns the enriched alert list.
    """
    stmt = (
        select(ActivityLog)
        .where(
            ActivityLog.action == "contract_ready",
            ActivityLog.resolved == False,
        )
        .order_by(desc(ActivityLog.created_at))
        .limit(50)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    items: List[Dict[str, Any]] = []
    for entry in entries:
        meta = entry.metadata_json or {}
        buyer = meta.get("buyer", {})
        deal = meta.get("deal", {})

        items.append({
            "alert_id": str(entry.id),
            "created_at": entry.created_at,
            "buyer_name": buyer.get("name"),
            "buyer_email": buyer.get("email"),
            "deal_address": deal.get("address"),
            "deal_state": deal.get("state"),
            "negotiated_price": meta.get("negotiated_price"),
            "my_payout": deal.get("my_payout"),
            "jv_partner_name": deal.get("jv_partner"),
            "resolved": entry.resolved,
            "resolved_at": meta.get("resolved_at"),
            "full_metadata": meta,
        })

    return items


@router.post("/contract-ready/{alert_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_contract_alert(
    alert_id: uuid.UUID,
    body: ContractAlertResolveRequest = None,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Mark a contract-ready alert as resolved.

    Updates the activity_log entry's metadata with resolved=True,
    resolved_at timestamp, and optional notes.
    """
    entry = await db.get(ActivityLog, alert_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract alert not found",
        )

    if entry.action != "contract_ready":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Activity log entry is not a contract-ready alert",
        )

    now = datetime.now(timezone.utc)
    entry.resolved = True
    entry.resolved_at = now
    meta = entry.metadata_json or {}
    meta["resolved_at"] = now.isoformat()
    if body and body.notes:
        meta["resolution_notes"] = body.notes
    entry.metadata_json = meta
    db.add(entry)
    await db.commit()

    logger.info(
        "Contract alert %s resolved (notes: %s)",
        alert_id, body.notes if body else "",
    )

    return {
        "alert_id": str(alert_id),
        "resolved": True,
        "resolved_at": now.isoformat(),
        "message": "Contract marked as sent.",
    }


# ---------------------------------------------------------------------------
# Negotiation escalation endpoints
# ---------------------------------------------------------------------------


@router.get("/negotiation")
async def get_negotiation_alerts(
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Fetch unresolved negotiation escalations from activity_log.

    Returns alerts where action='negotiation_escalation' and resolved=False.
    """
    stmt = (
        select(ActivityLog)
        .where(
            ActivityLog.action == "negotiation_escalation",
            ActivityLog.resolved == False,
        )
        .order_by(desc(ActivityLog.created_at))
        .limit(50)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    items: List[Dict[str, Any]] = []
    for entry in entries:
        meta = entry.metadata_json or {}
        items.append({
            "alert_id": str(entry.id),
            "created_at": entry.created_at,
            "buyer_name": meta.get("buyer_name", ""),
            "buyer_email": meta.get("buyer_email", ""),
            "deal_address": meta.get("deal_address", ""),
            "counter_price": meta.get("counter_price"),
            "floor_price": meta.get("floor_price"),
            "gap": meta.get("gap"),
            "deal_id": meta.get("deal_id"),
            "campaign_id": meta.get("campaign_id"),
            "buyer_id": meta.get("buyer_id"),
            "resolved": entry.resolved,
            "resolved_at": entry.resolved_at,
            "full_metadata": meta,
        })

    return items


class NegotiationApproveRequest(BaseModel):
    """Request body for approving a below-floor counter."""
    final_price: float


class NegotiationRejectRequest(BaseModel):
    """Request body for rejecting a below-floor counter."""
    counter_offer: Optional[float] = None  # Optional counter back to buyer


@router.post("/negotiation/{alert_id}/approve", status_code=status.HTTP_200_OK)
async def approve_negotiation(
    alert_id: uuid.UUID,
    body: NegotiationApproveRequest,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Approve a below-floor counter at the operator's specified final price.

    - Marks alert as resolved
    - Sends approval reply to buyer
    - Updates campaign.agreed_price = final_price
    - Updates campaign.conversation_stage = 'collecting_info'
    """
    entry = await db.get(ActivityLog, alert_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Negotiation alert not found")
    if entry.action != "negotiation_escalation":
        raise HTTPException(status_code=400, detail="Activity log entry is not a negotiation alert")

    meta = entry.metadata_json or {}
    campaign_id_str = meta.get("campaign_id")
    if not campaign_id_str:
        raise HTTPException(status_code=400, detail="Alert missing campaign_id")

    campaign = await db.get(Campaign, uuid.UUID(campaign_id_str))
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    buyer = await db.get(Buyer, uuid.UUID(meta["buyer_id"])) if meta.get("buyer_id") else None
    deal = await db.get(Deal, uuid.UUID(meta["deal_id"])) if meta.get("deal_id") else None

    # Send approval reply to buyer
    buyer_email = meta.get("buyer_email", "")
    if buyer_email and deal:
        try:
            approval_body = (
                f"Works for me — I'll take ${body.final_price:,.0f} for {deal.address}. "
                f"Let me get the paperwork started.\n\n{settings.operator_signature}"
            )
            await send_email(
                to=buyer_email,
                subject=f"Re: {deal.address}",
                body=approval_body,
                send_type="reply",
            )
        except Exception as e:
            logger.error("Failed to send approval email to buyer %s: %s", buyer_email, e, exc_info=True)

    # Update campaign
    campaign.agreed_price = body.final_price
    campaign.conversation_stage = "collecting_info"
    db.add(campaign)

    # Mark alert resolved
    now = datetime.now(timezone.utc)
    entry.resolved = True
    entry.resolved_at = now
    meta["resolved_at"] = now.isoformat()
    meta["resolution_action"] = "approved"
    meta["final_price"] = body.final_price
    entry.metadata_json = meta
    db.add(entry)

    await db.commit()

    logger.info(
        "Negotiation %s approved: final_price=%.0f, campaign=%s",
        alert_id, body.final_price, campaign_id_str,
    )

    return {
        "alert_id": str(alert_id),
        "resolved": True,
        "action": "approved",
        "final_price": body.final_price,
        "sent_to": buyer_email,
        "message": f"Counter approved at ${body.final_price:,.0f}. Buyer notified.",
    }


@router.post("/negotiation/{alert_id}/reject", status_code=status.HTTP_200_OK)
async def reject_negotiation(
    alert_id: uuid.UUID,
    body: NegotiationRejectRequest = None,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Reject a below-floor counter (or counter-back with operator's price).

    - If counter_offer provided: send that price back to buyer
    - If not: send polite decline
    - Mark alert as resolved
    """
    entry = await db.get(ActivityLog, alert_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Negotiation alert not found")
    if entry.action != "negotiation_escalation":
        raise HTTPException(status_code=400, detail="Activity log entry is not a negotiation alert")

    meta = entry.metadata_json or {}
    buyer_email = meta.get("buyer_email", "")
    deal_address = meta.get("deal_address", "")
    counter_price = meta.get("counter_price", 0)

    # Build reply based on whether operator wants to counter back
    counter_offer_price = body.counter_offer if body else None
    if counter_offer_price and buyer_email:
        # Send counter-back to buyer
        try:
            counter_body = (
                f"I appreciate the offer of ${float(counter_price):,.0f}, but "
                f"I'd need to be closer to ${counter_offer_price:,.0f} to make this "
                f"work on {deal_address}. Let me know if that's doable on your end.\n\n"
                f"{settings.operator_signature}"
            )
            await send_email(
                to=buyer_email,
                subject=f"Re: {deal_address}",
                body=counter_body,
                send_type="reply",
            )
        except Exception as e:
            logger.error("Failed to send counter email to buyer %s: %s", buyer_email, e, exc_info=True)
    elif buyer_email:
        # Send polite decline
        try:
            decline_body = (
                f"Thanks for the offer on {deal_address}. Unfortunately I can't "
                f"make the numbers work at that level. I'll keep you posted if "
                f"anything changes.\n\n{settings.operator_signature}"
            )
            await send_email(
                to=buyer_email,
                subject=f"Re: {deal_address}",
                body=decline_body,
                send_type="reply",
            )
        except Exception as e:
            logger.error("Failed to send decline email to buyer %s: %s", buyer_email, e, exc_info=True)

    # Mark alert resolved
    now = datetime.now(timezone.utc)
    entry.resolved = True
    entry.resolved_at = now
    meta["resolved_at"] = now.isoformat()
    meta["resolution_action"] = "countered" if counter_offer_price else "declined"
    meta["counter_sent"] = counter_offer_price
    entry.metadata_json = meta
    db.add(entry)

    await db.commit()

    action_str = f"countered at ${counter_offer_price:,.0f}" if counter_offer_price else "declined"
    logger.info(
        "Negotiation %s %s — buyer=%s",
        alert_id, action_str, buyer_email,
    )

    return {
        "alert_id": str(alert_id),
        "resolved": True,
        "action": "countered" if counter_offer_price else "declined",
        "counter_sent": counter_offer_price,
        "sent_to": buyer_email,
        "message": f"Counter {action_str}. Buyer notified.",
    }
