"""AI-powered pass reason extraction service.

Extracts structured pass reasons from buyer replies using Groq AI.
Categories: price_too_high, wrong_market, condition, title_issue, timing,
            asset_class, budget_changed, already_under_contract,
            no_reason_given, other
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.models import Buyer, Campaign, Deal
from app.services.groq_client import groq_chat_completion

logger = logging.getLogger(__name__)

_PASS_CATEGORIES = [
    "price_too_high",
    "wrong_market",
    "condition",
    "title_issue",
    "timing",
    "asset_class",
    "budget_changed",
    "already_under_contract",
    "no_reason_given",
    "other",
]

_SYSTEM_PROMPT = (
    "You are a real estate data extraction specialist. "
    "Extract structured pass reason data from a buyer's reply email. "
    "Be precise and objective. Do not infer beyond what the buyer actually said."
)

_USER_PROMPT_TEMPLATE = """DEAL DETAILS:
Address: {address}
City: {city}, {state}
Property Type: {property_type}
Asking Price: ${asking_price:,.0f}
Floor Price: ${floor_price:,.0f}
Contract Price: ${contract_price:,.0f}

BUYER PROFILE:
Name: {buyer_name}
Buy Box: {buy_box}

THREAD CONTEXT (last 3 exchanges):
{thread_context}

BUYER'S PASS REPLY:
{reply_body}

TASK — Extract the pass reason:

1. Category (pick exactly ONE from this list):
   - price_too_high: Buyer says price is too high, over budget, or expensive
   - wrong_market: Buyer says wrong area, city, or neighborhood
   - condition: Buyer mentions property condition, repairs needed
   - title_issue: Buyer mentions title problems, liens, or legal issues
   - timing: Buyer says not now, too early, check back later, maybe later
   - asset_class: Buyer says wrong property type (wants multi-family not single family, etc.)
   - budget_changed: Buyer says their budget has changed or their situation changed
   - already_under_contract: Buyer says they already have a deal or are already under contract
   - no_reason_given: Buyer passes without giving any specific reason
   - other: Any reason not covered above

2. Raw text: The buyer's exact words explaining why they're passing (max 100 chars)

3. Confidence:
   - high: Buyer explicitly stated the reason (e.g. "The price is too high")
   - medium: Reason is implied but clear (e.g. "Not in my budget" → implies price)
   - low: Vague or unclear (e.g. "Not for me", "Not interested")

4. Buy box signal: If the pass reveals a change in what the buyer wants, extract:
   - field: "price_max" | "price_min" | "pref_property_type" | "pref_cities" | null
   - direction: "lower" | "higher" | "narrower" | "broader" | null
   - signal_strength: "high" | "medium" | "low"
   If no buy box signal is detected, set buy_box_signal to null.

Return ONLY JSON:
{{
    "category": "price_too_high",
    "raw": "The price is too high for this market",
    "confidence": "high",
    "buy_box_signal": {{
        "field": "price_max",
        "direction": "lower",
        "signal_strength": "medium"
    }}
}}

If the buyer gives no specific reason:
{{
    "category": "no_reason_given",
    "raw": "",
    "confidence": "low",
    "buy_box_signal": null
}}
"""


async def extract_pass_reason(
    reply_body: str,
    thread_context: List[Campaign],
    deal: Deal,
    buyer: Buyer,
) -> dict:
    """Use AI to extract and categorize the pass reason from a buyer's reply.

    Args:
        reply_body: The buyer's reply email body text.
        thread_context: List of recent Campaign rows for this buyer+deal (ordered by sent_at desc).
        deal: The Deal object being replied about.
        buyer: The Buyer object who is passing.

    Returns:
        dict with keys:
            category (str): One of the PASS_CATEGORIES.
            raw (str): Buyer's exact words (max 100 chars).
            confidence (str): "high", "medium", or "low".
            buy_box_signal (dict or None): {"field", "direction", "signal_strength"} or None.
    """
    # Build thread context string (last 3 exchanges)
    thread_items = []
    for c in thread_context[:3]:
        sent_line = f"  Sent (touch {c.touch_number}): {c.subject or ''}"
        thread_items.append(sent_line)
        if c.reply_body:
            thread_items.append(f"  Reply: {c.reply_body[:200]}")
    thread_str = "\n".join(thread_items) if thread_items else "(no prior conversation)"

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        address=deal.address or "",
        city=deal.city or "",
        state=deal.state or "",
        property_type=deal.property_type or "",
        asking_price=float(deal.asking_price) if deal.asking_price else 0,
        floor_price=float(deal.floor_price) if deal.floor_price else 0,
        contract_price=float(deal.contract_price) if deal.contract_price else 0,
        buyer_name=buyer.full_name or "",
        buy_box=buyer.buy_box or "",
        thread_context=thread_str,
        reply_body=reply_body[:1000],
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            model="llama-3.1-8b-instant",
            temperature=0.1,
            max_tokens=300,
        )

        content = response.choices[0].message.content.strip()
        logger.debug("Pass reason extraction response: %.200s", content)

        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.strip().startswith("```"))

        parsed: dict = json.loads(content)

        category = parsed.get("category", "no_reason_given")
        if category not in _PASS_CATEGORIES:
            category = "no_reason_given"

        raw = (parsed.get("raw") or "").strip()[:100]
        confidence = parsed.get("confidence", "low")
        if confidence not in ("high", "medium", "low"):
            confidence = "low"

        buy_box_signal = parsed.get("buy_box_signal")
        if buy_box_signal and not isinstance(buy_box_signal, dict):
            buy_box_signal = None

        result = {
            "category": category,
            "raw": raw,
            "confidence": confidence,
            "buy_box_signal": buy_box_signal,
        }

        logger.info(
            "Pass reason extracted: category=%s, confidence=%s, raw=%.60s",
            category, confidence, raw,
        )

        return result

    except (json.JSONDecodeError, Exception) as e:
        logger.warning(
            "Failed to extract pass reason: %s — defaulting to no_reason_given",
            e, exc_info=True,
        )
        return {
            "category": "no_reason_given",
            "raw": "",
            "confidence": "low",
            "buy_box_signal": None,
        }
