"""Semantic matching router — find top buyer matches for a deal using
hard filters + vector similarity + max-2-active-deals enforcement.
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schemas import Deal
from app.schemas import MatchResponse
from app.services.matching_service import find_top_matches_for_deal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/match", tags=["matching"])


@router.post("/{deal_id}", response_model=MatchResponse)
async def match_buyers_for_deal(
    deal_id: UUID,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Find top semantic matches for a deal using hard filters + vector similarity.

    Applies the full matching pipeline:
    1. Hard filters: price range, property type, geography (from structured cols)
    2. pgvector cosine similarity ranking
    3. Similarity threshold (default 0.65, configurable via MATCH_SIMILARITY_THRESHOLD)
    4. Max 2 active deals per buyer (excludes capped buyers)
    5. Queued deal matches inserted for capped buyers
    """
    # Fetch the deal
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    # Check if the deal has an embedding
    if deal.deal_embedding is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Deal '{deal_id}' has no embedding. Ensure it was created with an embedding.",
        )

    result = await find_top_matches_for_deal(
        db=db,
        deal=deal,
        limit=limit,
    )

    logger.info(
        "Found %d matches for deal %s (address=%s) — %d skipped due to cap, %d queued",
        len(result.matches),
        deal_id,
        deal.address,
        result.skipped_due_to_cap,
        result.queued_count,
    )

    return MatchResponse(
        deal_id=deal_id,
        deal_address=deal.address,
        matches=result.matches,
    )
