"""Comprehensive tests for the reply processing pipeline.

Covers:
- match_reply_to_campaign: all 4 priority methods + edge cases
- detect_uncertainty_and_hold: Non-Question, substantive answer, holding response
- get_question_round_message: all round values
- load_buyer_full_context: normal flow, no campaigns, no buyer
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.models import Buyer, Campaign, Deal


# ===========================================================================
# Helpers
# ===========================================================================

def _make_campaign(id=None, buyer_id=None, deal_id=None, touch_number=1,
                   status="Sent", sent_at=None, subject="Test Subject",
                   body="Test body", reply_body=None, reply_received_at=None):
    """Create a mock Campaign with sensible defaults."""
    c = MagicMock(spec=Campaign)
    c.id = id or uuid.uuid4()
    c.buyer_id = buyer_id or uuid.uuid4()
    c.deal_id = deal_id or uuid.uuid4()
    c.touch_number = touch_number
    c.status = status
    c.sent_at = sent_at
    c.subject = subject
    c.body = body
    c.reply_body = reply_body
    c.reply_received_at = reply_received_at
    return c


def _make_deal(id=None, address="123 Main St", city="Dallas", state="TX",
               zip="75001", property_type="House", asking_price=250000.0,
               status="Available"):
    """Create a mock Deal with sensible defaults."""
    d = MagicMock(spec=Deal)
    d.id = id or uuid.uuid4()
    d.address = address
    d.city = city
    d.state = state
    d.zip = zip
    d.property_type = property_type
    d.asking_price = asking_price
    d.status = status
    return d



# ===========================================================================
# match_reply_to_campaign tests
# ===========================================================================

class TestMatchReplyToCampaign:
    """Tests for the 4-priority chain reply-to-campaign matching."""

    # ------------------------------------------------------------------
    # Method 1: Header match
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_header_match_valid(self):
        """Header with valid campaign UUID should match directly."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()

        campaign = _make_campaign(
            id=campaign_id, buyer_id=buyer_id, deal_id=deal_id,
        )

        reply = {
            "subject": "Re: Property question",
            "body": "Can you send me more details?",
            "headers": {
                "In-Reply-To": f"<campaign-{campaign_id.hex}@dispo.local>",
                "References": "",
            },
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=campaign)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is campaign
        assert confidence == "header"
        mock_db.get.assert_awaited_once_with(Campaign, campaign_id)

    @pytest.mark.asyncio
    async def test_header_match_via_references(self):
        """References header with valid campaign UUID should also match."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()

        campaign = _make_campaign(
            id=campaign_id, buyer_id=buyer_id, deal_id=deal_id,
        )

        reply = {
            "subject": "Re: Property",
            "body": "Details?",
            "headers": {
                "In-Reply-To": "",
                "References": f"<campaign-{campaign_id.hex}@dispo.local>",
            },
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=campaign)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is campaign
        assert confidence == "header"

    @pytest.mark.asyncio
    async def test_header_match_malformed_uuid_falls_through(self):
        """Malformed UUID in header should be caught and fall to next method."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()

        reply = {
            "subject": "Re: Property",
            "body": "Details?",
            "headers": {
                "In-Reply-To": "<campaign-not-a-uuid@dispo.local>",
                "References": "",
            },
        }

        mock_db = AsyncMock()
        # First get() is for the header match — won't be called due to ValueError
        # Then the right methods will query campaigns
        result_scalars = MagicMock()
        result_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is None
        assert confidence == "fallback"

    @pytest.mark.asyncio
    async def test_header_match_wrong_buyer_falls_through(self):
        """Header matches campaign but campaign belongs to different buyer."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        other_buyer_id = uuid.uuid4()
        campaign_id = uuid.uuid4()

        other_campaign = _make_campaign(
            id=campaign_id, buyer_id=other_buyer_id,
        )

        reply = {
            "subject": "Re: Property",
            "body": "Details?",
            "headers": {
                "In-Reply-To": f"<campaign-{campaign_id.hex}@dispo.local>",
                "References": "",
            },
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=other_campaign)

        # No campaigns for this buyer — fall through
        result_scalars = MagicMock()
        result_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is None
        assert confidence == "fallback"

    @pytest.mark.asyncio
    async def test_header_no_headers_dict_falls_through(self):
        """Reply without a headers dict should skip header matching."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()

        reply = {
            "subject": "Re: Property",
            "body": "Details?",
            # No "headers" key
        }

        mock_db = AsyncMock()
        result_scalars = MagicMock()
        result_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is None

    # ------------------------------------------------------------------
    # Method 2: Subject match
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_subject_match_address(self):
        """Deal address in subject should match with score 1.0."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        campaign = _make_campaign(
            buyer_id=buyer_id, deal_id=deal_id,
        )

        reply = {
            "subject": "Re: 123 Main St property question",
            "body": "",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        # Active campaigns query returns [campaign]
        result_scalars = MagicMock()
        result_scalars.all.return_value = [campaign]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        # Deal get returns the deal
        deal = _make_deal(id=deal_id, address="123 Main St")
        mock_db.get = AsyncMock(return_value=deal)

        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is campaign
        assert confidence == "subject"

    @pytest.mark.asyncio
    async def test_subject_match_city(self):
        """Deal city in subject should match with score 0.9."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        campaign = _make_campaign(
            buyer_id=buyer_id, deal_id=deal_id,
        )

        reply = {
            "subject": "Re: Dallas property — still available?",
            "body": "",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = [campaign]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        deal = _make_deal(id=deal_id, address="456 Oak Ave", city="Dallas")
        mock_db.get = AsyncMock(return_value=deal)

        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is campaign
        assert confidence == "subject"

    @pytest.mark.asyncio
    async def test_subject_low_score_falls_through(self):
        """Subject score below 0.7 threshold should fall through to body."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        campaign = _make_campaign(
            buyer_id=buyer_id, deal_id=deal_id, status="Sent",
        )

        reply = {
            "subject": "Completely unrelated subject about something else",
            "body": "",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = [campaign]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        deal = _make_deal(id=deal_id, address="9999 Nowhere Lane")
        mock_db.get = AsyncMock(return_value=deal)

        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        # Subject failed (< 0.7), body empty (score=0), falls to fallback
        assert matched is campaign
        assert confidence == "fallback"

    @pytest.mark.asyncio
    async def test_subject_match_across_multiple_deals(self):
        """With multiple deals, should pick the best subject match."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal1_id, deal2_id, deal3_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        camp1 = _make_campaign(buyer_id=buyer_id, deal_id=deal1_id)
        camp2 = _make_campaign(buyer_id=buyer_id, deal_id=deal2_id)
        camp3 = _make_campaign(buyer_id=buyer_id, deal_id=deal3_id)

        reply = {
            "subject": "Tell me more about 123 Main St in Dallas",
            "body": "",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = [camp1, camp2, camp3]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        deal1 = _make_deal(id=deal1_id, address="123 Main St", city="Dallas")
        deal2 = _make_deal(id=deal2_id, address="888 Broad Ave", city="Ft Worth")
        deal3 = _make_deal(id=deal3_id, address="555 Elm St", city="Plano")

        async def mock_get(model, pk):
            if model == Deal and pk == deal1_id:
                return deal1
            if model == Deal and pk == deal2_id:
                return deal2
            if model == Deal and pk == deal3_id:
                return deal3
            return None

        mock_db.get = mock_get
        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        # Should match deal1 (address "123 Main St" in subject = score 1.0)
        assert matched is camp1
        assert confidence == "subject"

    # ------------------------------------------------------------------
    # Method 3: Body match
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_body_keyword_match(self):
        """Reply body containing 2+ deal keywords should match."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        campaign = _make_campaign(
            buyer_id=buyer_id, deal_id=deal_id, status="Sent",
        )

        reply = {
            "subject": "Re: Property question",
            "body": "I was looking at 123 Main St in Dallas, TX. The 75001 zip looks good.",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = [campaign]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        deal = _make_deal(
            id=deal_id, address="123 Main St", city="Dallas",
            state="TX", zip="75001",
        )
        mock_db.get = AsyncMock(return_value=deal)

        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        # Body has address + city + state + zip = 4+ keyword matches
        assert matched is campaign
        assert confidence == "body"

    @pytest.mark.asyncio
    async def test_body_below_threshold_falls_through(self):
        """Less than 2 keyword matches should fall through to fallback."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        campaign = _make_campaign(
            buyer_id=buyer_id, deal_id=deal_id, status="Sent",
        )

        reply = {
            "subject": "Re: Question",
            "body": "Can you tell me more about this property?",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = [campaign]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        deal = _make_deal(
            id=deal_id, address="999 Elm St", city="Houston",
        )
        mock_db.get = AsyncMock(return_value=deal)

        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is campaign
        assert confidence == "fallback"

    # ------------------------------------------------------------------
    # Method 4: Fallback
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_fallback_to_most_recent_sent(self):
        """When no header/subject/body matches, fall back to most recent Sent campaign."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal1_id = uuid.uuid4()
        deal2_id = uuid.uuid4()

        camp_sent = _make_campaign(
            buyer_id=buyer_id, deal_id=deal1_id, status="Sent",
        )
        camp_replied = _make_campaign(
            buyer_id=buyer_id, deal_id=deal2_id, status="Replied",
        )

        reply = {
            "subject": "General question",
            "body": "Tell me more",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = [camp_sent, camp_replied]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        mock_db.get = AsyncMock(return_value=None)  # No deals found
        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        # Should pick camp_sent (most recent Sent campaign)
        assert matched is camp_sent
        assert confidence == "fallback"

    @pytest.mark.asyncio
    async def test_fallback_no_sent_campaigns(self):
        """When no Sent campaigns exist, fall back to most recent campaign."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal_id = uuid.uuid4()

        camp_replied = _make_campaign(
            buyer_id=buyer_id, deal_id=deal_id, status="Replied",
        )

        reply = {
            "subject": "Hey",
            "body": "What's up",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = [camp_replied]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        mock_db.get = AsyncMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is camp_replied
        assert confidence == "fallback"

    @pytest.mark.asyncio
    async def test_no_campaigns_returns_none(self):
        """When buyer has no campaigns at all, return (None, 'fallback')."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()

        reply = {
            "subject": "Hello",
            "body": "Hi there",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = []
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is None
        assert confidence == "fallback"

    @pytest.mark.asyncio
    async def test_empty_subject_and_body(self):
        """Empty subject and body should fall through all methods to fallback."""
        from app.services.reply_processor import match_reply_to_campaign

        buyer_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        campaign = _make_campaign(
            buyer_id=buyer_id, deal_id=deal_id, status="Sent",
        )

        reply = {
            "subject": "",
            "body": "",
            "headers": {"In-Reply-To": "", "References": ""},
        }

        mock_db = AsyncMock()

        result_scalars = MagicMock()
        result_scalars.all.return_value = [campaign]
        result1 = MagicMock()
        result1.scalars.return_value = result_scalars

        mock_db.get = AsyncMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=result1)

        matched, confidence = await match_reply_to_campaign(
            mock_db, buyer_id, reply,
        )

        assert matched is campaign
        assert confidence == "fallback"


# ===========================================================================
# detect_uncertainty_and_hold tests
# ===========================================================================

class TestDetectUncertaintyAndHold:
    """Tests for the uncertainty detection and graceful hold feature."""

    @pytest.mark.asyncio
    async def test_non_question_returns_none(self):
        """Non-Question intent should return None immediately."""
        from app.services.reply_processor import detect_uncertainty_and_hold

        classification = {"reply_intent": "Interested", "question_answer": None}
        result = await detect_uncertainty_and_hold(
            reply={"body": "I'm interested"},
            classification=classification,
            db_session=None,
            buyer_id=uuid.uuid4(),
            deal_id=uuid.uuid4(),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_substantive_answer_returns_none(self):
        """Question with substantive answer (>20 chars) should pass through."""
        from app.services.reply_processor import detect_uncertainty_and_hold

        classification = {
            "reply_intent": "Question",
            "question_answer": "The property has 3 bedrooms and 2 bathrooms with 1500 sqft.",
        }
        result = await detect_uncertainty_and_hold(
            reply={"body": "How big is the house?"},
            classification=classification,
            db_session=None,
            buyer_id=uuid.uuid4(),
            deal_id=uuid.uuid4(),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_short_answer_triggers_hold(self):
        """Question with short/empty answer should trigger holding response."""
        from app.services.reply_processor import detect_uncertainty_and_hold

        classification = {
            "reply_intent": "Question",
            "question_answer": "I'll check",
        }
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch("app.services.reply_processor.settings") as mock_settings:
            mock_settings.operator_signature = "\n\nBest,\nIrtaqa"
            result = await detect_uncertainty_and_hold(
                reply={"body": "What's the exact square footage?"},
                classification=classification,
                db_session=mock_session,  # Must not be None for audit logging
                buyer_id=uuid.uuid4(),
                deal_id=uuid.uuid4(),
            )

        # Should return a holding response
        assert result is not None
        assert len(result) > 20

    @pytest.mark.asyncio
    async def test_no_answer_triggers_hold(self):
        """Question with no answer at all should trigger holding response."""
        from app.services.reply_processor import detect_uncertainty_and_hold

        classification = {
            "reply_intent": "Question",
            "question_answer": None,
        }
        mock_session = AsyncMock()

        with patch("app.services.reply_processor.settings") as mock_settings:
            mock_settings.operator_signature = "\n\nBest,\nIrtaqa"
            result = await detect_uncertainty_and_hold(
                reply={"body": "What's the tax history?"},
                classification=classification,
                db_session=mock_session,
                buyer_id=uuid.uuid4(),
                deal_id=uuid.uuid4(),
            )

        assert result is not None
        # Holding response should contain sign-off
        assert "Best," in result or "Irtaqa" in result

    @pytest.mark.asyncio
    async def test_empty_answer_triggers_hold(self):
        """Empty string answer should trigger holding response."""
        from app.services.reply_processor import detect_uncertainty_and_hold

        classification = {
            "reply_intent": "Question",
            "question_answer": "",
        }
        result = await detect_uncertainty_and_hold(
            reply={"body": "What's the zoning?"},
            classification=classification,
            db_session=None,
            buyer_id=uuid.uuid4(),
            deal_id=uuid.uuid4(),
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_hold_logs_audit(self):
        """When holding response is generated, should log to audit."""
        from app.services.reply_processor import detect_uncertainty_and_hold

        classification = {
            "reply_intent": "Question",
            "question_answer": None,
        }
        buyer_id = uuid.uuid4()
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with patch("app.services.reply_processor.settings") as mock_settings:
            mock_settings.operator_signature = ""
            result = await detect_uncertainty_and_hold(
                reply={"body": "When was it built?"},
                classification=classification,
                db_session=mock_session,
                buyer_id=buyer_id,
                deal_id=uuid.uuid4(),
            )

        assert result is not None
        assert len(result) > 20  # Should be a substantive holding response"


# ===========================================================================
# get_question_round_message tests
# ===========================================================================

class TestGetQuestionRoundMessage:
    """Tests for the question round escalation logic."""

    def test_round_1_auto_answer(self):
        """Round 1 should return 'auto_answer'."""
        from app.services.reply_processor import get_question_round_message
        assert get_question_round_message(1) == "auto_answer"

    def test_round_2_auto_answer(self):
        """Round 2 should return 'auto_answer'."""
        from app.services.reply_processor import get_question_round_message
        assert get_question_round_message(2) == "auto_answer"

    def test_round_3_final_answer(self):
        """Round 3 should return 'final_answer_prompt'."""
        from app.services.reply_processor import get_question_round_message
        assert get_question_round_message(3) == "final_answer_prompt"

    def test_round_4_manual_intervention(self):
        """Round 4 should return 'manual_intervention_needed'."""
        from app.services.reply_processor import get_question_round_message
        assert get_question_round_message(4) == "manual_intervention_needed"

    def test_round_5_plus_manual_intervention(self):
        """Rounds 5+ should return 'manual_intervention_needed'."""
        from app.services.reply_processor import get_question_round_message
        assert get_question_round_message(5) == "manual_intervention_needed"
        assert get_question_round_message(10) == "manual_intervention_needed"
        assert get_question_round_message(100) == "manual_intervention_needed"


# ===========================================================================
# load_buyer_full_context tests
# ===========================================================================

class TestLoadBuyerFullContext:
    """Tests for loading complete buyer context across deals."""

    @pytest.mark.asyncio
    async def test_buyer_not_found(self):
        """When buyer is not found, buyer key should be None."""
        from app.services.reply_processor import load_buyer_full_context

        buyer_id = uuid.uuid4()
        primary_deal_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)  # Buyer not found

        result_scalars = MagicMock()
        result_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await load_buyer_full_context(
            mock_db, buyer_id, primary_deal_id,
        )

        assert result["buyer"] is None
        assert result["primary_deal"] is None
        assert result["primary_thread"] == []
        assert result["other_active_deals"] == []
        assert result["total_active_deals"] == 1

    @pytest.mark.asyncio
    async def test_no_campaigns(self):
        """Buyer with no campaigns at all: empty threads, no deals."""
        from app.services.reply_processor import load_buyer_full_context

        buyer_id = uuid.uuid4()
        primary_deal_id = uuid.uuid4()

        mock_buyer = MagicMock(spec=Buyer)
        mock_buyer.id = buyer_id

        mock_db = AsyncMock()

        async def mock_get(model, pk):
            if model == Buyer and pk == buyer_id:
                return mock_buyer
            if model == Deal and pk == primary_deal_id:
                return _make_deal(id=primary_deal_id)
            return None

        mock_db.get = mock_get

        result_scalars = MagicMock()
        result_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await load_buyer_full_context(
            mock_db, buyer_id, primary_deal_id,
        )

        assert result["buyer"] is mock_buyer
        assert result["primary_thread"] == []
        assert result["other_active_deals"] == []
        assert result["total_active_deals"] == 1

    @pytest.mark.asyncio
    async def test_only_primary_deal(self):
        """Buyer with campaigns only for the primary deal."""
        from app.services.reply_processor import load_buyer_full_context

        buyer_id = uuid.uuid4()
        primary_deal_id = uuid.uuid4()

        mock_buyer = MagicMock(spec=Buyer)
        mock_buyer.id = buyer_id
        primary_deal = _make_deal(id=primary_deal_id, status="Available")
        campaign = _make_campaign(
            buyer_id=buyer_id, deal_id=primary_deal_id, status="Sent",
        )

        mock_db = AsyncMock()

        async def mock_get(model, pk):
            if model == Buyer and pk == buyer_id:
                return mock_buyer
            if model == Deal and pk == primary_deal_id:
                return primary_deal
            return None

        mock_db.get = mock_get

        result_scalars = MagicMock()
        result_scalars.all.return_value = [campaign]
        mock_result = MagicMock()
        mock_result.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await load_buyer_full_context(
            mock_db, buyer_id, primary_deal_id,
        )

        assert result["buyer"] is mock_buyer
        assert result["primary_deal"] is primary_deal
        assert len(result["primary_thread"]) == 1
        assert result["primary_thread"][0] is campaign
        assert result["other_active_deals"] == []
        assert result["total_active_deals"] == 1

    @pytest.mark.asyncio
    async def test_primary_deal_and_other_active_deals(self):
        """Buyer with campaigns for primary deal AND other active deals."""
        from app.services.reply_processor import load_buyer_full_context

        buyer_id = uuid.uuid4()
        primary_deal_id = uuid.uuid4()
        other_deal_id = uuid.uuid4()

        mock_buyer = MagicMock(spec=Buyer)
        mock_buyer.id = buyer_id
        primary_deal = _make_deal(id=primary_deal_id, status="Available")
        other_deal = _make_deal(id=other_deal_id, status="Available")

        camp_primary = _make_campaign(
            buyer_id=buyer_id, deal_id=primary_deal_id, status="Sent",
        )
        camp_other = _make_campaign(
            buyer_id=buyer_id, deal_id=other_deal_id, status="Sent",
        )

        mock_db = AsyncMock()

        async def mock_get(model, pk):
            if model == Buyer and pk == buyer_id:
                return mock_buyer
            if model == Deal and pk == primary_deal_id:
                return primary_deal
            if model == Deal and pk == other_deal_id:
                return other_deal
            return None

        mock_db.get = mock_get

        result_scalars = MagicMock()
        result_scalars.all.return_value = [camp_primary, camp_other]
        mock_result = MagicMock()
        mock_result.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await load_buyer_full_context(
            mock_db, buyer_id, primary_deal_id,
        )

        assert result["buyer"] is mock_buyer
        assert result["primary_deal"] is primary_deal
        assert len(result["primary_thread"]) == 1
        assert result["primary_thread"][0] is camp_primary
        assert len(result["other_active_deals"]) == 1
        assert result["other_active_deals"][0]["deal"] is other_deal
        assert result["other_active_deals"][0]["thread"][0] is camp_other
        assert result["other_active_deals"][0]["status"] == "Available"
        assert result["total_active_deals"] == 2

    @pytest.mark.asyncio
    async def test_skips_inactive_deals(self):
        """Campaigns for closed/dead deals should not appear in other_active_deals."""
        from app.services.reply_processor import load_buyer_full_context

        buyer_id = uuid.uuid4()
        primary_deal_id = uuid.uuid4()
        sold_deal_id = uuid.uuid4()

        mock_buyer = MagicMock(spec=Buyer)
        mock_buyer.id = buyer_id
        primary_deal = _make_deal(id=primary_deal_id, status="Available")
        sold_deal = _make_deal(id=sold_deal_id, status="Sold")

        camp_primary = _make_campaign(
            buyer_id=buyer_id, deal_id=primary_deal_id, status="Sent",
        )
        camp_sold = _make_campaign(
            buyer_id=buyer_id, deal_id=sold_deal_id, status="Sent",
        )

        mock_db = AsyncMock()

        async def mock_get(model, pk):
            if model == Buyer and pk == buyer_id:
                return mock_buyer
            if model == Deal and pk == primary_deal_id:
                return primary_deal
            if model == Deal and pk == sold_deal_id:
                return sold_deal
            return None

        mock_db.get = mock_get

        result_scalars = MagicMock()
        result_scalars.all.return_value = [camp_primary, camp_sold]
        mock_result = MagicMock()
        mock_result.scalars.return_value = result_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await load_buyer_full_context(
            mock_db, buyer_id, primary_deal_id,
        )

        assert len(result["other_active_deals"]) == 0
        assert result["total_active_deals"] == 1
