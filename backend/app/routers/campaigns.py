"""Campaign management router — launch 6-touch email campaigns for a deal."""

import logging
import smtplib
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, async_session_factory
from app.models.models import ActivityLog, Buyer, BuyerEmail, Campaign, Deal, JVPartner
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
from app.services.campaign_launcher import launch_campaign_for_buyer
from app.services.gmail_monitor import check_for_replies
from app.services.gmail_service import send_email
from app.services.dead_letter_queue import move_to_dlq
from app.services.reply_processor import process_reply, extract_buybox_changes, get_question_round_message, detect_uncertainty_and_hold, match_reply_to_campaign

from app.services.buyer_scoring import assess_buyer_eligibility, check_fatigue_protection
from app.services.buyer_merge import merge_buy_boxes
from app.services.matching_service import (
    find_top_matches_for_deal,
    invalidate_queued_matches_for_buyer,
    process_queued_matches,
)
from app.services.negotiation import handle_counter_offer
from app.services.audit_logger import audit
from app.services.parse_buy_box import parse_buy_box

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

        # 4. Match reply to the correct campaign (thread-aware priority chain)
        campaign, confidence_level = await match_reply_to_campaign(db, buyer_id, reply)

        if not campaign:
            # Classify anyway for logging, but skip further processing
            classification = await process_reply(reply)
            results.append(ReplyCheckItem(
                from_email=from_email,
                subject=reply["subject"],
                reply_intent=classification["reply_intent"],
                buyer_id=buyer_id,
                matched=False,
                error="No sent campaign found for this buyer",
            ))
            continue

        # Log the match method used
        logger.info(
            "Reply from buyer %s matched to campaign %s via %s (deal: %s)",
            buyer_id, campaign.id, confidence_level, campaign.deal_id,
        )

        # 5. Classify the reply via Groq (ghost recovery cancelled inside if needed)
        classification = await process_reply(
            reply,
            db=db,
            buyer_id=buyer_id,
            deal_id=campaign.deal_id,
        )

        # Set match_confidence on fallback matches
        if confidence_level == "fallback":
            classification["match_confidence"] = "low"

        # ── Send pass reason follow-up question if confidence is low ──
        pass_reason_followup = classification.get("pass_reason_followup")
        if pass_reason_followup:
            try:
                buyer_obj_for_send = await db.get(Buyer, buyer_id)
                if buyer_obj_for_send and buyer_obj_for_send.email:
                    await send_email(
                        to=buyer_obj_for_send.email,
                        subject=f"Re: {reply.get('subject', '')}",
                        body=pass_reason_followup,
                        send_type="reply",
                    )
                    logger.info(
                        "Pass reason follow-up sent to buyer %s on deal %s",
                        buyer_id, campaign.deal_id,
                    )
            except Exception as followup_err:
                logger.warning(
                    "Failed to send pass reason follow-up to buyer %s: %s",
                    buyer_id, followup_err, exc_info=True,
                )

        # 6. Update the campaign with reply data
        campaign.reply_received_at = datetime.now(timezone.utc)
        campaign.reply_body = reply["body"]
        campaign.reply_intent = classification["reply_intent"]
        campaign.ai_extracted_insights = classification["ai_extracted_insights"]
        # FEATURE 2: Pass intent is terminal — frees buyer's active slot
        if classification["reply_intent"] == "Pass":
            campaign.status = "Passed"
        else:
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
                    # Regenerate embedding via local model
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

                    # Re-parse structured fields from merged buy_box
                    try:
                        parsed = await parse_buy_box(merged_buy_box)
                        buyer_obj.price_min = parsed.get("price_min")
                        buyer_obj.price_max = parsed.get("price_max")
                        buyer_obj.pref_property_type = parsed.get("pref_property_type")
                        buyer_obj.pref_cities = parsed.get("pref_cities")
                    except Exception as parse_err:
                        logger.warning("Failed to re-parse buy_box for buyer %s: %s", buyer_id, parse_err, exc_info=True)

                    # Invalidate queued matches since preferences changed
                    try:
                        await invalidate_queued_matches_for_buyer(db, buyer_id)
                    except Exception as inv_err:
                        logger.warning("Failed to invalidate queued matches for buyer %s: %s", buyer_id, inv_err, exc_info=True)

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
                    elif negotiation_result.get("action") == "escalated":
                        # Below floor — create escalation alert, send NO reply
                        await audit.log(
                            db,
                            entity_type="deal",
                            entity_id=campaign.deal_id,
                            action="negotiation_escalation",
                            metadata={
                                "alert_user": True,
                                "priority": "high",
                                "action_required": "Review and respond to below-floor counter",
                                "buyer_id": str(buyer_id),
                                "deal_id": str(campaign.deal_id),
                                "campaign_id": str(campaign.id),
                                "counter_price": counter_price,
                                "floor_price": negotiation_result["floor_price"],
                                "gap": negotiation_result["floor_price"] - counter_price,
                                "buyer_name": buyer_obj.full_name if buyer_obj else "",
                                "buyer_email": buyer_obj.email if buyer_obj else "",
                                "deal_address": deal.address if deal else "",
                            },
                        )
                        # Set campaign to negotiating so operator sees the flag
                        campaign.conversation_stage = "negotiating"
                        campaign.reply_intent = "negotiating"
                        db.add(campaign)
                        logger.info(
                            "Negotiation escalation for deal %s: $%.2f below floor $%.2f — awaiting operator decision",
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
            # ── FEATURE 2: Uncertainty detection — graceful hold if AI can't answer ──
            hold_response = await detect_uncertainty_and_hold(
                reply=reply,
                classification=classification,
                db_session=db,
                buyer_id=buyer_id,
                deal_id=campaign.deal_id,
            )
            if hold_response:
                # Use holding response instead of AI draft
                question_answer = hold_response
                # Still log that a hold was sent
                try:
                    await audit.log(
                        db,
                        entity_type="campaign",
                        entity_id=campaign.id,
                        action="uncertainty_hold_sent",
                        metadata={
                            "hold_response": hold_response[:200],
                            "buyer_id": str(buyer_id),
                            "deal_id": str(campaign.deal_id),
                        },
                    )
                except Exception as hold_err:
                    logger.warning("Failed to log hold response: %s", hold_err, exc_info=True)
            else:
                question_answer = classification.get("question_answer")

            current_round = campaign.question_round or 0
            new_round = current_round + 1
            campaign.question_round = new_round
            db.add(campaign)

            round_action = get_question_round_message(new_round)

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

        # 10b. Interested buyer → notify other active buyers on same deal
        # The contract alert is created inside process_reply(). Here we send
        # holding emails to all OTHER buyers still being pitched on this deal.
        if classification["reply_intent"] == "Interested":
            try:
                deal = await db.get(Deal, campaign.deal_id)
                if deal:
                    # Find other active campaigns for same deal (exclude closing buyer)
                    other_campaigns_result = await db.execute(
                        select(Campaign)
                        .where(
                            Campaign.deal_id == campaign.deal_id,
                            Campaign.buyer_id != buyer_id,
                            Campaign.status.in_(["Sent", "Replied"]),
                        )
                    )
                    other_campaigns = other_campaigns_result.scalars().all()

                    # De-duplicate by buyer_id
                    notified_buyer_ids = set()
                    for other_c in other_campaigns:
                        other_buyer_id = other_c.buyer_id
                        if other_buyer_id in notified_buyer_ids:
                            continue
                        notified_buyer_ids.add(other_buyer_id)

                        try:
                            other_buyer = await db.get(Buyer, other_buyer_id)
                            if not other_buyer or not other_buyer.email:
                                continue
                            if other_buyer.unsubscribed_at:
                                continue

                            # Generate brief holding email
                            holding_body = (
                                f"Hi {other_buyer.full_name},\n\n"
                                f"Quick update on {deal.address} — we've received strong interest "
                                f"on this property and have moved to contract with another buyer. "
                                f"We'll keep you posted if anything changes — appreciate your time.\n\n"
                                f"{settings.operator_signature}"
                            )

                            # Validate via AI validator
                            try:
                                from app.services.ai_validator import validate_ai_output
                                validation = await validate_ai_output(
                                    content=holding_body,
                                    content_type="reply_email",
                                    deal=deal,
                                    buyer=other_buyer,
                                )
                                if validation.severity != "block":
                                    holding_body = (
                                        validation.corrected_content or holding_body
                                    )
                            except Exception as e:
                                logger.warning("Holding email validation failed for buyer %s: %s", other_buyer_id, e)

                            await send_email(
                                to=other_buyer.email,
                                subject=f"Update on {deal.address}",
                                body=holding_body,
                                campaign_id=campaign.id.hex,
                                send_type="reply",
                            )

                            # Pause their remaining queued campaigns for this deal
                            queued_other = await db.execute(
                                select(Campaign)
                                .where(
                                    Campaign.buyer_id == other_buyer_id,
                                    Campaign.deal_id == campaign.deal_id,
                                    Campaign.status == "Queued",
                                )
                            )
                            for qc in queued_other.scalars().all():
                                qc.status = "Paused"
                                db.add(qc)

                            logger.info(
                                "Holding email sent to buyer %s for deal %s "
                                "(interested buyer: %s)",
                                other_buyer_id, campaign.deal_id, buyer_id,
                            )
                        except Exception as notify_err:
                            logger.warning(
                                "Failed to notify buyer %s about deal %s closing: %s",
                                other_buyer_id, campaign.deal_id, notify_err,
                                exc_info=True,
                            )

                    if notified_buyer_ids:
                        logger.info(
                            "Notified %d other buyer(s) on deal %s about closing",
                            len(notified_buyer_ids), campaign.deal_id,
                        )

            except Exception as e:
                logger.warning(
                    "Failed to notify other buyers for deal %s: %s",
                    campaign.deal_id, e, exc_info=True,
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

    # FEATURE 2: Event-driven queued match release
    # After buyers pass (Pass intent), immediately process their
    # queued matches so they can be matched to other deals.
    for result_item in results:
        if result_item.reply_intent == "Pass" and result_item.buyer_id:
            try:
                async with async_session_factory() as release_db:
                    released = await process_queued_matches(
                        release_db, buyer_id=result_item.buyer_id,
                    )
                    if released > 0:
                        await release_db.commit()
            except Exception as release_err:
                logger.warning(
                    "Failed to process queued matches for buyer %s "
                    "after Pass reply: %s",
                    result_item.buyer_id, release_err, exc_info=True,
                )

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
    if deal.jv_partner_id and settings.jv_rotator_enabled:
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
                # Log warning to activity log (Deal model has no notes column)
                logger.warning("JV partner warning for deal %s: %s", deal_id, jv_warning)

    # 3b. Calculate Deal Priority Score
    days_since_upload = (datetime.now(timezone.utc) - deal.created_at).days
    spread_value = float(deal.asking_price - deal.contract_price) if deal.asking_price and deal.contract_price else 0
    jv_reliability_score = 100
    if jv_partner is not None:
        jv_reliability_score = max(0, 100 - (jv_partner.title_issue_rate or 0) * 100)
    buyer_match_count = 0  # Will be set after matching

    # 3. Run semantic matching with hard filters + similarity threshold + max-2-active-deals
    match_result = await find_top_matches_for_deal(
        db=db,
        deal=deal,
        limit=match_limit,
    )
    all_matched = match_result.matches
    buyer_match_count = len(match_result.matches)

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

    # Fetch full Buyer records for eligibility/fatigue checks (BuyerMatchResult
    # only has basic fields, not engagement_score/pitches_this_week)
    matched_buyer_ids = [m.id for m in all_matched]
    buyer_records = await db.execute(
        select(Buyer).where(Buyer.id.in_(matched_buyer_ids))
    )
    buyer_map = {b.id: b for b in buyer_records.scalars().all()}

    # Build list of (match_result, buyer_object) tuples — deduplicated by buyer_id
    matched_pairs = []
    seen_buyer_ids = set()
    for m in all_matched:
        if m.id in seen_buyer_ids:
            logger.debug("Deduplicating buyer %s from matched pairs", m.id)
            continue
        seen_buyer_ids.add(m.id)
        b = buyer_map.get(m.id)
        if b:
            matched_pairs.append((m, b))

    if not matched_pairs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matched buyers with full records found.",
        )

    # Apply Smart Buyer Filtering (feature 1): engagement, recency, C-List rules
    eligible_pairs = []
    skipped_buyers = []
    for match, buyer in matched_pairs:
        allowed, reason = await assess_buyer_eligibility(
            buyer, deal.created_at, days_since_upload,
        )
        if allowed:
            eligible_pairs.append((match, buyer))
        else:
            skipped_buyers.append((match, reason))

    if skipped_buyers:
        logger.info(
            "Smart filtering skipped %d buyers: %s",
            len(skipped_buyers),
            "; ".join(f"{m.email}: {r}" for m, r in skipped_buyers[:5]),
        )

    # Apply Buyer Fatigue Protection (feature 8)
    fatigue_skipped = []
    final_pairs = []
    for match, buyer in eligible_pairs:
        allowed, reason = await check_fatigue_protection(buyer)
        if allowed:
            final_pairs.append((match, buyer))
        else:
            fatigue_skipped.append((match, reason))

    if fatigue_skipped:
        logger.info(
            "Fatigue protection skipped %d buyers: %s",
            len(fatigue_skipped),
            "; ".join(f"{m.email}: {r}" for m, r in fatigue_skipped[:5]),
        )

    if not final_pairs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"All {len(all_matched)} matched buyers filtered out by eligibility or fatigue rules. Deal may need older buyers or manual override.",
        )

    # FEATURE 1: 50 Verified Buyer Minimum Gate
    # If fewer than the configured minimum verified matched buyers are
    # available, block the launch and alert via activity log.
    # Count verified buyers BEFORE fatigue filtering — fatigue is a sending
    # delay, not a disqualification. Only hard-fail buyers (failed filters,
    # at 2-deal cap) are excluded from this threshold.
    verified_count = len(eligible_pairs)
    if verified_count < settings.min_verified_buyers_to_launch:
        logger.warning(
            "Campaign launch blocked for deal %s (%s): only %d eligible verified "
            "matched buyers (need %d) — %d in fatigue cooldown (excluded from "
            "threshold)",
            deal_id, deal.address, verified_count,
            settings.min_verified_buyers_to_launch,
            len(fatigue_skipped),
        )
        # Log to activity log so it surfaces on the dashboard
        await audit.log(
            db,
            entity_type="deal",
            entity_id=deal_id,
            action="campaign_launch_blocked",
            metadata={
                "reason": "insufficient_verified_buyers",
                "eligible_verified_matched": verified_count,
                "fatigue_skipped": len(fatigue_skipped),
                "required": settings.min_verified_buyers_to_launch,
                "deal_address": deal.address,
                "deal_id": str(deal_id),
                "severity": "warning",
                "alert_user": True,
            },
        )
        await db.commit()
        return JSONResponse(
            status_code=200,
            content={
                "launched": False,
                "reason": "insufficient_verified_buyers",
                "eligible_verified_matched": verified_count,
                "fatigue_skipped": len(fatigue_skipped),
                "required": settings.min_verified_buyers_to_launch,
                "message": (
                    f"Add more buyers matching this deal before pitching. "
                    f"Currently {verified_count} eligible verified buyers match "
                    f"(threshold excludes {len(fatigue_skipped)} in fatigue cooldown)."
                ),
            },
        )

    # Sort by tier for Staggered Campaign Launch (feature 7): A-List first
    tier_order = {"A-List": 0, "B-List": 1, "C-List": 2}
    final_pairs.sort(key=lambda p: tier_order.get(p[0].buyer_tier or 'C-List', 2))

    logger.info(
        "Launching campaign for deal %s (%s) with %d buyers (%d eligible after filtering, %d after fatigue)",
        deal_id, deal.address, len(final_pairs), len(eligible_pairs), len(final_pairs),
    )

    # Count tiers for staggered launch
    tier_counts = {"A-List": 0, "B-List": 0, "C-List": 0}
    for match, _ in final_pairs:
        tier = match.buyer_tier or 'C-List'
        if tier in tier_counts:
            tier_counts[tier] += 1

    # 4. Generate campaigns for each buyer via shared function
    # Re-fetch deal fresh to avoid MissingGreenlet on expired ORM attributes
    _fresh = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = _fresh.scalar_one()
    all_results: List[CampaignLaunchResult] = []
    total_touches_created = 0

    for match, buyer in final_pairs:
        launch_result = await launch_campaign_for_buyer(
            db=db,
            buyer=buyer,
            deal=deal,
            similarity_score=float(match.similarity),
        )

        if launch_result["success"]:
            total_touches_created += launch_result["touches_created"]
            buyer_touches = [
                CampaignTouch(
                    touch=t["touch"],
                    subject=t["subject"],
                    body=t["body"],
                    status=t["status"],
                    scheduled_at=t["scheduled_send_at"],
                )
                for t in launch_result["touches"]
            ]
        else:
            buyer_touches = []

        all_results.append(CampaignLaunchResult(
            buyer_id=match.id,
            buyer_name=match.full_name,
            buyer_email=match.email,
            buyer_tier=match.buyer_tier,
            similarity_score=float(match.similarity),
            touches=buyer_touches,
        ))

    # 5. Update deal status
    deal.status = "Campaign Launched"
    db.add(deal)
    _deal_address = deal.address  # cache before commit (avoids MissingGreenlet after rollback)

    # Commit everything — catch race condition duplicate inserts gracefully
    try:
        await db.commit()
    except Exception as commit_err:
        if "uq_campaigns_buyer_deal_touch" in str(commit_err) or "UniqueViolation" in str(commit_err):
            await db.rollback()
            logger.warning("Campaign launch race condition detected for deal %s — campaigns already exist", deal_id)
            return CampaignLaunchResponse(
                deal_id=deal_id,
                deal_address=_deal_address,
                total_buyers=0,
                total_campaigns_created=0,
                results=[],
                status="already_launched",
            )
        raise

    logger.info(
        "Campaign launched for deal %s: %d buyers, %d total touches",
        deal_id, len(final_pairs), total_touches_created,
    )

    return CampaignLaunchResponse(
        deal_id=deal_id,
        deal_address=_deal_address,
        total_buyers=len(final_pairs),
        total_campaigns_created=total_touches_created,
        results=all_results,
    )


@router.post("/{campaign_id}/manual-reply")
async def manual_reply(
    campaign_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Send a manual reply to a buyer's campaign conversation.

    Allows the operator to craft a custom reply that is sent directly to
    the buyer via Gmail. Only available on campaigns with Sent or Replied status.

    Args:
        campaign_id: UUID of the campaign to reply on.
        body: JSON body with "message" field containing the reply text.

    Returns:
        dict with success status and recipient email.
    """
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="message field is required and cannot be empty",
        )

    # 1. Load campaign
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign with id '{campaign_id}' not found",
        )

    if campaign.status not in ("Sent", "Replied"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Campaign status is '{campaign.status}', must be 'Sent' or 'Replied' to send a manual reply",
        )

    # 2. Load buyer email
    buyer_result = await db.execute(select(Buyer).where(Buyer.id == campaign.buyer_id))
    buyer = buyer_result.scalar_one_or_none()
    if not buyer or not buyer.email:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Buyer with id '{campaign.buyer_id}' not found or has no email",
        )

    # 3. Send email
    reply_subject = f"Re: {campaign.subject}" if campaign.subject else "Re: Your inquiry"
    reply_body = f"{message}\n\nBest,\n{settings.operator_name}"

    try:
        send_result = await send_email(
            to=buyer.email,
            subject=reply_subject,
            body=reply_body,
            campaign_id=campaign.id.hex,
            send_type="reply",
        )

        if send_result.get("status") != "sent":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Email send failed: {send_result.get('reason', 'unknown error')}",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to send manual reply for campaign %s: %s", campaign_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to send email: {str(e)[:200]}",
        )

    # 4. Update campaign
    campaign.reply_body = f"MANUAL: {message}"
    if campaign.conversation_stage == "pitching":
        campaign.conversation_stage = "replied"
    db.add(campaign)

    # 5. Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="campaign",
        entity_id=campaign.id,
        action="manual_reply_sent",
        metadata_json={
            "sent_to": buyer.email,
            "subject": reply_subject,
            "buyer_id": str(campaign.buyer_id),
            "deal_id": str(campaign.deal_id),
        },
    )
    db.add(log_entry)

    await db.commit()

    logger.info(
        "Manual reply sent for campaign %s (touch %d) to %s",
        campaign_id, campaign.touch_number, buyer.email,
    )

    return {"success": True, "sent_to": buyer.email}


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
            send_type="campaign",
        )

        if send_result.get("status") == "deferred_cap":
            logger.info(
                "Campaign %s (touch %d) deferred — daily cap reached",
                campaign_id, campaign.touch_number,
            )
            return SendResponse(
                campaign_id=campaign_id,
                to_email=buyer.email,
                subject=campaign.subject,
                message_id="",
                status="deferred_cap",
                sent_at="",
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
                send_type="campaign",
            )

            if send_result.get("status") == "deferred_cap":
                items.append(SendAllItem(
                    campaign_id=campaign.id,
                    touch_number=campaign.touch_number,
                    to_email=buyer.email,
                    status="deferred_cap",
                    error="Daily send cap reached",
                ))
                sent_count += 1  # Count as handled, not failed
                continue

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
        failed_count=failed_count,            results=items,
    )


# ---------------------------------------------------------------------------
# Campaign pause / resume
# ---------------------------------------------------------------------------


@router.post("/{deal_id}/pause")
async def pause_campaigns(
    deal_id: uuid.UUID,
    body: dict = {},
    db: AsyncSession = Depends(get_db),
):
    """Pause all Queued campaigns for a deal and set deal status to Paused."""
    reason = body.get("reason", "") if isinstance(body, dict) else ""

    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    # Find all Queued campaigns for this deal
    queued_result = await db.execute(
        select(Campaign).where(
            Campaign.deal_id == deal_id,
            Campaign.status == "Queued",
        )
    )
    queued_campaigns = queued_result.scalars().all()

    paused_count = 0
    for c in queued_campaigns:
        c.status = "Paused"
        db.add(c)
        paused_count += 1

    deal.status = "Paused"
    db.add(deal)

    # Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="deal",
        entity_id=deal_id,
        action="campaign_paused",
        metadata_json={
            "paused_count": paused_count,
            "reason": reason if reason else "manual_pause",
            "deal_address": deal.address,
        },
    )
    db.add(log_entry)

    await db.commit()

    logger.info(
        "Paused %d campaigns for deal %s (%s)%s",
        paused_count, deal_id, deal.address,
        f" — reason: {reason}" if reason else "",
    )

    return {"paused_count": paused_count}


@router.post("/{deal_id}/resume")
async def resume_campaigns(
    deal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Resume all Paused campaigns for a deal and set deal back to Campaign Launched."""
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    # Find all Paused campaigns for this deal
    paused_result = await db.execute(
        select(Campaign).where(
            Campaign.deal_id == deal_id,
            Campaign.status == "Paused",
        )
    )
    paused_campaigns = paused_result.scalars().all()

    resumed_count = 0
    for c in paused_campaigns:
        c.status = "Queued"
        db.add(c)
        resumed_count += 1

    deal.status = "Campaign Launched"
    db.add(deal)

    # Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="deal",
        entity_id=deal_id,
        action="campaign_resumed",
        metadata_json={
            "resumed_count": resumed_count,
            "deal_address": deal.address,
        },
    )
    db.add(log_entry)

    await db.commit()

    logger.info(
        "Resumed %d campaigns for deal %s (%s)",
        resumed_count, deal_id, deal.address,
    )

    return {"resumed_count": resumed_count}