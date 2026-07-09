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
from app.services.groq_client import groq_chat_completion, extract_json_block
from app.services.opt_out import append_unsubscribe_footer

__all__ = ['generate_touch_email', 'TOUCH_CONFIGS']


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
            "Break inbox monotony with specificity. This buyer gets deals "
            "from 10 wholesalers — be the one who clearly read their buy box. "
            "Lead with the numbers, not the pitch. Reciprocity principle: "
            "give value first (spread, address, numbers), ask nothing yet."
        ),
        "subject_formula": "{Address} — ${Asking:,} | ${Spread:,} spread",
        "body_structure": (
            "1. Open by referencing ONE specific thing from their buy box "
            "(city, property type, price range) — not generically, specifically. "
            "2. Three numbers only: ARV, asking price, spread. No adjectives. "
            "3. One line on condition — honest, not hyped. "
            "4. Low-friction close: 'Worth a look?' or 'Want the photos?'"
        ),
        "power_words": "spread, photos, numbers, cash",
        "cta_type": "Minimal ask: 'Worth a look?' — not 'Are you interested?'",
        "tone_note": (
            "Write like you're texting a fellow investor you respect, "
            "not pitching a stranger. Short sentences. No exclamation marks. "
            "No superlatives. Let the numbers do the selling."
        ),
    },
    {
        "touch": 2,
        "delay_days": 3,
        "arc": "Curiosity Gap + New Information",
        "arc_description": (
            "Zeigarnik effect — open loops demand closure. Touch 1 created "
            "curiosity with numbers. Touch 2 delivers something new they "
            "didn't have: a comp, a rehab estimate, or a detail that changes "
            "the picture. Give them a reason to re-engage that isn't 'just following up'."
        ),
        "subject_formula": "Re: {Address} — one thing I forgot to mention",
        "body_structure": (
            "1. Acknowledge Touch 1 briefly — one line, not apologetic. "
            "2. The new thing: a recent comp, a rehab number, an exit strategy "
            "angle you didn't mention. Make it genuinely useful. "
            "3. Soft question: 'Does that change anything for you?'"
        ),
        "power_words": "comp, rehab, exit, number, forgot",
        "cta_type": "Soft question that invites response without pressure",
        "tone_note": (
            "This email should feel like you remembered something important "
            "and sent a quick follow-up — not a scheduled drip sequence. "
            "Natural, human, slightly informal."
        ),
    },
    {
        "touch": 3,
        "delay_days": 6,
        "arc": "Social Proof + Market Context",
        "arc_description": (
            "Buyers trust validated opportunities. Reference real market "
            "activity — not fabricated urgency. The goal is to give them "
            "context that helps them make a decision, not pressure them."
        ),
        "subject_formula": "{Address} — update from my end",
        "body_structure": (
            "1. Brief update: what's happening with this deal or in this market. "
            "2. Social proof: reference genuine activity — 'this market is moving' "
            "or 'had some interest on this one' — NEVER invent specific buyer counts. "
            "3. Soft close: 'Still on your radar?' "
        ),
        "power_words": "update, market, moving, radar",
        "cta_type": "Simple question: 'Still on your radar?'",
        "tone_note": (
            "Factual, brief, zero hype. Sound like someone giving a friend "
            "a market update, not running a sales sequence."
        ),
    },
    {
        "touch": 4,
        "delay_days": 10,
        "arc": "New Angle + Value Reframe",
        "arc_description": (
            "If the price didn't land, maybe the exit strategy will. "
            "Present a different way to look at the same deal — a different "
            "exit, a different buyer profile who'd want this, a creative angle "
            "they may not have considered."
        ),
        "subject_formula": "{Address} — different angle on this one",
        "body_structure": (
            "1. One sentence acknowledging you've reached out before. "
            "2. The reframe: a different exit strategy, a different way to "
            "underwrite the deal, or a detail that hits differently now. "
            "3. Direct question: 'Does this version of the deal work better?'"
        ),
        "power_words": "angle, exit, strategy, reframe, different",
        "cta_type": "Direct question inviting a yes or a counter",
        "tone_note": (
            "Confident, not desperate. You're bringing new information, "
            "not begging. If the deal doesn't work for them, that's fine — "
            "say so implicitly through your calm tone."
        ),
    },
    {
        "touch": 5,
        "delay_days": 15,
        "arc": "Soft Urgency + Decision Point",
        "arc_description": (
            "Create a natural decision point without manufactured pressure. "
            "The deal has a lifecycle — it won't be available forever and "
            "that's simply true, not a sales tactic. Give them a clear "
            "yes/no framing."
        ),
        "subject_formula": "{Address} — decision deadline tomorrow",
        "body_structure": (
            "1. Acknowledge the timeline — you need to make a decision on "
            "this deal. Frame it as your own operational deadline, not pressure. "
            "2. Binary offer: in or out? Make it easy to say either. "
            "3. Brief: this is the shortest email in the sequence."
        ),
        "power_words": "deadline, decision, in or out, tomorrow",
        "cta_type": "Binary: 'In or out on this one?'",
        "tone_note": (
            "Extremely short. Three sentences maximum. The brevity itself "
            "signals the deadline is real. No explanation, no re-pitching "
            "the deal. They know the deal by now."
        ),
    },
    {
        "touch": 6,
        "delay_days": 21,
        "arc": "Clean Exit + Future Door Open",
        "arc_description": (
            "The breakup email. Not angry, not desperate — just closing the "
            "loop cleanly. The goal is to exit this deal conversation while "
            "keeping the relationship intact for the next deal. Buyers respect "
            "people who don't chase."
        ),
        "subject_formula": "Closing the loop on {Address}",
        "body_structure": (
            "1. Clean close: made a decision to move in a different direction "
            "on this one. Do not mention any partner or third party. "
            "2. Door open: 'When the next deal comes through that fits your "
            "criteria, I'll send it your way.' "
            "3. No ask. No CTA. Just a clean end."
        ),
        "power_words": "closing, loop, next, criteria",
        "cta_type": "None — this is an exit, not a pitch",
        "tone_note": (
            "Warm, brief, and final. The buyer should feel respected, not "
            "chased. This email should make them more likely to respond to "
            "your next deal, not less."
        ),
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
    rehab_estimate: Optional[float] = None,
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
        property_summary = f"{beds}bed/{int(baths) if baths and baths == int(baths) else baths}bath {sqft or ''}sqft".strip()
    elif property_type == "Land":
        property_summary = "land"
    else:
        property_summary = property_type

    # ── FEATURE 4: Buyer Intelligence section ──
    # IMPORTANT: These lines are INTERNAL CONTEXT for tone/angle only.
    # They must NEVER appear verbatim in the email body.
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
        "\nBUYER INTELLIGENCE (internal context ONLY — adjust tone/angle, NEVER quote in email body):\n"
        + "\n".join(f"- {line}" for line in intelligence_lines)
        + "\n"
    ) if intelligence_lines else ""

    operator_id_block = (
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
    )

    system_prompt = (
        f"OPERATOR IDENTITY — you ARE this person, write entirely as them:\n"
        f"Name: {settings.operator_name}\n"
        f"Sign-off every email with: {settings.operator_signature}\n"
        f"Tone: {settings.operator_tone}\n"
        f"Never use these words or phrases: {settings.operator_never_say}\n"
        f"Context: {settings.operator_context}\n\n"
        f"DEAL TYPE: This is an OFF-MARKET deal, not listed on MLS. "
        f"Mention 'off-market' naturally in touch 1. Use occasionally in later touches.\n\n"
        f"WRITING RULES:\n"
        f"- Write like a real investor texting another investor — not a marketer\n"
        f"- Short sentences. No exclamation marks. No superlatives.\n"
        f"- Never sound like a bulk email. Sound like one person writing to one person.\n"
        f"- Reference the buyer's specific buy box criteria — not generically\n"
        f"- Let numbers sell. Keep adjectives minimal.\n"
        f"- Write a COHESIVE pitch — not a bullet list of facts. Weave address, numbers, \n"
        f"  condition, and rehab into flowing sentences that tell one story.\n"
        f"- Mention rehab cost ONCE, integrated naturally (e.g. '$28k rehab, so you clear $35k').\n"
        f"- Baths: always write as integer if whole number (2 not 2.0).\n"
        f"- Do not reveal you are AI or automated under any circumstances\n"
        f"- Return ONLY valid JSON: {{\"subject\": \"...\", \"body\": \"...\"}}\n"
        f"- No markdown, no code fences, no explanation outside the JSON"
    )

    user_prompt = (
        f"Write touch #{touch} pitch email to a cash buyer.\n\n"
        f"BUYER PROFILE:\n"
        f"Name: {buyer_name}\n"
        f"Email: {buyer_email}\n"
        f"Buy Box: {buy_box}\n"
        f"Tier: {buyer_tier}\n\n"
        f"DEAL DETAILS:\n"
        f"Address: {address} — OFF-MARKET, not listed on MLS\n"
        f"Market: {market}\n"
        f"Property Type: {property_summary}\n"
        f"Condition: {condition_description}\n"
        f"\n"
        f"\n"
        f"KEY NUMBERS (copy exactly, do not compute anything yourself):\n"
        f"  Asking price: ${asking_price:,.0f}\n"
        + (
            f"  Rehab estimate: ${rehab_estimate:,.0f}\n"
            f"  Buyer all-in: ${asking_price + rehab_estimate:,.0f}\n"
            f"  ARV: ${arv:,.0f}\n"
            f"  BUYER PROFIT AFTER FLIP: ${arv - asking_price - rehab_estimate:,.0f} — always use this number\n"
            if rehab_estimate else
            f"  ARV: ${arv:,.0f}\n"
            f"  Gross spread before rehab: ${arv - asking_price:,.0f}\n"
            f"  Note: rehab unknown — mention buyer should factor rehab into their numbers\n"
        )
        + f"\n"
        f"{intelligence_block}"
        f"PSYCHOLOGICAL ARC FOR TOUCH {touch}: {config['arc_description']}\n"
        f"REQUIRED POWER WORDS: {config['power_words']}\n"
        f"LENGTH: Match the psychological arc. Touch 1-2: 4-6 sentences. "
        f"Touch 3-4: 3-5 sentences. Touch 5: 2-3 sentences. Touch 6: 3-4 sentences.\n"
        f"TONE: Professional, conversational, never desperate\n"
        f"TONE NOTE: {config.get('tone_note', '')}\n"
        f"CTA TYPE: {config['cta_type']}\n\n"
        f"SUBJECT FORMAT: Use numbers — e.g. '3/2 {city} | ${asking_price//1000:.0f}k | ${int(arv-asking_price-(rehab_estimate or 0))//1000:.0f}k profit'. 6-10 words.\n"
        f"TOUCH 1 RULE: Must mention 'off-market' naturally in the body (not just subject).\n"
        f"NEVER use: 'is listed', 'listed at', 'listed for', 'on the market', 'just listed' — this is OFF-MARKET, not MLS.\n"
        f"Body must reference buyer's specific criteria.\n"
        f"DO NOT end the body with a sign-off like 'Best, Irtaqa' — it is appended automatically.\n"
        f"DO NOT mention photos, attachments, or documents unless photos field is explicitly provided.\n"
        + (
            f"NUMBERS TO USE: ${rehab_estimate:,.0f} rehab, ${arv - asking_price - rehab_estimate:,.0f} buyer profit AFTER rehab, ${asking_price + rehab_estimate:,.0f} all-in.\n"
            f"Include ALL THREE numbers in the body naturally. Never say 'before rehab'. Never omit rehab cost.\n"
            if rehab_estimate else
            f"Rehab cost UNKNOWN — do NOT state any profit number. Just mention asking, ARV, condition. Tell buyer to factor in their own rehab costs.\n"
        )
        + f"DO NOT say 'the spread is X' — say 'buyer profit is X' or 'you clear X after rehab'.\n"
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
    rehab_estimate: Optional[float] = None,
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

        # Parse JSON robustly — handles markdown fences AND reasoning-model
        # <think> blocks (qwen-qwq etc.) that would otherwise break json.loads
        content = extract_json_block(content)
        parsed = json.loads(content)
        subject = parsed.get("subject", "").strip()
        body = parsed.get("body", "").strip()

        # Post-process subject: fix beds/baths hallucination
        if beds and baths:
            import re as _re_sub
            subject = _re_sub.sub(
                r'\b\d+/\d+\b',
                f"{beds}/{int(baths)}",
                subject
            )

        # Post-process subject: replace ALL wrong spread/profit variants with correct buyer profit
        if rehab_estimate and rehab_estimate > 0:
            _correct       = arv - asking_price - rehab_estimate
            _wrong_gross   = arv - asking_price               # $55k (ARV-asking, no rehab)
            _wrong_assign  = spread                           # $47k (assignment fee = asking - contract)
            _correct_k     = f"${int(_correct)//1000:.0f}k"
            _correct_full  = f"${_correct:,.0f}"
            for _wrong in [_wrong_gross, _wrong_assign]:
                if _wrong > 0 and _wrong != _correct:
                    _wk = f"${int(_wrong)//1000:.0f}k"
                    _wf = f"${_wrong:,.0f}"
                    subject = subject.replace(_wf, _correct_full).replace(_wk, _correct_k)
            logger.debug("Post-process subject: corrected to buyer profit %s", _correct_k)

        # Post-process: rehab framing — profit is always AFTER rehab
        if rehab_estimate and rehab_estimate > 0:
            body = body.replace("before rehab", "after rehab")
            body = body.replace("gross profit", "buyer profit")

        # Post-process: enforce rehab mention when available
        if rehab_estimate and rehab_estimate > 0:
            if f"${int(rehab_estimate):,}" not in body and f"${int(rehab_estimate)//1000:.0f}k" not in body.lower() and "28" not in body:
                # Insert rehab naturally before the CTA
                body = body.rstrip()
                if "worth a look" in body.lower():
                    body = body.replace(
                        "Worth a look?", f"Rehab is estimated at ${int(rehab_estimate):,}. Worth a look?"
                    ).replace(
                        "worth a look?", f"Rehab is estimated at ${int(rehab_estimate):,}. Worth a look?"
                    )
                else:
                    body = body + f" Rehab estimate is ${int(rehab_estimate):,}."

        # Post-process: remove "listed" language (implies MLS / on-market)
        body = body.replace("is listed at", "is priced at")
        body = body.replace("listed at $", "priced at $")
        body = body.replace("listed for $", "priced at $")
        body = body.replace("listed at", "priced at")
        body = body.replace("listed for", "priced at")
        body = body.replace("on the market", "available")
        body = body.replace("On the market", "Available")
        body = body.replace("hit the market", "came up")
        body = body.replace("just listed", "just came up")
        body = body.replace("new listing", "new deal")

        # Post-process: remove "factor in your own costs" when rehab is known
        if rehab_estimate and rehab_estimate > 0:
            body = body.replace("you should factor in your own costs", "")
            body = body.replace("factor in your own rehab costs", "")
            body = body.replace("factor in your own numbers", "")
            body = body.replace("but you should factor in your own costs", "")
            body = body.replace(", but you should factor in your own costs.", ".")
            body = re.sub(r'  +', ' ', body).strip()

        # Post-process: fix spread/profit framing
        body = body.replace("spread before rehab", "buyer profit after rehab")
        body = body.replace("spread before renovation", "buyer profit after rehab")
        body = body.replace("a spread of", "a buyer profit of")
        body = body.replace("the spread is", "the buyer profit is")
        body = body.replace("spread is $", "buyer profit is $")
        body = body.replace("spread of $", "buyer profit of $")
        # Post-process: remove photo hallucinations
        body = body.replace("Photos are attached.", "I can send photos if you want them.")
        body = body.replace("Photos are attached,", "I can send photos.")
        body = body.replace("photos are attached.", "I can send photos if you want them.")
        body = body.replace("Photos show ", "")
        body = body.replace("photos show ", "")
        body = body.replace("Photo shows ", "")
        body = body.replace("See attached photos.", "")
        body = body.replace("Attached photos.", "")
        body = body.replace("spread of $", "buyer profit of $")

        # Post-process: fix "us" framing — this is buyer's profit, not shared
        body = body.replace("gives us a spread", "gives you a profit")
        body = body.replace("gives us a", "gives you a")
        body = body.replace("giving us a spread", "giving you a profit")
        body = body.replace("giving us a", "giving you a")
        body = body.replace("we have a spread", "you have a profit")
        body = body.replace("our spread", "your profit")

        # Post-process: replace banned words with acceptable alternatives
        _BANNED_REPLACEMENTS = {
            "distressed": "value-add",
            "motivated seller": "seller",
            "below market": "under ARV",
            "as-is": "in current condition",
            "guaranteed": "projected",
            "no-brainer": "strong deal",
            "steal": "strong value",
            "act now": "worth a call",
            "limited time": "available now",
            "don't miss out": "worth considering",
            "once in a lifetime": "rare find",
        }
        for _banned, _replacement in _BANNED_REPLACEMENTS.items():
            if _banned.lower() in body.lower():
                import re as _re2
                body = _re2.sub(_re2.escape(_banned), _replacement, body, flags=_re2.IGNORECASE)

        # Post-process: enforce off-market mention in Touch 1
        if touch == 1 and "off-market" not in body.lower() and "off market" not in body.lower():
            body = "This is an off-market deal — not listed anywhere. " + body

        # Post-process: strip any sign-off the AI included (it gets appended correctly later)
        import re as _re
        body = _re.sub(r'\n\s*Best,\s*\nIrtaqa\s*$', '', body, flags=_re.IGNORECASE).rstrip()
        body = _re.sub(r'\n\s*Best,\s*Irtaqa\s*$', '', body, flags=_re.IGNORECASE).rstrip()

        # Post-process: generic number correction
        # Replace ANY dollar amount in body/subject that matches a wrong computed value
        # with the correct buyer profit. Works for all deals regardless of numbers.
        if rehab_estimate and rehab_estimate > 0:
            correct = arv - asking_price - rehab_estimate
            correct_full = f"${correct:,.0f}"
            correct_k    = f"${int(correct)//1000:.0f}k"
            # Build list of all wrong values the AI might compute
            wrong_values = set()
            wrong_values.add(arv - asking_price)          # gross margin (no rehab)
            wrong_values.add(spread)                       # assignment fee
            wrong_values.add(asking_price - correct)      # some other variant
            wrong_values.discard(correct)                  # never replace correct value
            wrong_values.discard(0)
            for wrong in wrong_values:
                if wrong > 0:
                    w_full = f"${wrong:,.0f}"
                    w_k    = f"${int(wrong)//1000:.0f}k"
                    if w_full in body:
                        body = body.replace(w_full, correct_full)
                        logger.info("Fixed body number: %s -> %s", w_full, correct_full)
                    if w_k in body:
                        body = body.replace(w_k, correct_k)
                        logger.info("Fixed body number k-format: %s -> %s", w_k, correct_k)

        # Append unsubscribe footer (which also handles sign-off placement)
        if buyer_id is not None:
            body = append_unsubscribe_footer(body, buyer_id)
        else:
            # No unsubscribe footer — ensure sign-off is present
            sign_off = settings.operator_signature.strip()
            if sign_off and sign_off not in body:
                body = body.rstrip() + "\n\n" + sign_off

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