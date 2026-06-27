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
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import ActivityLog
from app.schemas import ContractAlertItem, ContractAlertResolveRequest

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
