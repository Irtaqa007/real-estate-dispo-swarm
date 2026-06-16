"""JV Partner Rotation service.

Before launching a campaign, checks if the current JV partner has excessive
overprice flags and suggests a more reliable alternative if available.
"""

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import JVPartner

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OVERRICE_FLAG_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Rotation check
# ---------------------------------------------------------------------------


async def find_alternative_jv(
    db: AsyncSession,
    current_jv_id: str,
    exclude_ids: Optional[List[str]] = None,
) -> Optional[JVPartner]:
    """Find the most reliable alternative JV partner.

    Searches for JV partners with the lowest overprice flag count
    as a proxy for reliability.

    Args:
        db: Database session.
        current_jv_id: The current JV partner ID to exclude.
        exclude_ids: Additional IDs to exclude.

    Returns:
        The most reliable JV partner, or None if none found.
    """
    exclude = [current_jv_id]
    if exclude_ids:
        exclude.extend(exclude_ids)

    result = await db.execute(
        select(JVPartner)
        .where(JVPartner.id.notin_(exclude))
        .order_by(JVPartner.overprice_flag_count.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def check_jv_rotation(
    db: AsyncSession,
    jv_partner: JVPartner,
) -> Dict:
    """Check if JV rotation should be suggested for a partner.

    If the JV partner has >= 3 overprice flags, suggests switching
    to a more reliable alternative. If no alternative exists,
    flags as high-risk and requires manual confirmation.

    Args:
        db: Database session.
        jv_partner: The current JV partner.

    Returns:
        Dict with keys:
            needs_rotation (bool): Whether rotation is recommended.
            warning (str): Description of the issue.
            alternative (dict or None): Alternative JV partner info.
            requires_manual_confirm (bool): Whether user confirmation is needed.
    """
    overprice_count = jv_partner.overprice_flag_count or 0

    if overprice_count < OVERRICE_FLAG_THRESHOLD:
        return {
            "needs_rotation": False,
            "warning": "",
            "alternative": None,
            "requires_manual_confirm": False,
        }

    logger.info(
        "JV rotation check: %s (%s) has %d overprice flags (threshold: %d)",
        jv_partner.name, jv_partner.id, overprice_count, OVERRICE_FLAG_THRESHOLD,
    )

    # Find alternative
    alternative = await find_alternative_jv(db, str(jv_partner.id))

    if alternative:
        warning = (
            f"JV partner {jv_partner.name} has {overprice_count} overprice flags. "
            f"Consider switching to {alternative.name} "
            f"({alternative.overprice_flag_count} overprice flags)."
        )
        logger.info("JV rotation: found alternative %s for %s", alternative.name, jv_partner.name)

        return {
            "needs_rotation": True,
            "warning": warning,
            "alternative": {
                "id": str(alternative.id),
                "name": alternative.name,
                "email": alternative.email,
                "overprice_flag_count": alternative.overprice_flag_count,
                "title_issue_rate": alternative.title_issue_rate or 0,
                "total_deals_closed": alternative.total_deals_closed or 0,
            },
            "requires_manual_confirm": False,
        }
    else:
        # No alternative — require manual confirmation
        warning = (
            f"JV partner {jv_partner.name} has {overprice_count} overprice flags "
            f"and no alternative JV partner is available. "
            f"Manual confirmation required to proceed with this partner."
        )
        logger.warning("JV rotation: no alternative found for %s", jv_partner.name)

        return {
            "needs_rotation": True,
            "warning": warning,
            "alternative": None,
            "requires_manual_confirm": True,
        }


async def get_jv_reliability_score(jv_partner: JVPartner) -> float:
    """Calculate a reliability score for a JV partner (0-100).

    Factors:
    - overprice_flag_count: higher = lower score
    - title_issue_rate: higher = lower score
    - total_deals_closed: higher = higher score (experience bonus)

    Args:
        jv_partner: The JV partner to score.

    Returns:
        Reliability score (0-100).
    """
    overprice_count = jv_partner.overprice_flag_count or 0
    title_rate = jv_partner.title_issue_rate or 0
    deals_closed = jv_partner.total_deals_closed or 0

    # Base score starts at 100
    score = 100.0

    # Deduct for overprice flags (10 points each, max 40 deduction)
    score -= min(overprice_count * 10, 40)

    # Deduct for title issues (50 points * rate, max 40 deduction)
    score -= min(title_rate * 50, 40)

    # Experience bonus (2 points per closed deal, max 10 bonus)
    score += min(deals_closed * 2, 10)

    return max(0, min(100, score))
