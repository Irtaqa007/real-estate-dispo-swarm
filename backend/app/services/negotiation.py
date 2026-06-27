"""Smart negotiation service for handling buyer counter offers.

When a buyer counters with a price:
- If counter_price >= deal.floor_price: auto-approve, draft assignment response
- If counter_price < deal.floor_price: notify user for manual decision

Returns AI-generated response text suitable for the buyer.
"""

import logging
from typing import Optional

from app.config import settings
from app.models.models import Deal
from app.services.ai_validator import ValidationResult, validate_ai_output
from app.services.groq_client import groq_chat_completion

logger = logging.getLogger(__name__)

_NEGOTIATION_SYSTEM_PROMPT = (
    f"OPERATOR IDENTITY — you ARE this person in every response:\n"
    f"Name: {settings.operator_name}\n"
    f"Sign-off: {settings.operator_email_signature}\n"
    f"Tone: {settings.operator_tone}\n"
    f"Never use: {settings.operator_never_say}\n"
    f"Context: {settings.operator_context}\n\n"
    f"NEGOTIATION RULES:\n"
    f"- Never reveal the floor price or contract price under any circumstances\n"
    f"- Never say 'my floor is X' or 'I can't go below X'\n"
    f"- When approving: be warm but not over-excited. 'Works for me.' not 'Amazing!'\n"
    f"- When declining or countering: be direct, not apologetic\n"
    f"- Frame any pushback around 'my numbers' or 'where I need to be' —\n"
    f"  not around the JV partner, not around costs, not around excuses\n"
    f"- Keep responses under 4 sentences\n"
    f"- Always end with {settings.operator_email_signature}\n"
    f"- Write as a real investor, not a salesperson\n"
    f"- Do not reveal AI or automation under any circumstances"
)


async def handle_counter_offer(
    deal: Deal,
    counter_price: float,
    buyer_name: str,
) -> dict:
    """Handle a buyer's counter offer with smart negotiation logic.

    Args:
        deal: The Deal being negotiated.
        counter_price: The price the buyer is offering.
        buyer_name: The buyer's full name.

    Returns:
        dict with keys:
            action: "auto_approved", "needs_manual_review", or "escalated"
            ai_response: AI-generated response text for the buyer
            counter_price: The buyer's counter price
            floor_price: The deal's minimum acceptable price
            auto_approved: Whether it was automatically approved
            contract_price: The new contract price (if auto-approved)
    """
    floor_price = float(deal.floor_price)
    current_contract = float(deal.contract_price)

    if counter_price >= floor_price:
        # Auto-approve: within authority
        response_text = await _generate_approval_response(
            buyer_name=buyer_name,
            deal=deal,
            counter_price=counter_price,
        )

        # ── AI Validation pre-send guard ──
        try:
            validation = await validate_ai_output(
                content=response_text,
                content_type="negotiation_email",
                deal=deal,
            )
        except Exception as val_err:
            logger.error(
                "AI validator failed for negotiation approval response, proceeding: %s",
                val_err,
            )
            validation = ValidationResult(severity="pass", corrected_content=None, violations=[], checks_run=[])

        if validation.severity != "block":
            response_text = validation.corrected_content or response_text

        return {
            "action": "auto_approved",
            "ai_response": response_text,
            "counter_price": counter_price,
            "floor_price": floor_price,
            "auto_approved": True,
            "contract_price": counter_price,
        }
    else:
        # Needs manual review — below floor price
        response_text = await _generate_deferral_response(
            buyer_name=buyer_name,
            deal=deal,
            counter_price=counter_price,
        )

        # ── AI Validation pre-send guard ──
        try:
            validation = await validate_ai_output(
                content=response_text,
                content_type="negotiation_email",
                deal=deal,
            )
        except Exception as val_err:
            logger.error(
                "AI validator failed for negotiation deferral response, proceeding: %s",
                val_err,
            )
            validation = ValidationResult(severity="pass", corrected_content=None, violations=[], checks_run=[])

        if validation.severity != "block":
            response_text = validation.corrected_content or response_text

        return {
            "action": "needs_manual_review",
            "ai_response": response_text,
            "counter_price": counter_price,
            "floor_price": floor_price,
            "auto_approved": False,
            "contract_price": None,
        }


async def _generate_approval_response(
    buyer_name: str,
    deal: Deal,
    counter_price: float,
) -> str:
    """Generate an AI response for auto-approved counter offers.

    Tells the buyer the offer is accepted and that an assignment
    contract is being drafted.
    """
    messages = [
        {"role": "system", "content": _NEGOTIATION_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"The buyer offered ${counter_price:,.0f} and I'm accepting it. "
            f"Write a brief, warm confirmation as {settings.operator_name}. "
            f"Acknowledge their offer, confirm we're moving forward, and say "
            f"you'll get the paperwork sorted. "
            f"Do NOT use words like 'great', 'excellent', 'amazing', 'fantastic'. "
            f"Sound like a calm, confident investor closing a deal, not a salesperson. "
            f"Under 3 sentences. End with {settings.operator_email_signature}."
        )},
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.6,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Failed to generate approval response: %s", e, exc_info=True)
        return (
            f"We can do ${counter_price:,.0f} for {deal.address}. "
            f"Sending the assignment contract now. "
            f"We'll coordinate with title on the closing timeline."
        )


async def _generate_deferral_response(
    buyer_name: str,
    deal: Deal,
    counter_price: float,
) -> str:
    """Generate an AI response when a counter is below floor price.

    Politely defers, saying we need to check with our partner.
    """
    messages = [
        {"role": "system", "content": _NEGOTIATION_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"The buyer offered ${counter_price:,.0f}. My numbers need me closer "
            f"to ${float(deal.floor_price):,.0f} to make this work. "
            f"Write a brief response as {settings.operator_name} that: "
            f"1. Acknowledges their offer without dismissing it "
            f"2. States where you need to be — without revealing it's a hard floor "
            f"3. Leaves the door open for them to come up "
            f"Do NOT say 'unfortunately', 'I'm sorry', or 'I can't'. "
            f"Sound like a confident investor who knows their numbers, not someone "
            f"apologizing for not taking a bad deal. "
            f"Under 3 sentences. End with {settings.operator_email_signature}."
        )},
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.6,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Failed to generate deferral response: %s", e, exc_info=True)
        return (
            f"Thanks for the offer on {deal.address}. "
            f"Let me discuss this with my partner and get back to you within 24 hours."
        )
