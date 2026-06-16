"""Smart negotiation service for handling buyer counter offers.

When a buyer counters with a price:
- If counter_price >= deal.floor_price: auto-approve, draft assignment response
- If counter_price < deal.floor_price: notify user for manual decision

Returns AI-generated response text suitable for the buyer.
"""

import logging
from typing import Optional

from app.models.schemas import Deal
from app.services.groq_client import groq_chat_completion

logger = logging.getLogger(__name__)

_NEGOTIATION_SYSTEM_PROMPT = (
    "You are a wholesale real estate negotiator. "
    "Keep responses concise, professional, and conversational."
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
            f"The buyer {buyer_name} countered at ${counter_price:,.0f} "
            f"for {deal.address}. The deal's floor price is ${float(deal.floor_price):,.0f}. "
            f"We have authority to accept offers at or above floor price.\n\n"
            f"Write a professional response letting the buyer know:\n"
            f"1. We can do that price — happy to make it work\n"
            f"2. We're sending the assignment contract now\n"
            f"3. Next steps: title, closing timeline\n\n"
            f"Keep it to 3-4 sentences. Warm but professional."
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
            f"The buyer {buyer_name} countered at ${counter_price:,.0f} "
            f"for {deal.address} (asking: ${float(deal.asking_price):,.0f}). "
            f"This is below our floor price of ${float(deal.floor_price):,.0f}, "
            f"so we need partner approval.\n\n"
            f"Write a professional response:\n"
            f"1. Thank them for the offer\n"
            f"2. Say you need to discuss with your partner\n"
            f"3. You'll get back to them within 24 hours\n\n"
            f"Keep it to 3 sentences. Don't mention the floor price."
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
