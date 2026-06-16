"""Shared deal-to-buyer matching service.

Replaces the duplicated inline SQL in matching.py and campaigns.py with
a single function that enforces:

1. Hard filters (price range, property type, geography)
2. Minimum similarity threshold (configurable, default 0.65)
3. Max 2 active deals per buyer (exclude capped buyers)
4. Per-buyer deal queue insertion for capped buyers
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.schemas import Campaign, Deal, QueuedDealMatch
from app.schemas import BuyerMatchResult

logger = logging.getLogger(__name__)

# Campaign statuses that count as an "active" deal for the 2-deal cap
ACTIVE_DEAL_STATUSES = ("Sent", "Replied", "Under Contract")

# Default match limit
DEFAULT_MATCH_LIMIT = 20


class MatchResult:
    """Result of a matching operation."""

    def __init__(
        self,
        deal_id: UUID,
        deal_address: str,
        matches: List[BuyerMatchResult],
        skipped_due_to_cap: int = 0,
        queued_count: int = 0,
    ):
        self.deal_id = deal_id
        self.deal_address = deal_address
        self.matches = matches
        self.skipped_due_to_cap = skipped_due_to_cap
        self.queued_count = queued_count


async def find_top_matches_for_deal(
    db: AsyncSession,
    deal: Deal,
    limit: int = DEFAULT_MATCH_LIMIT,
    match_threshold: Optional[float] = None,
) -> MatchResult:
    """Find top buyer matches for a deal with hard filters, similarity
    threshold, and max-2-active-deals enforcement.

    Flow:
    1. Hard filter: price range, property type, geography (from structured cols)
    2. Semantic ranking: pgvector cosine similarity
    3. Similarity threshold: reject below configurable cutoff
    4. Max 2 active deals: exclude buyers already at cap
    5. Queue: insert queued_deal_matches for capped buyers

    Args:
        db: Database session.
        deal: The Deal to match buyers against.
        limit: Maximum number of matches to return.
        match_threshold: Minimum similarity score. Defaults to settings.

    Returns:
        MatchResult with matches, skipped_due_to_cap count, and queued_count.
    """
    if deal.deal_embedding is None:
        return MatchResult(
            deal_id=deal.id,
            deal_address=deal.address,
            matches=[],
        )

    threshold = match_threshold if match_threshold is not None else settings.match_similarity_threshold
    clean_embedding = [float(x) for x in deal.deal_embedding]
    embedding_str = str(clean_embedding)

    # -------------------------------------------------------------------
    # Step 1-2: Hard filters + similarity ranking in a single SQL query.
    #
    # The hard filter conditions use IS NULL / IS NOT NULL semantics:
    #   - price_min IS NULL → no lower bound
    #   - price_max IS NULL → no upper bound
    #   - pref_property_type IS NULL → accepts both House and Land
    #   - pref_cities IS NULL → any geography
    #
    # These are checked BEFORE the ORDER BY/LIMIT so that pgvector only
    # ranks buyers who actually pass the filters.
    # -------------------------------------------------------------------
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
          -- Hard filter: price range
          AND (b.price_min IS NULL OR :asking_price >= b.price_min)
          AND (b.price_max IS NULL OR :asking_price <= b.price_max)
          -- Hard filter: property type
          AND (b.pref_property_type IS NULL OR :property_type = b.pref_property_type)
          -- Hard filter: geography (city match or no preference)
          AND (b.pref_cities IS NULL OR :city = ANY(b.pref_cities) OR :city IS NULL)
          -- Similarity threshold
          AND GREATEST(0, 1 - (b.buy_box_embedding <=> :deal_embedding)) >= :threshold
        ORDER BY b.buy_box_embedding <=> CAST(:deal_embedding AS vector)
        LIMIT :fetch_limit
    """)

    # Fetch extra to account for buyers removed by the 2-deal cap
    fetch_limit = limit * 2

    rows = await db.execute(
        sql,
        {
            "deal_embedding": embedding_str,
            "asking_price": float(deal.asking_price),
            "property_type": deal.property_type,
            "city": deal.city or "",
            "threshold": threshold,
            "fetch_limit": fetch_limit,
        },
    )
    candidates = rows.fetchall()

    if not candidates:
        return MatchResult(
            deal_id=deal.id,
            deal_address=deal.address,
            matches=[],
        )

    # -------------------------------------------------------------------
    # Step 3-4: Check active deal counts per buyer in one batch query.
    # Counts distinct deals (a buyer could have multiple touches on one deal).
    # -------------------------------------------------------------------
    candidate_ids = [row.id for row in candidates]

    count_sql = text("""
        SELECT
            c.buyer_id,
            COUNT(DISTINCT c.deal_id) AS active_deal_count
        FROM campaigns c
        WHERE c.buyer_id = ANY(:buyer_ids)
          AND c.status = ANY(:active_statuses)
        GROUP BY c.buyer_id
    """)
    active_counts_rows = await db.execute(
        count_sql,
        {
            "buyer_ids": candidate_ids,
            "active_statuses": list(ACTIVE_DEAL_STATUSES),
        },
    )
    active_counts = {row.buyer_id: row.active_deal_count for row in active_counts_rows}

    # -------------------------------------------------------------------
    # Step 4-5: Split candidates into eligible and capped.
    # -------------------------------------------------------------------
    eligible = []
    capped = []

    for row in candidates:
        bid = row.id
        active_count = active_counts.get(bid, 0)
        if active_count >= 2:
            capped.append((bid, float(row.similarity)))
        else:
            eligible.append(row)

    # Insert queue entries for capped buyers
    if capped:
        now = datetime.now(timezone.utc)
        for bid, sim in capped:
            # Check if already queued
            existing = await db.execute(
                select(QueuedDealMatch).where(
                    QueuedDealMatch.buyer_id == bid,
                    QueuedDealMatch.deal_id == deal.id,
                    QueuedDealMatch.status == "waiting",
                )
            )
            if not existing.scalar_one_or_none():
                db.add(QueuedDealMatch(
                    buyer_id=bid,
                    deal_id=deal.id,
                    status="waiting",
                    similarity_score=sim,
                    queued_at=now,
                ))
        await db.commit()
        logger.info(
            "Queued %d capped buyers for deal %s (already at 2 active deals)",
            len(capped), deal.id,
        )

    # Sort eligible by similarity and apply limit
    eligible.sort(key=lambda r: float(r.similarity), reverse=True)
    eligible = eligible[:limit]

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
        for row in eligible
    ]

    logger.info(
        "Matching for deal %s: %d hard-filtered candidates, %d eligible, "
        "%d capped (queued), %d matches returned",
        deal.id, len(candidates), len(eligible), len(capped), len(matches),
    )

    return MatchResult(
        deal_id=deal.id,
        deal_address=deal.address,
        matches=matches,
        skipped_due_to_cap=len(capped),
        queued_count=len(capped),
    )


async def get_active_deal_count_for_buyer(db: AsyncSession, buyer_id: UUID) -> int:
    """Count the number of distinct active deals a buyer is involved in.

    Active statuses: Sent, Replied, Under Contract.
    Counts distinct deal_ids, not campaign touches (multiple touches
    on the same deal count as 1).

    Args:
        db: Database session.
        buyer_id: Buyer UUID.

    Returns:
        Number of active deals (0-2+).
    """
    result = await db.execute(
        select(Campaign.deal_id)
        .where(
            Campaign.buyer_id == buyer_id,
            Campaign.status.in_(ACTIVE_DEAL_STATUSES),
        )
        .distinct()
    )
    return len(result.fetchall())


async def process_queued_matches(db: AsyncSession) -> int:
    """Process queued deal matches for buyers whose active deal count
    has dropped below 2.

    For each buyer with queued matches:
    1. Count their current active deals
    2. If < 2, pick the oldest 'waiting' match
    3. Re-validate against the buyer's CURRENT buy_box and hard filters
    4. If still valid, mark as 'released' (the caller/campaign launch
       will pick it up)
    5. If no longer valid, mark as 'invalidated'

    Returns:
        Number of matches released.
    """
    # Find all unique buyers with waiting matches
    waiting_buyers = await db.execute(
        select(QueuedDealMatch.buyer_id)
        .where(QueuedDealMatch.status == "waiting")
        .distinct()
    )
    buyer_ids = [row.buyer_id for row in waiting_buyers]

    if not buyer_ids:
        return 0

    released_count = 0
    now = datetime.now(timezone.utc)

    for buyer_id in buyer_ids:
        try:
            active_count = await get_active_deal_count_for_buyer(db, buyer_id)
            if active_count >= 2:
                continue  # Still at cap

            # Buyer has room — pick the oldest waiting match
            oldest = await db.execute(
                select(QueuedDealMatch)
                .where(
                    QueuedDealMatch.buyer_id == buyer_id,
                    QueuedDealMatch.status == "waiting",
                )
                .order_by(QueuedDealMatch.queued_at.asc())
                .limit(1)
            )
            match = oldest.scalar_one_or_none()
            if not match:
                continue

            # Re-validate: check the deal still exists and buyer hard filters
            deal = await db.get(Deal, match.deal_id)
            if not deal:
                match.status = "invalidated"
                db.add(match)
                logger.info("Queued match %s invalidated: deal no longer exists", match.id)
                continue

            # Re-check hard filters against current buyer data
            from app.models.schemas import Buyer
            buyer = await db.get(Buyer, buyer_id)
            if not buyer or buyer.status != "Active" or buyer.buy_box_embedding is None:
                match.status = "invalidated"
                db.add(match)
                logger.info("Queued match %s invalidated: buyer no longer active", match.id)
                continue

            # Re-check price range
            if buyer.price_min is not None and float(deal.asking_price) < float(buyer.price_min):
                match.status = "invalidated"
                db.add(match)
                logger.info("Queued match %s invalidated: deal price below buyer's min", match.id)
                continue
            if buyer.price_max is not None and float(deal.asking_price) > float(buyer.price_max):
                match.status = "invalidated"
                db.add(match)
                logger.info("Queued match %s invalidated: deal price above buyer's max", match.id)
                continue

            # Re-check property type
            if buyer.pref_property_type is not None and deal.property_type != buyer.pref_property_type:
                match.status = "invalidated"
                db.add(match)
                logger.info("Queued match %s invalidated: wrong property type", match.id)
                continue

            # Re-check geography
            if buyer.pref_cities is not None and deal.city is not None:
                if deal.city not in buyer.pref_cities:
                    match.status = "invalidated"
                    db.add(match)
                    logger.info("Queued match %s invalidated: city not in preferences", match.id)
                    continue

            # All checks passed — release
            match.status = "released"
            match.released_at = now
            db.add(match)
            released_count += 1
            logger.info(
                "Queued match %s released: buyer %s now has %d active deals, "
                "match for deal %s is valid",
                match.id, buyer_id, active_count, match.deal_id,
            )

        except Exception as e:
            logger.warning(
                "Failed to process queued match for buyer %s: %s",
                buyer_id, e, exc_info=True,
            )
            continue

    if released_count > 0:
        await db.commit()
        logger.info("Released %d queued matches", released_count)

    return released_count


async def invalidate_queued_matches_for_buyer(db: AsyncSession, buyer_id: UUID) -> int:
    """Invalidate all 'waiting' queued matches for a buyer whose buy_box
    or structured preferences have changed.

    Called when a buyer's buy_box is updated (via reply classification
    or manual edit). Ensures stale matches don't fire later.

    NOTE: This function does NOT commit — the caller (endpoint or scheduler)
    is responsible for committing the transaction.

    Args:
        db: Database session.
        buyer_id: Buyer UUID.

    Returns:
        Number of queued matches invalidated.
    """
    result = await db.execute(
        select(QueuedDealMatch).where(
            QueuedDealMatch.buyer_id == buyer_id,
            QueuedDealMatch.status == "waiting",
        )
    )
    matches = result.scalars().all()

    count = 0
    for match in matches:
        match.status = "invalidated"
        db.add(match)
        count += 1

    if count > 0:
        logger.info(
            "Invalidated %d queued matches for buyer %s (buy_box changed)",
            count, buyer_id,
        )

    return count
