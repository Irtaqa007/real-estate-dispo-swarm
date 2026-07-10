"""Auto-stop triggers — pause campaigns when deal conditions change (closed, expired, fell through)."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

import app.database as _db
from app.models.models import ActivityLog, Campaign, Deal

logger = logging.getLogger(__name__)


async def check_deal_auto_stops() -> int:
    """Check all active deals for auto-stop conditions and pause campaigns.

    Three triggers:
    a) Deal Closed/Paid: if deal.status == 'Closed' or deal.payment_confirmed == True
       → pause all Queued campaigns
    b) Expired: if deal.expiry_date IS NOT NULL and deal.expiry_date < now()
       → set deal.status = 'Expired', pause all Queued campaigns
    c) Deal Fell Through: if deal.status == 'Deal_Fell_Through'
       → pause all Queued campaigns

    Returns:
        Number of deals affected.
    """
    async with _db.async_session_factory() as db:
        try:
            now = datetime.now(timezone.utc)
            affected_count = 0

            # Find all deals with active campaigns (Queued status)
            result = await db.execute(
                select(Deal).where(
                    Deal.status.in_([
                        "Available", "Campaign Launched", "Under Contract",
                    ])
                )
            )
            active_deals = result.scalars().all()

            for deal in active_deals:
                try:
                    triggered = False
                    action = None
                    reason = None

                    # a) Closed or payment confirmed
                    if deal.status == "Closed" or deal.payment_confirmed:
                        triggered = True
                        action = "auto_paused_deal_closed"
                        reason = "deal closed or payment confirmed"

                    # b) Expiry date passed
                    elif deal.expiry_date and deal.expiry_date < now:
                        triggered = True
                        action = "auto_paused_deal_expired"
                        reason = "deal expired"
                        deal.status = "Expired"
                        db.add(deal)

                    # c) Deal Fell Through / Dead
                    elif deal.status in ("Dead", "Deal_Fell_Through"):
                        triggered = True
                        action = "auto_paused_deal_fell_through"
                        reason = "deal fell through or marked dead"

                    if not triggered:
                        continue

                    # Pause all Queued campaigns for this deal
                    camp_result = await db.execute(
                        select(Campaign).where(
                            Campaign.deal_id == deal.id,
                            Campaign.status == "Queued",
                        )
                    )
                    queued_camps = camp_result.scalars().all()

                    paused_count = 0
                    for c in queued_camps:
                        c.status = "Paused"
                        db.add(c)
                        paused_count += 1

                    if paused_count == 0:
                        continue

                    # Log to activity_log
                    log_entry = ActivityLog(
                        id=uuid.uuid4(),
                        entity_type="deal",
                        entity_id=deal.id,
                        action=action,
                        metadata_json={
                            "paused_count": paused_count,
                            "reason": reason,
                            "deal_address": deal.address,
                            "deal_status": deal.status,
                            "expiry_date": deal.expiry_date.isoformat() if deal.expiry_date else None,
                        },
                    )
                    db.add(log_entry)

                    await db.commit()
                    affected_count += 1

                    logger.info(
                        "Auto-stop triggered for deal %s (%s): %s — paused %d campaigns",
                        deal.id, deal.address, action, paused_count,
                    )

                except Exception as e:
                    logger.error(
                        "Auto-stop check failed for deal %s: %s",
                        deal.id, e, exc_info=True,
                    )
                    await db.rollback()
                    continue

            if affected_count:
                logger.info(
                    "Auto-stop check complete: %d deal(s) affected",
                    affected_count,
                )
            return affected_count

        except Exception as e:
            logger.error("Auto-stop check failed: %s", e, exc_info=True)
            await db.rollback()
            return 0
