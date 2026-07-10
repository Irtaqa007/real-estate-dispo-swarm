"""Campaign sender — finds queued campaigns past their scheduled time and sends them."""

import logging
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

import app.database as _db
from app.models.models import ActivityLog, Buyer, Campaign, Deal
from app.services.gmail_service import send_email
from app.services.ai_validator import ValidationResult, validate_ai_output
from app.services.matching_service import process_queued_matches

logger = logging.getLogger(__name__)


async def process_scheduled_campaigns() -> int:
    """Find and send all queued campaigns whose scheduled_send_at has passed.

    For each eligible campaign:
    1. Verifies the previous touch for the same buyer+deal has been sent
    2. Verifies no touch for that buyer+deal has been replied (pause rule)
    3. Verifies the deal status is still "Available" or "Campaign Launched"
    4. Sends the email via Gmail SMTP
    5. Updates campaign status to "Sent"

    Returns:
        Number of campaigns successfully sent.
    """
    async with _db.async_session_factory() as db:
        try:
            now = datetime.now(timezone.utc)

            # 1. Find all queued campaigns past their scheduled time
            result = await db.execute(
                select(Campaign).where(
                    Campaign.status == "Queued",
                    Campaign.scheduled_send_at <= now,
                    Campaign.scheduled_send_at.isnot(None),
                    Campaign.sent_at.is_(None),  # Never resend if already sent
                )
            )
            queued_campaigns = result.scalars().all()

            if not queued_campaigns:
                logger.debug("Scheduler: no queued campaigns ready to send")
                return 0

            logger.info(
                "Scheduler: found %d queued campaigns ready to process",
                len(queued_campaigns),
            )

            # ── Batch-load Deals and Buyers to avoid N+1 queries ──
            unique_deal_ids = list({c.deal_id for c in queued_campaigns})
            unique_buyer_ids = list({c.buyer_id for c in queued_campaigns})

            deals_map: dict[uuid.UUID, Deal] = {}
            if unique_deal_ids:
                deal_rows = await db.execute(
                    select(Deal).where(Deal.id.in_(unique_deal_ids))
                )
                for d in deal_rows.scalars().all():
                    deals_map[d.id] = d

            buyers_map: dict[uuid.UUID, Buyer] = {}
            if unique_buyer_ids:
                buyer_rows = await db.execute(
                    select(Buyer).where(Buyer.id.in_(unique_buyer_ids))
                )
                for b in buyer_rows.scalars().all():
                    buyers_map[b.id] = b

            sent_count = 0
            deal_resolved_buyer_ids: set[uuid.UUID] = set()

            for campaign in queued_campaigns:
                try:
                    # 2. Check pause rule: has the buyer replied to any touch for this deal?
                    replied_result = await db.execute(
                        select(Campaign).where(
                            Campaign.buyer_id == campaign.buyer_id,
                            Campaign.deal_id == campaign.deal_id,
                            Campaign.status == "Replied",
                        )
                    )
                    if replied_result.first():
                        campaign.status = "Paused"
                        db.add(campaign)
                        logger.info(
                            "Scheduler: paused campaign %s (touch %d) — buyer already replied",
                            campaign.id, campaign.touch_number,
                        )
                        continue

                    # 3. Check deal status (from pre-loaded map)
                    deal = deals_map.get(campaign.deal_id)
                    if not deal:
                        logger.warning(
                            "Scheduler: deal %s not found for campaign %s",
                            campaign.deal_id, campaign.id,
                        )
                        campaign.status = "Failed"
                        db.add(campaign)
                        continue

                    if deal.status in ("Under Contract", "Sold", "Dead"):
                        campaign.status = "Paused"
                        db.add(campaign)
                        logger.info(
                            "Scheduler: paused campaign %s (touch %d) — deal status is '%s'",
                            campaign.id, campaign.touch_number, deal.status,
                        )
                        deal_resolved_buyer_ids.add(campaign.buyer_id)
                        continue

                    # 4. Check previous touch was sent (skip for touch 1)
                    if campaign.touch_number > 1:
                        prev_result = await db.execute(
                            select(Campaign).where(
                                Campaign.buyer_id == campaign.buyer_id,
                                Campaign.deal_id == campaign.deal_id,
                                Campaign.touch_number == campaign.touch_number - 1,
                            )
                        )
                        prev_campaign = prev_result.scalar_one_or_none()

                        if not prev_campaign or prev_campaign.status not in ("Sent", "Replied"):
                            logger.debug(
                                "Scheduler: skipping campaign %s (touch %d) — "
                                "previous touch not yet sent (status: %s)",
                                campaign.id, campaign.touch_number,
                                prev_campaign.status if prev_campaign else "N/A",
                            )
                            continue

                    # 5. Fetch buyer email (from pre-loaded map)
                    buyer = buyers_map.get(campaign.buyer_id)
                    if not buyer or not buyer.email:
                        logger.warning(
                            "Scheduler: buyer not found or no email for campaign %s",
                            campaign.id,
                        )
                        campaign.status = "Failed"
                        db.add(campaign)
                        continue

                    # 6. Send the email
                    if not campaign.subject or not campaign.body:
                        logger.warning(
                            "Scheduler: campaign %s has no subject or body, marking as Failed",
                            campaign.id,
                        )
                        campaign.status = "Failed"
                        db.add(campaign)
                        continue

                    # AI Validation pre-send guard
                    try:
                        validation = await validate_ai_output(
                            content=campaign.body,
                            content_type="campaign_email",
                            deal=deal,
                            buyer=buyer,
                        )
                    except Exception as val_err:
                        logger.error(
                            "AI validator failed for campaign %s, proceeding with unvalidated send: %s",
                            campaign.id, val_err,
                        )
                        validation = ValidationResult(severity="pass", corrected_content=None, violations=[], checks_run=[])

                    if validation.severity == "block":
                        logger.error(
                            "Scheduler: campaign %s (touch %d) blocked by validator: %s",
                            campaign.id, campaign.touch_number, validation.violations,
                        )
                        campaign.status = "Failed"
                        db.add(campaign)
                        continue

                    body_to_send = validation.corrected_content or campaign.body

                    # Fix subject spread at send time
                    subject_to_send = campaign.subject or ""
                    if deal.repair_estimate and deal.asking_price and deal.arv:
                        try:
                            rehab = float(deal.repair_estimate)
                            asking = float(deal.asking_price)
                            arv = float(deal.arv)
                            if rehab > 0:
                                correct_profit = arv - asking - rehab
                                wrong_spread = arv - asking
                                wrong_k = f"${wrong_spread//1000:.0f}k"
                                correct_k = f"${correct_profit//1000:.0f}k"
                                wrong_full = f"${wrong_spread:,.0f}"
                                correct_full = f"${correct_profit:,.0f}"
                                subject_to_send = (subject_to_send
                                    .replace(wrong_full, correct_full)
                                    .replace(wrong_k, correct_k))
                        except Exception as e:
                            logger.warning("Subject spread correction failed for campaign %s: %s", campaign.id, e)

                    result = await send_email(
                        to=buyer.email,
                        subject=subject_to_send,
                        body=body_to_send,
                        campaign_id=campaign.id.hex,
                        send_type="campaign",
                    )
                    if result.get("status") == "deferred_cap":
                        logger.info(
                            "Scheduler: campaign %s (touch %d) deferred — daily cap reached",
                            campaign.id, campaign.touch_number,
                        )
                        continue

                    campaign.status = "Sent"
                    campaign.sent_at = datetime.now(timezone.utc)
                    db.add(campaign)
                    sent_count += 1

                    logger.info(
                        "Scheduler: sent campaign %s (touch %d) to %s",
                        campaign.id, campaign.touch_number, buyer.email,
                    )

                except Exception as e:
                    logger.error(
                        "Scheduler: failed to process campaign %s: %s",
                        campaign.id, e, exc_info=True,
                    )
                    try:
                        campaign.status = "Failed"
                        db.add(campaign)
                    except Exception as e:
                        logger.warning("Failed to mark campaign %s as Failed after processing error: %s", campaign.id, e)

            await db.commit()

            for bid in deal_resolved_buyer_ids:
                try:
                    async with _db.async_session_factory() as release_db:
                        released = await process_queued_matches(release_db, buyer_id=bid)
                        if released > 0:
                            logger.info(
                                "Released %d queued matches for buyer %s (campaign paused due to deal resolution)",
                                released, bid,
                            )
                            await release_db.commit()
                except Exception as release_err:
                    logger.warning(
                        "Failed to process queued matches for buyer %s after campaign pause: %s",
                        bid, release_err, exc_info=True,
                    )

            logger.info("Scheduler: completed run — %d campaigns sent", sent_count)
            return sent_count

        except Exception as e:
            logger.error("Scheduler: error processing campaigns: %s", e, exc_info=True)
            await db.rollback()
            return 0
