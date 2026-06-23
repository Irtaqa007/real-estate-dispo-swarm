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
import uuid
from typing import Any, Dict, Optional

from app.config import settings
from app.services.audit_logger import audit
from app.services.groq_client import groq_chat_completion

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


async def process_reply(email_data: dict) -> dict:
    """Use Groq AI to classify a buyer's reply with multi-dimensional intent.

    Args:
        email_data: dict with keys ``subject`` and ``body`` (at minimum).

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

    user_prompt = _CLASSIFICATION_USER_PROMPT_TEMPLATE.format(
        subject=subject,
        body=body,
    )

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
            "ai_extracted_insights": f"Failed to classify: {body[:200]}",
            "buyer_profile_updates": {},
            "question_answer": None,
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
