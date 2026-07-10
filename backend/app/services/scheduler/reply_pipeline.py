"""Buyer reply pipeline — fetches Gmail replies, classifies via AI, updates campaign state."""

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

import app.database as _db
from app.models.models import ActivityLog, Buyer, Campaign, Deal, JVPartner, BuyerEmail
from app.services.gmail_monitor import check_for_replies
from app.services.gmail_service import send_email
from app.services.conversation_engine import process_conversation
from app.services.reply_processor import match_reply_to_campaign
from app.services.matching_service import process_queued_matches

logger = logging.getLogger(__name__)


async def process_buyer_replies() -> int:
    """Fetch buyer replies from Gmail and process them end-to-end.

    Pipeline per reply:
    1. Classify via Groq AI (intent, urgency, sentiment, topics)
    2. Match to the most recent Sent campaign for that buyer
    3. Update campaign with reply data + set status to "Replied"
    4. Update buyer's last_reply_at timestamp
    5. Auto-pause remaining queued touches for that buyer+deal
    6. Log to activity_log
    7. Smart Negotiation: below-floor counters create escalation alerts
    8. Auto-send assignment contract if buyer is Interested

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

            # ── Lazy-populated Deal/Buyer cache to avoid N+1 lookups ──
            _deals_cache: dict[uuid.UUID, Deal] = {}
            _buyers_cache: dict[uuid.UUID, Buyer] = {}

            async def _get_deal(deal_id: uuid.UUID) -> Deal | None:
                if deal_id not in _deals_cache:
                    _r = await db.execute(select(Deal).where(Deal.id == deal_id))
                    _deals_cache[deal_id] = _r.scalar_one_or_none()
                return _deals_cache.get(deal_id)

            async def _get_buyer(buyer_id: uuid.UUID) -> Buyer | None:
                if buyer_id not in _buyers_cache:
                    _r = await db.execute(select(Buyer).where(Buyer.id == buyer_id))
                    _buyers_cache[buyer_id] = _r.scalar_one_or_none()
                return _buyers_cache.get(buyer_id)

            processed_count = 0
            passed_buyer_ids: list[uuid.UUID] = []

            for reply in replies:
                try:
                    from_email = reply["from_email"]
                    buyer_id = buyer_map.get(from_email.lower())

                    if not buyer_id:
                        logger.info("Scheduler: reply from unknown buyer %s — skipping", from_email)
                        continue

                    # Deduplication: skip if this Gmail message was already processed
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

                    # Match reply to the correct campaign
                    campaign, confidence_level = await match_reply_to_campaign(db, buyer_id, reply)

                    if not campaign:
                        logger.info("Scheduler: no sent campaign found for buyer %s — skipping", from_email)
                        continue

                    logger.info(
                        "Scheduler: reply from buyer %s matched to campaign %s via %s (deal: %s)",
                        buyer_id, campaign.id, confidence_level, campaign.deal_id,
                    )

                    # Load deal and buyer via lazy cache (avoids N+1 when multiple
                    # replies reference the same deal or buyer)
                    deal_obj = await _get_deal(campaign.deal_id)
                    buyer_obj = await _get_buyer(buyer_id)

                    if not deal_obj or not buyer_obj:
                        logger.warning("Scheduler: deal or buyer not found for campaign %s", campaign.id)
                        continue

                    # Strip quoted thread from reply body
                    raw_body = reply.get("body", "")
                    clean_body = re.split(r'\n\s*On .{10,100}wrote:\s*\n', raw_body)[0].strip()
                    clean_body = "\n".join(
                        line for line in clean_body.splitlines()
                        if not line.strip().startswith(">")
                    ).strip()
                    if not clean_body:
                        clean_body = raw_body[:500]

                    # Build thread history
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

                    # Run conversation engine
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

                    # Update campaign with reply + new stage
                    campaign.reply_received_at = now
                    campaign.reply_body = raw_body
                    campaign.reply_intent = new_stage
                    campaign.conversation_stage = new_stage
                    campaign.ai_extracted_insights = str(
                        conv_result.get("notes", "") or conv_result.get("ai_extracted_insights", "")
                    )[:500]

                    if extracted.get("legal_name"):
                        campaign.buyer_legal_name = extracted["legal_name"]
                    if extracted.get("phone"):
                        campaign.buyer_phone = extracted["phone"]
                    if extracted.get("title_company"):
                        campaign.buyer_title_company = extracted["title_company"]
                    if extracted.get("agreed_price"):
                        try:
                            campaign.agreed_price = float(
                                str(extracted["agreed_price"]).replace(",", "").replace("$", "")
                            )
                        except Exception as e:
                            logger.warning("Failed to parse agreed_price for campaign %s: %s", campaign.id, e)

                    # Negotiation escalation: below-floor counter
                    _negotiation_escalation = conv_result.get("negotiation_escalation")
                    if _negotiation_escalation:
                        campaign.status = "Replied"
                        db.add(ActivityLog(
                            id=uuid.uuid4(),
                            entity_type="deal",
                            entity_id=campaign.deal_id,
                            action="negotiation_escalation",
                            metadata_json={
                                "alert_user": True,
                                "priority": "high",
                                "action_required": "Review and respond to below-floor counter",
                                "buyer_id": str(buyer_id),
                                "deal_id": str(campaign.deal_id),
                                "campaign_id": str(campaign.id),
                                "counter_price": _negotiation_escalation["counter_price"],
                                "floor_price": _negotiation_escalation["floor_price"],
                                "gap": _negotiation_escalation["gap"],
                                "buyer_name": _negotiation_escalation.get("buyer_name", buyer_obj.full_name if buyer_obj else ""),
                                "buyer_email": _negotiation_escalation.get("buyer_email", buyer_obj.email if buyer_obj else ""),
                                "deal_address": _negotiation_escalation.get("deal_address", deal_obj.address if deal_obj else ""),
                            },
                        ))
                        logger.info(
                            "Negotiation escalation logged for buyer %s on deal %s (counter < floor)",
                            buyer_id, campaign.deal_id,
                        )

                    # Handle terminal states
                    if conv_result["pass_detected"] or conv_result["unsubscribe_detected"]:
                        campaign.status = "Passed"
                        passed_buyer_ids.append(buyer_id)
                        _q = await db.execute(
                            select(Campaign).where(
                                Campaign.buyer_id == buyer_id,
                                Campaign.deal_id == campaign.deal_id,
                                Campaign.status == "Queued",
                            )
                        )
                        for qc in _q.scalars().all():
                            qc.status = "Paused"
                            db.add(qc)
                        if conv_result["unsubscribe_detected"]:
                            buyer_obj.unsubscribed_at = now
                            buyer_obj.status = "Do Not Contact"
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

                    # Send conversation engine reply
                    logger.info("Scheduler: next_message preview: %s", (next_message or "")[:100])
                    if next_message and not conv_result["pass_detected"]:
                        try:
                            await send_email(
                                to=buyer_obj.email,
                                subject=f"Re: {reply.get('subject', '')}",
                                body=next_message,
                                campaign_id=campaign.id.hex,
                                send_type="reply",
                            )
                            logger.info(
                                "Scheduler: conversation reply sent to buyer %s (stage: %s)",
                                buyer_id, new_stage,
                            )
                        except Exception as send_err:
                            logger.warning("Scheduler: failed to send reply to buyer %s: %s", buyer_id, send_err)

                    # Activity log
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
                    continue

            await db.commit()

            # Event-driven queued match release for passed buyers
            if passed_buyer_ids:
                for bid in passed_buyer_ids:
                    try:
                        async with _db.async_session_factory() as release_db:
                            released = await process_queued_matches(release_db, buyer_id=bid)
                            if released > 0:
                                logger.info("Released %d queued matches for buyer %s (Pass reply)", released, bid)
                            await release_db.commit()
                    except Exception as release_err:
                        logger.warning(
                            "Failed to process queued matches for buyer %s after Pass reply: %s",
                            bid, release_err, exc_info=True,
                        )

            logger.info(
                "Scheduler: reply processing complete — %d/%d replies processed",
                processed_count, len(replies),
            )
            return processed_count

        except Exception as e:
            logger.error("Scheduler: reply processing error: %s", e, exc_info=True)
            await db.rollback()
            return 0
