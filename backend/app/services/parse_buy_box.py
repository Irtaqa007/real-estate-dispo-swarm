"""Parse a buyer's free-text buy_box into structured filter fields.

Uses Groq AI to extract:
- price_min / price_max: Numeric price range the buyer operates in
- pref_property_type: House, Land, or None (both)
- pref_cities: Preferred cities/areas

The parsed fields are used for hard-filter matching (step 1) before
semantic similarity ranking (step 2).

If Groq is unavailable or parsing fails, the function returns None
for all fields, and matching proceeds with no hard filters for that buyer.
"""

import json
import logging
from typing import Dict, Optional

from app.services.groq_client import groq_chat_completion, extract_json_block

logger = logging.getLogger(__name__)


async def parse_buy_box(buy_box: str) -> Dict:
    """Parse a free-text buy_box into structured filter fields.

    Args:
        buy_box: The buyer's free-text buy_box string.

    Returns:
        Dict with keys:
            price_min (float or None): Minimum price (in dollars).
            price_max (float or None): Maximum price (in dollars).
            pref_property_type (str or None): "House", "Land", or None (both).
            pref_cities (list[str] or None): List of preferred cities/areas.
    """
    if not buy_box or not buy_box.strip():
        return {"price_min": None, "price_max": None, "pref_property_type": None, "pref_cities": None}

    messages = [
        {
            "role": "system",
            "content": (
                "You are a real estate data parser. Extract structured fields from a "
                "buyer's free-text buy box description. Return ONLY valid JSON, no explanations."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Parse this buyer's buy box and extract:\n"
                f"1. price_min: minimum price they'll pay (in dollars, or null if not specified)\n"
                f"2. price_max: maximum price they'll pay (in dollars, or null if not specified)\n"
                f"3. pref_property_type: \"House\", \"Land\", or null if they accept both or it's unclear\n"
                f"4. pref_cities: list of preferred cities/areas they mentioned, or null if none\n\n"
                f"Buy box: \"{buy_box}\"\n\n"
                f"Return ONLY a JSON object like:\n"
                f'{{"price_min": 150000, "price_max": 350000, '
                f'"pref_property_type": "House", "pref_cities": ["Dallas", "Fort Worth"]}}\n'
                f"Use null (not 0 or empty) for missing values."
            ),
        },
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=300,
        )
        content = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.strip().startswith("```"))

        parsed = json.loads(extract_json_block(content))
        result = {
            "price_min": _safe_float(parsed.get("price_min")),
            "price_max": _safe_float(parsed.get("price_max")),
            "pref_property_type": parsed.get("pref_property_type") or None,
            "pref_cities": parsed.get("pref_cities") or None,
        }

        # Validate pref_property_type
        if result["pref_property_type"] not in (None, "House", "Land"):
            result["pref_property_type"] = None

        logger.debug(
            "Parsed buy_box: price_min=%s, price_max=%s, type=%s, cities=%s",
            result["price_min"], result["price_max"],
            result["pref_property_type"], result["pref_cities"],
        )
        return result

    except Exception as e:
        logger.warning(
            "Failed to parse buy_box via Groq, falling back to None: %s",
            e, exc_info=True,
        )
        return {"price_min": None, "price_max": None, "pref_property_type": None, "pref_cities": None}


def _safe_float(val) -> Optional[float]:
    """Convert a value to float or return None."""
    if val is None:
        return None
    try:
        f = float(val)
        if f <= 0:
            return None
        return f
    except (ValueError, TypeError):
        return None
