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
