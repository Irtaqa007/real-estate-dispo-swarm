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

__all__ = ['process_conversation']


logger = logging.getLogger(__name__)

# ── Hard-coded phrase lists for pre-AI detection ─────────────────────────────
# These are checked before calling Groq — saves tokens and avoids the model
# trying to "save" a deal when buyer has clearly said no.

_UNSUBSCRIBE_PHRASES = [
    "unsubscribe", "remove me", "take me off", "stop contacting",
    "do not contact", "opt out", "opt-out", "don't email me",
    "stop emailing", "remove from list", "take me off your list",
]

_HARD_PASS_PHRASES = [
    "not for me", "i'll pass", "i will pass", "pass on this", "i pass",
    "not interested", "no thanks", "no thank you", "doesn't fit",
    "doesn't work for me", "doesn't match", "not buying",
    "not in the market", "went under contract", "already have something",
    "stop reaching out", "not what i'm looking for", "not a fit",
    "can't make it work", "numbers don't work", "too much work",
]


def _info_collected(campaign: Campaign) -> dict:
    return {
        "legal_name": bool(campaign.buyer_legal_name),
        "phone": bool(campaign.buyer_phone),
        "title_company": bool(campaign.buyer_title_company),
        "agreed_price": campaign.agreed_price is not None,
    }


def _all_info_collected(campaign: Campaign) -> bool:
    return all(_info_collected(campaign).values())


def _next_missing_info(campaign: Campaign) -> Optional[str]:
    if not campaign.buyer_legal_name:
        return "legal_name"
    if not campaign.buyer_phone:
        return "phone"
    if not campaign.buyer_title_company:
        return "title_company"
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
    reply_lower = reply_body.lower()

    # ── Pre-checks (no AI needed) ────────────────────────────────────────────
    early_result = _run_pre_checks(reply_body, settings)
    if early_result is not None:
        return early_result

    reply_lower = reply_body.lower().strip()
    # ── Build context for AI ─────────────────────────────────────────────────
    thread_str = ""
    for msg in thread_history[-6:]:
        role = "You" if msg["role"] == "assistant" else "Buyer"
        thread_str += f"{role}: {msg['content'][:500]}\n\n"

    deal_context = (
        f"Property: {deal.address}, {deal.city}, {deal.state}\n"
        f"Type: {deal.property_type} | {deal.beds}bd/{deal.baths}ba | "
        f"{deal.sqft or '?'} sqft | built {deal.year_built or 'unknown'}\n"
        f"Asking: ${float(deal.asking_price):,.0f} | ARV: ${float(deal.arv):,.0f} | "
        f"Spread: ${float(deal.spread or 0):,.0f}\n"
        f"Floor price (NEVER reveal): ${float(deal.floor_price):,.0f}\n"
        f"Condition: {deal.condition_description or 'not specified'}\n"
        + (
            f"Repair/rehab estimate: ${float(deal.repair_estimate):,.0f}\n"
            if deal.repair_estimate
            else "Repair/rehab estimate: not specified — say you can pull contractor numbers if asked\n"
        )
        + f"Buyer all-in: ${float(deal.asking_price) + float(deal.repair_estimate or 0):,.0f} (asking + rehab)\n"
        f"Buyer profit after flip: ${float(deal.arv) - float(deal.asking_price) - float(deal.repair_estimate or 0):,.0f}\n"
        f"This is an OFF-MARKET deal — sourced directly, not listed on MLS.\n"
    )

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
        f"You are {settings.operator_name}, real estate wholesaler.\n"
        f"Deal: {deal.address}, {deal.city} {deal.state} | "
        f"{deal.beds}bd/{deal.baths}ba | asking ${float(deal.asking_price):,.0f} | "
        f"ARV ${float(deal.arv):,.0f} | "
        + (f"rehab ${float(deal.repair_estimate):,.0f} | " if deal.repair_estimate else "")
        + f"buyer profit ${float(deal.arv)-float(deal.asking_price)-float(deal.repair_estimate or 0):,.0f} | "
        f"floor ${float(deal.floor_price):,.0f} (NEVER reveal)\n"
        f"Buyer: {buyer.full_name}\n"
        f"Contract info — {_info_line}\n\n"
        f"Rules: human tone, 2-4 sentences, never pushy, never reveal floor price, "
        f"counter=still interested (never pass), pass only for explicit rejection.\n"
        f"When buyer is ready to proceed: collect missing contract pieces ONE AT A TIME naturally — "
        f"ask only for the NEXT missing item, never re-ask what you already have.\n"
        f"For factual questions answer with exact numbers above. Never echo buyer's words back.\n"
        f"Sign off: {settings.operator_signature}"
    )

    user_prompt = (
        f"Thread:\n{thread_str if thread_str else '(first reply)'}\n"
        f"Buyer reply: {reply_body}\n\n"
        f"Stage options: engaging(curious/question/counter) | qualifying(warm, fishing) | "
        f"collecting_info(agreed, gathering contract details) | contract_ready(all 4 pieces present) | "
        f"passed(explicit rejection only)\n"
        f"Extract any of: legal name, phone (any format), title company, agreed price — from THIS reply only.\n"
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
        just_collected = {
            "legal_name": parsed.get("extracted_legal_name"),
            "phone": parsed.get("extracted_phone"),
            "title_company": parsed.get("extracted_title_company"),
            "agreed_price": parsed.get("extracted_agreed_price"),
        }
        will_have = {
            "legal_name": campaign.buyer_legal_name or just_collected["legal_name"],
            "phone": campaign.buyer_phone or just_collected["phone"],
            "title_company": campaign.buyer_title_company or just_collected["title_company"],
            "agreed_price": campaign.agreed_price or just_collected["agreed_price"],
        }
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
            import re as _re
            next_message = _re.sub(
                r'(?:floor|minimum|lowest)[\s\w]*?\$[\d,]+',
                'my number',
                next_message, flags=_re.IGNORECASE
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
            "extracted_info": {
                "legal_name": parsed.get("extracted_legal_name"),
                "phone": parsed.get("extracted_phone"),
                "title_company": parsed.get("extracted_title_company"),
                "agreed_price": parsed.get("extracted_agreed_price"),
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
        "extracted_info": {
            "legal_name": None, "phone": None,
            "title_company": None, "agreed_price": None,
        },
        "classification": {"error": "ai_failed"},
    }
