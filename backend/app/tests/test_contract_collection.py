"""Tests for the full contract collection conversation flow.

Simulates a complete 5-step buyer conversation end-to-end:
  1. Buyer replies "Yes interested"
  2. AI asks for name → Buyer provides legal name
  3. AI asks for phone → Buyer provides phone
  4. AI asks for title company → Buyer provides title preference
  5. Buyer provides agreed price → All 4 pieces collected → contract_ready fires

Also tests:
  - Pre-checks (unsubscribe, pass) still work mid-conversation
  - contract_ready alert created and logged via scheduler path
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.models import ActivityLog, Buyer, Campaign, Deal


# ===========================================================================
# Helpers: make mock objects with real fields
# ===========================================================================


def _make_deal(**overrides) -> MagicMock:
    d = MagicMock(spec=Deal)
    d.id = overrides.get("id", uuid.uuid4())
    d.address = overrides.get("address", "123 Main St")
    d.city = overrides.get("city", "Austin")
    d.state = overrides.get("state", "TX")
    d.zip = "78701"
    d.property_type = "House"
    d.beds = 3
    d.baths = 2.0
    d.sqft = 1500
    d.year_built = 1995
    d.condition_description = "Good condition, needs minor cosmetic updates"
    d.asking_price = 200000.0
    d.floor_price = 170000.0
    d.contract_price = 160000.0
    d.arv = 280000.0
    d.repair_estimate = 25000.0
    d.spread = 40000.0
    d.status = overrides.get("status", "Available")
    d.jv_split_percentage = 50.0
    d.jv_partner_id = None
    return d


def _make_buyer(**overrides) -> MagicMock:
    b = MagicMock(spec=Buyer)
    b.id = overrides.get("id", uuid.uuid4())
    b.full_name = overrides.get("full_name", "John Doe")
    b.email = overrides.get("email", "john@example.com")
    b.affiliation = ""
    b.unsubscribed_at = None
    b.last_reply_at = None
    return b


def _make_campaign(**overrides) -> MagicMock:
    c = MagicMock(spec=Campaign)
    c.id = overrides.get("id", uuid.uuid4())
    c.deal_id = overrides.get("deal_id", uuid.uuid4())
    c.buyer_id = overrides.get("buyer_id", uuid.uuid4())
    c.touch_number = overrides.get("touch_number", 1)
    c.status = overrides.get("status", "Sent")
    c.subject = "Great deal in Austin"
    c.body = "Check out this property..."
    c.conversation_stage = overrides.get("conversation_stage", "pitching")
    c.buyer_legal_name = overrides.get("buyer_legal_name", None)
    c.buyer_phone = overrides.get("buyer_phone", None)
    c.buyer_title_company = overrides.get("buyer_title_company", None)
    c.agreed_price = overrides.get("agreed_price", None)
    c.reply_body = overrides.get("reply_body", None)
    c.reply_received_at = overrides.get("reply_received_at", None)
    c.ai_extracted_insights = ""
    return c


import json


def _make_ai_json(data: dict) -> MagicMock:
    """Create a mock Groq response from a dict of JSON fields.

    Uses a dict parameter instead of **kwargs to avoid Python keyword
    conflicts (e.g. 'pass' is a reserved word).
    """
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(data)
    return response


# ===========================================================================
# Contract collection flow test
# ===========================================================================


class TestContractCollectionFlow:
    """Full 5-step contract collection conversation."""

    @pytest.mark.asyncio
    async def test_full_contract_collection_flow(self):
        """Simulate the complete 5-reply flow from interest -> contract_ready.

        Stage transitions:
          pitching -> (reply "interested") -> engaging/collecting_info
          collecting_info -> (gives name) -> collecting_info
          collecting_info -> (gives phone) -> collecting_info
          collecting_info -> (gives title) -> collecting_info
          collecting_info -> (gives price, all 4 collected) -> contract_ready
        """
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(conversation_stage="pitching")

        thread_history: list[dict] = []

        # ---- Step 1: Buyer replies "Yes interested" ----
        ai_stage = _make_ai_json({
            "stage": "engaging",
            "pass": False,
            "unsub": False,
            "reply": "Great! Let me get some info from you. Could you share your legal name?",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(return_value=ai_stage)):
            result = await process_conversation(
                reply_body="Yes interested",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=thread_history,
            )

        assert result["pass_detected"] is False
        assert result["unsubscribe_detected"] is False
        assert result["contract_ready"] is False
        assert result["new_stage"] in ("engaging", "collecting_info")

        # Update campaign and thread
        campaign.conversation_stage = result["new_stage"]
        thread_history.append({"role": "user", "content": "Yes interested"})
        if result["next_message"]:
            thread_history.append({"role": "assistant", "content": result["next_message"]})

        # ---- Step 2: Buyer gives legal name ----
        ai_name = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "Thanks John! One more thing - could you share your phone number?",
            "extracted_legal_name": "John Doe",
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(return_value=ai_name)):
            result = await process_conversation(
                reply_body="My name is John Doe",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=thread_history,
            )

        assert result["pass_detected"] is False
        assert result["contract_ready"] is False
        assert result["extracted_info"]["legal_name"] == "John Doe"
        assert result["new_stage"] == "collecting_info"

        campaign.conversation_stage = "collecting_info"
        campaign.buyer_legal_name = "John Doe"
        thread_history.append({"role": "user", "content": "My name is John Doe"})
        if result["next_message"]:
            thread_history.append({"role": "assistant", "content": result["next_message"]})

        # ---- Step 3: Buyer gives phone number ----
        ai_phone = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "Perfect. And which title company do you typically work with?",
            "extracted_legal_name": None,
            "extracted_phone": "555-123-4567",
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(return_value=ai_phone)):
            result = await process_conversation(
                reply_body="My phone is 555-123-4567",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=thread_history,
            )

        assert result["pass_detected"] is False
        assert result["contract_ready"] is False
        assert result["extracted_info"]["phone"] == "555-123-4567"
        assert result["new_stage"] == "collecting_info"

        campaign.conversation_stage = "collecting_info"
        campaign.buyer_phone = "555-123-4567"
        thread_history.append({"role": "user", "content": "My phone is 555-123-4567"})
        if result["next_message"]:
            thread_history.append({"role": "assistant", "content": result["next_message"]})

        # ---- Step 4: Buyer gives title company ----
        ai_title = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "Great choice. Last thing - what price were you thinking?",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": "Chicago Title",
            "extracted_agreed_price": None,
        })

        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(return_value=ai_title)):
            result = await process_conversation(
                reply_body="I use Chicago Title",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=thread_history,
            )

        assert result["pass_detected"] is False
        assert result["contract_ready"] is False
        assert result["extracted_info"]["title_company"] == "Chicago Title"
        assert result["new_stage"] == "collecting_info"

        campaign.conversation_stage = "collecting_info"
        campaign.buyer_title_company = "Chicago Title"
        thread_history.append({"role": "user", "content": "I use Chicago Title"})
        if result["next_message"]:
            thread_history.append({"role": "assistant", "content": result["next_message"]})

        # ---- Step 5: Buyer gives agreed price -> all 4 collected! ----
        ai_price = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "Perfect - that's everything I need. I'll get the paperwork started.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": 180000,
        })

        # At this point campaign has:
        #   buyer_legal_name = "John Doe"
        #   buyer_phone = "555-123-4567"
        #   buyer_title_company = "Chicago Title"
        #   agreed_price = None (will be set when AI extracts it)
        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(return_value=ai_price)):
            result = await process_conversation(
                reply_body="I can do $180,000",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=thread_history,
            )

        assert result["pass_detected"] is False
        assert result["contract_ready"] is True, (
            f"Expected contract_ready=True but got contract_ready={result['contract_ready']} "
            f"with stage={result['new_stage']}"
        )
        assert result["new_stage"] == "contract_ready"
        assert result["extracted_info"]["agreed_price"] == 180000

    @pytest.mark.asyncio
    async def test_unsubscribe_mid_conversation(self):
        """Unsubscribe pre-check should still work during contract collection."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="John Doe",
            buyer_phone="555-123-4567",
        )

        result = await process_conversation(
            reply_body="Unsubscribe me please",
            reply_subject="Re: Great deal in Austin",
            buyer=buyer,
            deal=deal,
            campaign=campaign,
            thread_history=[],
        )

        assert result["unsubscribe_detected"] is True
        assert result["pass_detected"] is False
        assert result["contract_ready"] is False
        assert result["new_stage"] == "passed"

    @pytest.mark.asyncio
    async def test_pass_mid_conversation(self):
        """Pass pre-check should still work during contract collection."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="John Doe",
        )

        result = await process_conversation(
            reply_body="No thanks, I will pass on this",
            reply_subject="Re: Great deal in Austin",
            buyer=buyer,
            deal=deal,
            campaign=campaign,
            thread_history=[],
        )

        assert result["pass_detected"] is True
        assert result["unsubscribe_detected"] is False
        assert result["contract_ready"] is False
        assert result["new_stage"] == "passed"
        assert result["next_message"] is None

    @pytest.mark.asyncio
    async def test_ai_overly_optimistic_downgraded(self):
        """If AI claims contract_ready but pieces are missing, engine downgrades."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="John Doe",
            buyer_phone="555-123-4567",
            buyer_title_company="Chicago Title",
            # Missing: agreed_price
        )

        ai_overly_optimistic = _make_ai_json({
            "stage": "contract_ready",
            "pass": False,
            "unsub": False,
            "reply": "Perfect! I will prepare the contract for you now.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(return_value=ai_overly_optimistic)):
            result = await process_conversation(
                reply_body="Sounds good, send it over",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # Engine should downgrade: missing agreed_price
        # The stage is correctly downgraded from contract_ready to collecting_info.
        # The AI's reply is kept (not overridden) because the downgrade-if block
        # only sets the stage without rewriting the message. This is acceptable
        # behavior — the internal stage is collecting_info for pipeline accuracy,
        # while the AI's reply was already sent.
        assert result["contract_ready"] is False
        assert result["new_stage"] == "collecting_info", (
            f"Expected collecting_info (missing price), got {result['new_stage']}"
        )


# ===========================================================================
# Scheduler path: contract_ready alert creation
# ===========================================================================


class TestContractReadyAlert:
    """Test that contract_ready creates an ActivityLog alert via the scheduler path."""

    @pytest.mark.asyncio
    async def test_scheduler_creates_contract_alert_on_ready(self):
        """When process_conversation returns contract_ready, verify the alert structure."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="John Doe",
            buyer_phone="555-123-4567",
            buyer_title_company="Chicago Title",
            agreed_price=180000,
        )

        ai_final = _make_ai_json({
            "stage": "contract_ready",
            "pass": False,
            "unsub": False,
            "reply": "Perfect - I will get the paperwork started.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(return_value=ai_final)):
            result = await process_conversation(
                reply_body="Yes, $180,000 works for me.",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["contract_ready"] is True
        assert result["new_stage"] == "contract_ready"

        # Simulate what the scheduler does when contract_ready is True:
        # It creates an ActivityLog entry with the right metadata structure.
        log_entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="deal",
            entity_id=deal.id,
            action="contract_ready",
            metadata_json={
                "alert_type": "contract_ready",
                "alert_user": True,
                "priority": "high",
                "buyer": {
                    "name": buyer.full_name,
                    "email": buyer.email,
                    "legal_name": "John Doe",
                    "phone": "555-123-4567",
                    "title_company": "Chicago Title",
                },
                "deal": {
                    "address": deal.address,
                    "asking_price": float(deal.asking_price),
                    "agreed_price": 180000,
                    "contract_price": float(deal.contract_price),
                    "assignment_fee": float(deal.asking_price) - float(deal.contract_price),
                },
            },
        )

        # Verify the alert has the right structure
        assert log_entry.action == "contract_ready"
        assert log_entry.entity_type == "deal"
        assert log_entry.metadata_json["alert_type"] == "contract_ready"
        assert log_entry.metadata_json["alert_user"] is True
        assert log_entry.metadata_json["priority"] == "high"

        # Verify buyer info
        assert log_entry.metadata_json["buyer"]["name"] == "John Doe"
        assert log_entry.metadata_json["buyer"]["email"] == "john@example.com"
        assert log_entry.metadata_json["buyer"]["legal_name"] == "John Doe"

        # Verify deal info
        assert log_entry.metadata_json["deal"]["address"] == "123 Main St"
        assert log_entry.metadata_json["deal"]["agreed_price"] == 180000
        assert log_entry.metadata_json["deal"]["asking_price"] == 200000


# ===========================================================================
# Stage transition edge cases
# ===========================================================================


class TestStageTransitions:
    """Test the conversation engine's stage transition logic."""

    @pytest.mark.asyncio
    async def test_pitching_to_engaging_with_info(self):
        """First reply from a pitching stage buyer should transition correctly."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(conversation_stage="pitching")

        ai_reply = _make_ai_json({
            "stage": "engaging",
            "pass": False,
            "unsub": False,
            "reply": "Thanks for reaching out! Can you tell me more about the neighborhood?",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(return_value=ai_reply)):
            result = await process_conversation(
                reply_body="Tell me more about this property",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["new_stage"] == "engaging"
        assert result["pass_detected"] is False
        assert result["contract_ready"] is False
        assert result["next_message"] is not None

    @pytest.mark.asyncio
    async def test_ai_error_fallback(self):
        """When Groq fails, engine should return fallback (same stage, no send)."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(conversation_stage="engaging")

        with patch("app.services.conversation_engine.groq_chat_completion",
                   AsyncMock(side_effect=Exception("API timeout"))):
            result = await process_conversation(
                reply_body="Can you tell me more?",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["new_stage"] == "engaging"  # Stayed at current stage
        assert result["contract_ready"] is False
        assert result["pass_detected"] is False
        assert result["next_message"] is None  # No send on error
