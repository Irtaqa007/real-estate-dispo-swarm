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
from app.services.groq_client import groq_chat_completion

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

    # ── Pre-check 1: Unsubscribe ─────────────────────────────────────────────
    for phrase in _UNSUBSCRIBE_PHRASES:
        if phrase in reply_lower:
            logger.info(
                "Conversation engine: pre-check unsubscribe detected for buyer %s", buyer.id
            )
            return {
                "next_message": (
                    f"Got it — removing you from my list. No further emails from me.\n\n"
                    f"{settings.operator_signature}"
                ),
                "new_stage": "passed",
                "contract_ready": False,
                "pass_detected": True,
                "unsubscribe_detected": True,
                "extracted_info": {
                    "legal_name": None, "phone": None,
                    "title_company": None, "agreed_price": None,
                },
                "classification": {
                    "stage_decision": "passed",
                    "notes": f"pre-check: unsubscribe phrase '{phrase}' detected",
                },
            }

    # ── Pre-check 2: Hard pass ───────────────────────────────────────────────
    for phrase in _HARD_PASS_PHRASES:
        if phrase in reply_lower:
            logger.info(
                "Conversation engine: pre-check pass detected for buyer %s (phrase: %s)",
                buyer.id, phrase,
            )
            return {
                "next_message": (
                    f"Understood — I'll keep you in mind if something more aligned comes up.\n\n"
                    f"{settings.operator_signature}"
                ),
                "new_stage": "passed",
                "contract_ready": False,
                "pass_detected": True,
                "unsubscribe_detected": False,
                "extracted_info": {
                    "legal_name": None, "phone": None,
                    "title_company": None, "agreed_price": None,
                },
                "classification": {
                    "stage_decision": "passed",
                    "notes": f"pre-check: pass phrase '{phrase}' detected",
                },
            }

    # ── Pre-check 3: Force contract_ready if all 4 pieces present in reply ───
    # AI sometimes misses when buyer provides everything in one message.
    _phone_in_reply = bool(re.search(r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b', reply_body))
    _title_in_reply = any(w in reply_lower for w in [
        "title", "escrow", "closing", "first american", "stewart",
        "chicago title", "old republic", "republic title", "fidelity",
        "title company", "title co", "closing attorney",
    ])
    _yes_in_reply = any(w in reply_lower for w in [
        "yes", "i'm in", "let's do", "let's go", "deal", "agreed",
        "sounds good", "move forward", "send the contract", "send contract",
        "i want to", "ready to go", "i'll take it", "works for me",
        "let's proceed", "i'm ready", "lock it up", "let's lock",
    ])
    _name_in_reply = (
        "my name is" in reply_lower
        or "legal name is" in reply_lower
        or "name for contract" in reply_lower
        or "full name is" in reply_lower
        or bool(re.search(r'(?:legal|full|contract)[\s:]+[A-Z][a-z]+ [A-Z][a-z]+', reply_body))
    )
    already_collected = _info_collected(campaign)
    if (
        _yes_in_reply and _phone_in_reply and _title_in_reply and _name_in_reply
        and not already_collected.get("agreed_price")  # not already in contract_ready
    ):
        logger.info(
            "Conversation engine: pre-check all contract info present in one reply for buyer %s",
            buyer.id,
        )
        return {
            "next_message": (
                f"Perfect — I have everything I need. I'll get the paperwork started and send it over shortly.\n\n"
                f"{settings.operator_signature}"
            ),
            "new_stage": "contract_ready",
            "contract_ready": True,
            "pass_detected": False,
            "unsubscribe_detected": False,
            "extracted_info": {
                "legal_name": None,  # AI will extract exact value below if needed
                "phone": None,
                "title_company": None,
                "agreed_price": float(deal.asking_price),
            },
            "classification": {
                "stage_decision": "contract_ready",
                "notes": "pre-check: all 4 contract pieces detected in single reply",
            },
        }

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
        f"This is an OFF-MARKET deal — sourced directly, not listed on MLS.\n"
    )

    info_status = ""
    if current_stage in ("qualifying", "collecting_info"):
        info_status = (
            f"\nCONTRACT INFO COLLECTED SO FAR:\n"
            f"- Legal name: {'YES: ' + campaign.buyer_legal_name if campaign.buyer_legal_name else 'NO — needed'}\n"
            f"- Phone: {'YES: ' + campaign.buyer_phone if campaign.buyer_phone else 'NO — needed'}\n"
            f"- Title company: {'YES: ' + campaign.buyer_title_company if campaign.buyer_title_company else 'NO — needed'}\n"
            f"- Agreed price: {'YES: $' + f'{float(campaign.agreed_price):,.0f}' if campaign.agreed_price else 'NO — not confirmed'}\n"
        )

    system_prompt = (
        f"You are {settings.operator_name}, a real estate wholesaler in a direct email "
        f"conversation with a buyer about an off-market deal.\n\n"
        f"Tone: {settings.operator_tone}\n"
        f"Never say: {settings.operator_never_say}\n"
        f"Context: {settings.operator_context}\n\n"
        f"DEAL:\n{deal_context}\n"
        f"BUYER: {buyer.full_name} | {buyer.buy_box or 'no buy box'}\n"
        f"STAGE: {current_stage}\n"
        f"{info_status}\n"
        f"RULES:\n"
        f"- You ARE the deal owner/wholesaler. Never say 'go back to the seller' or "
        f"'check with the seller' — you source these directly.\n"
        f"- Sound human. 3-5 sentences max.\n"
        f"- Never reveal the floor price.\n"
        f"- Never claim to be AI.\n"
        f"- Reference off-market naturally where relevant.\n"
        f"- Never be pushy. Confident but relaxed.\n"
        f"- Sign off with: {settings.operator_signature}\n"
        f"- CRITICAL: A counter offer is NOT a pass. Buyer countering = still interested.\n"
        f"- CRITICAL: Only set pass=true for explicit 'no', 'pass', 'not interested', "
        f"'stop emailing', 'remove me'. Not for questions, counters, or 'tell me more'.\n"
    )

    user_prompt = f"""THREAD:
{thread_str if thread_str else "(first reply)"}

BUYER'S REPLY (subject: {reply_subject}):
{reply_body}

STAGE RULES:
- Passing/not interested -> stage="passed", brief professional close, pass=true
- Unsubscribe -> stage="passed", brief opt-out confirmation, unsub=true
- Questions/curious/not committed -> stage="engaging", answer naturally
- Warm and interested -> stage="qualifying", ask ONE natural question
- Buyer agrees to price and wants to proceed -> stage="collecting_info", collect missing info ONE AT A TIME
  (legal name first, then phone, then title company)
- ALL FOUR collected (yes + legal name with first+last + phone digits + title company) -> stage="contract_ready"
- Counter offer -> stage="engaging", hold or negotiate (NEVER mark as pass)
- CRITICAL: "Let's move forward", "send me everything", "I'm in", "very interested" WITHOUT providing
  legal name + phone + title company = stage "engaging" or "qualifying", NEVER "contract_ready"

EXTRACTION: Extract legal_name/phone/title_company/agreed_price ONLY if explicitly provided.

Return ONLY JSON:
{{"stage":"engaging|qualifying|collecting_info|contract_ready|passed",
  "pass":false,"unsub":false,
  "reply":"your reply as {settings.operator_name} (null if no reply needed)",
  "extracted_legal_name":null,
  "extracted_phone":null,
  "extracted_title_company":null,
  "extracted_agreed_price":null,
  "notes":"brief reasoning"}}"""

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
        content = re.sub(r"```(?:json)?", "", content).strip().rstrip("```").strip()
        parsed = json.loads(content)

        new_stage = parsed.get("stage", current_stage)
        next_message = parsed.get("reply") or ""
        pass_detected = bool(parsed.get("pass")) or bool(parsed.get("unsub"))
        unsubscribe_detected = bool(parsed.get("unsub"))

        if pass_detected:
            new_stage = "passed"

        # Safety: contract_ready requires all 4 pieces
        if new_stage == "contract_ready":
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
            if not all(will_have.values()):
                new_stage = "collecting_info"

        if next_message:
            sign_off = settings.operator_signature.strip()
            if sign_off and sign_off not in next_message:
                next_message = next_message.rstrip() + "\n\n" + sign_off

        logger.info(
            "Conversation engine: buyer %s stage %s->%s | pass=%s | contract=%s | %s",
            buyer.id, current_stage, new_stage, pass_detected,
            new_stage == "contract_ready",
            parsed.get("notes", "")[:80],
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
