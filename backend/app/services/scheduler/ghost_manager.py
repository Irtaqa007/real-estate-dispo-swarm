"""Ghost detection and recovery — identifies buyers who went silent after replying and sends re-engagement touches."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import app.database as _db
from app.config import settings
from app.models.models import ActivityLog, Buyer, Campaign, Deal
from app.services.ghost_recovery import generate_ghost_recovery_email
from app.services.gmail_service import send_email

logger = logging.getLogger(__name__)


async def detect_and_flag_ghosts() -> int:
    """Detect buyers who replied at least once then went silent for 96+ hours.

    A ghost is a buyer who:
    1. Replied to at least one campaign touch (Campaign.status == "Replied")
    2. Then went silent — no reply for ghost_silence_hours after the last outbound
       email we sent them on this deal
    3. Has NOT passed, unsubscribed, or closed on this deal
    4. The deal is still active (Available or Campaign Launched)

    Non-responders are NOT ghosts — they are handled by the existing 6-touch sequence.

    Returns:
        Number of ghosts detected and flagged.
    """
    async with _db.async_session_factory() as db:
        try:
            now = datetime.now(timezone.utc)
            silence_cutoff = now - timedelta(hours=settings.ghost_silence_hours)

            # Find all distinct (buyer_id, deal_id) pairs with at least one Replied campaign
            # and no existing ghost detection
            replied_pairs = await db.execute(
                select(Campaign.buyer_id, Campaign.deal_id)
                .where(
                    Campaign.status == "Replied",
                    Campaign.ghost_detected_at.is_(None),
                    Campaign.ghost_recovery_touch == 0,
                )
                .distinct()
            )
            candidate_pairs = replied_pairs.all()

            if not candidate_pairs:
                return 0

            ghosts_detected = 0

            for buyer_id, deal_id in candidate_pairs:
                try:
                    # Check deal is still active
                    deal = await db.get(Deal, deal_id)
                    if not deal or deal.status not in ("Available", "Campaign Launched"):
                        continue

                    # Check buyer hasn't unsubscribed
                    buyer_check = await db.get(Buyer, buyer_id)
                    if not buyer_check or buyer_check.unsubscribed_at:
                        continue  # Do not ghost-detect unsubscribed buyers

                    # Check the buyer hasn't passed or unsubscribed on this deal
                    terminal_statuses = await db.execute(
                        select(Campaign).where(
                            Campaign.buyer_id == buyer_id,
                            Campaign.deal_id == deal_id,
                            Campaign.status.in_(["Passed", "Failed"]),
                        ).limit(1)
                    )
                    if terminal_statuses.first():
                        continue

                    # Find the latest outbound (Sent/Replied) campaign for this buyer+deal
                    last_outbound = await db.scalar(
                        select(Campaign)
                        .where(
                            Campaign.buyer_id == buyer_id,
                            Campaign.deal_id == deal_id,
                            Campaign.status.in_(["Sent", "Replied"]),
                            Campaign.sent_at.isnot(None),
                        )
                        .order_by(Campaign.sent_at.desc())
                    )

                    if not last_outbound:
                        continue

                    # Check silence condition: last sent email was more than ghost_silence_hours ago
                    if last_outbound.sent_at > silence_cutoff:
                        continue  # Not silent long enough yet

                    # Check the most recent reply on this buyer+deal
                    last_reply = await db.scalar(
                        select(Campaign)
                        .where(
                            Campaign.buyer_id == buyer_id,
                            Campaign.deal_id == deal_id,
                            Campaign.reply_received_at.isnot(None),
                        )
                        .order_by(Campaign.reply_received_at.desc())
                    )

                    # If there's a reply after the last sent email, the buyer is still active
                    if last_reply and last_reply.reply_received_at:
                        if last_reply.reply_received_at > last_outbound.sent_at:
                            continue

                    # ── This buyer+deal pair is a ghost ──
                    # Set ghost_detected_at on the most recent Replied campaign row
                    replied_campaign = await db.scalar(
                        select(Campaign)
                        .where(
                            Campaign.buyer_id == buyer_id,
                            Campaign.deal_id == deal_id,
                            Campaign.status == "Replied",
                        )
                        .order_by(Campaign.reply_received_at.desc().nullslast())
                    )

                    if not replied_campaign:
                        continue

                    replied_campaign.ghost_detected_at = now
                    replied_campaign.ghost_recovery_touch = 0
                    db.add(replied_campaign)

                    # Log to activity_log
                    hours_silent = (now - last_outbound.sent_at).total_seconds() / 3600
                    log_entry = ActivityLog(
                        id=uuid.uuid4(),
                        entity_type="campaign",
                        entity_id=replied_campaign.id,
                        action="ghost_detected",
                        metadata_json={
                            "buyer_id": str(buyer_id),
                            "deal_id": str(deal_id),
                            "last_reply_at": last_reply.reply_received_at.isoformat() if last_reply and last_reply.reply_received_at else None,
                            "hours_silent": round(hours_silent, 1),
                            "alert_user": False,
                        },
                    )
                    db.add(log_entry)

                    await db.commit()

                    logger.info(
                        "Ghost detected: buyer %s on deal %s (last reply: %s, silence: %.1f hours)",
                        buyer_id, deal_id,
                        last_reply.reply_received_at.isoformat() if last_reply and last_reply.reply_received_at else "unknown",
                        hours_silent,
                    )

                    ghosts_detected += 1

                except Exception as e:
                    logger.error(
                        "Failed to check ghost candidate buyer %s, deal %s: %s",
                        buyer_id, deal_id, e, exc_info=True,
                    )
                    await db.rollback()
                    continue

            if ghosts_detected:
                logger.info("Ghost detection complete: %d ghost(s) flagged", ghosts_detected)
            return ghosts_detected

        except Exception as e:
            logger.error("Ghost detection failed: %s", e, exc_info=True)
            await db.rollback()
            return 0


async def send_ghost_recovery_emails() -> int:
    """Send ghost recovery emails to buyers in ghost recovery.

    For each due recovery touch:
    1. Load full thread context for this buyer+deal
    2. Generate AI recovery email anchored to the conversation
    3. Send via send_email() with send_type="reply"
    4. On success: increment ghost_recovery_touch, set ghost_recovery_sent_at
    5. After all 5 touches sent with no reply: mark as "Dormant"

    Returns:
        Number of recovery emails sent.
    """
    async with _db.async_session_factory() as db:
        try:
            now = datetime.now(timezone.utc)

            # Find all Campaign rows in ghost recovery that are due for a touch
            ghosts = await db.execute(
                select(Campaign)
                .where(
                    Campaign.ghost_detected_at.isnot(None),
                    Campaign.ghost_recovery_touch < settings.ghost_max_recovery_touches,
                    Campaign.status.notin_(["Passed", "Failed", "Paused"]),
                )
                .order_by(Campaign.ghost_recovery_sent_at.asc().nullsfirst())
            )
            ghost_campaigns = ghosts.scalars().all()

            if not ghost_campaigns:
                return 0

            sent_count = 0
            processed_ids = set()

            for campaign in ghost_campaigns:
                # Skip if we already processed this buyer+deal pair (only one recovery at a time)
                pair_key = (campaign.buyer_id, campaign.deal_id)
                if pair_key in processed_ids:
                    continue

                try:
                    # Check deal is still active
                    deal = await db.get(Deal, campaign.deal_id)
                    if not deal or deal.status not in ("Available", "Campaign Launched"):
                        continue

                    # Check buyer hasn't unsubscribed
                    buyer = await db.get(Buyer, campaign.buyer_id)
                    if not buyer or not buyer.email or buyer.unsubscribed_at:
                        continue
                    if buyer.status == "Do Not Contact":
                        continue

                    # Check if this recovery touch is due
                    next_touch_index = campaign.ghost_recovery_touch
                    if next_touch_index >= len(settings.ghost_recovery_intervals_days):
                        continue

                    days_to_wait = settings.ghost_recovery_intervals_days[next_touch_index]
                    touch_due_at = campaign.ghost_detected_at + timedelta(days=days_to_wait)

                    if now < touch_due_at:
                        continue  # Not time yet

                    # Load full thread context for this buyer+deal
                    thread_result = await db.execute(
                        select(Campaign)
                        .where(
                            Campaign.buyer_id == campaign.buyer_id,
                            Campaign.deal_id == campaign.deal_id,
                        )
                        .order_by(Campaign.sent_at.asc().nullslast())
                    )
                    thread_campaigns = thread_result.scalars().all()

                    next_touch_number = campaign.ghost_recovery_touch + 1

                    # Generate recovery email
                    email_data = await generate_ghost_recovery_email(
                        buyer=buyer,
                        deal=deal,
                        touch_number=next_touch_number,
                        thread_context=thread_campaigns,
                    )

                    # ── Check validation result from ghost_recovery.py ──
                    if email_data.get("validation_blocked"):
                        logger.error(
                            "Ghost recovery email blocked by AI validator for buyer %s, deal %s: %s",
                            campaign.buyer_id, campaign.deal_id,
                            email_data.get("validation_violations", "unknown"),
                        )
                        continue

                    body_to_send = email_data["body"]

                    # Send via send_email with send_type="reply" (never blocked by daily cap)
                    result = await send_email(
                        to=buyer.email,
                        subject=email_data["subject"],
                        body=body_to_send,
                        campaign_id=campaign.id.hex,
                        send_type="reply",
                    )

                    if result.get("status") == "sent":
                        # Update recovery state on the ghost-detected campaign row
                        campaign.ghost_recovery_touch = next_touch_number
                        campaign.ghost_recovery_sent_at = now
                        db.add(campaign)

                        # If all 5 touches sent, mark as Dormant
                        if next_touch_number >= settings.ghost_max_recovery_touches:
                            dormant_result = await db.execute(
                                select(Campaign).where(
                                    Campaign.buyer_id == campaign.buyer_id,
                                    Campaign.deal_id == campaign.deal_id,
                                )
                            )
                            all_campaigns = dormant_result.scalars().all()
                            for c in all_campaigns:
                                c.status = "Dormant"
                                db.add(c)

                            log_entry = ActivityLog(
                                id=uuid.uuid4(),
                                entity_type="campaign",
                                entity_id=campaign.id,
                                action="buyer_dormant",
                                metadata_json={
                                    "buyer_id": str(campaign.buyer_id),
                                    "deal_id": str(campaign.deal_id),
                                    "reason": "5 ghost recovery touches sent with no response",
                                    "alert_user": False,
                                },
                            )
                            db.add(log_entry)

                            logger.info(
                                "Buyer %s marked dormant on deal %s after 5 ghost recovery touches with no response",
                                campaign.buyer_id, campaign.deal_id,
                            )

                        await db.commit()

                        processed_ids.add(pair_key)
                        sent_count += 1

                        logger.info(
                            "Ghost recovery touch %d sent to buyer %s on deal %s",
                            next_touch_number, campaign.buyer_id, campaign.deal_id,
                        )
                    else:
                        logger.warning(
                            "Ghost recovery send failed for buyer %s, deal %s: %s",
                            campaign.buyer_id, campaign.deal_id, result.get("status", "unknown"),
                        )

                except Exception as e:
                    logger.error(
                        "Failed to send ghost recovery for buyer %s, deal %s: %s",
                        campaign.buyer_id, campaign.deal_id, e, exc_info=True,
                    )
                    await db.rollback()
                    continue

            if sent_count:
                logger.info("Ghost recovery: %d recovery email(s) sent", sent_count)
            return sent_count

        except Exception as e:
            logger.error("Ghost recovery send failed: %s", e, exc_info=True)
            await db.rollback()
            return 0
