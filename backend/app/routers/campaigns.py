"""Campaign management router — launch 6-touch email campaigns for a deal."""

import logging
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schemas import ActivityLog, Buyer, BuyerEmail, Campaign, Deal, JVPartner
from app.schemas import (
    CampaignLaunchResponse,
    CampaignLaunchResult,
    CampaignResponse,
    CampaignTouch,
    CheckRepliesResponse,
    ReplyCheckItem,
    SendAllItem,
    SendAllResponse,
    SendResponse,
)
from app.services.email_generator import generate_touch_email, TOUCH_CONFIGS
from app.services.gmail_monitor import check_for_replies
from app.services.gmail_service import send_email
from app.services.dead_letter_queue import move_to_dlq
from app.services.reply_processor import process_reply, extract_buybox_changes, get_question_round_message
from app.services.title_coordinator import send_assignment_contract
from app.services.buyer_scoring import assess_buyer_eligibility, check_fatigue_protection, increment_pitch_count
from app.services.buyer_merge import merge_buy_boxes
from app.services.negotiation import handle_counter_offer
from app.services.audit_logger import audit
from app.services.jv_rotator import check_jv_rotation
from app.services.market_adjuster import check_touch_3_adjustment, check_touch_4_adjustment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


@router.get("", response_model=List[CampaignResponse])
async def list_campaigns(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all campaigns across all deals with pagination."""
    result = await db.execute(
        select(Campaign).offset(skip).limit(limit).order_by(Campaign.created_at.desc())
    )
    campaigns = result.scalars().all()
    return campaigns


@router.get("/deal/{deal_id}", response_model=List[CampaignResponse])
async def get_campaigns_by_deal(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """List all campaigns for a specific deal."""
    result = await db.execute(select(Campaign).where(Campaign.deal_id == deal_id))
    campaigns = result.scalars().all()
    return campaigns


@router.post("/check-replies", response_model=CheckRepliesResponse)
async def check_replies_endpoint(db: AsyncSession = Depends(get_db)):
    """Manually trigger a Gmail inbox check for buyer replies.

    Fetches unread emails from the Gmail inbox, matches them against
    known buyer email addresses, classifies each reply via Groq AI,
    updates the corresponding campaign records, auto-pauses remaining
    queued touches, and sends an assignment contract if the buyer is "Interested".

    Returns:
        CheckRepliesResponse with per-reply results.
    """
    # 1. Fetch all buyer email addresses from the database
    # Include both primary emails and additional buyer_emails
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
        return CheckRepliesResponse(
            total_replies_found=0,
            replies_processed=0,
            results=[],
        )

    # 2. Check Gmail inbox for replies
    replies = await check_for_replies(buyer_emails)

    if not replies:
        logger.info("No new replies found in inbox")
        return CheckRepliesResponse(
            total_replies_found=0,
            replies_processed=0,
            results=[],
        )

    # 3. Process each reply
    results: List[ReplyCheckItem] = []
    processed_count = 0

    for reply in replies:
        from_email = reply["from_email"]
        buyer_id = buyer_map.get(from_email.lower())

        if not buyer_id:
            results.append(ReplyCheckItem(
                from_email=from_email,
                subject=reply["subject"],
                reply_intent="unknown",
                matched=False,
                error="Buyer not found in database",
            ))
            continue

        # 4. Classify the reply via Groq
        classification = await process_reply(reply)

        # 5. Match to the most recent Sent campaign for this buyer
        campaign = await db.scalar(
            select(Campaign)
            .where(Campaign.buyer_id == buyer_id, Campaign.status == "Sent")
            .order_by(Campaign.sent_at.desc().nullslast())
        )

        if not campaign:
            results.append(ReplyCheckItem(
                from_email=from_email,
                subject=reply["subject"],
                reply_intent=classification["reply_intent"],
                buyer_id=buyer_id,
                matched=False,
                error="No sent campaign found for this buyer",
            ))
            continue

        # 6. Update the campaign with reply data
        campaign.reply_received_at = datetime.now(timezone.utc)
        campaign.reply_body = reply["body"]
        campaign.reply_intent = classification["reply_intent"]
        campaign.ai_extracted_insights = classification["ai_extracted_insights"]
        campaign.status = "Replied"
        db.add(campaign)

        # 7. Fetch buyer once for all updates
        # 7a. Buy Box Auto-Update (feature 1): if buybox changed, extract and update
        profile_updates = classification.get("buyer_profile_updates", {})
        buybox_updated_flag = False
        buyer_obj = await db.get(Buyer, buyer_id)
        if buyer_obj:
            if classification["reply_intent"] == "Buybox_Changed" or profile_updates.get("buy_box"):
                old_buy_box = buyer_obj.buy_box
                # Use Groq to extract detailed changes
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
                    buybox_updated_flag = True
                    db.add(campaign)
                    # Regenerate embedding via Cohere
                    try:
                        from app.services.embeddings import generate_embedding
                        new_embedding = await generate_embedding(
                            merged_buy_box,
                            input_type="search_query",
                        )
                        buyer_obj.buy_box_embedding = new_embedding
                        logger.info("Regenerated buy_box embedding for buyer %s", buyer_id)
                    except Exception as emb_err:
                        logger.warning("Failed to regenerate embedding for buyer %s: %s", buyer_id, emb_err, exc_info=True)
                    # Log old vs new buy_box to activity_log
                    try:
                        await audit.log_buyer_updated(
                            db, buyer_id,
                            changes={
                                "buy_box": {"old": old_buy_box[:200], "new": merged_buy_box[:200]},
                                "changes_summary": buybox_result.get("changes_summary", ""),
                            },
                            updated_by="ai_classification",
                        )
                    except Exception as audit_err:
                        logger.warning("Failed to log buybox update for buyer %s: %s", buyer_id, audit_err, exc_info=True)

            buyer_obj.last_reply_at = datetime.now(timezone.utc)
            db.add(buyer_obj)

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
                "reply_intent": classification["reply_intent"],
                "from_email": from_email,
                "subject": reply["subject"],
                "buyer_id": str(buyer_id),
                "deal_id": str(campaign.deal_id),
                "campaigns_paused": len(queued_campaigns),
                "sentiment": classification.get("sentiment"),
                "buybox_updated": bool(profile_updates.get("buy_box")),
            },
        )
        db.add(log_entry)

        # 10. Handle Unsubscribe intent: mark buyer as opted out
        if classification["reply_intent"] == "Unsubscribe" and buyer_obj:
            now = datetime.now(timezone.utc)
            buyer_obj.unsubscribed_at = now
            buyer_obj.status = "Do Not Contact"
            db.add(buyer_obj)
            logger.info(
                "Buyer %s (%s) unsubscribed via email reply",
                buyer_id, buyer_obj.email,
            )
            # Log unsubscribed action
            log_entry_unsub = ActivityLog(
                id=uuid.uuid4(),
                entity_type="buyer",
                entity_id=buyer_id,
                action="unsubscribed",
                metadata_json={
                    "email": buyer_obj.email,
                    "source": "reply_classification",
                    "reply_intent": "Unsubscribe",
                },
            )
            db.add(log_entry_unsub)

        # 10a. Smart Negotiation: if buyer counters, auto-approve or defer
        negotiation_result = None
        if classification["reply_intent"] == "Counter" and classification.get("counter_price") is not None:
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
                        # Update contract price to the agreed counter
                        deal.contract_price = counter_price
                        db.add(deal)
                        logger.info(
                            "Counter auto-approved for deal %s: $%.2f (floor: $%.2f)",
                            campaign.deal_id, counter_price, negotiation_result["floor_price"],
                        )
                    else:
                        logger.info(
                            "Counter below floor for deal %s: $%.2f (floor: $%.2f) — needs manual review",
                            campaign.deal_id, counter_price, negotiation_result["floor_price"],
                        )

                    # Store negotiation result in activity log metadata
                    if negotiation_result:
                        log_entry_meta = log_entry.metadata_json or {}
                        log_entry_meta["negotiation"] = {
                            "action": negotiation_result["action"],
                            "counter_price": counter_price,
                            "auto_approved": negotiation_result["auto_approved"],
                        }

            except Exception as e:
                logger.warning(
                    "Smart negotiation failed for buyer %s, deal %s: %s",
                    buyer_id, campaign.deal_id, e, exc_info=True,
                )

        # 10c. Auto-Follow-Up on "Question" replies (feature 5):
        # Draft answer via AI, track question_round, escalate if > 3 rounds
        if classification["reply_intent"] == "Question":
            current_round = campaign.question_round or 0
            new_round = current_round + 1
            campaign.question_round = new_round
            db.add(campaign)

            round_action = get_question_round_message(new_round)
            question_answer = classification.get("question_answer")

            if round_action == "auto_answer" and question_answer:
                logger.info(
                    "Auto-follow-up draft for buyer %s (round %d): %.100s",
                    buyer_id, new_round, question_answer,
                )
                # Store the answer in activity log - actual send happens via endpoint
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
                        },
                    )
                except Exception as q_err:
                    logger.warning("Failed to log auto-answer for campaign %s: %s", campaign.id, q_err, exc_info=True)

            elif round_action == "final_answer_prompt":
                logger.info(
                    "Buyer %s has round 3 questions. Sending final answer prompt.",
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
                        },
                    )
                except Exception as q_err:
                    logger.warning("Failed to log final answer for campaign %s: %s", campaign.id, q_err, exc_info=True)

            elif round_action == "manual_intervention_needed":
                logger.warning(
                    "BUYER %s HAS %d+ QUESTIONS — Manual intervention needed!",
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
                        },
                    )
                except Exception as q_err:
                    logger.warning("Failed to log escalation for campaign %s: %s", campaign.id, q_err, exc_info=True)

        # 10b. Auto-send assignment contract if buyer is Interested
        assignment_sent = False
        if classification["reply_intent"] == "Interested":
            try:
                deal = await db.get(Deal, campaign.deal_id)
                if deal and buyer_obj:
                    contract_result = await send_assignment_contract(
                        db=db,
                        deal=deal,
                        buyer_name=buyer_obj.full_name,
                        buyer_email=buyer_obj.email,
                    )
                    assignment_sent = contract_result.get("sent", False)
                    if assignment_sent:
                        logger.info(
                            "Assignment contract sent to %s for deal %s",
                            buyer_obj.email, deal.address,
                        )
            except Exception as e:
                logger.warning(
                    "Failed to send assignment contract for buyer %s, deal %s: %s",
                    buyer_id, campaign.deal_id, e, exc_info=True,
                )

        results.append(ReplyCheckItem(
            from_email=from_email,
            subject=reply["subject"],
            reply_intent=classification["reply_intent"],
            campaign_id=campaign.id,
            deal_id=campaign.deal_id,
            buyer_id=buyer_id,
            matched=True,
            campaigns_paused=len(queued_campaigns),
        ))
        processed_count += 1

    # 11. Commit all changes
    await db.commit()

    logger.info(
        "Check-replies complete: %d replies found, %d processed",
        len(replies), processed_count,
    )

    return CheckRepliesResponse(
        total_replies_found=len(replies),
        replies_processed=processed_count,
        results=results,
    )


@router.get("/id/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get a single campaign by its ID."""
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )
    return campaign


@router.post(
    "/{deal_id}/launch",
    status_code=status.HTTP_201_CREATED,
)
async def launch_campaign(
    deal_id: uuid.UUID,
    match_limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Launch a 6-touch email campaign for a deal.

    Fetches the deal, runs semantic matching against all Active + verified buyers,
    generates 6 psychologically-optimized emails per buyer using Groq AI,
    saves all touches to the campaigns table, and updates the deal status.

    Args:
        deal_id: UUID of the deal to launch a campaign for.
        match_limit: Maximum number of top-matched buyers to target.

    Returns:
        CampaignLaunchResponse with per-buyer results.
    """
    # 1. Fetch deal
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    if deal.status != "Available":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Deal status is '{deal.status}', must be 'Available' to launch a campaign",
        )

    # 2. Idempotency check: prevent duplicate launch for the same deal
    existing = await db.execute(
        select(Campaign).where(Campaign.deal_id == deal_id).limit(1)
    )
    if existing.scalar_one_or_none():
        total_result = await db.execute(
            select(Campaign).where(Campaign.deal_id == deal_id)
        )
        all_existing = total_result.scalars().all()
        logger.warning(
            "Duplicate campaign launch attempt for deal %s (%s) — %d campaigns already exist",
            deal_id, deal.address, len(all_existing),
        )
        return {
            "status": "already_launched",
            "deal_id": str(deal_id),
            "deal_address": deal.address,
            "campaigns_count": len(all_existing),
        }

    # 3. Check deal has embedding for matching
    if deal.deal_embedding is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deal has no embedding. Re-create the deal to generate one.",
        )

    # 3a. Predictive JV Flagging: check JV partner reliability before launch
    jv_warning = None
    jv_partner = None
    if deal.jv_partner_id:
        jv_result = await db.execute(
            select(JVPartner).where(JVPartner.id == deal.jv_partner_id)
        )
        jv_partner = jv_result.scalar_one_or_none()
        if jv_partner:
            jv_warnings = []
            if (jv_partner.overprice_flag_count or 0) > 3:
                jv_warnings.append(f"JV partner has {jv_partner.overprice_flag_count} overprice flags")
            if (jv_partner.title_issue_rate or 0) > 0.2:
                jv_warnings.append(f"JV partner title issue rate is {jv_partner.title_issue_rate:.0%}")
            if jv_warnings:
                jv_warning = "; ".join(jv_warnings)
                logger.warning(
                    "Predictive JV flagging for deal %s (%s): %s",
                    deal_id, deal.address, jv_warning,
                )
                # Add warning to deal notes
                existing_notes = deal.notes or ""
                warning_note = f"[JV WARNING {datetime.now(timezone.utc).strftime('%Y-%m-%d')}]: {jv_warning}"
                if warning_note not in existing_notes:
                    deal.notes = f"{existing_notes}\n{warning_note}".strip()
                    db.add(deal)

    # 3b. Calculate Deal Priority Score
    days_since_upload = (datetime.now(timezone.utc) - deal.created_at).days
    spread_value = float(deal.spread) if deal.spread else 0
    jv_reliability_score = 100
    if jv_partner is not None:
        jv_reliability_score = max(0, 100 - (jv_partner.title_issue_rate or 0) * 100)
    buyer_match_count = 0  # Will be set after matching

    # 3. Run semantic matching (same logic as matching router)
    clean_embedding = [float(x) for x in deal.deal_embedding]
    embedding_str = str(clean_embedding)

    # Enhanced SQL: fetch more fields for smart filtering + fatigue + eligibility
    sql = text("""
        SELECT
            b.id,
            b.full_name,
            b.email,
            b.buy_box,
            b.buyer_tier,
            b.engagement_score,
            b.last_pitch_sent_at,
            b.pitches_this_week,
            GREATEST(0, 1 - (b.buy_box_embedding <=> :deal_embedding)) AS similarity
        FROM buyers b
        WHERE b.status = 'Active'
          AND b.email_verified = TRUE
          AND b.buy_box_embedding IS NOT NULL
          AND b.unsubscribed_at IS NULL
        ORDER BY b.buy_box_embedding <=> CAST(:deal_embedding AS vector)
        LIMIT :limit
    """)

    rows = await db.execute(
        sql,
        {
            "deal_embedding": embedding_str,
            "limit": match_limit * 2,  # Fetch extra to account for filtering
        },
    )
    all_matched = rows.fetchall()
    buyer_match_count = len(all_matched)

    # Calculate priority score
    deal.priority_score = (
        (spread_value / 1000) * 0.4 +
        (buyer_match_count * 10) * 0.3 +
        (100 - jv_reliability_score) * 0.2 +
        (days_since_upload * 5) * 0.1
    )
    db.add(deal)

    if not all_matched:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matched buyers found for this deal. Create buyers with buy_box embeddings first.",
        )

    # Apply Smart Buyer Filtering (feature 1): engagement, recency, C-List rules
    eligible_buyers = []
    skipped_buyers = []
    for row in all_matched:
        allowed, reason = await assess_buyer_eligibility(
            row, deal.created_at, days_since_upload,
        )
        if allowed:
            eligible_buyers.append(row)
        else:
            skipped_buyers.append((row, reason))

    if skipped_buyers:
        logger.info(
            "Smart filtering skipped %d buyers: %s",
            len(skipped_buyers),
            "; ".join(f"{r[0].email}: {r[1]}" for r in skipped_buyers[:5]),
        )

    # Apply Buyer Fatigue Protection (feature 8)
    fatigue_skipped = []
    final_buyers = []
    for row in eligible_buyers:
        allowed, reason = await check_fatigue_protection(
            # Create a minimal Buyer-like object for the check
            row
        )
        if allowed:
            final_buyers.append(row)
        else:
            fatigue_skipped.append((row, reason))

    if fatigue_skipped:
        logger.info(
            "Fatigue protection skipped %d buyers: %s",
            len(fatigue_skipped),
            "; ".join(f"{r[0].email}: {r[1]}" for r in fatigue_skipped[:5]),
        )

    if not final_buyers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"All {len(all_matched)} matched buyers filtered out by eligibility or fatigue rules. Deal may need older buyers or manual override.",
        )

    # Sort by tier for Staggered Campaign Launch (feature 7): A-List first
    tier_order = {"A-List": 0, "B-List": 1, "C-List": 2}
    final_buyers.sort(key=lambda r: tier_order.get(getattr(r, 'buyer_tier', 'C-List') or 'C-List', 2))

    logger.info(
        "Launching campaign for deal %s (%s) with %d buyers (%d eligible after filtering, %d after fatigue)",
        deal_id, deal.address, len(final_buyers), len(eligible_buyers), len(final_buyers),
    )

    # Count tiers for staggered launch
    tier_counts = {"A-List": 0, "B-List": 0, "C-List": 0}
    for row in final_buyers:
        tier = getattr(row, 'buyer_tier', 'C-List') or 'C-List'
        if tier in tier_counts:
            tier_counts[tier] += 1
        else:
            tier_counts[tier] = 1

    # 4. Generate campaigns for each buyer (staggered)
    launch_time = datetime.now(timezone.utc)
    all_results: List[CampaignLaunchResult] = []
    total_touches_created = 0

    # Track which touch 1 campaigns need staggered scheduling
    touch_1_delayed = []  # B-List and C-List buyers whose touch 1 should be delayed

    for row in final_buyers:
        buyer_touches = []
        touch_records = []

        for config in TOUCH_CONFIGS:
            touch_num = config["touch"]

            # Generate email via Groq (with unsubscribe footer)
            email_data = await generate_touch_email(
                touch=touch_num,
                buyer_name=row.full_name,
                buyer_email=row.email,
                buy_box=row.buy_box,
                buyer_tier=row.buyer_tier,
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
                buyer_id=row.id,
            )

            # Calculate scheduled send time based on touch timing
            scheduled = launch_time + timedelta(days=config["delay_days"])

            # Staggered launch (feature 7):
            # - Day 0: A-List touch 1 sends immediately
            # - Day 1: B-List touch 1 gets scheduled for day 1
            # - Day 3: C-List touch 1 gets scheduled for day 3
            buyer_tier = getattr(row, 'buyer_tier', 'C-List') or 'C-List'
            if touch_num == 1:
                if buyer_tier == "A-List":
                    touch_status = "Sent"  # Send immediately
                    scheduled_send = launch_time  # Will send now
                elif buyer_tier == "B-List":
                    touch_status = "Queued"
                    scheduled_send = launch_time + timedelta(days=1)
                else:  # C-List
                    touch_status = "Queued"
                    scheduled_send = launch_time + timedelta(days=3)
            else:
                touch_status = "Queued" if touch_num > 1 else "Sent"
                # For B-List and C-List, their touch 2+ offsets are relative to
                # when their touch 1 fires, not the original launch time
                base_time = launch_time
                if buyer_tier == "B-List":
                    base_time = launch_time + timedelta(days=1)
                elif buyer_tier == "C-List":
                    base_time = launch_time + timedelta(days=3)
                scheduled_send = base_time + timedelta(days=config["delay_days"])

            # Create Campaign DB record
            campaign_record = Campaign(
                id=uuid.uuid4(),
                deal_id=deal_id,
                buyer_id=row.id,
                touch_number=touch_num,
                status=touch_status,
                subject=email_data.get("subject", ""),
                body=email_data.get("body", ""),
                scheduled_send_at=scheduled_send,
            )

            # Send touch 1 immediately (A-List only)
            if touch_num == 1 and touch_status == "Sent":
                try:
                    await send_email(
                        to=row.email,
                        subject=email_data.get("subject", ""),
                        body=email_data.get("body", ""),
                        campaign_id=campaign_record.id.hex,
                    )
                    campaign_record.sent_at = datetime.now(timezone.utc)

                    # Increment fatigue counter for this buyer
                    buyer_obj = await db.get(Buyer, row.id)
                    if buyer_obj:
                        await increment_pitch_count(db, buyer_obj)
                except Exception as e:
                    logger.warning("Failed to auto-send touch 1 for buyer %s: %s", row.id, e, exc_info=True)
                    campaign_record.status = "Queued"
            touch_records.append(campaign_record)
            total_touches_created += 1

            buyer_touches.append(CampaignTouch(
                touch=touch_num,
                subject=email_data.get("subject", ""),
                body=email_data.get("body", ""),
                status=email_data.get("status", "Queued"),
                scheduled_at=scheduled.isoformat(),
            ))

        # Save all touches for this buyer
        db.add_all(touch_records)

        all_results.append(CampaignLaunchResult(
            buyer_id=row.id,
            buyer_name=row.full_name,
            buyer_email=row.email,
            buyer_tier=row.buyer_tier,
            similarity_score=float(row.similarity),
            touches=buyer_touches,
        ))

    # 5. Update deal status
    deal.status = "Campaign Launched"
    db.add(deal)

    # Commit everything
    await db.commit()

    logger.info(
        "Campaign launched for deal %s: %d buyers, %d total touches",
        deal_id, len(final_buyers), total_touches_created,
    )

    return CampaignLaunchResponse(
        deal_id=deal_id,
        deal_address=deal.address,
        total_buyers=len(final_buyers),
        total_campaigns_created=total_touches_created,
        results=all_results,
    )


@router.post("/{campaign_id}/send", response_model=SendResponse)
async def send_campaign_email(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Send a single campaign email.

    Fetches the campaign by ID, verifies its status is "Ready" or "Queued",
    sends the email via Gmail SMTP, and updates the campaign status to "Sent".
    """
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign with id '{campaign_id}' not found",
        )

    if campaign.status not in ("Ready", "Queued"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Campaign status is '{campaign.status}', must be 'Ready' or 'Queued' to send",
        )

    if not campaign.subject or not campaign.body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign has no subject or body content",
        )

    # Fetch buyer to get their email address
    buyer_result = await db.execute(select(Buyer).where(Buyer.id == campaign.buyer_id))
    buyer = buyer_result.scalar_one_or_none()
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Buyer with id '{campaign.buyer_id}' not found for this campaign",
        )

    # Duplicate prevention: check if this exact email was already sent to this buyer
    existing = await db.execute(
        select(Campaign)
        .where(Campaign.buyer_id == campaign.buyer_id)
        .where(Campaign.deal_id == campaign.deal_id)
        .where(Campaign.touch_number == campaign.touch_number)
        .where(Campaign.status == "Sent")
    )
    if existing.scalar_one_or_none():
        logger.warning(
            "Duplicate send attempt for campaign %s — buyer %s, deal %s, touch %d already sent",
            campaign_id, campaign.buyer_id, campaign.deal_id, campaign.touch_number,
        )
        return SendResponse(
            campaign_id=campaign_id,
            to_email=buyer.email,
            subject=campaign.subject,
            message_id="",
            status="already_sent",
            sent_at="",
        )

    # Send the email
    try:
        send_result = await send_email(
            to=buyer.email,
            subject=campaign.subject,
            body=campaign.body,
            campaign_id=campaign.id.hex,
        )

        # Update campaign status
        campaign.status = "Sent"
        campaign.sent_at = datetime.now(timezone.utc)
        db.add(campaign)
        await db.commit()

        logger.info(
            "Campaign %s (touch %d) sent to %s — message_id: %s",
            campaign_id, campaign.touch_number, buyer.email, send_result["message_id"],
        )

        return SendResponse(
            campaign_id=campaign_id,
            to_email=buyer.email,
            subject=campaign.subject,
            message_id=send_result["message_id"],
            status=send_result["status"],
            sent_at=send_result["sent_at"],
        )

    except (smtplib.SMTPException, ValueError) as e:
        # Move to dead letter queue
        try:
            await move_to_dlq(db, campaign, str(e))
            await db.commit()
        except Exception as dlq_err:
            logger.error("Failed to move campaign %s to DLQ: %s", campaign.id, dlq_err, exc_info=True)

        error_msg = str(e)[:200]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Email send failed after retries: {error_msg}",
        )


@router.post("/{deal_id}/send-all", response_model=SendAllResponse)
async def send_all_campaigns(
    deal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Send all 'Ready' campaign emails for a deal.

    Fetches all campaigns for the given deal where status = "Ready",
    sends each one via Gmail SMTP, and updates each to "Sent".
    Failed sends are collected and returned without interrupting the batch.
    """
    # Verify deal exists
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal with id '{deal_id}' not found",
        )

    # Fetch all Ready campaigns for this deal
    result = await db.execute(
        select(Campaign).where(
            Campaign.deal_id == deal_id,
            Campaign.status == "Ready",
        )
    )
    campaigns = result.scalars().all()

    if not campaigns:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No 'Ready' campaigns found for deal '{deal_id}'",
        )

    # Pre-fetch all buyers to avoid N+1 queries
    buyer_ids = list(set(c.buyer_id for c in campaigns))
    buyer_result = await db.execute(
        select(Buyer).where(Buyer.id.in_(buyer_ids))
    )
    buyers_map = {b.id: b for b in buyer_result.scalars().all()}

    sent_count = 0
    failed_count = 0
    items: List[SendAllItem] = []

    for campaign in campaigns:
        buyer = buyers_map.get(campaign.buyer_id)
        if not buyer:
            items.append(SendAllItem(
                campaign_id=campaign.id,
                touch_number=campaign.touch_number,
                to_email="unknown",
                status="failed",
                error=f"Buyer {campaign.buyer_id} not found",
            ))
            failed_count += 1
            continue

        try:
            send_result = await send_email(
                to=buyer.email,
                subject=campaign.subject or "",
                body=campaign.body or "",
                campaign_id=campaign.id.hex,
            )

            campaign.status = "Sent"
            campaign.sent_at = datetime.now(timezone.utc)
            db.add(campaign)

            items.append(SendAllItem(
                campaign_id=campaign.id,
                touch_number=campaign.touch_number,
                to_email=buyer.email,
                status="sent",
                message_id=send_result["message_id"],
            ))
            sent_count += 1

            logger.info(
                "Sent campaign %s (touch %d) to %s",
                campaign.id, campaign.touch_number, buyer.email,
            )

        except Exception as e:
            logger.warning(
                "Failed to send campaign %s (touch %d) to %s: %s",
                campaign.id, campaign.touch_number, buyer.email, e, exc_info=True,
            )
            items.append(SendAllItem(
                campaign_id=campaign.id,
                touch_number=campaign.touch_number,
                to_email=buyer.email,
                status="failed",
                error=str(e)[:200],
            ))
            failed_count += 1

    await db.commit()

    logger.info(
        "Send-all for deal %s: %d sent, %d failed out of %d",
        deal_id, sent_count, failed_count, len(campaigns),
    )

    return SendAllResponse(
        deal_id=deal_id,
        total_ready=len(campaigns),
        sent_count=sent_count,
        failed_count=failed_count,
        results=items,
    )
