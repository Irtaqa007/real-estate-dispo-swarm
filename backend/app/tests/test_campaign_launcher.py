"""Tests for the campaign_launcher service.

Covers:
- launch_campaign_for_buyer: idempotency, eligibility, fatigue, full flow,
  tier-based scheduling, touch 1 auto-send for A-List, failure handling
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import Campaign, Deal, Buyer
from app.services import campaign_launcher as cl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def buyer_id():
    return uuid.uuid4()


@pytest.fixture
def deal_id():
    return uuid.uuid4()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.add_all = MagicMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_buyer(buyer_id):
    buyer = MagicMock(spec=Buyer)
    buyer.id = buyer_id
    buyer.full_name = "Jane Investor"
    buyer.email = "jane@example.com"
    buyer.buy_box = "Houses in Dallas under $300k, 3+ beds"
    buyer.buyer_tier = "B-List"
    buyer.engagement_score = 45.0
    buyer.pitches_this_week = 1
    buyer.last_pitch_sent_at = None
    return buyer


@pytest.fixture
def mock_deal(deal_id):
    deal = MagicMock(spec=Deal)
    deal.id = deal_id
    deal.address = "456 Oak Ave"
    deal.city = "Dallas"
    deal.state = "TX"
    deal.property_type = "House"
    deal.arv = 350000.0
    deal.asking_price = 250000.0
    deal.spread = 100000.0
    deal.condition_description = "Needs cosmetic rehab, no major structural issues"
    deal.beds = 3
    deal.baths = 2.0
    deal.sqft = 1800
    deal.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    return deal


# ===========================================================================
# Tests
# ===========================================================================


class TestLaunchCampaignForBuyer:

    @pytest.mark.asyncio
    async def test_skips_when_campaigns_already_exist(self, mock_db, mock_buyer, mock_deal):
        """Should skip launch if campaigns already exist for this buyer+deal."""
        existing_campaign = MagicMock(spec=Campaign)
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=existing_campaign)
        ))

        result = await cl.launch_campaign_for_buyer(mock_db, mock_buyer, mock_deal)

        assert result["success"] is False
        assert result["reason"] == "campaigns_already_exist"
        assert result["touches_created"] == 0
        mock_db.add_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_buyer_ineligible(self, mock_db, mock_buyer, mock_deal):
        """Should skip launch if buyer fails eligibility check."""
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(False, "Already pitched 3 days ago"))):
            result = await cl.launch_campaign_for_buyer(mock_db, mock_buyer, mock_deal)

        assert result["success"] is False
        assert "ineligible" in result["reason"]
        mock_db.add_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_buyer_fatigued(self, mock_db, mock_buyer, mock_deal):
        """Should skip launch if buyer is fatigued (too many pitches this week)."""
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(True, None))):
            with patch.object(cl, "check_fatigue_protection",
                              AsyncMock(return_value=(False, "Fatigue: 3/3 pitches this week"))):
                result = await cl.launch_campaign_for_buyer(mock_db, mock_buyer, mock_deal)

        assert result["success"] is False
        assert "fatigued" in result["reason"]
        mock_db.add_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_flow_creates_6_touches(self, mock_db, mock_buyer, mock_deal):
        """Full flow: should create 6 Campaign records with correct fields."""
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        fake_email = {"subject": "Test Subject", "body": "Test body"}

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(True, None))):
            with patch.object(cl, "check_fatigue_protection",
                              AsyncMock(return_value=(True, None))):
                with patch.object(cl, "generate_touch_email",
                                  AsyncMock(return_value=fake_email)):
                    result = await cl.launch_campaign_for_buyer(
                        mock_db, mock_buyer, mock_deal, similarity_score=0.85
                    )

        assert result["success"] is True
        assert result["touches_created"] == 6
        assert len(result["campaign_ids"]) == 6

        # Verify add_all was called with 6 Campaign records
        mock_db.add_all.assert_called_once()
        campaigns = mock_db.add_all.call_args[0][0]
        assert len(campaigns) == 6

        # Verify touch numbers
        touch_numbers = [c.touch_number for c in campaigns]
        assert touch_numbers == [1, 2, 3, 4, 5, 6]

        # Verify all are for correct buyer+deal
        for c in campaigns:
            assert c.buyer_id == mock_buyer.id
            assert c.deal_id == mock_deal.id

    @pytest.mark.asyncio
    async def test_blist_scheduling(self, mock_db, mock_buyer, mock_deal):
        """B-List buyer: touch 1 queued for day 1, touch 2 for day 3, etc."""
        mock_buyer.buyer_tier = "B-List"
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        fake_email = {"subject": "Test", "body": "Body"}

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(True, None))):
            with patch.object(cl, "check_fatigue_protection",
                              AsyncMock(return_value=(True, None))):
                with patch.object(cl, "generate_touch_email",
                                  AsyncMock(return_value=fake_email)):
                    result = await cl.launch_campaign_for_buyer(
                        mock_db, mock_buyer, mock_deal
                    )

        campaigns = mock_db.add_all.call_args[0][0]

        # B-List: touch 1 at day 1, touch 2 at day 1+2=3, touch 3 at day 1+4=5, etc.
        assert campaigns[0].status == "Queued"  # Touch 1 not sent immediately
        assert campaigns[0].touch_number == 1

        # Touches 2-6 should all be Queued
        for c in campaigns[1:]:
            assert c.status == "Queued"

    @pytest.mark.asyncio
    async def test_clist_scheduling(self, mock_db, mock_buyer, mock_deal):
        """C-List buyer: touch 1 queued for day 3, touch 2 for day 5, etc."""
        mock_buyer.buyer_tier = "C-List"
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        fake_email = {"subject": "Test", "body": "Body"}

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(True, None))):
            with patch.object(cl, "check_fatigue_protection",
                              AsyncMock(return_value=(True, None))):
                with patch.object(cl, "generate_touch_email",
                                  AsyncMock(return_value=fake_email)):
                    result = await cl.launch_campaign_for_buyer(
                        mock_db, mock_buyer, mock_deal
                    )

        campaigns = mock_db.add_all.call_args[0][0]
        # C-List: all touches Queued, touch 1 at day 3
        assert campaigns[0].status == "Queued"

    @pytest.mark.asyncio
    async def test_alist_touch1_sent_immediately(self, mock_db, mock_buyer, mock_deal):
        """A-List buyer: touch 1 should be Sent immediately via send_email."""
        mock_buyer.buyer_tier = "A-List"
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        fake_email = {"subject": "Hot Deal", "body": "Check this out"}

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(True, None))):
            with patch.object(cl, "check_fatigue_protection",
                              AsyncMock(return_value=(True, None))):
                with patch.object(cl, "generate_touch_email",
                                  AsyncMock(return_value=fake_email)):
                    with patch.object(cl, "send_email",
                                      AsyncMock(return_value={"message_id": "msg123"})):
                        with patch.object(cl, "increment_pitch_count",
                                          AsyncMock()):
                            result = await cl.launch_campaign_for_buyer(
                                mock_db, mock_buyer, mock_deal
                            )

        campaigns = mock_db.add_all.call_args[0][0]

        # Touch 1 should be Sent (A-List)
        assert campaigns[0].status == "Sent"
        assert campaigns[0].sent_at is not None
        assert campaigns[0].touch_number == 1

        # Touches 2-6 should be Queued
        for c in campaigns[1:]:
            assert c.status == "Queued"

    @pytest.mark.asyncio
    async def test_send_failure_falls_back_to_queued(self, mock_db, mock_buyer, mock_deal):
        """If send_email fails for A-List touch 1, status falls back to Queued."""
        mock_buyer.buyer_tier = "A-List"
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        fake_email = {"subject": "Test", "body": "Body"}

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(True, None))):
            with patch.object(cl, "check_fatigue_protection",
                              AsyncMock(return_value=(True, None))):
                with patch.object(cl, "generate_touch_email",
                                  AsyncMock(return_value=fake_email)):
                    with patch.object(cl, "send_email",
                                      AsyncMock(side_effect=RuntimeError("SMTP error"))):
                        result = await cl.launch_campaign_for_buyer(
                            mock_db, mock_buyer, mock_deal
                        )

        campaigns = mock_db.add_all.call_args[0][0]
        # Touch 1 should fall back to Queued after send failure
        assert campaigns[0].status == "Queued"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_does_not_commit(self, mock_db, mock_buyer, mock_deal):
        """Function should NOT commit — caller is responsible."""
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        fake_email = {"subject": "Test", "body": "Body"}

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(True, None))):
            with patch.object(cl, "check_fatigue_protection",
                              AsyncMock(return_value=(True, None))):
                with patch.object(cl, "generate_touch_email",
                                  AsyncMock(return_value=fake_email)):
                    await cl.launch_campaign_for_buyer(mock_db, mock_buyer, mock_deal)

        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_defaults_tier_to_clist(self, mock_db, mock_buyer, mock_deal):
        """Buyer with no buyer_tier should default to C-List scheduling."""
        mock_buyer.buyer_tier = None
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        fake_email = {"subject": "Test", "body": "Body"}

        with patch.object(cl, "assess_buyer_eligibility",
                          AsyncMock(return_value=(True, None))):
            with patch.object(cl, "check_fatigue_protection",
                              AsyncMock(return_value=(True, None))):
                with patch.object(cl, "generate_touch_email",
                                  AsyncMock(return_value=fake_email)):
                    result = await cl.launch_campaign_for_buyer(
                        mock_db, mock_buyer, mock_deal
                    )

        campaigns = mock_db.add_all.call_args[0][0]
        # None tier → C-List: touch 1 should be Queued, not Sent
        assert campaigns[0].status == "Queued"
        assert result["success"] is True
