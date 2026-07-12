"""Conversation state machine for buyer engagement.

Replaces the dumb intent-bucket system with a real conversation engine.
The AI reads the full thread, current stage, and deal data — then decides
what to say next to naturally move the buyer toward commitment.

Conversation stages:
  pitching        -> Sending initial 6-touch sequence, no reply yet
  engaging        -> Buyer replied with interest/questions, conversation active
  qualifying      -> Buyer is warm, AI asking qualifying questions
  collecting_info -> Buyer agreed to buy, collecting: legal name, phone, title company
  contract_ready  -> All info collected, operator notified to generate contract
  closed          -> Deal closed
  dormant         -> No response after ghost recovery
  passed          -> Buyer passed

Contract alert fires ONLY when:
  1. Buyer explicitly agrees on price and wants to proceed
  2. Legal name collected
  3. Phone collected
  4. Title company preference collected
"""

import json
import logging
import re
from typing import Optional

from app.config import settings
from app.models.models import Campaign, Deal, Buyer
from app.services.groq_client import groq_chat_completion, extract_json_block



logger = logging.getLogger(__name__)

# Regex to extract dollar amounts from buyer replies as fallback.
# Group 1 = numeric part (with optional commas), Group 2 = optional k/thousand suffix.
# Handles: $185,000, $185k, $185000, 185k, 185,000, 185000, $185,000.00
_PRICE_FALLBACK_RE = re.compile(
    r'\$?(?:'
    r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)'  # $185,000 or $185,000.00
    r'|'
    r'(\d{4,})'                               # $185000 (no commas, 4+ digits)
    r')'
    r'\s*(k|K|thousand)?\b'                  # optional k/thousand suffix
)


def _extract_price_fallback(reply_body: str, known_prices: set[float]) -> Optional[float]:
    """Extract a dollar amount from reply that the AI might have missed.

    Only extracts if:
    - The amount looks like a deal price (> $10k)
    - It doesn't exactly match known deal prices (asking, ARV, repair, floor, contract)
    This prevents false extraction when the buyer is just repeating deal info.
    """
    if not reply_body:
        return None
    for match in _PRICE_FALLBACK_RE.finditer(reply_body):
        # Group 1 = comma-format number, Group 2 = no-comma number, Group 3 = suffix
        raw_num = match.group(1) or match.group(2) or ""
        if not raw_num:
            continue
        raw = raw_num.replace(",", "")
        try:
            price = float(raw)
        except (ValueError, TypeError):
            continue
        # Check for k/thousand suffix (group 3)
        suffix = (match.group(3) or "").strip().lower()
        if suffix in ("k", "thousand"):
            price *= 1000
        # Skip if it matches a known deal price exactly
        if price in known_prices:
            continue
        # Must be in a reasonable price range ($10k - $10M)
        if 10000 <= price <= 10_000_000:
            return price
    return None


async def process_conversation(
    reply_body: str,
    reply_subject: str,
    buyer: Buyer,
    deal: Deal,
    campaign: Campaign,
    thread_history: list[dict],
) -> dict:
    """Process a buyer reply and decide what to say next.

    Returns:
        dict with keys: next_message, new_stage, extracted_info,
                        contract_ready, pass_detected, unsubscribe_detected,
                        classification
    """
    current_stage = campaign.conversation_stage or "pitching"

    # ── Pre-checks (regex-based, context-aware) ───────────────────────────────
    # Uses word-boundary regex patterns instead of brittle substring matching
    # so variations like "opt me out", "opting out", "don't contact" are all caught.
    reply_lower = reply_body.lower().strip()

    _UNSUB_PATTERNS = [
        re.compile(r'\bunsub(?:cribe)?\b'),                    # unsub, unsubscribe
        re.compile(r'\bremove\s+me\b'),                       # remove me
        re.compile(r'\btake\s+me\s+off\b'),                  # take me off
        re.compile(r'\bstop\s+(?:contacting|emailing|messaging|sending)\b'),  # stop *_ing
        re.compile(r'\b(?:do\s+not|don\'t)\s+contact\b'),   # do not/don't contact
        re.compile(r'\bopt(?:ing)?(?:[\s-]+me)?[\s-]+out\b'),  # opt out/opt me out/opt-out/opting out
    ]
    _PASS_PATTERNS = [
        re.compile(r'\bnot\s+(?:for\s+)?me\b'),              # not for me, not me
        re.compile(r'\b(?:i\'?ll|i\s+will)\s+pass\b'),       # i'll pass, i will pass
        re.compile(r'\bpass(?:ing)?\s+on\s+(?:this|it|that|the|a)\b'),  # pass on this/passing on this
        re.compile(r'\bnot\s+interested\b'),                  # not interested
        re.compile(r'\bno\s+(?:thank\s+(?:you|u)|thanks)\b'),  # no thank you/no thanks
        re.compile(r'\bdoesn\'?t\s+fit\b'),                   # doesn't fit
        re.compile(r'\bnot\s+in\s+the\s+market\b'),          # not in the market
        re.compile(r'\b(?:price|cost)\s+is\s+too\s+high\b'), # price/cost is too high
        re.compile(r'\bnot\s+buying\b'),                       # not buying
        re.compile(r'\bnot\s+(?:looking|searching)\b'),        # not looking for/not searching
        re.compile(r'\bstop\s+reaching\s+out\b'),             # stop reaching out
        re.compile(r'\balready\s+(?:bought|have|got)\b'),     # already bought/already have/already got
    ]

    if any(p.search(reply_lower) for p in _UNSUB_PATTERNS):
        return {
            "next_message": f"Got it - removing you from my list.\n\n{settings.operator_signature}",
            "new_stage": "passed", "contract_ready": False,
            "pass_detected": False, "unsubscribe_detected": True,
            "extracted_info": {}, "notes": "Unsubscribe via pre-check",
        }
    if any(p.search(reply_lower) for p in _PASS_PATTERNS):
        return {
            "next_message": None,
            "new_stage": "passed", "contract_ready": False,
            "pass_detected": True, "unsubscribe_detected": False,
            "extracted_info": {}, "notes": "Pass via pre-check",
        }


    # ── Build context for AI ─────────────────────────────────────────────────
    thread_str = ""
    for msg in thread_history[-6:]:
        role = "You" if msg["role"] == "assistant" else "Buyer"
        thread_str += f"{role}: {msg['content'][:500]}\n\n"

    # Always show accumulated contract state — buyers drop pieces at any stage,
    # across any number of messages. The AI must know exactly what is still missing.
    _missing = []
    if not campaign.buyer_legal_name:
        _missing.append("legal name")
    if not campaign.buyer_phone:
        _missing.append("phone number")
    if not campaign.buyer_title_company:
        _missing.append("title company")
    if not campaign.agreed_price:
        _missing.append("agreed price")
    info_status = (
        f"\nCONTRACT INFO COLLECTED SO FAR (accumulated across the whole conversation):\n"
        f"- Legal name: {'YES: ' + campaign.buyer_legal_name if campaign.buyer_legal_name else 'MISSING'}\n"
        f"- Phone: {'YES: ' + campaign.buyer_phone if campaign.buyer_phone else 'MISSING'}\n"
        f"- Title company: {'YES: ' + campaign.buyer_title_company if campaign.buyer_title_company else 'MISSING'}\n"
        f"- Agreed price: {'YES: $' + f'{float(campaign.agreed_price):,.0f}' if campaign.agreed_price else 'MISSING'}\n"
        + (
            f"STILL MISSING: {', '.join(_missing)}. If the buyer is committed, ask naturally for the "
            f"NEXT missing item only — never re-ask for anything marked YES, never demand everything at once.\n"
            if _missing else
            "ALL FOUR PIECES COLLECTED.\n"
        )
    )

    # Build compact collected-info line (only show what's missing)
    _have = []
    _need = []
    for label, val in [("name", campaign.buyer_legal_name),
                       ("phone", campaign.buyer_phone),
                       ("title", campaign.buyer_title_company),
                       ("price", campaign.agreed_price)]:
        (_have if val else _need).append(label)
    _info_line = (
        f"Collected: {', '.join(_have) or 'none'}. Still need: {', '.join(_need)}."
        if _need else
        "All 4 collected."
    )

    system_prompt = (
        # ── OPERATOR IDENTITY ──
        f"You are {settings.operator_name}. You are a solo real estate wholesaler (a woman).\n"
        f"You are NOT a licensed real estate agent. You are NOT based in the US.\n"
        f"You do NOT run a company or agency. You do NOT have employees.\n\n"
        f"WHAT YOU DO:\n"
        f"You source off-market deals from your US-based JV partners who handle the seller side.\n"
        f"You then pitch these deals to cash buyers at a discount.\n"
        f"You have the property under contract (via your JV partner) before pitching.\n"
        f"You split the profit with your JV partners when a deal closes.\n"
        f"You have been doing this for 3 years and are doing more deals than ever.\n\n"
        f"YOUR JV PARTNERS:\n"
        f"You have a small network of trusted US-local partners who deal directly with sellers.\n"
        f"They handle the seller side, you handle the buyer side.\n"
        f"You do not disclose partner names or details to buyers.\n\n"
        f"MARKETS YOU OPERATE IN:\n"
        f"You only work in US states where wholesaling does not legally require a real estate license.\n"
        f"You do not operate in states with strict assignment restriction laws.\n\n"
        # ── CURRENT DEAL ──
        f"CURRENT DEAL:\n"
        f"Property: {deal.address}, {deal.city} {deal.state} | "
        f"{deal.beds}bd/{deal.baths}ba | {deal.sqft or '?'} sqft | built {deal.year_built or '?'} | "
        f"asking ${float(deal.asking_price):,.0f} | "
        f"ARV ${float(deal.arv):,.0f} | "
        + (f"rehab ${float(deal.repair_estimate):,.0f} | " if deal.repair_estimate else "")
        + f"buyer profit ${float(deal.arv)-float(deal.asking_price)-float(deal.repair_estimate or 0):,.0f} | "
        f"floor ${float(deal.floor_price):,.0f} (NEVER reveal)\n"
        f"Buyer: {buyer.full_name}\n"
        f"Contract info — {_info_line}\n\n"
        # ── HOW TO ANSWER IDENTITY QUESTIONS ──
        f"HOW TO ANSWER IDENTITY QUESTIONS:\n"
        f"- 'Who are you?' -> 'I'm Irtaqa, I source off-market investment deals for cash buyers.'\n"
        f"- 'What company are you with?' -> 'I'm independent — I work with a small network of partners who find deals on the seller side.'\n"
        f"- 'Are you a realtor or licensed agent?' -> 'No, I'm not a licensed agent. I'm a wholesaler — I connect buyers with off-market deals.'\n"
        f"- 'Are you local?' -> 'I'm not local but my partners on the ground are — they handle the seller side and verify everything on-site.'\n"
        f"- 'How long have you been doing this?' -> 'About three years. I've been doing more volume lately.'\n"
        f"- 'Can I call you?' -> 'Email is best for me — I'm not always available by phone but I respond quickly.'\n"
        f"- 'Do you have a website or portfolio?' -> 'I don't have a public portfolio — I work by referral and direct outreach.'\n"
        f"- 'Can I see your portfolio?' -> 'I don't keep a formal portfolio. I work deal-by-deal — happy to walk you through this one and share whatever details you need.'\n"
        f"- 'Who do you work with?' -> 'I work with a small group of US-based partners. We've been running this for a few years.'\n"
        f"- 'Is this a wholesaler deal?' -> 'Yes, it's a wholesaler deal. I source off-market through my partners and bring it to cash buyers like you.'\n"
        f"- 'Are you the owner?' -> 'I have the property under contract through my partner network — same end result for you as a buyer.'\n\n"
        # ── CONVERSATION RULES ──
        f"RULES:\n"
        f"Professional, warm, direct. You are a businessperson who knows her deals and her buyers.\n"
        f"Confident but never pushy. Concise, natural — no corporate language, no fluff.\n"
        f"counter=still interested (never pass), pass only for explicit rejection.\n"
        f"When buyer is ready to proceed: collect missing contract pieces ONE AT A TIME naturally — "
        f"ask only for the NEXT missing item, never re-ask what you already have.\n"
        f"You have the deal data listed above. NEVER invent facts that are not listed there.\n"
        f"For factual questions answer ONLY from the exact data above. If the answer is not in the data above,"
        f" say 'Let me check on that' or 'I'll find out'. Never guess or make up a number or status.\n"
        f"BAD (hallucination): claiming any specific status about flood zones, HOA fees, taxes,"
        f" liens, foundation condition, inspections, schools, crime rates, rental estimates,"
        f" basement, historical status, permits, or any other unverified property detail.\n"
        f"GOOD (from data): 'The asking price is $195,000' / 'It has 4 bedrooms' / 'The square footage is 1,650' / 'It was built in 1994'.\n"
        f"GOOD (uncertain): 'Let me check on that for you' / 'I'll pull those details and get back to you'.\n"
        f"CRITICAL: If you do NOT have the data, say you will check — then STOP.\n"
        f"  NEVER say 'I don't know' and then immediately guess a number anyway.\n"
        f"  If you say 'let me check on that', the sentence must END there — no estimates.\n"
        f"  Making up facts after saying you don't know is just as harmful as guessing directly.\n\n"
        # ── WHAT TO NEVER SAY ──
        f"WHAT TO NEVER SAY:\n"
        f"- Never say you are a licensed real estate agent\n"
        f"- Never say you are based in the US or in any specific US city\n"
        f"- Never say you work for a company, brokerage, or agency\n"
        f"- Never invent company names, license numbers, or years of experience beyond what is stated above\n"
        f"- Never say you are an AI, bot, or automated system\n"
        f"- Never reveal your JV partner names or details\n"
        f"- Never reveal the floor price or your contract price with the seller\n"
        f"- Never claim to have done a specific number of deals unless asked, and if asked say 'quite a few over the past three years'\n"
        f"- Never echo buyer's words back\n\n"
        f"Sign off: {settings.operator_signature}"
    )

    user_prompt = (
        f"Thread:\n{thread_str if thread_str else '(first reply)'}\n"
        f"Buyer reply: {reply_body}\n\n"
        f"Stage options: engaging(curious/question/counter) | qualifying(warm, fishing) | "
        f"collecting_info(agreed, gathering contract details) | contract_ready(all 4 pieces present) | "
        f"passed(explicit rejection only)\n"
        f"Extract NUMBERS (as raw numbers, no $ or commas) for: legal name, phone (any format), title company, agreed price.\n"
        f"CRITICAL: agreed_price = ANY dollar amount the buyer mentions about the deal price.\n"
        f"  Extract it REGARDLESS of stage — even if it's a counter or question.\n"
        f"  Examples: 'I'll do it at $185,000' -> 185000, 'I'll pay $180k' -> 180000, 'What about $190k?' -> 190000\n"
        f"  Return as a plain number: 185000 NOT '$185,000'. If no price mentioned, return null.\n"
        f"Return ONLY JSON: {{\"stage\":\"...\",\"pass\":false,\"unsub\":false,"
        f"\"reply\":\"...\",\"extracted_legal_name\":null,\"extracted_phone\":null,"
        f"\"extracted_title_company\":null,\"extracted_agreed_price\":null}}"
    )

    try:
        response = await groq_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        content = response.choices[0].message.content.strip()
        content = extract_json_block(content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # One strict retry — reasoning models occasionally wrap output in prose.
            logger.warning(
                "Conversation engine: JSON parse failed, retrying strict. raw=%.200s", content
            )
            retry = await groq_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt + "\n\nOutput RAW JSON only. No prose, no markdown."},
                ],
                temperature=0.2,
                max_tokens=800,
            )
            content = extract_json_block(retry.choices[0].message.content.strip())
            parsed = json.loads(content)

        new_stage = parsed.get("stage", current_stage)
        next_message = parsed.get("reply") or ""
        pass_detected = bool(parsed.get("pass")) or bool(parsed.get("unsub"))
        unsubscribe_detected = bool(parsed.get("unsub"))

        if pass_detected:
            new_stage = "passed"

        # State-completion logic: merge what the campaign already holds with what
        # this message just provided. contract_ready is a function of ACCUMULATED
        # state — not of any single message, and not of the AI's stage label alone.
        ai_extracted_price = parsed.get("extracted_agreed_price")
        # Regex fallback: if AI didn't extract a price but the reply clearly contains
        # a dollar amount the buyer is offering, extract it here.
        if ai_extracted_price is None:
            known = {
                float(deal.asking_price) if deal.asking_price else None,
                float(deal.arv) if deal.arv else None,
                float(deal.repair_estimate) if deal.repair_estimate else None,
                float(deal.floor_price) if deal.floor_price else None,
                float(deal.contract_price) if deal.contract_price else None,
            }
            known.discard(None)
            fallback_price = _extract_price_fallback(reply_body, known)  # type: ignore[arg-type]
            if fallback_price is not None:
                logger.info(
                    "Price extraction fallback: extracted $%.0f from reply '%s' (AI missed it)",
                    fallback_price, reply_body[:60],
                )
                ai_extracted_price = fallback_price
        just_collected = {
            "legal_name": parsed.get("extracted_legal_name"),
            "phone": parsed.get("extracted_phone"),
            "title_company": parsed.get("extracted_title_company"),
            "agreed_price": ai_extracted_price,
        }
        will_have = {
            "legal_name": campaign.buyer_legal_name or just_collected["legal_name"],
            "phone": campaign.buyer_phone or just_collected["phone"],
            "title_company": campaign.buyer_title_company or just_collected["title_company"],
            "agreed_price": campaign.agreed_price or just_collected["agreed_price"],
        }

        # ── Below-floor counter escalation ────────────────────────────────
        # If the buyer suggested a price below the floor, escalate to operator
        # instead of auto-proceeding. The operator can accept, reject, or counter.
        _counter_price = will_have["agreed_price"]
        _negotiation_escalation = None
        if _counter_price is not None and deal.floor_price is not None:
            try:
                cp = float(_counter_price)
                fp = float(deal.floor_price)
                if cp < fp:
                    _negotiation_escalation = {
                        "counter_price": cp,
                        "floor_price": fp,
                        "gap": fp - cp,
                        "buyer_name": buyer.full_name,
                        "buyer_email": buyer.email,
                        "deal_address": deal.address,
                        "deal_id": str(deal.id),
                        "buyer_id": str(buyer.id),
                        "campaign_id": str(campaign.id),
                    }
                    logger.info(
                        "Negotiation escalation: buyer %s counter $%.0f < floor $%.0f (gap $%.0f)",
                        buyer.id, cp, fp, fp - cp,
                    )
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse counter/floor price: %s", e)
        all_pieces_complete = all(will_have.values())

        _still_missing = [k for k, v in will_have.items() if not v]
        _pieces_have = [k for k, v in will_have.items() if v]

        if new_stage == "contract_ready" and not all_pieces_complete:
            # AI jumped early — downgrade
            logger.info(
                "Conversation engine: AI said contract_ready but missing %s — downgrading to collecting_info",
                _still_missing,
            )
            new_stage = "collecting_info"
        elif not all_pieces_complete and _pieces_have and new_stage not in ("passed", "collecting_info"):
            # Some pieces collected but AI didn't recognise we're mid-collection.
            # Force collecting_info so the AI asks for the next missing piece.
            logger.info(
                "Conversation engine: have %s, missing %s — forcing collecting_info from %s",
                _pieces_have, _still_missing, new_stage,
            )
            new_stage = "collecting_info"
            # Ask for the single next missing piece naturally
            _ask_for = _still_missing[0].replace("_", " ")
            next_message = (
                f"Got it. One more thing — could you share your {_ask_for}?"
                f"\n\n{settings.operator_signature}"
            )
        elif all_pieces_complete and new_stage not in ("passed",) and not pass_detected:
            # Everything is in hand (possibly gathered across many messages) —
            # complete the state machine even if the AI was being cautious.
            if new_stage != "contract_ready":
                logger.info(
                    "Conversation engine: all 4 pieces accumulated — upgrading stage %s -> contract_ready",
                    new_stage,
                )
            new_stage = "contract_ready"
            next_message = (
                "Perfect — that's everything I need. I'll get the paperwork started "
                "and send the contract over shortly."
            )

        # ── Below-floor escalation overrides all stage transitions ──
        # When the buyer's counter is below floor, force negotiating stage,
        # suppress any automated reply, and flag for operator decision.
        if _negotiation_escalation:
            new_stage = "negotiating"
            next_message = ""  # No auto-reply — wait for operator
            logger.info(
                "Conversation engine: forced stage=negotiating for buyer %s "
                "(counter $%.0f < floor $%.0f)",
                buyer.id, _negotiation_escalation["counter_price"],
                _negotiation_escalation["floor_price"],
            )

        # Anti-echo guard: if the model parroted the buyer's message, suppress it.
        if next_message:
            _nm = re.sub(r"\W+", " ", next_message.lower()).strip()
            _rb = re.sub(r"\W+", " ", (reply_body or "").lower()).strip()
            if _nm and _rb and (_nm in _rb or _rb in _nm):
                logger.warning(
                    "Conversation engine echoed buyer reply — suppressing send. echo=%.80s",
                    next_message,
                )
                next_message = ""

        if next_message:
            # Never expose floor price — strip any mention
            next_message = re.sub(
                r'(?:floor|minimum|lowest)[\s\w]*?\$[\d,]+',
                'my number',
                next_message, flags=re.IGNORECASE
            )
            sign_off = settings.operator_signature.strip()
            if sign_off and sign_off not in next_message:
                next_message = next_message.rstrip() + "\n\n" + sign_off

        logger.info(
            "Conversation engine: buyer %s stage %s->%s | pass=%s | contract=%s | %s",
            buyer.id, current_stage, new_stage, pass_detected,
            new_stage == "contract_ready",
            parsed.get("notes", "")[:80],
        )

        logger.info(
            "Conversation engine: %s -> %s | extracted=%s | reply=%s",
            current_stage, new_stage,
            [k for k, v in {
                "name": parsed.get("extracted_legal_name"),
                "phone": parsed.get("extracted_phone"),
                "title": parsed.get("extracted_title_company"),
                "price": parsed.get("extracted_agreed_price"),
            }.items() if v] or "none",
            "yes" if next_message else "no",
        )
        return {
            "next_message": next_message if next_message else None,
            "new_stage": new_stage,
            "contract_ready": new_stage == "contract_ready",
            "pass_detected": pass_detected,
            "unsubscribe_detected": unsubscribe_detected,
            "negotiation_escalation": _negotiation_escalation,
            "extracted_info": {
                "legal_name": parsed.get("extracted_legal_name"),
                "phone": parsed.get("extracted_phone"),
                "title_company": parsed.get("extracted_title_company"),
                "agreed_price": ai_extracted_price,
            },
            "classification": parsed,
        }

    except json.JSONDecodeError as e:
        logger.error(
            "Conversation engine JSON parse error: %s | content: %.200s",
            e, content if "content" in dir() else "",
        )
        return _fallback(current_stage)
    except Exception as e:
        logger.error("Conversation engine error: %s", e, exc_info=True)
        return _fallback(current_stage)


def _fallback(stage: str) -> dict:
    return {
        "next_message": None,
        "new_stage": stage,
        "contract_ready": False,
        "pass_detected": False,
        "unsubscribe_detected": False,
        "negotiation_escalation": None,
        "extracted_info": {
            "legal_name": None, "phone": None,
            "title_company": None, "agreed_price": None,
        },
        "classification": {"error": "ai_failed"},
    }
