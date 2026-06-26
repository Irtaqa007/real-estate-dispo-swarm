"""Reply processing service using Groq AI for multi-dimensional buyer reply classification.

Supports:
- Multi-dimensional intent classification (primary_intent, urgency, sentiment, topics, etc.)
- Buy Box Auto-Update: When reply_intent = "Buybox_Changed", extracts new criteria
- Auto-Follow-Up: When reply_intent = "Question", drafts answer immediately
- Smart Negotiation: Counter offers with auto-approve or defer

Returns structured intent with: primary_intent, urgency, sentiment, topics, recommended_action.
"""

import json
import logging
import re
import uuid
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.schemas import Buyer, Campaign, Deal, JVPartner
from datetime import datetime, timedelta, timezone

from app.services.ai_validator import validate_ai_output
from app.services.audit_logger import audit
from app.services.groq_client import groq_chat_completion
from app.services.pass_reason_extractor import extract_pass_reason
from app.models.schemas import BuyerReengagementSchedule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Multi-dimensional classification prompt
# ---------------------------------------------------------------------------

_CLASSIFICATION_SYSTEM_PROMPT = (
    "You are a wholesale real estate expert and negotiator. "
    "Analyze this buyer reply email and extract structured data.\n"
    f"OPERATOR IDENTITY (you respond as this person):\n"
    f"Name: {settings.operator_name}\n"
    f"Sign-off: {settings.operator_email_signature}\n"
    f"Tone: {settings.operator_tone}\n"
    f"Never use: {settings.operator_never_say}\n"
    f"{settings.operator_context}\n"
    f"IMPORTANT: Reply in first person as {settings.operator_name}. "
    f"Do not reveal you are AI or automated."
)

_CLASSIFICATION_USER_PROMPT_TEMPLATE = """REPLY EMAIL:
Subject: {subject}
Body: {body}

CLASSIFY INTO:
Primary intent (pick ONE):
- Interested: Wants to proceed, see property, or make offer
- Counter: Negotiating price or terms (include counter_price if mentioned)
- Pass: Not interested, wrong criteria, timing
- Question: Asking for more info (specify topics below)
- Unsubscribe: Wants off list
- Buybox_Changed: Updated buying criteria — they mention changes to what they're looking for
- Other: Doesn't fit above

Also extract:
1. urgency: High/Medium/Low
2. sentiment: 1-5 (1=very negative, 5=very positive)
3. topics: list of mentioned topics (e.g. ["price", "photos", "walkthrough", "financing"])
4. recommended_action: What should happen next (send_photos, schedule_walkthrough, send_contract, discuss_with_partner, draft_answer, etc.)
5. counter_price: If intent is Counter, extract the offered price as a number (or null)
6. summary: One-sentence summary of what the buyer wants
7. buybox_changes: If intent is Buybox_Changed, extract the FULL updated buying criteria text from their reply (or null)
8. question_answer: If intent is Question, provide a direct 2-3 sentence answer to their question (or null)

Return ONLY JSON:
{{
    "primary_intent": "...",
    "urgency": "Medium",
    "sentiment": 3,
    "topics": ["price", "photos"],
    "recommended_action": "send_photos",
    "counter_price": null,
    "summary": "...",
    "buybox_changes": "I'm now looking for 3-4 bedroom houses in the downtown area under $250k...",
    "question_answer": "The property is currently vacant and we can schedule a walkthrough as early as tomorrow..."
}}"""

_INTENT_MAP: Dict[str, str] = {
    "Interested": "Interested",
    "Counter": "Counter",
    "Pass": "Pass",
    "Question": "Question",
    "Unsubscribe": "Unsubscribe",
    "Buybox_Changed": "Buybox_Changed",
    "Other": "Other",
}


async def process_reply(
    email_data: dict,
    db: Optional[AsyncSession] = None,
    buyer_id: Optional[uuid.UUID] = None,
    deal_id: Optional[uuid.UUID] = None,
) -> dict:
    """Use Groq AI to classify a buyer's reply with multi-dimensional intent.

    Args:
        email_data: dict with keys ``subject`` and ``body`` (at minimum).
        db: Optional DB session for ghost recovery cancellation.
        buyer_id: Required with db and deal_id for ghost recovery check.
        deal_id: Required with db and buyer_id for ghost recovery check.

    Returns:
        dict with keys:
            reply_intent (str) — backward-compatible single-string intent.
            primary_intent (str) — the primary intent classification.
            urgency (str) — High/Medium/Low.
            sentiment (int) — sentiment score 1-5.
            topics (list[str]) — extracted topics.
            recommended_action (str) — AI recommendation.
            counter_price (float|None) — if Counter intent, the offered price.
            ai_extracted_insights (str) — natural-language summary.
            buyer_profile_updates (dict) — buy box changes detected.
            question_answer (str|None) — if Question intent, the auto-drafted answer.
    """
    subject = (email_data.get("subject") or "").strip()
    body = (email_data.get("body") or "").strip()
    from_email = (email_data.get("from_email") or "unknown").strip()

    # ── Ghost recovery cancellation: if buyer is in ghost recovery for this deal, reset it ──
    if db is not None and buyer_id is not None and deal_id is not None:
        try:
            ghost_rows = await db.execute(
                select(Campaign).where(
                    Campaign.buyer_id == buyer_id,
                    Campaign.deal_id == deal_id,
                    Campaign.ghost_detected_at.isnot(None),
                )
            )
            ghost_cancelled = False
            ghost_touches_before = 0
            for gc in ghost_rows.scalars().all():
                ghost_touches_before = gc.ghost_recovery_touch
                gc.ghost_detected_at = None
                gc.ghost_recovery_touch = 0
                gc.ghost_recovery_sent_at = None
                db.add(gc)
                ghost_cancelled = True

            if ghost_cancelled:
                logger.info(
                    "Ghost recovery cancelled: buyer %s replied on deal %s after %d recovery touches",
                    buyer_id, deal_id, ghost_touches_before,
                )
                await audit.log(
                    db,
                    entity_type="campaign",
                    entity_id=uuid.uuid4(),
                    action="ghost_recovery_cancelled",
                    metadata={
                        "buyer_id": str(buyer_id),
                        "deal_id": str(deal_id),
                        "recovery_touches_sent": ghost_touches_before,
                        "alert_user": False,
                    },
                )
                await db.flush()
        except Exception as e:
            logger.error(
                "Failed to cancel ghost recovery for buyer %s, deal %s: %s",
                buyer_id, deal_id, e, exc_info=True,
            )

    # ── Load full buyer context (all open threads across all deals) ──
    full_context = None
    if db is not None and buyer_id is not None and deal_id is not None:
        try:
            full_context = await load_buyer_full_context(
                db=db,
                buyer_id=buyer_id,
                primary_deal_id=deal_id,
            )
        except Exception as e:
            logger.warning(
                "Could not load full buyer context for buyer %s: %s",
                buyer_id, e,
            )
            full_context = None

    user_prompt = _CLASSIFICATION_USER_PROMPT_TEMPLATE.format(
        subject=subject,
        body=body,
    )

    # ── Inject other-active-deals context into the AI prompt ──
    if full_context and full_context["other_active_deals"]:
        other_lines = [
            "",
            "CONTEXT — OTHER ACTIVE DEALS WITH THIS BUYER:",
            "Be aware of these when responding. If the buyer references another deal, "
            "acknowledge it naturally. Never confuse deal details across threads.",
            "",
        ]
        for item in full_context["other_active_deals"]:
            other_deal = item["deal"]
            other_thread = item["thread"]
            last_interaction = max(
                (c.sent_at for c in other_thread if c.sent_at),
                default=None,
            )
            last_str = (
                last_interaction.strftime("%Y-%m-%d")
                if last_interaction else "unknown"
            )
            other_lines.append(
                f"- {other_deal.address}, {other_deal.city} "
                f"({other_deal.property_type}) | "
                f"Asking: ${float(other_deal.asking_price):,.0f} | "
                f"Last interaction: {last_str} | "
                f"Status: {item['status']}"
            )
        user_prompt += "\n" + "\n".join(other_lines)

    messages = [
        {"role": "system", "content": _CLASSIFICATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=500,
        )

        content = response.choices[0].message.content.strip()
        logger.debug("Reply classification for %s: %.200s", from_email, content)

        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            )

        parsed: dict = json.loads(content)

        raw_intent = (parsed.get("primary_intent") or "").strip()
        primary_intent = _INTENT_MAP.get(raw_intent, "Other")
        urgency = (parsed.get("urgency") or "Medium").strip()
        sentiment = int(parsed.get("sentiment", 3))
        topics = parsed.get("topics", [])
        if not isinstance(topics, list):
            topics = []
        recommended_action = (parsed.get("recommended_action") or "").strip()
        counter_price = parsed.get("counter_price")
        if counter_price is not None:
            counter_price = float(counter_price)
        insights = (parsed.get("summary") or "").strip()
        buybox_changes = (parsed.get("buybox_changes") or "").strip()
        question_answer = (parsed.get("question_answer") or "").strip()

        # Clamp sentiment 1-5
        sentiment = max(1, min(5, sentiment))

        # Validate urgency
        if urgency not in ("High", "Medium", "Low"):
            urgency = "Medium"

        logger.info(
            "Classified reply from %s as '%s' (urgency=%s, sentiment=%d, topics=%s, "
            "buybox_changed=%s, question=%s)",
            from_email, primary_intent, urgency, sentiment, topics,
            bool(buybox_changes), bool(question_answer),
        )

        # ── Pass reason capture: extract and store structured pass reason ──
        pass_reason_followup = None
        if primary_intent == "Pass" and db is not None and buyer_id is not None and deal_id is not None:
            try:
                # Find the campaign being replied to for this buyer+deal
                campaign_result = await db.execute(
                    select(Campaign)
                    .where(
                        Campaign.buyer_id == buyer_id,
                        Campaign.deal_id == deal_id,
                    )
                    .order_by(Campaign.sent_at.desc().nullslast())
                    .limit(1)
                )
                campaign_row = campaign_result.scalar_one_or_none()

                if campaign_row:
                    # Load deal and buyer
                    deal = await db.get(Deal, deal_id)
                    buyer = await db.get(Buyer, buyer_id)

                    if deal and buyer:
                        # Load thread context (last 3 Campaign rows for this buyer+deal)
                        thread_result = await db.execute(
                            select(Campaign)
                            .where(
                                Campaign.buyer_id == buyer_id,
                                Campaign.deal_id == deal_id,
                            )
                            .order_by(Campaign.sent_at.desc().nullslast())
                            .limit(3)
                        )
                        thread_campaigns = list(thread_result.scalars().all())

                        # Call AI extraction
                        pass_result = await extract_pass_reason(
                            reply_body=body,
                            thread_context=thread_campaigns,
                            deal=deal,
                            buyer=buyer,
                        )

                        now = datetime.now(timezone.utc)

                        # Update campaign pass fields
                        campaign_row.pass_reason_category = pass_result["category"]
                        campaign_row.pass_reason_raw = pass_result["raw"]
                        campaign_row.pass_reason_confidence = pass_result["confidence"]
                        campaign_row.passed_at = now
                        db.add(campaign_row)

                        # Update deal pass_count and pass_reasons_summary
                        deal.pass_count = (deal.pass_count or 0) + 1
                        existing_summary = deal.pass_reasons_summary or {}
                        existing_summary[pass_result["category"]] = existing_summary.get(pass_result["category"], 0) + 1
                        deal.pass_reasons_summary = existing_summary
                        db.add(deal)

                        # Update JV partner stats
                        if deal.jv_partner_id:
                            jv = await db.get(JVPartner, deal.jv_partner_id)
                            if jv:
                                jv.total_passes = (jv.total_passes or 0) + 1
                                if pass_result["category"] == "price_too_high":
                                    jv.overprice_flag_count = (jv.overprice_flag_count or 0) + 1
                                if pass_result["category"] == "title_issue":
                                    jv.title_issue_count = (jv.title_issue_count or 0) + 1
                                if pass_result["category"] == "condition":
                                    jv.condition_issue_count = (jv.condition_issue_count or 0) + 1
                                existing_jv_breakdown = jv.pass_reasons_breakdown or {}
                                existing_jv_breakdown[pass_result["category"]] = existing_jv_breakdown.get(pass_result["category"], 0) + 1
                                jv.pass_reasons_breakdown = existing_jv_breakdown
                                db.add(jv)

                        # Apply buy box signal if detected
                        if pass_result.get("buy_box_signal"):
                            try:
                                signal = pass_result["buy_box_signal"]
                                field = signal.get("field")
                                direction = signal.get("direction")
                                signal_strength = signal.get("signal_strength", "medium")

                                if field == "price_max" and direction == "lower":
                                    if buyer.price_max is not None:
                                        buyer.price_max = buyer.price_max * 0.9  # Reduce by 10%
                                elif field == "price_max" and direction == "higher":
                                    if buyer.price_max is not None:
                                        buyer.price_max = buyer.price_max * 1.1  # Increase by 10%
                                elif field == "price_min" and direction == "lower":
                                    if buyer.price_min and buyer.price_min > 0:
                                        buyer.price_min = buyer.price_min * 0.9
                                    # If price_min is None or 0, skip — no valid
                                    # baseline to adjust from
                                elif field == "price_min" and direction == "higher":
                                    if buyer.price_min and buyer.price_min > 0:
                                        buyer.price_min = buyer.price_min * 1.1
                                    # If price_min is None or 0, skip — no valid
                                    # baseline to adjust from
                                elif field == "pref_property_type" and direction == "narrower":
                                    if buyer.pref_property_type != "Land":
                                        buyer.pref_property_type = None  # Reset to both
                                elif field == "pref_cities" and direction == "narrower":
                                    buyer.pref_cities = []  # Reset city filter

                                db.add(buyer)
                                logger.info(
                                    "Buy box signal applied to buyer %s: field=%s, direction=%s, strength=%s",
                                    buyer_id, field, direction, signal_strength,
                                )
                            except Exception as signal_err:
                                logger.warning(
                                    "Failed to apply buy box signal for buyer %s: %s",
                                    buyer_id, signal_err, exc_info=True,
                                )

                        # Create activity log entry
                        await audit.log(
                            db,
                            entity_type="deal",
                            entity_id=deal_id,
                            action="buyer_passed",
                            metadata={
                                "buyer_id": str(buyer_id),
                                "deal_id": str(deal_id),
                                "jv_partner_id": str(deal.jv_partner_id) if deal.jv_partner_id else None,
                                "pass_reason_category": pass_result["category"],
                                "pass_reason_raw": pass_result["raw"],
                                "confidence": pass_result["confidence"],
                                "deal_pass_count": deal.pass_count,
                                "alert_user": False,
                            },
                        )

                        # Generate follow-up question if confidence is low
                        if pass_result["confidence"] == "low":
                            followup_body = (
                                "Totally understand — just so I can match you better "
                                "next time, was it the price, location, condition, "
                                "or something else?"
                            )
                            sign_off = settings.operator_email_signature.strip()
                            if sign_off:
                                followup_body += "\n\n" + sign_off
                            pass_reason_followup = followup_body

                        logger.info(
                            "Pass reason captured: buyer %s, deal %s, category=%s, confidence=%s",
                            buyer_id, deal_id, pass_result["category"], pass_result["confidence"],
                        )

            except Exception as pass_err:
                logger.warning(
                    "Failed to capture pass reason for buyer %s, deal %s: %s",
                    buyer_id, deal_id, pass_err, exc_info=True,
                )
                try:
                    await db.rollback()
                except Exception as rb_err:
                    logger.error(
                        "Rollback failed after pass reason capture error "
                        "for buyer %s, deal %s: %s",
                        buyer_id, deal_id, rb_err, exc_info=True,
                    )

        # ── AI Validation pre-send guard for reply content ──
        validation_blocked = False
        if question_answer and db is not None:
            try:
                v_deal = await db.get(Deal, deal_id) if deal_id else None
                v_buyer = await db.get(Buyer, buyer_id) if buyer_id else None
                validation = await validate_ai_output(
                    content=question_answer,
                    content_type="reply_email",
                    deal=v_deal,
                    buyer=v_buyer,
                )
                if validation.severity == "block":
                    logger.error(
                        "Reply email blocked by AI validator for buyer %s "
                        "deal %s: %s",
                        buyer_id, deal_id, validation.violations,
                    )
                    validation_blocked = True
                else:
                    question_answer = (
                        validation.corrected_content or question_answer
                    )
            except Exception as val_err:
                logger.error(
                    "AI validator failed for reply email, proceeding "
                    "with unvalidated content: %s", val_err,
                )

        # ── Future buying window detection ──
        # Runs on every reply regardless of primary intent
        if db is not None and buyer_id is not None:
            try:
                reengagement_result = await detect_future_buying_window(
                    reply_body=body,
                    thread_context=full_context,
                    buyer_id=buyer_id,
                    deal_id=deal_id,
                    db=db,
                )
                if reengagement_result:
                    logger.info(
                        "Future buying window detected for buyer %s: target=%s (stated: '%s')",
                        buyer_id, reengagement_result["target_date"],
                        reengagement_result["stated_window_raw"],
                    )
            except Exception as re_err:
                logger.warning(
                    "Future buying window detection failed for buyer %s: %s",
                    buyer_id, re_err, exc_info=True,
                )

        return {
            "reply_intent": primary_intent,
            "primary_intent": primary_intent,
            "urgency": urgency,
            "sentiment": sentiment,
            "topics": topics,
            "recommended_action": recommended_action,
            "counter_price": counter_price,
            "ai_extracted_insights": insights,
            "buyer_profile_updates": (
                {"buy_box": buybox_changes} if buybox_changes else {}
            ),
            "question_answer": question_answer or None,
            "pass_reason_followup": pass_reason_followup,
            "validation_blocked": validation_blocked or None,
        }

    except json.JSONDecodeError as e:
        logger.error(
            "Failed to parse Groq JSON for reply from %s: %s\nResponse: %.200s",
            from_email, e, content if 'content' in locals() else "(no response)",
        )
        return {
            "reply_intent": "Other",
            "primary_intent": "Other",
            "urgency": "Medium",
            "sentiment": 3,
            "topics": [],
            "recommended_action": "",
            "counter_price": None,
            "ai_extracted_insights": f"Failed to classify: {body[:200]}",            "buyer_profile_updates": {},
            "question_answer": None,
            "pass_reason_followup": None,
        }

    except Exception as e:
        logger.error(
            "Groq API error classifying reply from %s: %s",
            from_email, e, exc_info=True,
        )
        return {
            "reply_intent": "Other",
            "primary_intent": "Other",
            "urgency": "Medium",
            "sentiment": 3,
            "topics": [],
            "recommended_action": "",
            "counter_price": None,
            "ai_extracted_insights": f"Classification error: {e}",
            "buyer_profile_updates": {},
            "question_answer": None,
            "pass_reason_followup": None,
        }


# ---------------------------------------------------------------------------
# Buy Box Auto-Update
# ---------------------------------------------------------------------------


async def extract_buybox_changes(reply_body: str, old_buy_box: str) -> dict:
    """Use Groq to extract buying criteria changes from a reply.

    Called when the reply is classified as Buybox_Changed.

    Args:
        reply_body: The buyer's reply text.
        old_buy_box: The current buy box text.

    Returns:
        dict with keys: criteria_changed (bool), new_criteria (str),
        changes_summary (str).
    """
    messages = [
        {
            "role": "system",
            "content": "You extract buying criteria changes from buyer replies accurately.",
        },
        {
            "role": "user",
            "content": (
                f"Extract any buying criteria changes from this reply.\n\n"
                f"Old buy box: {old_buy_box}\n\n"
                f"Reply: {reply_body}\n\n"
                f"Return JSON:\n"
                f"{{\n"
                f"  \"criteria_changed\": true/false,\n"
                f"  \"new_criteria\": \"full updated buy box text\",\n"
                f"  \"changes_summary\": \"what changed specifically\"\n"
                f"}}"
            ),
        },
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.2,
            max_tokens=300,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.strip().startswith("```"))

        parsed = json.loads(content)
        return {
            "criteria_changed": parsed.get("criteria_changed", False),
            "new_criteria": (parsed.get("new_criteria") or "").strip(),
            "changes_summary": (parsed.get("changes_summary") or "").strip(),
        }
    except Exception as e:
        logger.warning("Failed to extract buybox changes: %s", e, exc_info=True)
        return {
            "criteria_changed": False,
            "new_criteria": "",
            "changes_summary": f"Extraction failed: {e}",
        }


# ---------------------------------------------------------------------------
# Auto-Follow-Up on Questions
# ---------------------------------------------------------------------------


def get_question_round_message(question_round: int) -> str:
    """Get the appropriate auto-follow-up message based on question round.

    Args:
        question_round: How many times this buyer has asked a question (1-based).

    Returns:
        str: The action/message for this round.
    """
    if question_round == 1:
        return "auto_answer"
    elif question_round == 2:
        return "auto_answer"
    elif question_round == 3:
        return "final_answer_prompt"
    else:
        return "manual_intervention_needed"


# ---------------------------------------------------------------------------
# Uncertainty Detection & Graceful Hold (Feature 2 - Part D)
# ---------------------------------------------------------------------------


async def detect_uncertainty_and_hold(
    reply: dict,
    classification: dict,
    db_session,
    buyer_id,
    deal_id,
) -> Optional[str]:
    """Check if a buyer's question can be answered confidently from available data.

    If the question cannot be answered from the deal record, buyer profile, or
    existing thread context, generates a graceful holding response instead of
    guessing, and flags for manual follow-up via audit log.

    Args:
        reply: The raw reply dict with subject, body, from_email.
        classification: The classification dict from process_reply().
        db_session: Database session for audit logging.
        buyer_id: UUID of the buyer.
        deal_id: UUID of the deal.

    Returns:
        str or None: The holding response text if uncertainty detected, else None.
    """
    if classification.get("reply_intent") != "Question":
        return None

    question_answer = classification.get("question_answer")
    
    # If the AI already provided a substantive answer (more than 20 chars), 
    # assume confidence and let it through
    if question_answer and len(question_answer) > 20:
        return None

    # Generate appropriate holding response
    import random
    holding_responses = [
        "Let me pull that up and get back to you shortly.",
        "Good question — let me double check that and come back to you today.",
        "I want to make sure I give you the right number on that — give me a few hours.",
        "Let me look into that and follow up with the details shortly.",
    ]
    holding_text = random.choice(holding_responses)
    
    # Sign-off
    sign_off = settings.operator_email_signature.strip()
    if sign_off:
        holding_text += "\n\n" + sign_off

    # Log uncertainty flag to activity log
    try:
        await audit.log(
            db_session,
            entity_type="campaign",
            entity_id=uuid.uuid4(),
            action="uncertainty_flag",
            metadata={
                "buyer_id": str(buyer_id),
                "deal_id": str(deal_id),
                "question_asked": reply.get("body", "")[:500],
                "response_sent": holding_text,
                "alert_user": True,
                "action_required": "Answer buyer's question manually and follow up",
            },
        )
    except Exception as e:
        logger.warning("Failed to log uncertainty flag: %s", e, exc_info=True)

    logger.info(
        "Uncertainty detected for buyer %s — generated holding response",
        buyer_id,
    )
    return holding_text


# ---------------------------------------------------------------------------
# Multi-thread reply matching — priority chain
# ---------------------------------------------------------------------------


_RE_CAMPAIGN_ID = re.compile(r"<campaign-([a-f0-9-]+)@dispo\.local>")


async def match_reply_to_campaign(
    db: AsyncSession,
    buyer_id: uuid.UUID,
    reply: dict,
) -> Tuple[Optional[Campaign], str]:
    """Match a buyer reply to the correct campaign using a priority chain.

    Priority:
    1. "header" — In-Reply-To / References header Message-ID match
    2. "subject" — Deal address / city in subject line (fuzzy if needed)
    3. "body" — Keyword match in reply body
    4. "fallback" — Most recent Sent campaign (existing behavior, last resort)

    Args:
        db: Database session.
        buyer_id: The buyer who sent the reply.
        reply: Incoming reply dict with subject, body, and optional headers.

    Returns:
        Tuple of (matched_campaign, confidence_level).
        confidence_level is "header", "subject", "body", or "fallback".
    """
    # ── Method 1: In-Reply-To / References header matching ──
    headers = reply.get("headers", {})
    header_text = " ".join([
        headers.get("In-Reply-To", ""),
        headers.get("References", ""),
    ])
    header_match = _RE_CAMPAIGN_ID.search(header_text)
    if header_match:
        try:
            campaign_uuid = uuid.UUID(header_match.group(1))
            campaign = await db.get(Campaign, campaign_uuid)
            if campaign and campaign.buyer_id == buyer_id:
                logger.info(
                    "Reply from buyer %s matched to campaign %s via header (deal: %s)",
                    buyer_id, campaign.id, campaign.deal_id,
                )
                return campaign, "header"
        except ValueError:
            pass

    # ── Load all active campaigns for this buyer ──
    active_campaigns = await db.execute(
        select(Campaign)
        .where(
            Campaign.buyer_id == buyer_id,
            Campaign.status.in_(["Sent", "Replied"]),
        )
        .order_by(Campaign.sent_at.desc().nullslast())
    )
    all_campaigns: List[Campaign] = list(active_campaigns.scalars().all())

    if not all_campaigns:
        return None, "fallback"

    reply_subject = (reply.get("subject") or "").lower()
    reply_body = (reply.get("body") or "").lower()

    # Gather unique deal ids and fetch the Deal records
    deal_ids = list({c.deal_id for c in all_campaigns})
    deals_map: Dict[uuid.UUID, Deal] = {}
    for did in deal_ids:
        deal = await db.get(Deal, did)
        if deal:
            deals_map[did] = deal

    # ── Method 2: Subject line deal matching ──
    if reply_subject:
        best_subject_did: Optional[uuid.UUID] = None
        best_subject_score = 0.0

        for did, deal in deals_map.items():
            if deal.address and deal.address.lower() in reply_subject:
                score = 1.0
            elif deal.city and deal.city.lower() in reply_subject:
                score = 0.9
            else:
                score = SequenceMatcher(
                    None, (deal.address or "").lower(), reply_subject
                ).ratio()

            if score > best_subject_score:
                best_subject_score = score
                best_subject_did = did

        if best_subject_score >= 0.7 and best_subject_did:
            deal_campaigns = [c for c in all_campaigns if c.deal_id == best_subject_did]
            if deal_campaigns:
                logger.info(
                    "Reply from buyer %s matched to campaign %s via subject (deal: %s, score: %.2f)",
                    buyer_id, deal_campaigns[0].id, best_subject_did, best_subject_score,
                )
                return deal_campaigns[0], "subject"

    # ── Method 3: Body content semantic matching ──
    if reply_body:
        best_body_did: Optional[uuid.UUID] = None
        best_body_score = 0

        for did, deal in deals_map.items():
            keywords: List[str] = [
                deal.address or "",
                deal.city or "",
                deal.state or "",
                str(deal.zip) if deal.zip else "",
                deal.property_type or "",
                str(int(deal.asking_price)) if deal.asking_price else "",
            ]
            score = sum(1 for kw in keywords if kw and kw.lower() in reply_body)

            if score > best_body_score:
                best_body_score = score
                best_body_did = did

        if best_body_score >= 2 and best_body_did:
            deal_campaigns = [c for c in all_campaigns if c.deal_id == best_body_did]
            if deal_campaigns:
                logger.info(
                    "Reply from buyer %s matched to campaign %s via body (deal: %s, score: %d)",
                    buyer_id, deal_campaigns[0].id, best_body_did, best_body_score,
                )
                return deal_campaigns[0], "body"

    # ── Method 4: Fallback — most recent Sent campaign ──
    sent_campaigns = [c for c in all_campaigns if c.status == "Sent"]
    fallback = sent_campaigns[0] if sent_campaigns else all_campaigns[0]

    logger.warning(
        "Reply from buyer %s matched to most-recent campaign (fallback) — "
        "could not determine deal from headers, subject, or body. Review manually.",
        buyer_id,
    )
    return fallback, "fallback"


# ---------------------------------------------------------------------------
# Buyer full-context loader (all open threads)
# ---------------------------------------------------------------------------


async def load_buyer_full_context(
    db: AsyncSession,
    buyer_id: uuid.UUID,
    primary_deal_id: uuid.UUID,
) -> dict:
    """Load complete buyer context across all active deals.

    Returns:
        dict with keys:
            buyer: Buyer object.
            primary_deal: Deal object being replied about.
            primary_thread: List of Campaign rows for primary deal, ordered by sent_at.
            other_active_deals: List of dicts with deal, thread, status.
            total_active_deals: int.
    """
    buyer = await db.get(Buyer, buyer_id)

    # Load all campaigns for this buyer
    campaigns_result = await db.execute(
        select(Campaign)
        .where(Campaign.buyer_id == buyer_id)
        .order_by(Campaign.sent_at.asc().nullslast())
    )
    all_campaigns: List[Campaign] = list(campaigns_result.scalars().all())

    deal_ids = {c.deal_id for c in all_campaigns}

    # Load all relevant deals
    primary_deal = await db.get(Deal, primary_deal_id)
    active_deal_ids: set[uuid.UUID] = set()
    deal_objects: Dict[uuid.UUID, Deal] = {}

    for did in deal_ids:
        deal = await db.get(Deal, did)
        if deal and deal.status in ("Available", "Campaign Launched", "Under Contract"):
            active_deal_ids.add(did)
            deal_objects[did] = deal

    # Split campaigns into primary thread vs other active deals
    primary_thread = [c for c in all_campaigns if c.deal_id == primary_deal_id]

    other_active_deals: List[dict] = []
    for did in active_deal_ids:
        if did == primary_deal_id:
            continue
        other_deal = deal_objects.get(did)
        if not other_deal:
            continue
        other_thread = [c for c in all_campaigns if c.deal_id == did]
        other_active_deals.append({
            "deal": other_deal,
            "thread": other_thread,
            "status": other_deal.status,
        })

    return {
        "buyer": buyer,
        "primary_deal": primary_deal,
        "primary_thread": primary_thread,
        "other_active_deals": other_active_deals,
        "total_active_deals": 1 + len(other_active_deals),
    }


# ---------------------------------------------------------------------------
# Future buying window detection
# ---------------------------------------------------------------------------


_DETECTION_SYSTEM_PROMPT = (
    "You are a real estate assistant that extracts future buying intent from buyer replies. "
    "Respond ONLY in JSON."
)

_DETECTION_USER_PROMPT_TEMPLATE = """Does this message contain a signal that the buyer intends
to buy in the future but not right now? Look for:
- Specific months or quarters ('September', 'Q4', 'next year')
- Relative timeframes ('in 3 months', 'after summer', 'early next year')
- Conditional timing ('once I sell my current property', 'when my lease ends')

If a future buying signal exists, extract it.
If no signal, return null.

Respond ONLY in JSON:
{{
  'has_future_signal': true/false,
  'stated_window_raw': 'exact words from message',
  'target_date': 'YYYY-MM-DD or null if only relative',
  'target_month': 'YYYY-MM or null',
  'confidence': 'high/medium/low'
}}
If has_future_signal is false, return:
{{'has_future_signal': false}}

MESSAGE:
{reply_body}"""


async def detect_future_buying_window(
    reply_body: str,
    thread_context: Optional[list] = None,
    buyer_id: Optional[uuid.UUID] = None,
    deal_id: Optional[uuid.UUID] = None,
    db: Optional[AsyncSession] = None,
) -> Optional[dict]:
    """Detect if a buyer signals a future buying window in their reply.

    Uses llama-3.1-8b-instant for speed. If a future signal is detected
    with sufficient confidence, creates a BuyerReengagementSchedule record.

    Args:
        reply_body: The buyer's reply text.
        thread_context: Optional full buyer context dict from load_buyer_full_context().
        buyer_id: Buyer UUID (required for creating schedule).
        deal_id: Optional deal UUID.
        db: Database session (required for creating schedule).

    Returns:
        dict with keys {stated_window_raw, target_date, confidence} if signal detected,
        None if no signal or confidence too low.
    """
    if not reply_body or not reply_body.strip():
        return None

    user_prompt = _DETECTION_USER_PROMPT_TEMPLATE.format(reply_body=reply_body)

    messages = [
        {"role": "system", "content": _DETECTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            model="llama-3.1-8b-instant",
            temperature=0.2,
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()

        # Strip markdown code fences
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            )

        parsed = json.loads(content)

        if not parsed.get("has_future_signal"):
            return None

        confidence = (parsed.get("confidence") or "low").strip().lower()
        if confidence == "low":
            logger.debug(
                "Future buying signal detected but confidence=low for buyer %s: %s",
                buyer_id, parsed.get("stated_window_raw", ""),
            )
            return None

        stated_raw = (parsed.get("stated_window_raw") or "").strip()
        if not stated_raw:
            return None

        # Resolve target_date
        target_date_str = parsed.get("target_date")
        target_month_str = parsed.get("target_month")
        now = datetime.now(timezone.utc)
        resolved_date: Optional[datetime] = None

        if target_date_str:
            try:
                resolved_date = datetime.strptime(target_date_str, "%Y-%m-%d")
                resolved_date = resolved_date.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        if resolved_date is None and target_month_str:
            try:
                dt = datetime.strptime(target_month_str, "%Y-%m")
                resolved_date = dt.replace(day=1, tzinfo=timezone.utc)
            except ValueError:
                pass

        if resolved_date is None:
            # Relative timeframe — default to 3 months from now
            # as a safe fallback
            resolved_date = now + timedelta(days=90)

        # Ensure target_date is in the future
        if resolved_date <= now:
            # If the resolved date is today or past, push to next month
            resolved_date = now + timedelta(days=30)

        # Create BuyerReengagementSchedule record
        if db is not None and buyer_id is not None:
            try:
                context_summary = reply_body[:200]
                schedule_entry = BuyerReengagementSchedule(
                    id=uuid.uuid4(),
                    buyer_id=buyer_id,
                    deal_id=deal_id,
                    stated_window_raw=stated_raw,
                    target_date=resolved_date,
                    context_summary=context_summary,
                    status="waiting",
                )
                db.add(schedule_entry)
                await db.flush()
            except Exception as create_err:
                logger.warning(
                    "Failed to create reengagement schedule for buyer %s: %s",
                    buyer_id, create_err, exc_info=True,
                )

        return {
            "stated_window_raw": stated_raw,
            "target_date": resolved_date,
            "confidence": confidence,
        }

    except json.JSONDecodeError as e:
        logger.warning(
            "Failed to parse future buying window detection JSON: %s\nResponse: %.200s",
            e, content if 'content' in locals() else "(no response)",
        )
        return None
    except Exception as e:
        logger.warning(
            "Future buying window detection failed for buyer %s: %s",
            buyer_id, e, exc_info=True,
        )
        return None
