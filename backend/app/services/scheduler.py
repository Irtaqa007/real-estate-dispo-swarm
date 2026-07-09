"""Background task scheduler for automated 24/7 operations.

Maintains two independent intervals:
- Reply interval (5 min): time-sensitive tasks (reply processing, ghost detection, campaign sends)
- Hourly interval (60 min): daily/maintenance tasks (auto-match, insights, aging, etc.)

The loop ticks every 60 seconds and dispatches tasks based on elapsed time
since each interval's last run. Each task is individually wrapped in
try/except so one failure never crashes the entire scheduler.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import app.database as _db
from app.config import settings
from app.models.models import ActivityLog, Buyer, Campaign, Deal, FailedCampaign, JVPartner
from app.services.dead_letter_queue import retry_failed_campaign
from app.services.gmail_monitor import check_for_replies
from app.services.gmail_service import send_email
from app.services.groq_client import get_rate_limit_status
from app.services.title_coordinator import process_title_emails, run_title_chases, send_assignment_contract
from app.services.buyer_scoring import run_tier_promotions, reset_pitch_counters, calculate_and_update_engagement
from app.services.aging_monitor import run_aging_monitor
from app.services.buyer_insights import update_all_buyer_insights
from app.services.embeddings import generate_embedding
from app.services.conversation_engine import process_conversation
from app.services.reply_processor import process_reply, extract_buybox_changes, get_question_round_message, detect_uncertainty_and_hold, match_reply_to_campaign
from app.services.negotiation import handle_counter_offer
from app.services.audit_logger import audit
from app.services.state_persistence import (
    save_all_state,
    load_gmail_daily_sends,
    save_gmail_daily_sends,
    save_scheduler_heartbeat,
)
from app.services.ghost_recovery import generate_ghost_recovery_email
from app.services.ai_validator import ValidationResult, validate_ai_output
from app.services.email_generator import generate_touch_email
from app.models.models import BuyerReengagementSchedule
from app.services.circuit_breaker import gmail_circuit_breaker, get_cb_queue
from app.services.matching_service import process_queued_matches, invalidate_queued_matches_for_buyer, match_all_active_deals
from app.services.resilience import get_metrics, get_idempotency_store
from app.services.groq_client import get_call_count_today, get_calls_today_date
from app.services.buyer_merge import merge_buy_boxes
from app.models.models import BuyerEmail
from app.services.parse_buy_box import parse_buy_box

logger = logging.getLogger(__name__)

# Scheduler intervals
REPLY_INTERVAL_SECONDS = 30       # 5 minutes: time-sensitive tasks
DAILY_INTERVAL_SECONDS = 60 * 60      # 1 hour: daily/maintenance tasks
TICK_INTERVAL_SECONDS = 60            # Outer loop sleep (1 minute tick)

# ---------------------------------------------------------------------------
# Core scheduling logic
# ---------------------------------------------------------------------------


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

            sent_count = 0
            # FEATURE 2: Track buyers whose campaigns were paused due to
            # deal resolution, so we can trigger immediate queued match release.
            # Only track deal-status pauses (Under Contract/Sold/Dead) — the
            # "already replied" pause doesn't free a slot since the replied
            # campaign still counts as active.
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
                        # Buyer already replied — pause remaining campaigns
                        campaign.status = "Paused"
                        db.add(campaign)
                        logger.info(
                            "Scheduler: paused campaign %s (touch %d) — buyer already replied",
                            campaign.id, campaign.touch_number,
                        )
                        continue

                    # 3. Check deal status
                    deal = await db.get(Deal, campaign.deal_id)
                    if not deal:
                        logger.warning(
                            "Scheduler: deal %s not found for campaign %s",
                            campaign.deal_id, campaign.id,
                        )
                        campaign.status = "Failed"
                        db.add(campaign)
                        continue

                    if deal.status in ("Under Contract", "Sold", "Dead"):
                        # Deal is no longer active — pause all remaining touches
                        campaign.status = "Paused"
                        db.add(campaign)
                        logger.info(
                            "Scheduler: paused campaign %s (touch %d) — deal status is '%s'",
                            campaign.id, campaign.touch_number, deal.status,
                        )
                        # FEATURE 2: Slot freed because deal resolved — track for release
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
                            # Previous touch not yet sent — skip this run
                            logger.debug(
                                "Scheduler: skipping campaign %s (touch %d) — "
                                "previous touch not yet sent (status: %s)",
                                campaign.id, campaign.touch_number,
                                prev_campaign.status if prev_campaign else "N/A",
                            )
                            continue

                    # 5. Fetch buyer email
                    buyer = await db.get(Buyer, campaign.buyer_id)
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

                    # ── AI Validation pre-send guard ──
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

                    # Fix subject spread at send time (catches campaigns generated before fix)
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
                        except Exception:
                            pass

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

                    # 7. Update campaign status
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
                    # Mark as failed so we don't retry indefinitely
                    try:
                        campaign.status = "Failed"
                        db.add(campaign)
                    except Exception:
                        pass

            # Commit all changes
            await db.commit()

            # FEATURE 2: Event-driven queued match release
            # After campaigns are paused because a deal resolved (Under Contract,
            # Sold, Dead), trigger immediate release for affected buyers.
            for bid in deal_resolved_buyer_ids:
                try:
                    async with _db.async_session_factory() as release_db:
                        released = await process_queued_matches(
                            release_db, buyer_id=bid,
                        )
                        if released > 0:
                            logger.info(
                                "Released %d queued matches for buyer %s "
                                "(campaign paused due to deal resolution)",
                                released, bid,
                            )
                            await release_db.commit()
                except Exception as release_err:
                    logger.warning(
                        "Failed to process queued matches for buyer %s "
                        "after campaign pause: %s",
                        bid, release_err, exc_info=True,
                    )

            logger.info("Scheduler: completed run — %d campaigns sent", sent_count)
            return sent_count

        except Exception as e:
            logger.error("Scheduler: error processing campaigns: %s", e, exc_info=True)
            await db.rollback()
            return 0


async def process_buyer_replies() -> int:
    """Fetch buyer replies from Gmail and process them end-to-end.

    Replicates the full pipeline from the /api/campaigns/check-replies endpoint
    so replies are classified, saved, and acted upon even when the manual
    endpoint is not called.

    Pipeline per reply:
    1. Classify via Groq AI (intent, urgency, sentiment, topics)
    2. Match to the most recent Sent campaign for that buyer
    3. Update campaign with reply data + set status to "Replied"
    4. Update buyer's last_reply_at timestamp
    5. Buy Box Auto-Update: if buybox changed, extract new criteria via Groq,
       regenerate embedding, log changes
    6. Auto-pause remaining queued touches for that buyer+deal
    7. Log to activity_log
    8. Smart Negotiation: Counter offers auto-approved if >= floor price
    9. Auto-Follow-Up: Question replies get AI-drafted answers
    10. Auto-send assignment contract if buyer is Interested

    Returns:
        Number of replies fully processed.
    """
    async with _db.async_session_factory() as db:
        try:
            # 1. Fetch all buyer email addresses (primary + additional)
            buyer_result = await db.execute(
                select(Buyer.id, Buyer.email).where(Buyer.email.isnot(None))
            )
            all_buyers = buyer_result.all()
            buyer_emails = [b.email for b in all_buyers]
            buyer_map = {b.email.lower(): b.id for b in all_buyers}

            # Also index additional emails from buyer_emails table
            be_result = await db.execute(
                select(BuyerEmail.buyer_id, BuyerEmail.email)
            )
            for row in be_result.all():
                email_lower = row.email.lower()
                if email_lower not in buyer_map:
                    buyer_map[email_lower] = row.buyer_id
                    buyer_emails.append(row.email)

            if not buyer_emails:
                return 0

            # 2. Poll Gmail inbox for replies
            replies = await check_for_replies(buyer_emails)

            if not replies:
                logger.debug("Scheduler: no new buyer replies found in inbox")
                return 0

            logger.info("Scheduler: found %d buyer replies to process", len(replies))

            # 3. Process each reply with per-reply isolation
            # so one failure never loses the entire batch
            processed_count = 0
            # FEATURE 2: Track buyers who replied with Pass to trigger
            # immediate queued match release after commit
            passed_buyer_ids: list[uuid.UUID] = []

            for reply in replies:
                try:
                    from_email = reply["from_email"]
                    buyer_id = buyer_map.get(from_email.lower())

                    if not buyer_id:
                        logger.info("Scheduler: reply from unknown buyer %s — skipping", from_email)
                        continue

                    # 4. Deduplication: skip if this Gmail message was already processed
                    gmail_message_id = reply.get("message_id") or reply.get("id")
                    if gmail_message_id:
                        existing_log = await db.execute(
                            select(ActivityLog).where(
                                ActivityLog.action == "reply_received",
                                ActivityLog.metadata_json["gmail_message_id"].astext
                                    == gmail_message_id,
                            ).limit(1)
                        )
                        existing_entry = existing_log.scalar_one_or_none()
                        if existing_entry:
                            # Only skip if the campaign was actually fully processed
                            # (has reply_received_at set). If it was logged but rolled back,
                            # the campaign won't have reply_received_at — reprocess it.
                            _camp_check = await db.execute(
                                select(Campaign).where(
                                    Campaign.buyer_id == buyer_id,
                                    Campaign.status.in_(["Sent", "Replied", "Passed", "Contract_Pending"]),
                                    Campaign.reply_received_at.isnot(None),
                                ).limit(1)
                            )
                            if _camp_check.scalar_one_or_none():
                                logger.debug(
                                    "Scheduler: skipping already-processed reply %s",
                                    gmail_message_id,
                                )
                                continue
                            else:
                                logger.info(
                                    "Scheduler: reply %s was logged but campaign not updated — reprocessing",
                                    gmail_message_id,
                                )

                    # 5. Match reply to the correct campaign (thread-aware priority chain)
                    campaign, confidence_level = await match_reply_to_campaign(db, buyer_id, reply)

                    if not campaign:
                        logger.info(
                            "Scheduler: no sent campaign found for buyer %s — skipping",
                            from_email,
                        )
                        continue

                    logger.info(
                        "Scheduler: reply from buyer %s matched to campaign %s via %s (deal: %s)",
                        buyer_id, campaign.id, confidence_level, campaign.deal_id,
                    )

                    # 5. Load deal and buyer fresh to avoid expired ORM state
                    _deal_r = await db.execute(select(Deal).where(Deal.id == campaign.deal_id))
                    deal_obj = _deal_r.scalar_one_or_none()
                    _buyer_r = await db.execute(select(Buyer).where(Buyer.id == buyer_id))
                    buyer_obj = _buyer_r.scalar_one_or_none()

                    if not deal_obj or not buyer_obj:
                        logger.warning("Scheduler: deal or buyer not found for campaign %s", campaign.id)
                        continue

                    # Strip quoted thread from reply body before passing to AI
                    import re as _re
                    raw_body = reply.get("body", "")
                    # Remove everything after "On ... wrote:" pattern (quoted original)
                    clean_body = _re.split(r'\n\s*On .{10,100}wrote:\s*\n', raw_body)[0].strip()
                    # Also remove lines starting with > (quoted lines)
                    clean_body = "\n".join(
                        line for line in clean_body.splitlines()
                        if not line.strip().startswith(">")
                    ).strip()
                    if not clean_body:
                        clean_body = raw_body[:500]  # Fallback if stripping removed everything

                    # 5b. Build thread history for conversation engine
                    _thread_r = await db.execute(
                        select(Campaign).where(
                            Campaign.buyer_id == buyer_id,
                            Campaign.deal_id == campaign.deal_id,
                        ).order_by(Campaign.sent_at.asc().nullslast())
                    )
                    thread_campaigns = _thread_r.scalars().all()
                    thread_history = []
                    for tc in thread_campaigns:
                        if tc.body:
                            thread_history.append({"role": "assistant", "content": tc.body[:600]})
                        if tc.reply_body:
                            thread_history.append({"role": "user", "content": tc.reply_body[:600]})

                    # 5c. Run conversation engine
                    logger.info(
                        "Scheduler: calling conversation engine | stage=%s clean_body=%.80s",
                        campaign.conversation_stage or "pitching", clean_body,
                    )
                    conv_result = await process_conversation(
                        reply_body=clean_body,
                        reply_subject=reply.get("subject", ""),
                        buyer=buyer_obj,
                        deal=deal_obj,
                        campaign=campaign,
                        thread_history=thread_history,
                    )

                    now = datetime.now(timezone.utc)
                    new_stage = conv_result["new_stage"]
                    next_message = conv_result.get("next_message")
                    extracted = conv_result.get("extracted_info", {})

                    # 6. Update campaign with reply + new stage
                    campaign.reply_received_at = now
                    campaign.reply_body = raw_body
                    campaign.reply_intent = new_stage
                    campaign.conversation_stage = new_stage
                    campaign.ai_extracted_insights = str(conv_result.get("notes", "") or conv_result.get("ai_extracted_insights", ""))[:500]

                    # Store extracted contract info
                    if extracted.get("legal_name"):
                        campaign.buyer_legal_name = extracted["legal_name"]
                    if extracted.get("phone"):
                        campaign.buyer_phone = extracted["phone"]
                    if extracted.get("title_company"):
                        campaign.buyer_title_company = extracted["title_company"]
                    if extracted.get("agreed_price"):
                        try:
                            campaign.agreed_price = float(str(extracted["agreed_price"]).replace(",","").replace("$",""))
                        except Exception:
                            pass

                    # Handle terminal states
                    if conv_result["pass_detected"] or conv_result["unsubscribe_detected"]:
                        campaign.status = "Passed"
                        passed_buyer_ids.append(buyer_id)
                        if conv_result["unsubscribe_detected"]:
                            buyer_obj.unsubscribed_at = now
                            buyer_obj.status = "Do Not Contact"
                            _q = await db.execute(
                                select(Campaign).where(
                                    Campaign.buyer_id == buyer_id,
                                    Campaign.status == "Queued",
                                )
                            )
                            for qc in _q.scalars().all():
                                qc.status = "Paused"
                                db.add(qc)
                    elif conv_result["contract_ready"]:
                        campaign.status = "Contract_Pending"
                        jv = await db.get(JVPartner, deal_obj.jv_partner_id) if deal_obj.jv_partner_id else None
                        asking = float(deal_obj.asking_price)
                        contract_p = float(deal_obj.contract_price)
                        split_pct = float(deal_obj.jv_split_percentage or 50) / 100
                        assignment_fee = asking - contract_p
                        my_payout = assignment_fee * (1.0 - split_pct)
                        db.add(ActivityLog(
                            id=uuid.uuid4(),
                            entity_type="deal",
                            entity_id=campaign.deal_id,
                            action="contract_ready",
                            metadata_json={
                                "alert_type": "contract_ready",
                                "alert_user": True,
                                "priority": "high",
                                "buyer": {
                                    "name": buyer_obj.full_name,
                                    "email": buyer_obj.email,
                                    "legal_name": campaign.buyer_legal_name,
                                    "phone": campaign.buyer_phone,
                                    "title_company": campaign.buyer_title_company,
                                },
                                "deal": {
                                    "address": deal_obj.address,
                                    "asking_price": asking,
                                    "agreed_price": float(campaign.agreed_price) if campaign.agreed_price else asking,
                                    "assignment_fee": assignment_fee,
                                    "my_payout": my_payout,
                                    "jv_partner": jv.name if jv else "",
                                },
                            },
                        ))
                        # Pause remaining touches
                        _queued = await db.execute(
                            select(Campaign).where(
                                Campaign.buyer_id == buyer_id,
                                Campaign.deal_id == campaign.deal_id,
                                Campaign.status == "Queued",
                            )
                        )
                        for qc in _queued.scalars().all():
                            qc.status = "Paused"
                            db.add(qc)
                    else:
                        campaign.status = "Replied"

                    db.add(campaign)
                    buyer_obj.last_reply_at = now
                    db.add(buyer_obj)

                    # 7. Send conversation engine reply
                    logger.info(
                        "Scheduler: next_message preview: %s",
                        (next_message or "")[:100]
                    )
                    if next_message and not conv_result["pass_detected"]:
                        try:
                            await send_email(
                                to=buyer_obj.email,
                                subject=f"Re: {reply.get('subject', '')}",
                                body=next_message,
                                send_type="reply",
                            )
                            logger.info(
                                "Scheduler: conversation reply sent to buyer %s (stage: %s)",
                                buyer_id, new_stage,
                            )
                        except Exception as send_err:
                            logger.warning(
                                "Scheduler: failed to send reply to buyer %s: %s",
                                buyer_id, send_err,
                            )

                    # 8. Activity log
                    db.add(ActivityLog(
                        id=uuid.uuid4(),
                        entity_type="campaign",
                        entity_id=campaign.id,
                        action="reply_received",
                        metadata_json={
                            "conversation_stage": new_stage,
                            "from_email": from_email,
                            "subject": reply.get("subject", ""),
                            "buyer_id": str(buyer_id),
                            "deal_id": str(campaign.deal_id),
                            "contract_ready": conv_result["contract_ready"],
                            "pass_detected": conv_result["pass_detected"],
                            "gmail_message_id": gmail_message_id or "",
                        },
                    ))


                    processed_count += 1

                except Exception as reply_err:
                    logger.error(
                        "Scheduler: failed to process reply from %s: %s",
                        reply.get("from_email", "unknown"),
                        reply_err, exc_info=True,
                    )
                    # Isolate: continue to next reply, don't roll back successes
                    continue

            # Commit all changes
            await db.commit()

            # FEATURE 2: Event-driven queued match release
            # After buyers pass (Pass intent), immediately process their
            # queued matches so they can be matched to other deals.
            # Use a fresh session since the current one is committed.
            if passed_buyer_ids:
                for bid in passed_buyer_ids:
                    try:
                        async with _db.async_session_factory() as release_db:
                            released = await process_queued_matches(
                                release_db, buyer_id=bid,
                            )
                            if released > 0:
                                logger.info(
                                    "Released %d queued matches for buyer %s (Pass reply)",
                                    released, bid,
                                )
                            await release_db.commit()
                    except Exception as release_err:
                        logger.warning(
                            "Failed to process queued matches for buyer %s "
                            "after Pass reply: %s",
                            bid, release_err, exc_info=True,
                        )

            logger.info(
                "Scheduler: reply processing complete — %d/%d replies processed",
                processed_count, len(replies),
            )
            return processed_count

        except Exception as e:
            logger.error(
                "Scheduler: reply processing error: %s", e, exc_info=True,
            )
            await db.rollback()
            return 0


# ---------------------------------------------------------------------------
# Ghost detection and recovery
# ---------------------------------------------------------------------------


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
                        # Do NOT increment ghost_recovery_touch — will retry next cycle
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
                            # Update all campaigns for this buyer+deal to Dormant
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

                            # Log dormant event
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
                        # Do NOT increment ghost_recovery_touch — will retry next cycle

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


# ---------------------------------------------------------------------------
# Buyer re-engagement scheduler (daily)
# ---------------------------------------------------------------------------


async def fire_buyer_reengagements() -> int:
    """Fire re-engagement emails for buyers whose scheduled target_date has arrived.

    For each due reengagement:
    1. Verify buyer is still active (not unsubscribed, not Do Not Contact)
    2. Find best matching active deal
    3. Check 2-deal cap and idempotency
    4. Generate AI re-engagement email with context from original statement
    5. Validate via validate_ai_output()
    6. Send email and create Campaign row
    7. Mark schedule as 'fired'

    Returns:
        Number of re-engagement emails fired.
    """
    async with _db.async_session_factory() as db:
        try:
            now = datetime.now(timezone.utc)

            # Find all due re-engagements
            result = await db.execute(
                select(BuyerReengagementSchedule).where(
                    BuyerReengagementSchedule.status == "waiting",
                    BuyerReengagementSchedule.target_date <= now,
                )
            )
            due_schedules = result.scalars().all()

            if not due_schedules:
                return 0

            fired_count = 0

            for schedule in due_schedules:
                try:
                    # 1. Load buyer — check active status
                    buyer = await db.get(Buyer, schedule.buyer_id)
                    if (
                        not buyer
                        or not buyer.email
                        or buyer.unsubscribed_at is not None
                        or buyer.status != "Active"
                    ):
                        schedule.status = "cancelled"
                        schedule.cancelled_at = now
                        schedule.cancellation_reason = "buyer_inactive"
                        db.add(schedule)
                        logger.info(
                            "Re-engagement cancelled for buyer %s: buyer inactive",
                            schedule.buyer_id,
                        )
                        continue

                    # 2. Find best matching active deal
                    deal = await db.get(Deal, schedule.deal_id) if schedule.deal_id else None
                    if not deal or deal.status not in ("Available", "Campaign Launched"):
                        # Find alternative active deal
                        deal_result = await db.execute(
                            select(Deal).where(
                                Deal.status.in_(["Available", "Campaign Launched"]),
                                Deal.deal_embedding.isnot(None),
                            )
                            .order_by(Deal.created_at.desc())
                            .limit(1)
                        )
                        best_deal = deal_result.scalar_one_or_none()
                        if not best_deal:
                            schedule.status = "no_deal_found"
                            db.add(schedule)
                            logger.warning(
                                "No matching deal found for re-engagement buyer %s",
                                schedule.buyer_id,
                            )
                            continue
                        deal = best_deal

                    # 3. Check idempotency: buyer already has campaign for this deal
                    existing_campaign = await db.execute(
                        select(Campaign).where(
                            Campaign.buyer_id == schedule.buyer_id,
                            Campaign.deal_id == deal.id,
                        ).limit(1)
                    )
                    if existing_campaign.scalar_one_or_none():
                        logger.info(
                            "Re-engagement skip for buyer %s deal %s: campaign already exists",
                            schedule.buyer_id, deal.id,
                        )
                        schedule.status = "cancelled"
                        schedule.cancelled_at = now
                        schedule.cancellation_reason = "campaign_already_exists"
                        db.add(schedule)
                        continue

                    # 4. Check 2-deal cap
                    from app.services.matching_service import get_active_deal_count_for_buyer
                    active_count = await get_active_deal_count_for_buyer(db, schedule.buyer_id)
                    if active_count >= 2:
                        # Queue as QueuedDealMatch instead
                        from app.models.models import QueuedDealMatch
                        existing_qm = await db.execute(
                            select(QueuedDealMatch).where(
                                QueuedDealMatch.buyer_id == schedule.buyer_id,
                                QueuedDealMatch.deal_id == deal.id,
                                QueuedDealMatch.status == "waiting",
                            )
                        )
                        if not existing_qm.scalar_one_or_none():
                            db.add(QueuedDealMatch(
                                buyer_id=schedule.buyer_id,
                                deal_id=deal.id,
                                status="waiting",
                                queued_at=now,
                            ))
                        logger.info(
                            "Re-engagement queued for buyer %s — at 2-deal cap",
                            schedule.buyer_id,
                        )
                        continue

                    # 5. Generate re-engagement email
                    target_month_str = schedule.target_date.strftime("%B %Y")

                    # Generate touch 1 but with re-engagement context injected via buy_box text
                    reengagement_context = (
                        f"IMPORTANT RE-ENGAGEMENT CONTEXT:\n"
                        f"This buyer previously indicated they would be ready "
                        f"to buy around {target_month_str}.\n"
                        f"They said: '{schedule.stated_window_raw}'\n"
                        f"Open the email by naturally referencing that they "
                        f"mentioned this timeframe — make them feel remembered, "
                        f"not marketed to."
                    )

                    # Temporarily prepend to buy_box for the email generator
                    original_buy_box = buyer.buy_box
                    enhanced_buy_box = f"{reengagement_context}\n\n{original_buy_box}"
                    buyer.buy_box = enhanced_buy_box

                    try:
                        email_data = await generate_touch_email(
                            touch=1,
                            buyer_name=buyer.full_name,
                            buyer_email=buyer.email,
                            buy_box=enhanced_buy_box,
                            buyer_tier=buyer.buyer_tier or "C-List",
                            address=deal.address,
                            city=deal.city or "",
                            state=deal.state or "",
                            property_type=deal.property_type,
                            arv=float(deal.arv),
                            asking_price=float(deal.asking_price),
                            spread=float(deal.spread) if deal.spread else 0,
                            condition_description=deal.condition_description,
                            beds=deal.beds,
                            baths=deal.baths,
                            sqft=deal.sqft,
                            buyer_id=buyer.id,
                        )
                    finally:
                        # Restore original buy_box
                        buyer.buy_box = original_buy_box

                    subject = email_data.get("subject", "")
                    body = email_data.get("body", "")

                    if not subject or not body:
                        logger.warning(
                            "Re-engagement email generation failed for buyer %s",
                            schedule.buyer_id,
                        )
                        continue

                    # 6. Validate via AI validator
                    try:
                        validation = await validate_ai_output(
                            content=body,
                            content_type="campaign_email",
                            deal=deal,
                            buyer=buyer,
                        )
                    except Exception as val_err:
                        logger.error(
                            "AI validator failed for re-engagement, proceeding unvalidated: %s",
                            val_err,
                        )
                        validation = ValidationResult(
                            severity="pass", corrected_content=None,
                            violations=[], checks_run=[],
                        )

                    if validation.severity == "block":
                        logger.error(
                            "Re-engagement email blocked by validator for buyer %s: %s",
                            schedule.buyer_id, validation.violations,
                        )
                        continue

                    body_to_send = validation.corrected_content or body

                    # 7. Send email
                    campaign_id = uuid.uuid4()
                    send_result = await send_email(
                        to=buyer.email,
                        subject=subject,
                        body=body_to_send,
                        campaign_id=campaign_id.hex,
                        send_type="campaign",
                    )

                    # 8. Create Campaign row
                    campaign_record = Campaign(
                        id=campaign_id,
                        deal_id=deal.id,
                        buyer_id=schedule.buyer_id,
                        touch_number=1,
                        status="Sent" if send_result.get("status") == "sent" else "Queued",
                        sent_at=now if send_result.get("status") == "sent" else None,
                        subject=subject,
                        body=body_to_send,
                        scheduled_send_at=now,
                    )
                    db.add(campaign_record)

                    # 9. Mark schedule as fired
                    schedule.status = "fired"
                    schedule.fired_at = now
                    db.add(schedule)

                    # 10. Activity log
                    log_entry = ActivityLog(
                        id=uuid.uuid4(),
                        entity_type="buyer",
                        entity_id=schedule.buyer_id,
                        action="reengagement_fired",
                        metadata_json={
                            "buyer_id": str(schedule.buyer_id),
                            "deal_id": str(deal.id),
                            "target_date": schedule.target_date.isoformat(),
                            "stated_window_raw": schedule.stated_window_raw,
                            "alert_user": False,
                        },
                    )
                    db.add(log_entry)

                    await db.commit()
                    fired_count += 1

                    logger.info(
                        "Re-engagement fired for buyer %s -> deal %s "
                        "(target was %s, stated: '%s')",
                        schedule.buyer_id, deal.id,
                        schedule.target_date.strftime("%Y-%m-%d"),
                        schedule.stated_window_raw,
                    )

                except Exception as e:
                    logger.error(
                        "Failed to fire re-engagement for buyer %s: %s",
                        schedule.buyer_id, e, exc_info=True,
                    )
                    await db.rollback()
                    continue

            if fired_count:
                logger.info(
                    "Buyer re-engagement complete: %d re-engagement(s) fired",
                    fired_count,
                )
            return fired_count

        except Exception as e:
            logger.error("Buyer re-engagement failed: %s", e, exc_info=True)
            await db.rollback()
            return 0


# ---------------------------------------------------------------------------
# Background task runner
# ---------------------------------------------------------------------------

_scheduler_task: asyncio.Task | None = None
_running = False


async def _scheduler_loop() -> None:
    """Run the scheduler loop with two independent intervals.

    Reply interval (5 min): process_buyer_replies, detect_and_flag_ghosts,
                            process_scheduled_campaigns
    Hourly interval (60 min): all other tasks (auto-match, insights, aging,
                              reengagement, ghost recovery, midnight reset,
                              queued matches, state persistence, DLQ retry)

    On each 60-second tick, checks which interval is due and runs the
    corresponding task group. Writes a heartbeat every tick.
    """
    global _running
    _running = True

    logger.info(
        "Scheduler: background task started "
        "(reply_interval=%ds, hourly_interval=%ds, tick=%ds)",
        REPLY_INTERVAL_SECONDS,
        DAILY_INTERVAL_SECONDS,
        TICK_INTERVAL_SECONDS,
    )

    # Track when each task group last ran
    _last_reply_run = 0.0
    _last_hourly_run = 0.0
    _last_daily_run_date = None
    _last_auto_match_time = datetime.min.replace(tzinfo=timezone.utc)
    _tick_count = 0

    # ── Run auto-match once on startup ──
    try:
        if settings.auto_match_enabled:
            result = await match_all_active_deals()
            if result["deals_processed"] > 0:
                logger.info(
                    "Initial auto-match: %d deals, %d campaigns launched, %d queued",
                    result["deals_processed"],
                    result["campaigns_launched"],
                    result["buyers_queued"],
                )
            _last_auto_match_time = datetime.now(timezone.utc)
    except Exception as e:
        logger.error("Initial auto-match failed: %s", e, exc_info=True)

    try:
        while _running:
            _tick_count += 1
            now = time.monotonic()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            is_new_day = (_last_daily_run_date != today)

            # ── Write scheduler heartbeat (never blocks the loop) ──
            try:
                await save_scheduler_heartbeat(_tick_count)
            except Exception:
                pass

            # ====================================================================
            # REPLY INTERVAL — runs every 5 minutes (time-sensitive tasks)
            # ====================================================================
            if now - _last_reply_run >= REPLY_INTERVAL_SECONDS:
                # --- Task R1: Process scheduled campaigns ---
                try:
                    sent = await process_scheduled_campaigns()
                    if sent > 0:
                        logger.info("Scheduler: sent %d campaigns", sent)
                except Exception as e:
                    logger.error("Scheduler: campaign processing failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Task R2: Check for buyer replies ---
                try:
                    processed = await process_buyer_replies()
                    if processed > 0:
                        logger.info("Scheduler: processed %d buyer replies", processed)
                except Exception as e:
                    logger.error("Scheduler: reply processing failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Task R3: Ghost detection (time-sensitive) ---
                try:
                    ghosts = await detect_and_flag_ghosts()
                    if ghosts > 0:
                        logger.info("Scheduler: detected %d ghost buyer(s)", ghosts)
                except Exception as e:
                    logger.error("Scheduler: ghost detection failed: %s", e, exc_info=True)

                _last_reply_run = now

            if not _running:
                break

            # ====================================================================
            # HOURLY INTERVAL — runs every 60 minutes (maintenance tasks)
            # ====================================================================
            if now - _last_hourly_run >= DAILY_INTERVAL_SECONDS:
                # --- Task H1: Process queued deal matches ---
                try:
                    async with _db.async_session_factory() as db:
                        released = await process_queued_matches(db)
                        if released > 0:
                            logger.info("Scheduler: released %d queued deal matches", released)
                except Exception as e:
                    logger.error("Scheduler: queued match processing failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Task H2: Monitor title company emails ---
                try:
                    result = await process_title_emails()
                    if result.get("total_found", 0) > 0:
                        logger.info(
                            "Scheduler: processed %d title emails (%d actions)",
                            result["total_found"], result["processed"],
                        )
                except Exception as e:
                    logger.error("Scheduler: title email monitoring failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Task H3: Daily tier promotions (new day only) ---
                if is_new_day:
                    try:
                        async with _db.async_session_factory() as db:
                            # Recalculate engagement scores before tier promotions
                            await calculate_and_update_engagement(db)
                            promotions = await run_tier_promotions(db)
                            if promotions:
                                logger.info(
                                    "Scheduler: %d buyers promoted via auto-tier scoring",
                                    len(promotions),
                                )
                    except Exception as e:
                        logger.error("Scheduler: tier promotions failed: %s", e, exc_info=True)

                    if not _running:
                        break

                    # --- Task H4: Weekly fatigue counter reset ---
                    try:
                        async with _db.async_session_factory() as db:
                            reset_count = await reset_pitch_counters(db)
                            if reset_count > 0:
                                logger.info(
                                    "Scheduler: reset pitch counters for %d buyers",
                                    reset_count,
                                )
                    except Exception as e:
                        logger.error("Scheduler: pitch counter reset failed: %s", e, exc_info=True)

                if not _running:
                    break

                # ── Gmail daily send counter midnight reset ──
                try:
                    from zoneinfo import ZoneInfo
                    now_tz = datetime.now(ZoneInfo(settings.gmail_timezone))
                    counter = await load_gmail_daily_sends()
                    counter_date = counter.get("date", "")
                    today_tz = now_tz.strftime("%Y-%m-%d")
                    if counter_date and counter_date != today_tz:
                        yesterday_count = counter.get("count", 0)
                        next_midnight = now_tz.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                        await save_gmail_daily_sends(0, today_tz, next_midnight.isoformat())
                        logger.info(
                            "Gmail daily counter reset. Yesterday: %d sends.",
                            yesterday_count,
                        )
                except Exception as e:
                    logger.error("Scheduler: gmail daily counter reset failed: %s", e, exc_info=True)

                if not _running:
                    break

                # --- Run independent hourly tasks concurrently ---
                async def _task_aging() -> None:
                    if is_new_day:
                        try:
                            async with _db.async_session_factory() as db:
                                aging_actions = await run_aging_monitor(db)
                                if aging_actions:
                                    logger.info(
                                        "Scheduler: %d aging escalation actions taken",
                                        len(aging_actions),
                                    )
                        except Exception as e:
                            logger.error("Scheduler: aging monitor failed: %s", e, exc_info=True)

                async def _task_insights() -> None:
                    if is_new_day and datetime.now(timezone.utc).weekday() == 0:
                        try:
                            async with _db.async_session_factory() as db:
                                count = await update_all_buyer_insights(db)
                                if count > 0:
                                    logger.info(
                                        "Scheduler: updated portfolio insights for %d buyers",
                                        count,
                                    )
                        except Exception as e:
                            logger.error("Scheduler: buyer insights update failed: %s", e, exc_info=True)

                async def _task_persist() -> None:
                    try:
                        cb_queue_items = get_cb_queue()
                        metrics = get_metrics()
                        idem_store = get_idempotency_store()
                        groq_count = get_call_count_today()
                        groq_date = get_calls_today_date() or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        await save_all_state(
                            cb_queue=cb_queue_items,
                            metrics=metrics,
                            idempotency_store=idem_store,
                            groq_count=groq_count,
                            groq_date=groq_date,
                        )
                        logger.debug("Scheduler: persisted in-memory state to DB")
                    except Exception as e:
                        logger.error("Scheduler: failed to persist in-memory state: %s", e, exc_info=True)

                async def _task_dlq_retry() -> None:
                    try:
                        async with _db.async_session_factory() as db:
                            result = await db.execute(
                                select(FailedCampaign)
                                .where(FailedCampaign.resolved == False)
                                .order_by(FailedCampaign.last_retry_at.asc().nullsfirst())
                                .limit(5)
                            )
                            failed_campaigns = result.scalars().all()
                            for dlq_entry in failed_campaigns:
                                if not _running:
                                    break
                                retry_result = await retry_failed_campaign(db, dlq_entry)
                                if retry_result.get("success"):
                                    logger.info("DLQ auto-retry succeeded for campaign %s", dlq_entry.campaign_id)
                                elif "Cooldown" in retry_result.get("error", ""):
                                    break
                    except Exception as e:
                        logger.error("Scheduler: DLQ auto-retry failed: %s", e, exc_info=True)

                async def _task_auto_match() -> None:
                    nonlocal _last_auto_match_time
                    hours_since = (
                        datetime.now(timezone.utc) - _last_auto_match_time
                    ).total_seconds() / 3600
                    if (
                        settings.auto_match_enabled
                        and hours_since >= settings.auto_match_interval_hours
                    ):
                        try:
                            result = await match_all_active_deals()
                            if result["deals_processed"] > 0:
                                logger.info(
                                    "Periodic auto-match: %d deals, %d campaigns, %d queued",
                                    result["deals_processed"],
                                    result["campaigns_launched"],
                                    result["buyers_queued"],
                                )
                            _last_auto_match_time = datetime.now(timezone.utc)
                        except Exception as e:
                            logger.error("Periodic auto-match failed: %s", e, exc_info=True)

                async def _task_ghost_recovery() -> None:
                    try:
                        sent = await send_ghost_recovery_emails()
                        if sent > 0:
                            logger.info("Scheduler: sent %d ghost recovery email(s)", sent)
                    except Exception as e:
                        logger.error("Scheduler: ghost recovery send failed: %s", e, exc_info=True)

                async def _task_reengagement() -> None:
                    try:
                        fired = await fire_buyer_reengagements()
                        if fired > 0:
                            logger.info("Scheduler: fired %d buyer re-engagement(s)", fired)
                    except Exception as e:
                        logger.error("Scheduler: buyer re-engagement failed: %s", e, exc_info=True)

                async def _task_title_chases() -> None:
                    try:
                        sent = await run_title_chases()
                        if sent > 0:
                            logger.info("Scheduler: sent %d title chase email(s)", sent)
                    except Exception as e:
                        logger.error("Scheduler: title chase failed: %s", e, exc_info=True)

                await asyncio.gather(
                    _task_aging(),
                    _task_insights(),
                    _task_persist(),
                    _task_dlq_retry(),
                    _task_auto_match(),
                    _task_ghost_recovery(),
                    _task_reengagement(),
                    _task_title_chases(),
                    return_exceptions=True,
                )

                _last_hourly_run = now

            _last_daily_run_date = today

            if not _running:
                break

            # Sleep 60 seconds between ticks
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Scheduler: background task cancelled")
        _running = False
    except Exception as e:
        logger.error("Scheduler: fatal error — will restart in 30s: %s", e, exc_info=True)
        _running = False
        await asyncio.sleep(30)
        # Auto-restart the scheduler after fatal errors
        logger.info("Scheduler: restarting after fatal error")
        asyncio.get_event_loop().create_task(_scheduler_loop())


def is_scheduler_running() -> bool:
    """Check if the scheduler background task is currently running."""
    return _running


def start_scheduler() -> None:
    """Start the background scheduler task.

    Safe to call multiple times — will not start a second instance.
    """
    global _scheduler_task

    if _scheduler_task is not None and not _scheduler_task.done():
        logger.warning("Scheduler: already running, skipping start")
        return

    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("Scheduler: started")


async def stop_scheduler() -> None:
    """Gracefully stop the background scheduler task."""
    global _running, _scheduler_task

    _running = False

    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("Scheduler: stopped")