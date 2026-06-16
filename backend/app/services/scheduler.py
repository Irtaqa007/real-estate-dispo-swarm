"""Background task scheduler for automated 24/7 operations.

Runs every hour as an asyncio background task, performing:
1. Process scheduled campaign sends
2. Check for buyer replies in Gmail
3. Monitor title company emails

Each task is individually wrapped in try/except so one failure
never crashes the entire scheduler.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

import app.database as _db
from app.models.schemas import ActivityLog, Buyer, Campaign, Deal, FailedCampaign
from app.services.dead_letter_queue import retry_failed_campaign
from app.services.gmail_monitor import check_for_replies
from app.services.gmail_service import send_email
from app.services.groq_client import get_rate_limit_status
from app.services.title_coordinator import process_title_emails, send_assignment_contract
from app.services.buyer_scoring import run_tier_promotions, reset_pitch_counters
from app.services.aging_monitor import run_aging_monitor
from app.services.buyer_insights import update_all_buyer_insights
from app.services.embeddings import generate_embedding
from app.services.reply_processor import process_reply, extract_buybox_changes, get_question_round_message
from app.services.negotiation import handle_counter_offer
from app.services.audit_logger import audit
from app.services.state_persistence import save_all_state
from app.services.circuit_breaker import gmail_circuit_breaker, get_cb_queue
from app.services.matching_service import process_queued_matches, invalidate_queued_matches_for_buyer
from app.services.resilience import get_metrics, get_idempotency_store
from app.services.groq_client import get_call_count_today, get_calls_today_date
from app.services.buyer_merge import merge_buy_boxes
from app.models.schemas import BuyerEmail

logger = logging.getLogger(__name__)

# Interval between scheduler runs (in seconds)
SCHEDULER_INTERVAL_SECONDS = 60 * 60  # 1 hour

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

                    await send_email(
                        to=buyer.email,
                        subject=campaign.subject,
                        body=campaign.body,
                        campaign_id=campaign.id.hex,
                    )

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
       regenerate Cohere embedding, log changes
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

            for reply in replies:
                try:
                    from_email = reply["from_email"]
                    buyer_id = buyer_map.get(from_email.lower())

                    if not buyer_id:
                        logger.info("Scheduler: reply from unknown buyer %s — skipping", from_email)
                        continue

                    # 4. Classify the reply via Groq
                    classification = await process_reply(reply)
                    reply_intent = classification["reply_intent"]

                    # 5. Match to the most recent Sent campaign for this buyer
                    campaign = await db.scalar(
                        select(Campaign)
                        .where(Campaign.buyer_id == buyer_id, Campaign.status == "Sent")
                        .order_by(Campaign.sent_at.desc().nullslast())
                    )

                    if not campaign:
                        logger.info(
                            "Scheduler: no sent campaign found for buyer %s — skipping",
                            from_email,
                        )
                        continue

                    # 6. Update the campaign with reply data
                    now = datetime.now(timezone.utc)
                    campaign.reply_received_at = now
                    campaign.reply_body = reply["body"]
                    campaign.reply_intent = reply_intent
                    campaign.ai_extracted_insights = classification["ai_extracted_insights"]
                    campaign.status = "Replied"
                    db.add(campaign)

                    # 7. Fetch buyer and update last_reply_at
                    buyer_obj = await db.get(Buyer, buyer_id)
                    if buyer_obj:
                        buyer_obj.last_reply_at = now
                        db.add(buyer_obj)

                        # 7a. Buy Box Auto-Update
                        profile_updates = classification.get("buyer_profile_updates", {})
                        if reply_intent == "Buybox_Changed" or profile_updates.get("buy_box"):
                            old_buy_box = buyer_obj.buy_box
                            buybox_result = await extract_buybox_changes(
                                reply_body=reply.get("body", ""),
                                old_buy_box=old_buy_box,
                            )
                            if buybox_result.get("criteria_changed") and buybox_result.get("new_criteria"):
                                # Smart merge: never remove old criteria, intelligently combine
                                merged_buy_box = await merge_buy_boxes(
                                    old_buy_box, buybox_result["new_criteria"]
                                )
                                buyer_obj.buy_box = merged_buy_box
                                campaign.buyer_profile_updated = True
                                db.add(campaign)
                                # Regenerate embedding (already imported at module level)
                                try:
                                    new_embedding = await generate_embedding(
                                        merged_buy_box,
                                        input_type="search_query",
                                    )
                                    buyer_obj.buy_box_embedding = new_embedding
                                    logger.info(
                                        "Scheduler: regenerated buy_box embedding for buyer %s",
                                        buyer_id,
                                    )
                                except Exception as emb_err:
                                    logger.warning(
                                        "Scheduler: failed to regenerate embedding for buyer %s: %s",
                                        buyer_id, emb_err, exc_info=True,
                                    )

                                # Re-parse structured fields from merged buy_box
                                try:
                                    from app.services.parse_buy_box import parse_buy_box
                                    parsed = await parse_buy_box(merged_buy_box)
                                    buyer_obj.price_min = parsed.get("price_min")
                                    buyer_obj.price_max = parsed.get("price_max")
                                    buyer_obj.pref_property_type = parsed.get("pref_property_type")
                                    buyer_obj.pref_cities = parsed.get("pref_cities")
                                except Exception as parse_err:
                                    logger.warning(
                                        "Scheduler: failed to re-parse buy_box for buyer %s: %s",
                                        buyer_id, parse_err, exc_info=True,
                                    )

                                # Invalidate queued matches since preferences changed
                                try:
                                    await invalidate_queued_matches_for_buyer(db, buyer_id)
                                except Exception as inv_err:
                                    logger.warning(
                                        "Scheduler: failed to invalidate queued matches for buyer %s: %s",
                                        buyer_id, inv_err, exc_info=True,
                                    )

                                # Log to activity_log
                                try:
                                    await audit.log_buyer_updated(
                                        db,
                                        buyer_id,
                                        changes={
                                            "buy_box": {
                                                "old": old_buy_box[:200],
                                                "new": merged_buy_box[:200],
                                            },
                                            "changes_summary": buybox_result.get("changes_summary", ""),
                                        },
                                        updated_by="ai_classification",
                                    )
                                except Exception as audit_err:
                                    logger.warning(
                                        "Scheduler: failed to log buybox update for buyer %s: %s",
                                        buyer_id, audit_err, exc_info=True,
                                    )

                    # 8. Auto-pause remaining queued campaigns for this buyer + deal
                    queued_result = await db.execute(
                        select(Campaign).where(
                            Campaign.buyer_id == buyer_id,
                            Campaign.deal_id == campaign.deal_id,
                            Campaign.status == "Queued",
                        )
                    )
                    queued_campaigns = queued_result.scalars().all()
                    for qc in queued_campaigns:
                        qc.status = "Paused"
                        db.add(qc)

                    # 9. Log to activity_log
                    log_entry = ActivityLog(
                        id=uuid.uuid4(),
                        entity_type="campaign",
                        entity_id=campaign.id,
                        action="reply_received",
                        metadata_json={
                            "reply_intent": reply_intent,
                            "from_email": from_email,
                            "subject": reply["subject"],
                            "buyer_id": str(buyer_id),
                            "deal_id": str(campaign.deal_id),
                            "campaigns_paused": len(queued_campaigns),
                            "sentiment": classification.get("sentiment"),
                            "source": "scheduler",
                        },
                    )
                    db.add(log_entry)

                    # 10a. Smart Negotiation: handle counter offers
                    if reply_intent == "Counter" and classification.get("counter_price") is not None:
                        try:
                            deal = await db.get(Deal, campaign.deal_id)
                            if deal:
                                counter_price = classification["counter_price"]
                                negotiation_result = await handle_counter_offer(
                                    deal=deal,
                                    counter_price=counter_price,
                                    buyer_name=buyer_obj.full_name if buyer_obj else "Buyer",
                                )
                                if negotiation_result["auto_approved"]:
                                    deal.contract_price = counter_price
                                    db.add(deal)
                                    logger.info(
                                        "Scheduler: counter auto-approved for deal %s: $%.2f (floor: $%.2f)",
                                        campaign.deal_id, counter_price,
                                        negotiation_result["floor_price"],
                                    )
                                else:
                                    logger.info(
                                        "Scheduler: counter below floor for deal %s: $%.2f (floor: $%.2f)",
                                        campaign.deal_id, counter_price,
                                        negotiation_result["floor_price"],
                                    )
                        except Exception as e:
                            logger.warning(
                                "Scheduler: smart negotiation failed for buyer %s, deal %s: %s",
                                buyer_id, campaign.deal_id, e, exc_info=True,
                            )

                    # 10b. Auto-Follow-Up on Question replies
                    if reply_intent == "Question":
                        current_round = campaign.question_round or 0
                        new_round = current_round + 1
                        campaign.question_round = new_round
                        db.add(campaign)

                        round_action = get_question_round_message(new_round)
                        question_answer = classification.get("question_answer")

                        if round_action == "auto_answer" and question_answer:
                            logger.info(
                                "Scheduler: auto-follow-up draft for buyer %s (round %d): %.100s",
                                buyer_id, new_round, question_answer,
                            )
                            try:
                                await audit.log(
                                    db,
                                    entity_type="campaign",
                                    entity_id=campaign.id,
                                    action="question_auto_answer",
                                    metadata={
                                        "question_round": new_round,
                                        "question": reply.get("subject", ""),
                                        "auto_answer": question_answer,
                                        "buyer_id": str(buyer_id) if buyer_id else None,
                                        "deal_id": str(campaign.deal_id),
                                        "source": "scheduler",
                                    },
                                )
                            except Exception as q_err:
                                logger.warning(
                                    "Scheduler: failed to log auto-answer for campaign %s: %s",
                                    campaign.id, q_err, exc_info=True,
                                )

                        elif round_action == "final_answer_prompt":
                            logger.info(
                                "Scheduler: buyer %s has round 3 questions — sending final answer prompt",
                                buyer_id,
                            )
                            try:
                                await audit.log(
                                    db,
                                    entity_type="campaign",
                                    entity_id=campaign.id,
                                    action="question_final_answer",
                                    metadata={
                                        "question_round": new_round,
                                        "message": "Buyer has asked 3 questions. Sending final answer with close prompt.",
                                        "auto_answer": question_answer,
                                        "source": "scheduler",
                                    },
                                )
                            except Exception as q_err:
                                logger.warning(
                                    "Scheduler: failed to log final answer for campaign %s: %s",
                                    campaign.id, q_err, exc_info=True,
                                )

                        elif round_action == "manual_intervention_needed":
                            logger.warning(
                                "Scheduler: buyer %s has %d+ questions — manual intervention needed!",
                                buyer_id, new_round,
                            )
                            try:
                                await audit.log(
                                    db,
                                    entity_type="campaign",
                                    entity_id=campaign.id,
                                    action="question_escalated",
                                    metadata={
                                        "question_round": new_round,
                                        "message": "Buyer has 4+ questions. Manual intervention needed.",
                                        "severity": "high",
                                        "alert_user": True,
                                        "source": "scheduler",
                                    },
                                )
                            except Exception as q_err:
                                logger.warning(
                                    "Scheduler: failed to log escalation for campaign %s: %s",
                                    campaign.id, q_err, exc_info=True,
                                )

                    # 10c. Auto-send assignment contract if buyer is Interested
                    if reply_intent == "Interested":
                        try:
                            deal = await db.get(Deal, campaign.deal_id)
                            if deal and buyer_obj:
                                contract_result = await send_assignment_contract(
                                    db=db,
                                    deal=deal,
                                    buyer_name=buyer_obj.full_name,
                                    buyer_email=buyer_obj.email,
                                )
                                if contract_result.get("sent", False):
                                    logger.info(
                                        "Scheduler: assignment contract sent to %s for deal %s",
                                        buyer_obj.email, deal.address,
                                    )
                        except Exception as e:
                            logger.warning(
                                "Scheduler: failed to send assignment contract for buyer %s, deal %s: %s",
                                buyer_id, campaign.deal_id, e, exc_info=True,
                            )

                    # 10d. Handle Unsubscribe intent: mark buyer as opted out
                    if reply_intent == "Unsubscribe" and buyer_obj:
                        now = datetime.now(timezone.utc)
                        buyer_obj.unsubscribed_at = now
                        buyer_obj.status = "Do Not Contact"
                        db.add(buyer_obj)
                        logger.info(
                            "Scheduler: buyer %s (%s) unsubscribed via email reply",
                            buyer_id, buyer_obj.email,
                        )
                        log_entry_unsub = ActivityLog(
                            id=uuid.uuid4(),
                            entity_type="buyer",
                            entity_id=buyer_id,
                            action="unsubscribed",
                            metadata_json={
                                "email": buyer_obj.email,
                                "source": "scheduler_reply_classification",
                                "reply_intent": "Unsubscribe",
                            },
                        )
                        db.add(log_entry_unsub)

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
# Background task runner
# ---------------------------------------------------------------------------

_scheduler_task: asyncio.Task | None = None
_running = False


async def _scheduler_loop() -> None:
    """Run the scheduler loop every SCHEDULER_INTERVAL_SECONDS.

    Each task is wrapped individually so one failure doesn't crash the loop.
    Tasks:
    - process_scheduled_campaigns: Send queued campaigns past their schedule
    - check_replies: Check Gmail inbox for buyer replies
    - monitor_title_emails: Check Gmail inbox for title company emails
    - run_tier_promotions: Daily auto-tier promotion for buyers
    - reset_pitch_counters: Weekly fatigue counter reset
    """
    global _running
    _running = True

    logger.info("Scheduler: background task started (interval=%ds)", SCHEDULER_INTERVAL_SECONDS)

    # Track when daily tasks last ran to avoid running them every hour
    _last_daily_run_date = None

    try:
        while _running:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            is_new_day = (_last_daily_run_date != today)

            # ---- Task 1: Process scheduled campaigns ----
            try:
                sent = await process_scheduled_campaigns()
                if sent > 0:
                    logger.info("Scheduler: sent %d campaigns", sent)
            except Exception as e:
                logger.error("Scheduler: campaign processing failed: %s", e, exc_info=True)

            if not _running:
                break

            # ---- Task 2: Check for buyer replies ----
            try:
                processed = await process_buyer_replies()
                if processed > 0:
                    logger.info("Scheduler: processed %d buyer replies", processed)
            except Exception as e:
                logger.error("Scheduler: reply processing failed: %s", e, exc_info=True)

            if not _running:
                break

            # ---- Task 2b: Process queued deal matches ----
            try:
                async with _db.async_session_factory() as db:
                    released = await process_queued_matches(db)
                    if released > 0:
                        logger.info("Scheduler: released %d queued deal matches", released)
            except Exception as e:
                logger.error("Scheduler: queued match processing failed: %s", e, exc_info=True)

            if not _running:
                break

            # ---- Task 3: Monitor title company emails ----
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

            # ---- Task 4: Daily tier promotions ----
            if is_new_day:
                try:
                    async with _db.async_session_factory() as db:
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

                # ---- Task 5: Weekly fatigue counter reset ----
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

            # ---- Tasks 6-8: Run independent tasks concurrently ----
            # Aging monitor, buyer insights, state persistence, and DLQ retry
            # are independent of each other — run them in parallel to reduce
            # total cycle time.
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
                                for action in aging_actions:
                                    logger.info(
                                        "  Aging: deal %s (%d days old) → %s",
                                        action["address"], action["days_old"], action["action"],
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
                """Auto-retry up to 5 failed campaigns per scheduler cycle."""
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
                                break  # Remaining entries are also within cooldown
                except Exception as e:
                    logger.error("Scheduler: DLQ auto-retry failed: %s", e, exc_info=True)

            await asyncio.gather(
                _task_aging(),
                _task_insights(),
                _task_persist(),
                _task_dlq_retry(),
                return_exceptions=True,
            )

            _last_daily_run_date = today

            if not _running:
                break

            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Scheduler: background task cancelled")
        _running = False
    except Exception as e:
        logger.error("Scheduler: fatal error: %s", e, exc_info=True)
        _running = False


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
