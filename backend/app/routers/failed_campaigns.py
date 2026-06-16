"""Dead Letter Queue router for failed campaign emails.

Provides endpoints to list failed campaigns and retry individual entries.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.schemas import FailedCampaign
from app.schemas import FailedCampaignResponse, FailedCampaignRetryResponse
from app.services.dead_letter_queue import retry_failed_campaign

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/failed-campaigns", tags=["failed-campaigns"])


@router.get("", response_model=List[FailedCampaignResponse])
async def list_failed_campaigns(
    resolved: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """List all entries in the dead letter queue.

    Args:
        resolved: If False (default), returns only unresolved entries.
                  If True, returns only resolved entries.

    Returns:
        List of FailedCampaignResponse.
    """
    result = await db.execute(
        select(FailedCampaign)
        .where(FailedCampaign.resolved == resolved)
        .order_by(FailedCampaign.created_at.desc())
        .options(selectinload(FailedCampaign.campaign))
    )
    entries = result.scalars().all()
    return entries


@router.post("/{entry_id}/retry", response_model=FailedCampaignRetryResponse)
async def retry_failed_campaign_endpoint(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retry sending a failed campaign from the dead letter queue.

    Fetches the DLQ entry, re-attempts to send the email via Gmail SMTP,
    and marks the entry as resolved if successful.

    Args:
        entry_id: UUID of the FailedCampaign entry to retry.

    Returns:
        FailedCampaignRetryResponse with retry result.
    """
    import uuid as uuid_mod
    try:
        entry_uuid = uuid_mod.UUID(entry_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID: '{entry_id}'",
        )

    result = await db.execute(
        select(FailedCampaign).where(FailedCampaign.id == entry_uuid)
    )
    entry = result.scalar_one_or_none()

    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FailedCampaign entry '{entry_id}' not found",
        )

    if entry.resolved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"FailedCampaign entry '{entry_id}' is already resolved",
        )

    outcome = await retry_failed_campaign(db, entry)
    await db.commit()

    return FailedCampaignRetryResponse(
        id=entry.id,
        campaign_id=entry.campaign_id,
        retry_count=entry.retry_count,
        success=outcome["success"],
        error=outcome.get("error"),
    )
