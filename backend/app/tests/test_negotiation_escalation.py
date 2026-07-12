"""Tests for negotiation escalation — below-floor counter alerts.

Covers:
- conversation_engine: below-floor creates escalation, above-floor auto-approves
- GET /api/alerts/negotiation: returns unresolved alerts
- POST /api/alerts/negotiation/{id}/approve: marks resolved, updates campaign, sends reply
- POST /api/alerts/negotiation/{id}/reject: marks resolved, sends decline or counter
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.models import ActivityLog, Buyer, Campaign, Deal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_deal(
    deal_id: uuid.UUID = None,
    floor_price: float = 180000.0,
    asking_price: float = 200000.0,
    status: str = "Available",
) -> Deal:
    d = MagicMock(spec=Deal)
    d.id = deal_id or uuid.uuid4()
    d.address = "123 Main St"
    d.city = "Austin"
    d.state = "TX"
    d.property_type = "House"
    d.beds = 3
    d.baths = 2
    d.sqft = 1800
    d.arv = 300000.0
    d.asking_price = asking_price
    d.floor_price = floor_price
    d.contract_price = 160000.0
    d.spread = asking_price - 160000.0
    d.repair_estimate = 20000.0
    d.condition_description = "Good condition"
    d.year_built = 1995
    d.status = status
    return d


def _make_buyer(buyer_id: uuid.UUID = None) -> Buyer:
    b = MagicMock(spec=Buyer)
    b.id = buyer_id or uuid.uuid4()
    b.full_name = "Test Buyer"
    b.email = "buyer@test.com"
    return b


def _make_campaign(
    campaign_id: uuid.UUID = None,
    deal_id: uuid.UUID = None,
    buyer_id: uuid.UUID = None,
    stage: str = "pitching",
) -> Campaign:
    c = MagicMock(spec=Campaign)
    c.id = campaign_id or uuid.uuid4()
    c.deal_id = deal_id or uuid.uuid4()
    c.buyer_id = buyer_id or uuid.uuid4()
    c.conversation_stage = stage
    c.buyer_legal_name = None
    c.buyer_phone = None
    c.buyer_title_company = None
    c.agreed_price = None
    return c


def _make_negotiation_alert(
    alert_id: uuid.UUID = None,
    campaign_id: uuid.UUID = None,
    deal_id: uuid.UUID = None,
    buyer_id: uuid.UUID = None,
    resolved: bool = False,
) -> ActivityLog:
    entry = MagicMock(spec=ActivityLog)
    entry.id = alert_id or uuid.uuid4()
    entry.action = "negotiation_escalation"
    entry.resolved = resolved
    entry.resolved_at = None
    entry.created_at = datetime.now(timezone.utc)
    entry.metadata_json = {
        "buyer_id": str(buyer_id or uuid.uuid4()),
        "deal_id": str(deal_id or uuid.uuid4()),
        "campaign_id": str(campaign_id or uuid.uuid4()),
        "counter_price": 150000.0,
        "floor_price": 180000.0,
        "gap": 30000.0,
        "buyer_email": "buyer@test.com",
        "deal_address": "123 Main St",
        "buyer_name": "Test Buyer",
    }
    return entry


# ===========================================================================
# Tests: conversation_engine — below-floor counter
# ===========================================================================

class TestConversationEngineCounter:

    @pytest.mark.asyncio
    async def test_counter_below_floor_creates_alert(self):
        """Below-floor counter should produce negotiation_escalation data."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=180000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(deal_id=deal.id, buyer_id=buyer.id)

        # Mock groq to return a stage where agreed_price is below floor
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"engaging","pass":false,"unsub":false,'
            '"reply":"I can do $150k","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":150000}'
        )

        with patch("app.services.conversation_engine.groq_chat_completion",
                    AsyncMock(return_value=mock_response)):
            result = await process_conversation(
                reply_body="I can do $150,000",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["negotiation_escalation"] is not None
        esc = result["negotiation_escalation"]
        assert esc["counter_price"] == 150000.0
        assert esc["floor_price"] == 180000.0
        assert esc["gap"] == 30000.0
        assert esc["buyer_id"] == str(buyer.id)
        assert esc["deal_id"] == str(deal.id)
        assert esc["campaign_id"] == str(campaign.id)

    @pytest.mark.asyncio
    async def test_counter_below_floor_no_auto_reply(self):
        """Below-floor counter should suppress auto-reply (next_message is empty/None)."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=180000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(deal_id=deal.id, buyer_id=buyer.id)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"engaging","pass":false,"unsub":false,'
            '"reply":"I can do $150k","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":150000}'
        )

        with patch("app.services.conversation_engine.groq_chat_completion",
                    AsyncMock(return_value=mock_response)):
            result = await process_conversation(
                reply_body="I can do $150,000",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # next_message should be empty/None (no auto-reply to below-floor counter)
        assert result["next_message"] is None or result["next_message"] == ""
        assert result["new_stage"] == "negotiating"

    @pytest.mark.asyncio
    async def test_counter_above_floor_auto_approves(self):
        """Above-floor counter should NOT create escalation (no negotiation_escalation)."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=180000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(deal_id=deal.id, buyer_id=buyer.id)

        # Agreed price at 190k is above floor of 180k
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"engaging","pass":false,"unsub":false,'
            '"reply":"$190k works for me","extracted_legal_name":"Test Buyer",'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":190000}'
        )

        with patch("app.services.conversation_engine.groq_chat_completion",
                    AsyncMock(return_value=mock_response)):
            result = await process_conversation(
                reply_body="$190k works for me",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # No escalation — price is above floor
        assert result["negotiation_escalation"] is None
        # Should proceed to collecting_info (legal name was extracted)
        assert result["new_stage"] == "collecting_info"
        # Should have a reply (auto-approve)
        assert result["next_message"] is not None


# ===========================================================================
# Tests: GET /api/alerts/negotiation
# ===========================================================================

class TestGetNegotiationAlerts:

    @pytest.mark.asyncio
    async def test_get_negotiation_alerts_returns_unresolved_only(self):
        """GET /api/alerts/negotiation should return unresolved alerts."""
        from app.routers.alerts import get_negotiation_alerts

        alert_id_1 = uuid.uuid4()
        alert_id_2 = uuid.uuid4()
        now = datetime.now(timezone.utc)
        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()

        # Resolved alert
        resolved_entry = MagicMock(spec=ActivityLog)
        resolved_entry.id = alert_id_1
        resolved_entry.action = "negotiation_escalation"
        resolved_entry.resolved = True
        resolved_entry.resolved_at = now
        resolved_entry.created_at = now
        resolved_entry.metadata_json = {
            "buyer_id": str(buyer_id),
            "deal_id": str(deal_id),
            "campaign_id": str(campaign_id),
            "buyer_email": "buyer@test.com",
            "counter_price": 160000.0,
            "floor_price": 180000.0,
            "gap": 20000.0,
            "deal_address": "123 Main St",
        }

        # Unresolved alert
        unresolved_entry = MagicMock(spec=ActivityLog)
        unresolved_entry.id = alert_id_2
        unresolved_entry.action = "negotiation_escalation"
        unresolved_entry.resolved = False
        unresolved_entry.resolved_at = None
        unresolved_entry.created_at = now
        unresolved_entry.metadata_json = {
            "buyer_id": str(buyer_id),
            "deal_id": str(deal_id),
            "campaign_id": str(campaign_id),
            "buyer_email": "buyer@test.com",
            "counter_price": 150000.0,
            "floor_price": 180000.0,
            "gap": 30000.0,
            "deal_address": "123 Main St",
            "buyer_name": "Test Buyer",
        }

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            unresolved_entry, resolved_entry,
        ]
        mock_db.execute = AsyncMock(return_value=mock_result)

        alerts = await get_negotiation_alerts(db=mock_db)

        # Both should be returned (endpoint returns all matching action,
        # frontend filters by resolved)
        assert len(alerts) == 2
        # Find unresolved
        unresolved = [a for a in alerts if not a["resolved"]]
        assert len(unresolved) == 1
        assert unresolved[0]["counter_price"] == 150000.0

    @pytest.mark.asyncio
    async def test_resolved_alerts_not_in_list(self):
        """Resolved alerts should still appear but with resolved=True."""
        from app.routers.alerts import get_negotiation_alerts

        # Create only a resolved entry
        resolved_entry = _make_negotiation_alert(resolved=True)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [resolved_entry]
        mock_db.execute = AsyncMock(return_value=mock_result)

        alerts = await get_negotiation_alerts(db=mock_db)

        resolved_alerts = [a for a in alerts if a["resolved"]]
        assert len(resolved_alerts) == 1


# ===========================================================================
# Tests: POST /api/alerts/negotiation/{id}/approve
# ===========================================================================

class TestApproveNegotiation:

    @pytest.mark.asyncio
    async def test_approve_alert_sends_reply(self):
        """Approving should send an email to the buyer."""
        from app.routers.alerts import approve_negotiation
        from app.routers.alerts import NegotiationApproveRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        campaign = _make_campaign(campaign_id, deal_id, buyer_id)
        deal = _make_deal(deal_id)
        entry = _make_negotiation_alert(alert_id, campaign_id, deal_id, buyer_id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Campaign: campaign,
            Buyer: None,
            Deal: deal,
        }.get(model))

        body = NegotiationApproveRequest(final_price=165000.0)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            result = await approve_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        assert result["action"] == "approved"
        assert result["final_price"] == 165000.0

        # Email should have been sent
        send_mock.assert_called_once()
        call_kwargs = send_mock.call_args.kwargs
        assert "buyer@test.com" in call_kwargs.get("to", "")
        assert "165,000" in call_kwargs.get("body", "") or "$165,000" in call_kwargs.get("body", "")

        # Alert should be marked resolved
        assert entry.resolved is True
        assert entry.resolved_at is not None

    @pytest.mark.asyncio
    async def test_approve_sets_agreed_price(self):
        """Approving should set campaign.agreed_price to the final price."""
        from app.routers.alerts import approve_negotiation
        from app.routers.alerts import NegotiationApproveRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        campaign = _make_campaign(campaign_id, deal_id, buyer_id)
        deal = _make_deal(deal_id)
        entry = _make_negotiation_alert(alert_id, campaign_id, deal_id, buyer_id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Campaign: campaign,
            Buyer: None,
            Deal: deal,
        }.get(model))

        body = NegotiationApproveRequest(final_price=170000.0)

        with patch("app.routers.alerts.send_email", AsyncMock()):
            await approve_negotiation(alert_id, body, db=mock_db)

        assert campaign.agreed_price == 170000.0

    @pytest.mark.asyncio
    async def test_approve_sets_stage_collecting_info(self):
        """Approving should set campaign.conversation_stage to collecting_info."""
        from app.routers.alerts import approve_negotiation
        from app.routers.alerts import NegotiationApproveRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        campaign = _make_campaign(campaign_id, deal_id, buyer_id, stage="negotiating")
        deal = _make_deal(deal_id)
        entry = _make_negotiation_alert(alert_id, campaign_id, deal_id, buyer_id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Campaign: campaign,
            Buyer: None,
            Deal: deal,
        }.get(model))

        body = NegotiationApproveRequest(final_price=170000.0)

        with patch("app.routers.alerts.send_email", AsyncMock()):
            await approve_negotiation(alert_id, body, db=mock_db)

        assert campaign.conversation_stage == "collecting_info"


# ===========================================================================
# Tests: POST /api/alerts/negotiation/{id}/reject
# ===========================================================================

class TestRejectNegotiation:

    @pytest.mark.asyncio
    async def test_reject_without_counter_sends_decline(self):
        """Rejecting without a counter should send a polite decline."""
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        deal = _make_deal(deal_id)
        entry = _make_negotiation_alert(alert_id, campaign_id, deal_id, buyer_id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Deal: deal,
        }.get(model))

        body = NegotiationRejectRequest(counter_offer=None)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            result = await reject_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        assert result["action"] == "declined"

        # Email should have been sent with a decline message
        send_mock.assert_called_once()
        call_kwargs = send_mock.call_args.kwargs
        assert "buyer@test.com" in call_kwargs.get("to", "")
        body_text = call_kwargs.get("body", "")
        assert any(w in body_text.lower() for w in ["thanks", "unfortunately", "appreciate"])

        # Alert should be marked resolved
        assert entry.resolved is True
        assert entry.resolved_at is not None

    @pytest.mark.asyncio
    async def test_reject_with_counter_sends_counter_offer(self):
        """Rejecting with a counter_offer should send that price back to the buyer."""
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        deal = _make_deal(deal_id)
        entry = _make_negotiation_alert(alert_id, campaign_id, deal_id, buyer_id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Deal: deal,
        }.get(model))

        body = NegotiationRejectRequest(counter_offer=175000.0)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            result = await reject_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        assert result["action"] == "countered"
        assert result["counter_sent"] == 175000.0

        # Email should have been sent with the counter price
        send_mock.assert_called_once()
        call_kwargs = send_mock.call_args.kwargs
        body_text = call_kwargs.get("body", "")
        assert "175,000" in body_text or "$175,000" in body_text

    @pytest.mark.asyncio
    async def test_reject_marks_resolved(self):
        """Rejecting should mark the alert as resolved regardless of counter or not."""
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        deal = _make_deal(deal_id)
        entry = _make_negotiation_alert(alert_id, campaign_id, deal_id, buyer_id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Deal: deal,
        }.get(model))

        # No counter — just decline
        body = NegotiationRejectRequest(counter_offer=None)

        with patch("app.routers.alerts.send_email", AsyncMock()):
            result = await reject_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        assert entry.resolved is True
        assert entry.resolved_at is not None
        assert entry.metadata_json["resolution_action"] in ("declined", "countered")


# ===========================================================================
# End-to-end negotiation escalation flow tests
# ===========================================================================


class TestNegotiationEscalationFlow:
    """End-to-end style tests for below-floor counter escalation,
    similar to the contract collection step-by-step tests."""

    @pytest.mark.asyncio
    async def test_below_floor_counter_from_interest(self):
        """Buyer counters below floor from pitching stage → escalation created, no auto-reply."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=180000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(stage="pitching")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"engaging","pass":false,"unsub":false,'
            '"reply":"I can do $150k","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":150000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="I can do $150,000",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # Escalation created
        assert result["negotiation_escalation"] is not None, (
            "Expected negotiation_escalation for below-floor counter"
        )
        esc = result["negotiation_escalation"]
        assert esc["counter_price"] == 150000.0
        assert esc["floor_price"] == 180000.0
        assert esc["gap"] == 30000.0

        # Stage forced to negotiating
        assert result["new_stage"] == "negotiating", (
            f"Expected stage='negotiating', got {result['new_stage']}"
        )

        # No auto-reply
        assert result["next_message"] is None or result["next_message"] == "", (
            "Expected no auto-reply for below-floor counter"
        )

        # Not a pass or unsubscribe
        assert result["pass_detected"] is False
        assert result["unsubscribe_detected"] is False

        # Not contract_ready
        assert result["contract_ready"] is False

    @pytest.mark.asyncio
    async def test_below_floor_during_collection_escalates(self):
        """Mid-collection, buyer gives below-floor price → escalation overrides, not contract_ready."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=180000.0)
        buyer = _make_buyer()
        # Campaign is mid-collection with 3 of 4 pieces collected
        campaign = _make_campaign(
            stage="collecting_info",
            deal_id=deal.id,
            buyer_id=buyer.id,
        )
        campaign.buyer_legal_name = "Ahmad Raza Khan"
        campaign.buyer_phone = "923001234567"
        campaign.buyer_title_company = "First American Title"
        campaign.agreed_price = None

        # AI extracts agreed_price below floor
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"collecting_info","pass":false,"unsub":false,'
            '"reply":"$150k sounds good","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":150000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="$150k sounds good",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # All 4 pieces would be present (3 from campaign + 1 from AI),
        # but below-floor escalation should override contract_ready
        assert result["negotiation_escalation"] is not None, (
            "Expected escalation even during collection when price is below floor"
        )
        esc = result["negotiation_escalation"]
        assert esc["counter_price"] == 150000.0
        assert esc["floor_price"] == 180000.0

        # Stage should be negotiating (overridden from what would be contract_ready)
        assert result["new_stage"] == "negotiating", (
            f"Expected stage='negotiating', got {result['new_stage']}"
        )

        # Not contract_ready — escalation takes priority
        assert result["contract_ready"] is False, (
            "Below-floor price should NOT result in contract_ready"
        )

        # No auto-reply
        assert result["next_message"] is None or result["next_message"] == ""

    @pytest.mark.asyncio
    async def test_below_floor_escalation_metadata(self):
        """Verify all fields in the escalation dict are populated correctly."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=175000.0, asking_price=200000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(stage="pitching", deal_id=deal.id, buyer_id=buyer.id)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"engaging","pass":false,"unsub":false,'
            '"reply":"$160k","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":160000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="$160k",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        esc = result["negotiation_escalation"]
        assert esc is not None

        # Price fields
        assert esc["counter_price"] == 160000.0
        assert esc["floor_price"] == 175000.0
        assert esc["gap"] == 15000.0

        # Buyer info
        assert esc["buyer_name"] == buyer.full_name
        assert esc["buyer_email"] == buyer.email
        assert esc["buyer_id"] == str(buyer.id)

        # Deal info
        assert esc["deal_id"] == str(deal.id)
        assert esc["deal_address"] == deal.address

        # Campaign info
        assert esc["campaign_id"] == str(campaign.id)

    @pytest.mark.asyncio
    async def test_price_at_exactly_floor_no_escalation(self):
        """Price exactly at floor should NOT create an escalation."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=180000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(stage="engaging")

        # Agreed price exactly at floor (use distinct reply to avoid anti-echo guard)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"collecting_info","pass":false,"unsub":false,'
            '"reply":"Perfect — let me grab your details. What\'s your legal name?",'
            '"extracted_legal_name":"Ahmad Raza Khan",'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":180000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="$180k works for me",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # No escalation — price equals floor (not below)
        assert result["negotiation_escalation"] is None, (
            "Price at floor should not trigger escalation"
        )
        # Should proceed to collecting_info since legal name was extracted
        assert result["new_stage"] == "collecting_info"
        # Should have an auto-reply (not suppressed by anti-echo guard)
        assert result["next_message"] is not None

    @pytest.mark.asyncio
    async def test_price_slightly_above_floor_no_escalation(self):
        """Price just above floor should proceed normally without escalation."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=180000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(stage="engaging")

        # Agreed price slightly above floor
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"collecting_info","pass":false,"unsub":false,'
            '"reply":"$195k and we have a deal","extracted_legal_name":"Ahmad Raza Khan",'
            '"extracted_phone":"923001234567","extracted_title_company":null,'
            '"extracted_agreed_price":195000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="$195k and we have a deal",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # No escalation — price is above floor
        assert result["negotiation_escalation"] is None
        # Should move to collecting_info with legal name and phone
        assert result["new_stage"] == "collecting_info"
        # Extracted info should be present
        assert result["extracted_info"]["legal_name"] == "Ahmad Raza Khan"
        assert result["extracted_info"]["phone"] == "923001234567"

    @pytest.mark.asyncio
    async def test_below_floor_with_floor_at_zero(self):
        """When floor_price is 0 or None, no escalation should occur."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=0.0)
        buyer = _make_buyer()
        campaign = _make_campaign(stage="engaging")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"collecting_info","pass":false,"unsub":false,'
            '"reply":"$150k sounds good","extracted_legal_name":"Ahmad Raza Khan",'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":150000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="$150k sounds good",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # No escalation — floor is 0.0, 150000 > 0
        assert result["negotiation_escalation"] is None
        assert result["new_stage"] == "collecting_info"

    @pytest.mark.asyncio
    async def test_above_floor_completes_contract_collection(self):
        """Above-floor price when all 4 pieces are present → contract_ready, not escalation."""
        from app.services.conversation_engine import process_conversation

        deal = _make_deal(floor_price=170000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(
            stage="collecting_info",
            deal_id=deal.id,
            buyer_id=buyer.id,
        )
        campaign.buyer_legal_name = "Ahmad Raza Khan"
        campaign.buyer_phone = "923001234567"
        campaign.buyer_title_company = "First American Title"
        campaign.agreed_price = None

        # AI extracts agreed_price above floor → all 4 collected, no escalation
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"collecting_info","pass":false,"unsub":false,'
            '"reply":"$180k works","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":180000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="$180k works",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        # No escalation — above floor
        assert result["negotiation_escalation"] is None

        # contract_ready fires because all 4 pieces are present
        assert result["contract_ready"] is True, (
            f"Expected contract_ready=True, got {result['contract_ready']}"
        )
        assert result["new_stage"] == "contract_ready"
        assert result["extracted_info"]["agreed_price"] == 180000


# ===========================================================================
# End-to-end: operator approve/reject flow for negotiation alerts
# ===========================================================================


class TestOperatorNegotiationFlow:
    """End-to-end tests for the operator's approve/reject flow.
    Full lifecycle: below-floor counter → alert → operator approves or rejects."""

    @pytest.mark.asyncio
    async def test_full_approve_flow(self):
        """Full lifecycle: below-floor counter → alert created → operator approves → campaign updated."""
        from app.services.conversation_engine import process_conversation
        from app.routers.alerts import approve_negotiation, NegotiationApproveRequest

        deal = _make_deal(floor_price=180000.0, asking_price=200000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(stage="pitching", deal_id=deal.id, buyer_id=buyer.id)

        # ── Step 1: Buyer counters below floor → escalation created ──
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"engaging","pass":false,"unsub":false,'
            '"reply":"I can do $150k","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":150000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="I can do $150,000",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["negotiation_escalation"] is not None

        # ── Step 2: Simulate scheduler creating the ActivityLog alert ──
        esc = result["negotiation_escalation"]
        alert_id = uuid.uuid4()
        alert_entry = MagicMock(spec=ActivityLog)
        alert_entry.id = alert_id
        alert_entry.action = "negotiation_escalation"
        alert_entry.resolved = False
        alert_entry.resolved_at = None
        alert_entry.created_at = datetime.now(timezone.utc)
        alert_entry.metadata_json = {
            "buyer_id": str(buyer.id),
            "deal_id": str(deal.id),
            "campaign_id": str(campaign.id),
            "counter_price": esc["counter_price"],
            "floor_price": esc["floor_price"],
            "gap": esc["gap"],
            "buyer_email": buyer.email,
            "deal_address": deal.address,
            "buyer_name": buyer.full_name,
        }

        # ── Step 3: Operator approves at $165,000 ──
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: alert_entry,
            Campaign: campaign,
            Deal: deal,
        }.get(model))

        body = NegotiationApproveRequest(final_price=165000.0)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            approve_result = await approve_negotiation(alert_id, body, db=mock_db)

        assert approve_result["resolved"] is True
        assert approve_result["action"] == "approved"
        assert approve_result["final_price"] == 165000.0
        assert approve_result["sent_to"] == "buyer@test.com"

        # Campaign should be updated
        assert campaign.agreed_price == 165000.0
        assert campaign.conversation_stage == "collecting_info"

        # Email should have been sent with the approved price
        send_mock.assert_called_once()
        call_kwargs = send_mock.call_args.kwargs
        assert call_kwargs["to"] == "buyer@test.com"
        assert "165,000" in call_kwargs["body"]

        # Alert resolved
        assert alert_entry.resolved is True
        assert alert_entry.resolved_at is not None
        assert alert_entry.metadata_json["resolution_action"] == "approved"
        assert alert_entry.metadata_json["final_price"] == 165000.0

    @pytest.mark.asyncio
    async def test_full_reject_flow(self):
        """Full lifecycle: below-floor counter → alert → operator rejects → decline sent."""
        from app.services.conversation_engine import process_conversation
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest

        deal = _make_deal(floor_price=180000.0, asking_price=200000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(stage="pitching", deal_id=deal.id, buyer_id=buyer.id)

        # ── Step 1: Below-floor counter ──
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"engaging","pass":false,"unsub":false,'
            '"reply":"$150k is my max","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":150000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="$150k is my max",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["negotiation_escalation"] is not None

        # ── Step 2: Simulate scheduler alert creation ──
        esc = result["negotiation_escalation"]
        alert_id = uuid.uuid4()
        alert_entry = MagicMock(spec=ActivityLog)
        alert_entry.id = alert_id
        alert_entry.action = "negotiation_escalation"
        alert_entry.resolved = False
        alert_entry.resolved_at = None
        alert_entry.created_at = datetime.now(timezone.utc)
        alert_entry.metadata_json = {
            "buyer_id": str(buyer.id),
            "deal_id": str(deal.id),
            "campaign_id": str(campaign.id),
            "counter_price": esc["counter_price"],
            "floor_price": esc["floor_price"],
            "gap": esc["gap"],
            "buyer_email": buyer.email,
            "deal_address": deal.address,
            "buyer_name": buyer.full_name,
        }

        # ── Step 3: Operator rejects without counter ──
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: alert_entry,
            Deal: None,
        }.get(model))

        body = NegotiationRejectRequest(counter_offer=None)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            reject_result = await reject_negotiation(alert_id, body, db=mock_db)

        assert reject_result["resolved"] is True
        assert reject_result["action"] == "declined"
        assert reject_result["counter_sent"] is None

        # Decline email should have been sent
        send_mock.assert_called_once()
        call_kwargs = send_mock.call_args.kwargs
        assert call_kwargs["to"] == "buyer@test.com"
        body_text = call_kwargs["body"].lower()
        assert any(w in body_text for w in ["thanks", "unfortunately", "appreciate"])

        # Alert resolved
        assert alert_entry.resolved is True
        assert alert_entry.metadata_json["resolution_action"] == "declined"

    @pytest.mark.asyncio
    async def test_full_reject_with_counter_flow(self):
        """Full lifecycle: below-floor counter → alert → operator rejects with counter → counter sent."""
        from app.services.conversation_engine import process_conversation
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest

        deal = _make_deal(floor_price=180000.0, asking_price=200000.0)
        buyer = _make_buyer()
        campaign = _make_campaign(stage="pitching", deal_id=deal.id, buyer_id=buyer.id)

        # ── Step 1: Below-floor counter ──
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"stage":"engaging","pass":false,"unsub":false,'
            '"reply":"Best I can do is $150k","extracted_legal_name":null,'
            '"extracted_phone":null,"extracted_title_company":null,'
            '"extracted_agreed_price":150000}'
        )

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_response),
        ):
            result = await process_conversation(
                reply_body="Best I can do is $150k",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["negotiation_escalation"] is not None

        # ── Step 2: Simulate scheduler alert creation ──
        esc = result["negotiation_escalation"]
        alert_id = uuid.uuid4()
        alert_entry = MagicMock(spec=ActivityLog)
        alert_entry.id = alert_id
        alert_entry.action = "negotiation_escalation"
        alert_entry.resolved = False
        alert_entry.resolved_at = None
        alert_entry.created_at = datetime.now(timezone.utc)
        alert_entry.metadata_json = {
            "buyer_id": str(buyer.id),
            "deal_id": str(deal.id),
            "campaign_id": str(campaign.id),
            "counter_price": esc["counter_price"],
            "floor_price": esc["floor_price"],
            "gap": esc["gap"],
            "buyer_email": buyer.email,
            "deal_address": deal.address,
            "buyer_name": buyer.full_name,
        }

        # ── Step 3: Operator counters back at $175,000 ──
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: alert_entry,
            Deal: None,
        }.get(model))

        body = NegotiationRejectRequest(counter_offer=175000.0)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            reject_result = await reject_negotiation(alert_id, body, db=mock_db)

        assert reject_result["resolved"] is True
        assert reject_result["action"] == "countered"
        assert reject_result["counter_sent"] == 175000.0

        # Counter email should have been sent with the counter price
        send_mock.assert_called_once()
        call_kwargs = send_mock.call_args.kwargs
        assert call_kwargs["to"] == "buyer@test.com"
        body_text = call_kwargs["body"]
        check = any(s in body_text for s in ["175,000", "$175,000"])
        assert check, f"Expected counter price 175,000 in body: {body_text[:200]}"

        # Alert resolved
        assert alert_entry.resolved is True
        assert alert_entry.metadata_json["resolution_action"] == "countered"
        assert alert_entry.metadata_json["counter_sent"] == 175000.0

    @pytest.mark.asyncio
    async def test_approve_nonexistent_alert_404(self):
        """Approving a nonexistent alert should return 404."""
        from app.routers.alerts import approve_negotiation, NegotiationApproveRequest
        from fastapi import HTTPException

        alert_id = uuid.uuid4()
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        body = NegotiationApproveRequest(final_price=160000.0)

        with pytest.raises(HTTPException) as exc:
            await approve_negotiation(alert_id, body, db=mock_db)

        assert exc.value.status_code == 404
        assert "Negotiation alert not found" in exc.value.detail

    @pytest.mark.asyncio
    async def test_approve_wrong_action_type_400(self):
        """Approving a non-negotiation alert should return 400."""
        from app.routers.alerts import approve_negotiation, NegotiationApproveRequest
        from fastapi import HTTPException

        alert_id = uuid.uuid4()
        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "contract_ready"  # Wrong type

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        body = NegotiationApproveRequest(final_price=160000.0)

        with pytest.raises(HTTPException) as exc:
            await approve_negotiation(alert_id, body, db=mock_db)

        assert exc.value.status_code == 400
        assert "not a negotiation alert" in exc.value.detail

    @pytest.mark.asyncio
    async def test_reject_nonexistent_alert_404(self):
        """Rejecting a nonexistent alert should return 404."""
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest
        from fastapi import HTTPException

        alert_id = uuid.uuid4()
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        body = NegotiationRejectRequest(counter_offer=None)

        with pytest.raises(HTTPException) as exc:
            await reject_negotiation(alert_id, body, db=mock_db)

        assert exc.value.status_code == 404
        assert "Negotiation alert not found" in exc.value.detail

    @pytest.mark.asyncio
    async def test_reject_wrong_action_type_400(self):
        """Rejecting a non-negotiation alert should return 400."""
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest
        from fastapi import HTTPException

        alert_id = uuid.uuid4()
        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "contract_ready"  # Wrong type

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        body = NegotiationRejectRequest(counter_offer=None)

        with pytest.raises(HTTPException) as exc:
            await reject_negotiation(alert_id, body, db=mock_db)

        assert exc.value.status_code == 400
        assert "not a negotiation alert" in exc.value.detail

    @pytest.mark.asyncio
    async def test_approve_alert_missing_campaign_id_400(self):
        """Approving an alert without campaign_id should return 400."""
        from app.routers.alerts import approve_negotiation, NegotiationApproveRequest
        from fastapi import HTTPException

        alert_id = uuid.uuid4()
        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "negotiation_escalation"
        mock_entry.metadata_json = {
            "buyer_email": "buyer@test.com",
            # No campaign_id
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        body = NegotiationApproveRequest(final_price=160000.0)

        with pytest.raises(HTTPException) as exc:
            await approve_negotiation(alert_id, body, db=mock_db)

        assert exc.value.status_code == 400
        assert "missing campaign_id" in exc.value.detail.lower()


# ===========================================================================
# Edge case tests for operator approve/reject flow
# ===========================================================================


class TestOperatorNegotiationEdgeCases:
    """Parameterized edge case tests for extreme prices, missing fields,
    and boundary conditions in the operator approve/reject flow."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("final_price,expected_in_body", [
        (10_000_000.0, "$10,000,000"),
        (0.01, "$0"),  # :,.0f rounds to zero decimals
        (0.0, "$0"),
        (-1000.0, "$-1,000"),  # Format: $ precedes the sign
        (172000.501, "$172,001"),  # :,.0f rounds up
    ])
    async def test_approve_extreme_prices(self, final_price, expected_in_body):
        """Approving with extreme prices should work without errors."""
        from app.routers.alerts import approve_negotiation, NegotiationApproveRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        campaign = _make_campaign(campaign_id, deal_id, buyer_id)
        deal = _make_deal(deal_id)
        entry = _make_negotiation_alert(alert_id, campaign_id, deal_id, buyer_id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Campaign: campaign,
            Deal: deal,
        }.get(model))

        body = NegotiationApproveRequest(final_price=final_price)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            result = await approve_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        assert result["action"] == "approved"
        assert result["final_price"] == final_price

        # Email should be sent with the price formatted (:,.0f = zero decimals)
        send_mock.assert_called_once()
        body_text = send_mock.call_args.kwargs.get("body", "")
        assert expected_in_body in body_text, (
            f"Expected '{expected_in_body}' in email body, got: {body_text[:200]}"
        )

        # Campaign should be updated
        assert campaign.agreed_price == final_price

    @pytest.mark.asyncio
    @pytest.mark.parametrize("counter_offer,expected_action,expected_in_body", [
        (10_000_000.0, "countered", "$10,000,000"),
        (0.01, "countered", "$0"),  # :,.0f rounds to zero decimals
        (0.0, "declined", None),      # 0.0 is falsy in Python → treated as no counter
        (-1000.0, "countered", "$-1,000"),
        (None, "declined", None),
    ])
    async def test_reject_extreme_counter_offers(self, counter_offer, expected_action, expected_in_body):
        """Rejecting with extreme counter offers should work without errors."""
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        deal = _make_deal(deal_id)
        entry = _make_negotiation_alert(alert_id, campaign_id, deal_id, buyer_id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Deal: deal,
        }.get(model))

        body = NegotiationRejectRequest(counter_offer=counter_offer)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            result = await reject_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        assert result["action"] == expected_action
        assert result["counter_sent"] == counter_offer

        if expected_in_body:
            send_mock.assert_called_once()
            body_text = send_mock.call_args.kwargs.get("body", "")
            assert expected_in_body in body_text, (
                f"Expected '{expected_in_body}' in email body, got: {body_text[:200]}"
            )
        else:
            # Counter is falsy (None or 0.0) → decline email still sent
            send_mock.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("metadata_overrides,expect_email", [
        ({"buyer_email": ""}, False),
        ({"buyer_email": "buyer@test.com", "deal_address": ""}, True),
        ({"buyer_email": "nonexistent@example.com"}, True),
    ])
    async def test_approve_various_metadata_fields(self, metadata_overrides, expect_email):
        """Approve should handle various metadata configurations gracefully."""
        from app.routers.alerts import approve_negotiation, NegotiationApproveRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        campaign = _make_campaign(campaign_id, deal_id, buyer_id)
        deal = _make_deal(deal_id)

        entry = MagicMock(spec=ActivityLog)
        entry.id = alert_id
        entry.action = "negotiation_escalation"
        entry.resolved = False
        entry.resolved_at = None
        entry.created_at = datetime.now(timezone.utc)
        entry.metadata_json = {
            "buyer_id": str(buyer_id),
            "deal_id": str(deal_id),
            "campaign_id": str(campaign_id),
            "counter_price": 150000.0,
            "floor_price": 180000.0,
            "gap": 30000.0,
            "buyer_email": "buyer@test.com",
            "deal_address": "123 Main St",
            "buyer_name": "Test Buyer",
            **metadata_overrides,
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Campaign: campaign,
            Deal: deal,
        }.get(model))

        body = NegotiationApproveRequest(final_price=165000.0)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            result = await approve_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True

        if expect_email:
            send_mock.assert_called_once()
        else:
            # Empty buyer_email should skip sending
            send_mock.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("metadata_overrides,expect_email", [
        ({"buyer_email": ""}, False),
        ({"buyer_email": "reject@test.com"}, True),
    ])
    async def test_reject_various_metadata_fields(self, metadata_overrides, expect_email):
        """Reject should handle various metadata configurations gracefully."""
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest

        campaign_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        alert_id = uuid.uuid4()

        deal = _make_deal(deal_id)

        entry = MagicMock(spec=ActivityLog)
        entry.id = alert_id
        entry.action = "negotiation_escalation"
        entry.resolved = False
        entry.resolved_at = None
        entry.created_at = datetime.now(timezone.utc)
        entry.metadata_json = {
            "buyer_id": str(buyer_id),
            "deal_id": str(deal_id),
            "campaign_id": str(campaign_id),
            "counter_price": 150000.0,
            "floor_price": 180000.0,
            "gap": 30000.0,
            "buyer_email": "buyer@test.com",
            "deal_address": "123 Main St",
            "buyer_name": "Test Buyer",
            **metadata_overrides,
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, pk: {
            ActivityLog: entry,
            Deal: deal,
        }.get(model))

        body = NegotiationRejectRequest(counter_offer=None)

        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            result = await reject_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True

        if expect_email:
            send_mock.assert_called_once()
        else:
            send_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_approve_metadata_is_none(self):
        """Approve with metadata_json=None should not crash."""
        from app.routers.alerts import approve_negotiation, NegotiationApproveRequest
        from fastapi import HTTPException

        alert_id = uuid.uuid4()

        entry = MagicMock(spec=ActivityLog)
        entry.id = alert_id
        entry.action = "negotiation_escalation"
        entry.resolved = False
        entry.resolved_at = None
        entry.metadata_json = None

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=entry)

        body = NegotiationApproveRequest(final_price=165000.0)

        # Should raise 400 because meta.get("campaign_id") will be None
        with pytest.raises(HTTPException) as exc:
            await approve_negotiation(alert_id, body, db=mock_db)

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_reject_metadata_is_none(self):
        """Reject with metadata_json=None should not crash."""
        from app.routers.alerts import reject_negotiation, NegotiationRejectRequest

        alert_id = uuid.uuid4()

        entry = MagicMock(spec=ActivityLog)
        entry.id = alert_id
        entry.action = "negotiation_escalation"
        entry.resolved = False
        entry.resolved_at = None
        entry.metadata_json = None

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=entry)

        body = NegotiationRejectRequest(counter_offer=None)

        # Should work without crashing (meta.get("buyer_email") returns None,
        # but reject handles missing email gracefully)
        with patch("app.routers.alerts.send_email", AsyncMock()) as send_mock:
            result = await reject_negotiation(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        send_mock.assert_not_called()  # No email since buyer_email is missing
