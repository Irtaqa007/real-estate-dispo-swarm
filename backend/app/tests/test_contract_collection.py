"""Tests for the full contract collection conversation flow.

Simulates individual steps of a 5-step buyer conversation:
  1. Interest reply → starts collecting legal name
  2. Name collected → asks for phone
  3. Phone collected → asks for title company
  4. Title company collected → asks for agreed price
  5. All 4 pieces collected → contract_ready fires
  6. Pass mid-conversation stops the flow

Also tests edge cases:
  - Pre-checks (unsubscribe) still work mid-conversation
  - AI overly optimistic downgrade (claims contract_ready but pieces missing)
  - AI error fallback (Groq failure returns safe defaults)
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.models import Buyer, Campaign, Deal


# ===========================================================================
# Helpers
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
    b.full_name = overrides.get("full_name", "Ahmad Raza Khan")
    b.email = overrides.get("email", "ahmad@example.com")
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


def _make_ai_json(data: dict) -> MagicMock:
    """Create a mock Groq response from a dict of JSON fields."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(data)
    return response


# ===========================================================================
# Contract collection flow — individual step tests
# ===========================================================================


class TestContractCollectionSteps:
    """Each step of the 5-reply contract collection flow tested independently."""

    @pytest.mark.asyncio
    async def test_interest_reply_starts_collecting_info(self):
        """Interest reply from pitching stage should transition to collecting_info
        and ask for legal name."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(conversation_stage="pitching")

        ai_response = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "Great to hear you're interested! Could you share your legal name so I can get the paperwork started?",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=ai_response),
        ):
            result = await process_conversation(
                reply_body="Yes I'm interested, let's do it",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["new_stage"] == "collecting_info", (
            f"Expected collecting_info, got {result['new_stage']}"
        )
        assert result["pass_detected"] is False
        assert result["contract_ready"] is False
        assert result["next_message"] is not None
        # Should ask for legal name
        assert "name" in result["next_message"].lower(), (
            f"Expected next_message to ask for name, got: {result['next_message']}"
        )

    @pytest.mark.asyncio
    async def test_name_collected_asks_for_phone(self):
        """When buyer provides legal name, the system should extract it and ask for phone."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name=None,
        )

        ai_response = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "Thanks Ahmad! Could you share your phone number so we can move forward?",
            "extracted_legal_name": "Ahmad Raza Khan",
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=ai_response),
        ):
            result = await process_conversation(
                reply_body="Ahmad Raza Khan",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["extracted_info"]["legal_name"] == "Ahmad Raza Khan", (
            f"Expected legal_name='Ahmad Raza Khan', got {result['extracted_info']['legal_name']}"
        )
        assert result["next_message"] is not None
        # Should ask for phone number
        assert "phone" in result["next_message"].lower(), (
            f"Expected next_message to ask for phone, got: {result['next_message']}"
        )

    @pytest.mark.asyncio
    async def test_phone_collected_asks_for_title(self):
        """When buyer provides phone, the system should extract it and ask for title company."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="Ahmad Raza Khan",
            buyer_phone=None,
        )

        ai_response = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "Perfect. Which title company do you typically work with for closings?",
            "extracted_legal_name": None,
            "extracted_phone": "923001234567",
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=ai_response),
        ):
            result = await process_conversation(
                reply_body="923001234567",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["extracted_info"]["phone"] == "923001234567", (
            f"Expected phone='923001234567', got {result['extracted_info']['phone']}"
        )
        assert result["next_message"] is not None
        # Should ask for title company
        assert "title" in result["next_message"].lower(), (
            f"Expected next_message to ask for title company, got: {result['next_message']}"
        )

    @pytest.mark.asyncio
    async def test_title_collected_asks_for_price(self):
        """When buyer provides title company, the system should extract it and ask for agreed price."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="Ahmad Raza Khan",
            buyer_phone="923001234567",
            buyer_title_company=None,
        )

        ai_response = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "Great choice! Last thing — what price are you comfortable with?",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": "First American Title",
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=ai_response),
        ):
            result = await process_conversation(
                reply_body="First American Title",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["extracted_info"]["title_company"] == "First American Title", (
            f"Expected title_company='First American Title', got {result['extracted_info']['title_company']}"
        )
        assert result["next_message"] is not None
        # Should ask for agreed price
        text = result["next_message"].lower()
        assert any(w in text for w in ["price", "offer", "number", "comfortable"]), (
            f"Expected next_message to ask for price, got: {result['next_message']}"
        )

    @pytest.mark.asyncio
    async def test_all_info_fires_contract_ready(self):
        """When all 4 pieces are collected, the system should fire contract_ready."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="Ahmad Raza Khan",
            buyer_phone="923001234567",
            buyer_title_company="First American Title",
            agreed_price=None,
        )

        ai_response = _make_ai_json({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "That works for me — I'll start the paperwork now.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": 172000,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=ai_response),
        ):
            result = await process_conversation(
                reply_body="172000",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["contract_ready"] is True, (
            f"Expected contract_ready=True, got contract_ready={result['contract_ready']} "
            f"with stage={result['new_stage']}"
        )
        assert result["new_stage"] == "contract_ready", (
            f"Expected contract_ready stage, got {result['new_stage']}"
        )
        assert result["extracted_info"]["agreed_price"] == 172000, (
            f"Expected agreed_price=172000, got {result['extracted_info']['agreed_price']}"
        )

    @pytest.mark.asyncio
    async def test_pass_during_collection_stops_flow(self):
        """Pass pre-check should still work mid-contract-collection."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="Ahmad Raza Khan",
            # buyer_phone is None — mid-collection
        )

        result = await process_conversation(
            reply_body="Actually never mind, pass",
            reply_subject="Re: Great deal in Austin",
            buyer=buyer,
            deal=deal,
            campaign=campaign,
            thread_history=[],
        )

        assert result["pass_detected"] is True, "Expected pass_detected=True"
        assert result["new_stage"] == "passed", (
            f"Expected stage='passed', got {result['new_stage']}"
        )
        assert result["next_message"] is None, (
            f"Expected next_message=None on pass, got: {result['next_message']}"
        )


# ===========================================================================
# Edge cases
# ===========================================================================


class TestContractCollectionEdgeCases:
    """Pre-checks, AI downgrades, and error fallbacks during collection."""

    @pytest.mark.asyncio
    async def test_unsubscribe_mid_conversation(self):
        """Unsubscribe pre-check should work during contract collection."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="Ahmad Raza Khan",
            buyer_phone="923001234567",
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
    async def test_ai_overly_optimistic_downgraded(self):
        """If AI claims contract_ready but pieces are missing, engine downgrades."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(
            conversation_stage="collecting_info",
            buyer_legal_name="Ahmad Raza Khan",
            buyer_phone="923001234567",
            buyer_title_company="First American Title",
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

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=ai_overly_optimistic),
        ):
            result = await process_conversation(
                reply_body="Sounds good, send it over",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # Engine should downgrade: missing agreed_price
        assert result["contract_ready"] is False
        assert result["new_stage"] == "collecting_info", (
            f"Expected collecting_info (missing price), got {result['new_stage']}"
        )

    @pytest.mark.asyncio
    async def test_ai_error_fallback(self):
        """When Groq fails, engine should return fallback (same stage, no send)."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal()
        buyer = _make_buyer()
        campaign = _make_campaign(conversation_stage="collecting_info")

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(side_effect=Exception("API timeout")),
        ):
            result = await process_conversation(
                reply_body="Can you tell me more?",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["new_stage"] == "collecting_info"  # Stayed at current stage
        assert result["contract_ready"] is False
        assert result["pass_detected"] is False
        assert result["next_message"] is None  # No send on error
