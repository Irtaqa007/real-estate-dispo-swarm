"""Semantic matching router — find top buyer matches for a deal using vector similarity."""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schemas import Deal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/match", tags=["matching"])


class BuyerMatchResult(BaseModel):
    """A single buyer match result with similarity score."""

    id: UUID
    full_name: str
    email: str
    buy_box: str
    affiliation: Optional[str] = None
    buyer_tier: Optional[str] = None
    similarity: float


class MatchResponse(BaseModel):
    """Response containing ranked buyer matches for a deal."""

    deal_id: UUID
    deal_address: str
    matches: List[BuyerMatchResult]


@router.post("/{deal_id}", response_model=MatchResponse)
async def match_buyers_for_deal(
    deal_id: UUID,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Find top semantic matches for a deal using vector similarity.

    Uses pgvector's cosine distance operator (<=>) to rank active, verified
    buyers whose buy_box embeddings are most similar to the deal embedding.
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

    # Use pgvector's <=> cosine distance operator.
    # Cosine distance ranges from 0 (identical) to 2 (opposite).
    # Convert to similarity: 1 - distance gives 1 (identical) to -1 (opposite).
    # Clamp to 0-1 for the response.
    sql = text("""
        SELECT
            b.id,
            b.full_name,
            b.email,
            b.buy_box,
            b.affiliation,
            b.buyer_tier,
            GREATEST(0, 1 - (b.buy_box_embedding <=> :deal_embedding)) AS similarity
        FROM buyers b
        WHERE b.status = 'Active'
          AND b.email_verified = TRUE
          AND b.buy_box_embedding IS NOT NULL
          AND b.unsubscribed_at IS NULL
        ORDER BY b.buy_box_embedding <=> CAST(:deal_embedding AS vector)
        LIMIT :limit
    """)

    # pgvector accepts vector strings in the format [0.1, 0.2, ...]
    # asyncpg cannot serialize a Python list as the vector type directly,
    # but it can serialize a string which CAST(... AS vector) parses correctly.
    # Ensure all values are plain Python floats (not numpy types) for string conversion.
    clean_embedding = [float(x) for x in deal.deal_embedding]
    embedding_str = str(clean_embedding)

    rows = await db.execute(
        sql,
        {
            "deal_embedding": embedding_str,
            "limit": limit,
        },
    )

    matches = [
        BuyerMatchResult(
            id=row.id,
            full_name=row.full_name,
            email=row.email,
            buy_box=row.buy_box,
            affiliation=row.affiliation,
            buyer_tier=row.buyer_tier,
            similarity=float(row.similarity),
        )
        for row in rows
    ]

    logger.info(
        "Found %d matches for deal %s (address=%s)",
        len(matches),
        deal_id,
        deal.address,
    )

    return MatchResponse(
        deal_id=deal_id,
        deal_address=deal.address,
        matches=matches,
    )
