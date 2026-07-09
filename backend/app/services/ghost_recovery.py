"""Ghost detection and recovery email generation service.

Handles:
1. AI-powered ghost recovery email generation (5-touch arc with thread context)
2. Each recovery email is anchored to the actual conversation that went cold

A "ghost" is a buyer who replied at least once, then went silent for 96+ hours.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.config import settings
from app.models.models import Buyer, Campaign, Deal
from app.services.ai_validator import validate_ai_output
from app.services.groq_client import groq_chat_completion, extract_json_block

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ghost recovery touch arcs
# ---------------------------------------------------------------------------

TOUCH_ARCS = {
    1: {
        "arc": "Soft Re-entry",
        "description": (
            "Reference the last conversation specifically. Zero pressure. Day 3 after silence. "
            "\"Just wanted to circle back on [specific thing discussed]\". "
            "Acknowledge the buyer's previous interest naturally."
        ),
        "instruction": "Soft re-entry. Reference the exact topic or question the buyer last discussed. "
                        "Do NOT mention the silence gap. Act as if timing is natural. "
                        "One new piece of information or update on the same deal.",
    },
    2: {
        "arc": "New Angle",
        "description": (
            "Give them a reason to re-open the conversation. New information, Day 7. "
            "Updated detail, or a different value angle on the same deal."
        ),
        "instruction": "New value angle. If possible, share a different aspect of the deal "
                        "that wasn't discussed before (e.g., a different exit strategy, "
                        "updated numbers, a creative angle). Keep it fresh.",
    },
    3: {
        "arc": "Honest Check-in",
        "description": (
            "Low pressure exit ramp offered. Day 12. "
            "\"Are you still looking at deals in [their market] or has your focus shifted?\""
        ),
        "instruction": "The honest check-in. Acknowledge the natural passage of time. "
                        "Give the buyer an easy way to say \"not right now\" without burning the relationship. "
                        "Ask if their criteria or timeline has changed.",
    },
    4: {
        "arc": "Pattern Interrupt",
        "description": (
            "Extremely short. Day 18. One line only. Nothing else."
        ),
        "instruction": (
            "One line only. The entire email body is one sentence. "
            "Format: '[First name] — still looking at deals in [their market]?' "
            "Nothing else. No context, no re-pitch, no explanation. "
            "The brevity is intentional — it signals confidence, not desperation. "
            f"Sign with {settings.operator_signature} on a new line."
        ),
    },
    5: {
        "arc": "Exit",
        "description": (
            "Final close or clean exit with future hook. Day 25."
        ),
        "instruction": (
            "The final email. Clean, warm, no bitterness. "
            "Three sentences maximum: "
            "1. You're closing the loop on this deal. "
            "2. When the next deal comes that fits their criteria, "
            "   you'll send it their way. "
            "3. Wish them well. "
            "No ask. No CTA. The relationship stays warm even as the deal closes. "
            "Never mention why they didn't respond. Never mention the silence. "
            f"Sign with {settings.operator_signature}."
        ),
    },
}

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    f"OPERATOR IDENTITY — you ARE this person in all recovery emails:\n"
    f"Name: {settings.operator_name}\n"
    f"Sign-off: {settings.operator_signature}\n"
    f"Tone: {settings.operator_tone}\n"
    f"Never use: {settings.operator_never_say}\n"
    f"Context: {settings.operator_context}\n\n"
    f"GHOST RECOVERY RULES:\n"
    f"- Never reference the silence or the fact they didn't respond\n"
    f"- Never say 'I haven't heard from you' or 'just checking in'\n"
    f"- Act as if the timing of this email is completely natural\n"
    f"- The thread context below is critical — reference specific things "
    f"  from the conversation that went cold, not generic deal details\n"
    f"- Sound like a real investor who reached out when they thought of "
    f"  something relevant — not a system running a drip sequence\n"
    f"- Do not reveal AI or automation under any circumstances\n"
    f"- Return ONLY JSON: {{\"subject\": \"...\", \"body\": \"...\"}}"
)


def _build_operator_identity_block() -> str:
    """Build the operator identity block for the AI prompt."""
    return (
        "\n"
        "OPERATOR IDENTITY (you ARE this person, write as them):\n"
        f"Name: {settings.operator_name}\n"
        f"Sign-off: {settings.operator_signature}\n"
        f"Tone: {settings.operator_tone}\n"
        f"Never use these words/phrases: {settings.operator_never_say}\n"
        f"Personal context (use naturally if relevant):\n"
        f"{settings.operator_context}\n"
        f"IMPORTANT: Subject line must NEVER contain the operator name — "
        f"subject lines are deal-focused only.\n"
        f"IMPORTANT: Reply in first person as {settings.operator_name}. "
        f"Do not reveal you are AI or automated."
    )


def _build_buyer_intel_block(
    buyer: Buyer,
    deal: Deal,
) -> str:
    """Build the buyer intelligence block for the AI prompt."""
    lines = []

    if buyer.deals_closed and buyer.deals_closed > 0:
        lines.append(f"Buyer has closed {buyer.deals_closed} deal(s) previously.")
    if buyer.engagement_score and buyer.engagement_score > 0:
        lines.append(f"Engagement score: {buyer.engagement_score:.0f}/100.")
    if buyer.last_reply_at:
        days_since = (datetime.now(timezone.utc) - buyer.last_reply_at).days
        lines.append(f"Last reply was {days_since} day(s) ago.")
    if buyer.pref_cities:
        cities_str = ", ".join(str(c) for c in buyer.pref_cities if c)
        if cities_str:
            lines.append(f"Buyer's preferred cities: [{cities_str}].")
    if buyer.price_min is not None or buyer.price_max is not None:
        price_range = f"${buyer.price_min:,.0f}–${buyer.price_max:,.0f}" if buyer.price_min is not None and buyer.price_max is not None else \
                      f"${buyer.price_min:,.0f}+" if buyer.price_min is not None else \
                      f"Up to ${buyer.price_max:,.0f}"
        lines.append(f"Buyer price range: {price_range}.")

    if not lines:
        return ""

    return "\nBUYER INTELLIGENCE:\n" + "\n".join(f"- {line}" for line in lines) + "\n"


def _build_thread_context_block(campaigns: Sequence[Campaign]) -> str:
    """Build the conversation thread context from all Campaign rows."""
    if not campaigns:
        return ""

    context_lines = ["\nCONVERSATION THREAD (chronological):"]
    for c in campaigns:
        if c.body:
            timestamp = c.sent_at.strftime("%Y-%m-%d %H:%M UTC") if c.sent_at else "unknown"
            context_lines.append(f"\n  [{timestamp}] OUTBOUND (touch {c.touch_number}):")
            context_lines.append(f"  Subject: {c.subject or '(no subject)'}")
            # Truncate very long bodies for context
            body_preview = c.body[:300] + "..." if len(c.body) > 300 else c.body
            context_lines.append(f"  Body: {body_preview}")
        if c.reply_body:
            reply_ts = c.reply_received_at.strftime("%Y-%m-%d %H:%M UTC") if c.reply_received_at else "unknown"
            context_lines.append(f"\n  [{reply_ts}] BUYER REPLY (reply_intent: {c.reply_intent or 'unknown'}):")
            reply_preview = c.reply_body[:300] + "..." if len(c.reply_body) > 300 else c.reply_body
            context_lines.append(f"  {reply_preview}")

    return "\n".join(context_lines) + "\n"


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------


async def generate_ghost_recovery_email(
    buyer: Buyer,
    deal: Deal,
    touch_number: int,
    thread_context: Sequence[Campaign],
) -> dict:
    """Generate a ghost recovery email using Groq AI anchored to the conversation thread.

    Args:
        buyer: The Buyer model instance.
        deal: The Deal model instance.
        touch_number: Which recovery touch (1-5).
        thread_context: All Campaign rows for this buyer+deal, ordered by sent_at,
                       including both outbound body and reply_body fields.

    Returns:
        dict with keys: subject, body, touch_number.
    """
    if touch_number < 1 or touch_number > 5:
        raise ValueError(f"Invalid ghost recovery touch number: {touch_number}. Must be 1-5.")

    touch_config = TOUCH_ARCS[touch_number]
    arc_desc = touch_config["description"]
    arc_instruction = touch_config["instruction"]

    # Build deal summary
    market_parts = [p for p in [deal.city or "", deal.state or ""] if p]
    market = ", ".join(market_parts) if market_parts else "the area"

    property_summary = ""
    if deal.property_type == "House":
        parts = []
        if deal.beds:
            parts.append(f"{deal.beds}bed")
        if deal.baths:
            parts.append(f"{deal.baths}bath")
        if deal.sqft:
            parts.append(f"{deal.sqft}sqft")
        property_summary = " ".join(parts) if parts else "property"
    else:
        property_summary = deal.property_type.lower()

    operator_block = _build_operator_identity_block()
    buyer_intel = _build_buyer_intel_block(buyer, deal)
    thread_block = _build_thread_context_block(thread_context)

    user_prompt = (
        f"Write ghost recovery touch #{touch_number} email to re-engage a cash buyer who went silent.\n\n"
        f"BUYER:\n"
        f"Name: {buyer.full_name}\n"
        f"Email: {buyer.email}\n"
        f"Buy Box: {buyer.buy_box}\n"
        f"Tier: {buyer.buyer_tier or 'C-List'}\n"
        f"{buyer_intel}"
        f"\nDEAL:\n"
        f"Address: {deal.address}\n"
        f"Market: {market}\n"
        f"Property: {property_summary}\n"
        f"ARV: ${float(deal.arv):,.0f}\n"
        f"Asking: ${float(deal.asking_price):,.0f}\n"
        f"Repair Estimate: ${float(deal.repair_estimate) if deal.repair_estimate else 0:,.0f}\n"
        f"Floor: ${float(deal.floor_price):,.0f}\n"
        f"Type: {deal.property_type}\n"
        f"Condition: {deal.condition_description[:200]}\n"
        f"{thread_block}"
        f"\nRECOVERY TOUCH #{touch_number} ARC:\n"
        f"Arc: {arc_desc}\n"
        f"Instruction: {arc_instruction}\n\n"
        f"Return ONLY JSON:\n"
        f"{{\n"
        f'  "subject": "6-10 word subject line",\n'
        f'  "body": "email body text (2-5 sentences max)"\n'
        f"}}\n"
        f"Subject line must NOT contain the operator name. "
        f"Body must reference the specific conversation thread above. "
        f"Do NOT include unsubscribe links — this is a reply to an existing conversation."
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.7,
            max_tokens=350,
        )

        content = response.choices[0].message.content.strip()
        logger.debug("Ghost recovery touch %d response: %.200s", touch_number, content)

        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.strip().startswith("```"))

        parsed: dict = json.loads(extract_json_block(content))
        subject = (parsed.get("subject") or "").strip()
        body = (parsed.get("body") or "").strip()

        # Append operator sign-off if not already present
        sign_off = settings.operator_signature.strip()
        if sign_off and sign_off not in body:
            body = body.rstrip() + "\n\n" + sign_off

        # ── AI Validation pre-send guard ──
        try:
            validation = await validate_ai_output(
                content=body,
                content_type="ghost_recovery_email",
                deal=deal,
                buyer=buyer,
            )
            if validation.severity != "block":
                body = validation.corrected_content or body
            else:
                logger.error(
                    "Ghost recovery email blocked by AI validator "
                    "for buyer %s deal %s: %s",
                    buyer.id, deal.id, validation.violations,
                )
                return {
                    "subject": subject,
                    "body": body,
                    "touch_number": touch_number,
                    "validation_blocked": True,
                    "validation_violations": validation.violations,
                }
        except Exception as val_err:
            logger.error(
                "AI validator failed for ghost recovery email, "
                "proceeding with unvalidated content: %s", val_err,
            )

        logger.info(
            "Generated ghost recovery touch %d for buyer %s on deal %s: '%.60s'",
            touch_number, buyer.id, deal.id, subject,
        )

        return {
            "subject": subject,
            "body": body,
            "touch_number": touch_number,
        }

    except json.JSONDecodeError as e:
        logger.error(
            "Failed to parse Groq JSON for ghost recovery touch %d: %s\nResponse: %.200s",
            touch_number, e, content if 'content' in locals() else "(no response)",
        )
        return {
            "subject": f"Re: {deal.address}",
            "body": (
                f"Hi {buyer.full_name},\n\n"
                f"Circling back on {deal.address} — wanted to see if you're still "
                f"looking in {market}.\n\n"
                f"{settings.operator_signature}"
            ),
            "touch_number": touch_number,
        }
    except Exception as e:
        logger.error(
            "Groq API error for ghost recovery touch %d: %s",
            touch_number, e, exc_info=True,
        )
        return {
            "subject": f"Re: {deal.address}",
            "body": (
                f"Hi {buyer.full_name},\n\n"
                f"Just checking in on {deal.address}.\n\n"
                f"{settings.operator_signature}"
            ),
            "touch_number": touch_number,
        }
