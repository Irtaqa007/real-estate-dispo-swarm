"""Psychologically-optimized 6-touch email campaign engine using Groq AI.

Each touch follows a specific psychological arc with targeted word frequency,
trigger weights, and CTA psychology based on wholesale real estate expertise.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from uuid import UUID

from app.config import settings
from app.services.groq_client import groq_chat_completion
from app.services.opt_out import append_unsubscribe_footer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Touch configuration — each touch defines its psychological arc, prompt
# instructions, power words, CTA type, and scheduling delay from launch.
# ---------------------------------------------------------------------------

TOUCH_CONFIGS = [
    {
        "touch": 1,
        "delay_days": 0,
        "arc": "Pattern Interrupt + Reciprocity",
        "arc_description": (
            "Break inbox monotony. Offer value first (numbers, photos) before asking. "
            "Reciprocity principle: give data, get attention."
        ),
        "subject_formula": "{Address} — ${Asking:,} | ${Spread:,} spread",
        "body_structure": (
            "1. Pattern interrupt: Reference buyer's specific criteria from buy_box. "
            "2. Value stack: 3 numbers only — ARV, asking, spread. No fluff. "
            "3. Reciprocity hook: Offer Google Drive folder with photos. "
            "4. Low-friction CTA."
        ),
        "power_words": "photos, spread, cash, numbers",
        "cta_type": "Low-friction: 'Worth a look?' (not 'Buy now')",
    },
    {
        "touch": 2,
        "delay_days": 2,
        "arc": "Curiosity Gap + Value Deepening",
        "arc_description": (
            "Zeigarnik effect — brains hate open loops. Touch 1 created curiosity; "
            "Touch 2 closes part of it while opening a new loop."
        ),
        "subject_formula": "Re: {Address} — rehab estimate + comps inside",
        "body_structure": (
            "1. Loop closure: 'Quick follow-up on the deal I sent Tuesday'. "
            "2. New value: Rehab estimate, recent comp within 0.5 miles. "
            "3. Social proof seed: 'Similar property on {Street} closed last month at ${CompPrice}'. "
            "4. Curiosity CTA: 'Want the full comp report?'"
        ),
        "power_words": "comps, estimate, closed, similar, report",
        "cta_type": "Curiosity gap: 'Want the full comp report?'",
    },
    {
        "touch": 3,
        "delay_days": 4,
        "arc": "Social Proof + Authority",
        "arc_description": (
            "Buyers trust validated opportunities. 'Others are looking' triggers "
            "FOMO without desperation."
        ),
        "subject_formula": "{Address} — getting serious interest",
        "body_structure": (
            "1. Authority frame: 'Quick update from my desk'. "
            "2. Social proof: Reference genuine deal activity or market "
            "demand without fabricating specific buyer counts. "
            "Use language like 'this one is getting attention' or "
            "'moving quickly in this market' — never invent specific "
            "numbers of buyers or inquiries. "
            "3. Scarcity nudge: Haven't released it to the full list yet. "
            "4. Exclusivity CTA: 'Want first shot before I open it up?'"
        ),
        "power_words": "network, attention, moving, released, first shot, interest",
        "cta_type": "Exclusivity: 'Want first shot before I open it up?'",
    },
    {
        "touch": 4,
        "delay_days": 7,
        "arc": "Scarcity + Loss Aversion",
        "arc_description": (
            "Loss aversion is 2x stronger than gain pursuit. Frame missing the deal "
            "as a loss, not missing a gain."
        ),
        "subject_formula": "Checking in — {Address} still available (for now)",
        "body_structure": (
            "1. Status update: 'This one's still on my desk'. "
            "2. Competitive pressure: Another buyer submitted a soft offer — reviewing tomorrow. "
            "3. Loss frame: 'Don't want you to miss it if it's in your wheelhouse'. "
            "4. Action CTA: 'Can you lock it up by {Date}?'"
        ),
        "power_words": "soft offer, reviewing, miss, lock it up, wheelhouse",
        "cta_type": "Specific deadline: 'Can you lock it up by {Date}?'",
    },
    {
        "touch": 5,
        "delay_days": 9,
        "arc": "Urgency Peak + Authority Anchor",
        "arc_description": (
            "Time pressure + authority commitment. 'I need to decide' transfers urgency "
            "to the buyer."
        ),
        "subject_formula": "Last call: {Address} → releasing to partner tomorrow",
        "body_structure": (
            "1. Deadline clarity: Need to give JV partner yes/no by 5 PM tomorrow. "
            "2. Final value reminder: ARV, asking, spread — one last time. "
            "3. Authority stance: 'If you're in, I'll hold it. If not, I'll release it'. "
            "4. Binary CTA: 'In or out?'"
        ),
        "power_words": "last call, releasing, partner, hold, binary, in or out",
        "cta_type": "Binary: 'In or out?'",
    },
    {
        "touch": 6,
        "delay_days": 10,
        "arc": "Breakup + Future Commitment",
        "arc_description": (
            "15-25% reply rate on breakup emails. The 'loss' of the relationship "
            "triggers re-engagement."
        ),
        "subject_formula": "Closing the loop on {Address}",
        "body_structure": (
            "1. Clean close: Released the deal to my JV partner this morning. "
            "2. No blame: 'Timing wasn't right — totally understand'. "
            "3. Future hook: 'I'll keep you posted on the next heavy rehab. Usually get 2-3 per month'. "
            "4. Open door CTA: 'If anything changes on your end, just reply'."
        ),
        "power_words": "released, timing, keep you posted, next, reply",
        "cta_type": "Open door: 'If anything changes on your end, just reply'",
    },
]

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(
    touch: int,
    buyer_name: str,
    buyer_email: str,
    buy_box: str,
    buyer_tier: str,
    address: str,
    city: str,
    state: str,
    property_type: str,
    arv: float,
    asking_price: float,
    spread: float,
    condition_description: str,
    beds: Optional[int] = None,
    baths: Optional[float] = None,
    sqft: Optional[int] = None,
    # FEATURE 4: Buyer Intelligence parameters
    deals_closed: int = 0,
    last_reply_at: Optional[datetime] = None,
    engagement_score: float = 0.0,
    portfolio_insights: Optional[dict] = None,
    avg_spread_closed: Optional[float] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    pref_cities: Optional[list[str]] = None,
) -> list[dict]:
    """Build the Groq chat prompt for generating a touch email."""

    config = TOUCH_CONFIGS[touch - 1]

    # Build market descriptor for buy_box reference
    market_parts = [p for p in [city, state] if p]
    market = ", ".join(market_parts) if market_parts else "the area"

    # Property summary
    if property_type == "House" and beds and baths:
        property_summary = f"{beds}bed/{baths}bath {sqft or ''}sqft".strip()
    elif property_type == "Land":
        property_summary = "land"
    else:
        property_summary = property_type

    # ── FEATURE 4: Buyer Intelligence section ──
    intelligence_lines = []
    if deals_closed > 0:
        intelligence_lines.append(
            f"Buyer has closed {deals_closed} deal(s) previously — reference their track "
            f"record naturally in the email if relevant and natural, but don't mention "
            f"it if it would feel forced."
        )
    if last_reply_at:
        days_since = (datetime.now(timezone.utc) - last_reply_at).days
        if days_since <= 7:
            intelligence_lines.append(
                f"Buyer last engaged {days_since} day(s) ago — warm tone, recent engagement."
            )
        elif days_since <= 30:
            intelligence_lines.append(
                f"Buyer last engaged {days_since} day(s) ago — moderate, normal follow-up."
            )
        else:
            intelligence_lines.append(
                f"Buyer last engaged {days_since} day(s) ago — re-engagement tone needed."
            )
    if engagement_score > 0:
        if engagement_score >= 50:
            intelligence_lines.append(
                f"Engagement score: {engagement_score:.0f}/100 — very active, responsive buyer."
            )
        elif engagement_score >= 20:
            intelligence_lines.append(
                f"Engagement score: {engagement_score:.0f}/100 — moderately active buyer."
            )
        else:
            intelligence_lines.append(
                f"Engagement score: {engagement_score:.0f}/100 — low activity, needs convincing."
            )
    if avg_spread_closed is not None and avg_spread_closed > 0:
        if spread > 0:
            pct_diff = ((spread - avg_spread_closed) /
                        avg_spread_closed) * 100
            if pct_diff >= 10:
                comparison = (
                    f"above their typical deal ({pct_diff:.0f}% "
                    f"higher than their avg ${avg_spread_closed:,.0f})"
                )
            elif pct_diff <= -10:
                comparison = (
                    f"below their typical deal ({abs(pct_diff):.0f}% "
                    f"lower than their avg ${avg_spread_closed:,.0f})"
                )
            else:
                comparison = (
                    f"right in line with their typical "
                    f"${avg_spread_closed:,.0f} avg deal"
                )
            intelligence_lines.append(
                f"This deal's spread (${spread:,.0f}) is {comparison} "
                f"— calibrate pitch accordingly."
            )
        else:
            # spread not available, just note their typical size
            intelligence_lines.append(
                f"Buyer typically closes deals with ~${avg_spread_closed:,.0f} "
                f"spread — reference their experience level naturally."
            )
    if pref_cities:
        cities_str = ", ".join(str(c) for c in pref_cities if c)
        if cities_str:
            intelligence_lines.append(
                f"Buyer's preferred cities: [{cities_str}] — confirm this deal is in "
                f"their geography either explicitly or implicitly."
            )
    if price_min is not None or price_max is not None:
        price_range = f"${price_min:,.0f}–${price_max:,.0f}" if price_min is not None and price_max is not None else \
                      f"${price_min:,.0f}+" if price_min is not None else \
                      f"Up to ${price_max:,.0f}"
        intelligence_lines.append(
            f"Buyer price range: {price_range} — confirm the deal fits within this range."
        )
    if portfolio_insights:
        pi_str = json.dumps(portfolio_insights, default=str)[:300]
        intelligence_lines.append(
            f"Portfolio insight context: {pi_str}"
        )

    intelligence_block = (
        "\nBUYER INTELLIGENCE (use to personalize the email):\n"
        + "\n".join(f"- {line}" for line in intelligence_lines)
        + "\n"
    ) if intelligence_lines else ""

    operator_id_block = (
        "\n"
        "OPERATOR IDENTITY (you ARE this person, write as them):\n"
        f"Name: {settings.operator_name}\n"
        f"Sign-off: {settings.operator_email_signature}\n"
        f"Tone: {settings.operator_tone}\n"
        f"Never use these words/phrases: {settings.operator_never_say}\n"
        f"Personal context (use naturally if relevant):\n"
        f"{settings.operator_context}\n"
        f"IMPORTANT: Subject line must NEVER contain the operator name — "
        f"subject lines are deal-focused only.\n"
    )

    system_prompt = (
        "You are a wholesale real estate disposition expert with 15 years of experience. "
        "You write concise, professional pitch emails to cash buyers. "
        "Never sound desperate. Be conversational and direct. "
        "Always reference the buyer's specific criteria from their buy box. "
        f"Return ONLY valid JSON with keys: subject, body. No markdown, no code fences."
        f"{operator_id_block}"
    )

    user_prompt = (
        f"Write touch #{touch} pitch email to a cash buyer.\n\n"
        f"BUYER PROFILE:\n"
        f"Name: {buyer_name}\n"
        f"Email: {buyer_email}\n"
        f"Buy Box: {buy_box}\n"
        f"Tier: {buyer_tier}\n\n"
        f"DEAL DETAILS:\n"
        f"Address: {address}\n"
        f"Market: {market}\n"
        f"Property Type: {property_summary}\n"
        f"ARV: ${arv:,.0f}\n"
        f"Asking: ${asking_price:,.0f}\n"
        f"Spread: ${spread:,.0f}\n"
        f"Condition: {condition_description}\n"
        f"{intelligence_block}"
        f"PSYCHOLOGICAL ARC FOR TOUCH {touch}: {config['arc_description']}\n"
        f"REQUIRED POWER WORDS: {config['power_words']}\n"
        f"MAX SENTENCES: 4\n"
        f"TONE: Professional, conversational, never desperate\n"
        f"CTA TYPE: {config['cta_type']}\n\n"
        f"Subject line must be 6-10 words. Body must reference buyer's specific criteria.\n"
        f"Return ONLY JSON: {{\"subject\": \"...\", \"body\": \"...\"}}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ---------------------------------------------------------------------------
# Email generation
# ---------------------------------------------------------------------------

async def generate_touch_email(
    touch: int,
    buyer_name: str,
    buyer_email: str,
    buy_box: str,
    buyer_tier: str,
    address: str,
    city: str,
    state: str,
    property_type: str,
    arv: float,
    asking_price: float,
    spread: float,
    condition_description: str,
    beds: Optional[int] = None,
    baths: Optional[float] = None,
    sqft: Optional[int] = None,
    buyer_id: Optional[UUID] = None,
    # FEATURE 4: Buyer Intelligence parameters
    deals_closed: int = 0,
    last_reply_at: Optional[datetime] = None,
    engagement_score: float = 0.0,
    portfolio_insights: Optional[dict] = None,
    avg_spread_closed: Optional[float] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    pref_cities: Optional[list[str]] = None,
) -> dict:
    """Generate a single touch email using Groq AI.

    Args:
        buyer_id: If provided, a CAN-SPAM compliant unsubscribe link
                  is appended to the email body.
        deals_closed: Number of deals this buyer has closed.
        last_reply_at: When the buyer last replied (for recency tone).
        engagement_score: Activity score 0-100.
        portfolio_insights: Any stored portfolio insights dict.
        avg_spread_closed: Buyer's average deal spread.
        price_min/max: Buyer's preferred price range.
        pref_cities: Buyer's preferred cities/areas.

    Returns:
        dict with keys: subject, body, touch, status, scheduled_at
    """
    config = TOUCH_CONFIGS[touch - 1]
    messages = _build_prompt(
        touch=touch,
        buyer_name=buyer_name,
        buyer_email=buyer_email,
        buy_box=buy_box,
        buyer_tier=buyer_tier,
        address=address,
        city=city,
        state=state,
        property_type=property_type,
        arv=arv,
        asking_price=asking_price,
        spread=spread,
        condition_description=condition_description,
        beds=beds,
        baths=baths,
        sqft=sqft,
        deals_closed=deals_closed,
        last_reply_at=last_reply_at,
        engagement_score=engagement_score,
        portfolio_insights=portfolio_insights,
        avg_spread_closed=avg_spread_closed,
        price_min=price_min,
        price_max=price_max,
        pref_cities=pref_cities,
    )

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.7,
            max_tokens=300,
        )

        content = response.choices[0].message.content.strip()
        logger.debug("Groq response for touch %d: %.200s", touch, content)

        # Parse JSON from response — handle possible markdown fences
        if content.startswith("```"):
            # Extract JSON from code fence
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.strip().startswith("```"))

        parsed = json.loads(content)
        subject = parsed.get("subject", "").strip()
        body = parsed.get("body", "").strip()

        # Append operator sign-off if not already present (post-generation guardrail)
        sign_off = settings.operator_email_signature.strip()
        if sign_off and not body.rstrip().endswith(sign_off):
            body = body.rstrip() + "\n\n" + sign_off

        # Append unsubscribe footer if buyer_id is known
        if buyer_id is not None:
            body = append_unsubscribe_footer(body, buyer_id)

        logger.info("Generated touch %d for %s: '%s'", touch, buyer_name, subject[:60])

        return {
            "subject": subject,
            "body": body,
            "touch": touch,
            "status": "Ready" if touch == 1 else "Queued",
            "scheduled_at": None,  # Will be set by caller
        }

    except json.JSONDecodeError as e:
        logger.error("Failed to parse Groq JSON for touch %d: %s\nResponse: %.200s", touch, e, content)
        return {
            "subject": f"Touch {touch} — {address}",
            "body": f"Follow-up on {address} (auto-generated).",
            "touch": touch,
            "status": "Failed",
            "scheduled_at": None,
        }
    except Exception as e:
        logger.error("Groq API error for touch %d: %s", touch, e, exc_info=True)
        return {
            "subject": f"Touch {touch} — {address}",
            "body": f"Follow-up on {address} (auto-generated).",
            "touch": touch,
            "status": "Failed",
            "scheduled_at": None,
        }


def get_touch_schedule(launch_at: Optional[datetime] = None) -> list[dict]:
    """Get the schedule of all 6 touches without generating content.

    Useful for previewing the campaign timeline.
    """
    if launch_at is None:
        launch_at = datetime.now(timezone.utc)

    schedule = []
    for config in TOUCH_CONFIGS:
        scheduled = launch_at + timedelta(days=config["delay_days"])
        schedule.append({
            "touch": config["touch"],
            "delay_days": config["delay_days"],
            "scheduled_at": scheduled.isoformat(),
            "arc": config["arc"],
            "subject_formula": config["subject_formula"],
            "cta_type": config["cta_type"],
        })

    return schedule
