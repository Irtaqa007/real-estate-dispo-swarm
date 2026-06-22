"""Tests for the 50-verified-buyer minimum gate in launch_campaign.

Covers:
- Launch blocked when verified matched buyers < threshold
- Launch proceeds when verified matched buyers >= threshold
- ActivityLog entry created when blocked
- Configurable threshold via settings
- Edge cases: 0 matched buyers, exactly threshold, one below
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import Buyer, Campaign, Deal, JVPartner
from app.routers import campaigns as campaigns_router
from app.schemas import BuyerMatchResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def deal_id():
    return uuid.uuid4()


@pytest.fixture
def mock_deal(deal_id):
    deal = MagicMock(spec=Deal)
    deal.id = deal_id
    deal.address = "123 Test St"
    deal.city = "Dallas"
    deal.state = "TX"
    deal.property_type = "House"
    deal.arv = 350000.0
    deal.asking_price = 250000.0
    deal.spread = 100000.0
    deal.contract_price = 150000.0
    deal.floor_price = 180000.0
    deal.status = "Available"
    deal.deal_embedding = [0.1] * 1024
    deal.jv_partner_id = None
    deal.notes = None
    deal.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    return deal


def _make_buyer_match(index: int) -> BuyerMatchResult:
    """Create a BuyerMatchResult with sequential unique IDs."""
    return BuyerMatchResult(
        id=uuid.uuid4(),
        full_name=f"Buyer {index}",
        email=f"buyer{index}@test.com",
        buy_box="Houses in Dallas under $300k",
        affiliation="Test Co",
        buyer_tier="B-List",
        similarity=0.85 - (index * 0.01),
    )


def _make_buyer_object(match: BuyerMatchResult) -> MagicMock:
    """Create a mock Buyer object from a BuyerMatchResult."""
    buyer = MagicMock(spec=Buyer)
    buyer.id = match.id
    buyer.full_name = match.full_name
    buyer.email = match.email
    buyer.buy_box = match.buy_box
    buyer.buyer_tier = match.buyer_tier
    buyer.engagement_score = 50.0
    buyer.pitches_this_week = 0
    buyer.last_pitch_sent_at = None
    buyer.status = "Active"
    buyer.email_verified = True
    return buyer


class FakeScalarResult:
    """Mimics a DB result with scalar_one_or_none()."""
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else ([self._value] if self._value else [])


class FakeRowsResult:
    """Mimics a DB result with scalars().all()."""
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows


# ===========================================================================
# 50-Buyer Minimum Gate Tests
# ===========================================================================

class TestFiftyBuyerGate:

    @pytest.mark.asyncio
    @patch.object(campaigns_router, "find_top_matches_for_deal")
    @patch.object(campaigns_router, "assess_buyer_eligibility")
    @patch.object(campaigns_router, "check_fatigue_protection")
    @patch.object(campaigns_router, "settings")
    @patch.object(campaigns_router, "audit")
    async def test_blocked_below_threshold(
        self,
        mock_audit,
        mock_settings,
        mock_check_fatigue,
        mock_assess_eligibility,
        mock_find_matches,
        mock_deal,
        deal_id,
    ):
        """Should block launch when verified matched buyers < 50."""
        # Create 40 matched buyers (below threshold of 50)
        matches = [_make_buyer_match(i) for i in range(40)]
        buyers = [_make_buyer_object(m) for m in matches]

        # Configure mocks
        mock_settings.min_verified_buyers_to_launch = 50

        # Mock find_top_matches_for_deal
        match_result = MagicMock()
        match_result.matches = matches
        mock_find_matches.return_value = match_result

        # All buyers pass eligibility and fatigue
        mock_assess_eligibility.return_value = (True, None)
        mock_check_fatigue.return_value = (True, None)

        # Mock the DB session
        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        # Mock deal query (first call), idempotency check (second call),
        # and buyer records query (third call)
        deal_result = FakeScalarResult(mock_deal)
        no_campaigns = FakeScalarResult(None)
        buyer_records = FakeRowsResult(buyers)

        db.execute = AsyncMock(side_effect=[
            deal_result,        # 1: fetch deal
            no_campaigns,       # 2: idempotency check
            buyer_records,      # 3: fetch buyer records
        ])

        # Mock audit.log
        mock_audit.log = AsyncMock()

        # Call the endpoint
        response = await campaigns_router.launch_campaign(
            deal_id=deal_id,
            match_limit=20,
            db=db,
        )

        # Verify the response
        assert response.status_code == 200
        content = json.loads(response.body)
        assert content["launched"] is False
        assert content["reason"] == "insufficient_verified_buyers"
        assert content["verified_matched"] == 40
        assert content["required"] == 50
        assert "40 verified buyers" in content["message"]

        # Verify ActivityLog was created
        mock_audit.log.assert_awaited_once()
        log_call = mock_audit.log.call_args[1]
        assert log_call["entity_type"] == "deal"
        assert log_call["entity_id"] == deal_id
        assert log_call["action"] == "campaign_launch_blocked"
        assert log_call["metadata"]["reason"] == "insufficient_verified_buyers"
        assert log_call["metadata"]["verified_matched"] == 40
        assert log_call["metadata"]["required"] == 50
        assert log_call["metadata"]["alert_user"] is True

        # Verify db.commit was called (to persist the activity log)
        db.commit.assert_awaited()

        # Verify no campaign launches happened
        mock_find_matches.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(campaigns_router, "find_top_matches_for_deal")
    @patch.object(campaigns_router, "assess_buyer_eligibility")
    @patch.object(campaigns_router, "check_fatigue_protection")
    @patch.object(campaigns_router, "settings")
    @patch.object(campaigns_router, "launch_campaign_for_buyer")
    async def test_proceeds_when_at_threshold(
        self,
        mock_launch,
        mock_settings,
        mock_check_fatigue,
        mock_assess_eligibility,
        mock_find_matches,
        mock_deal,
        deal_id,
    ):
        """Should proceed with launch when verified matched buyers >= 50."""
        # Create 50 matched buyers (exactly at threshold)
        mock_settings.min_verified_buyers_to_launch = 50
        matches = [_make_buyer_match(i) for i in range(50)]
        buyers = [_make_buyer_object(m) for m in matches]

        match_result = MagicMock()
        match_result.matches = matches
        mock_find_matches.return_value = match_result

        mock_assess_eligibility.return_value = (True, None)
        mock_check_fatigue.return_value = (True, None)
        mock_launch.return_value = {
            "success": True,
            "touches_created": 6,
            "reason": "ok",
            "campaign_ids": [str(uuid.uuid4()) for _ in range(6)],
            "touches": [
                {"touch": t, "subject": f"Subject {t}", "body": f"Body {t}",
                 "status": "Queued", "scheduled_send_at": None}
                for t in range(1, 7)
            ],
        }

        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        deal_result = FakeScalarResult(mock_deal)
        no_campaigns = FakeScalarResult(None)
        buyer_records = FakeRowsResult(buyers)

        db.execute = AsyncMock(side_effect=[
            deal_result,        # 1: fetch deal
            no_campaigns,       # 2: idempotency check
            buyer_records,      # 3: fetch buyer records
        ])

        response = await campaigns_router.launch_campaign(
            deal_id=deal_id,
            match_limit=50,
            db=db,
        )

        # Verify it's a normal CampaignLaunchResponse (not a blocked JSONResponse)
        assert response.deal_id == deal_id
        assert response.total_buyers == 50
        assert response.total_campaigns_created == 300  # 50 buyers * 6 touches

        # Verify deal status was updated
        assert mock_deal.status == "Campaign Launched"
        db.add.assert_any_call(mock_deal)

        # Verify campaigns were launched for all buyers
        assert mock_launch.await_count == 50

    @pytest.mark.asyncio
    @patch.object(campaigns_router, "find_top_matches_for_deal")
    @patch.object(campaigns_router, "assess_buyer_eligibility")
    @patch.object(campaigns_router, "check_fatigue_protection")
    @patch.object(campaigns_router, "settings")
    @patch.object(campaigns_router, "audit")
    async def test_blocked_with_zero_matches(
        self,
        mock_audit,
        mock_settings,
        mock_check_fatigue,
        mock_assess_eligibility,
        mock_find_matches,
        mock_deal,
        deal_id,
    ):
        """Should block launch with 0 matched buyers and proper message."""
        # No buyers at all - the endpoint should raise an HTTPException
        # before reaching the gate check, since there are no matched buyers
        match_result = MagicMock()
        match_result.matches = []
        mock_find_matches.return_value = match_result

        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        deal_result = FakeScalarResult(mock_deal)
        no_campaigns = FakeScalarResult(None)

        db.execute = AsyncMock(side_effect=[
            deal_result,        # 1: fetch deal
            no_campaigns,       # 2: idempotency check
        ])

        # Should raise 404 before reaching the gate
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await campaigns_router.launch_campaign(
                deal_id=deal_id,
                match_limit=20,
                db=db,
            )

        assert exc_info.value.status_code == 404
        assert "No matched buyers found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    @patch.object(campaigns_router, "find_top_matches_for_deal")
    @patch.object(campaigns_router, "assess_buyer_eligibility")
    @patch.object(campaigns_router, "check_fatigue_protection")
    @patch.object(campaigns_router, "settings")
    @patch.object(campaigns_router, "audit")
    async def test_blocks_when_all_filtered_out_by_eligibility(
        self,
        mock_audit,
        mock_settings,
        mock_check_fatigue,
        mock_assess_eligibility,
        mock_find_matches,
        mock_deal,
        deal_id,
    ):
        """Should block when eligibility filtering drops below threshold."""
        # 60 matched but only 30 pass eligibility
        all_matches = [_make_buyer_match(i) for i in range(60)]
        all_buyers = [_make_buyer_object(m) for m in all_matches]

        match_result = MagicMock()
        match_result.matches = all_matches
        mock_find_matches.return_value = match_result

        mock_settings.min_verified_buyers_to_launch = 50

        # Only 30 pass eligibility, rest fail
        elig_results = [(True, None) if i < 30 else (False, "Low engagement") 
                       for i in range(60)]
        
        eligible_buyers = [all_buyers[i] for i in range(30)]
        eligible_matches = [all_matches[i] for i in range(30)]

        # Mock assess_buyer_eligibility to return varying results
        # We need a callable side_effect that inspects the buyer safely
        async def eligibility_side_effect(buyer_row, *args, **kwargs):
            idx = next((i for i, b in enumerate(all_buyers) if b.id == buyer_row.id), -1)
            if 0 <= idx < 30:
                return (True, None)
            return (False, "Low engagement")

        mock_assess_eligibility.side_effect = eligibility_side_effect
        mock_check_fatigue.return_value = (True, None)
        mock_audit.log = AsyncMock()

        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        deal_result = FakeScalarResult(mock_deal)
        no_campaigns = FakeScalarResult(None)
        buyer_records = FakeRowsResult(all_buyers)

        db.execute = AsyncMock(side_effect=[
            deal_result,        # 1: fetch deal
            no_campaigns,       # 2: idempotency check
            buyer_records,      # 3: fetch buyer records
        ])

        response = await campaigns_router.launch_campaign(
            deal_id=deal_id,
            match_limit=60,
            db=db,
        )

        # Should be blocked since only 30 pass eligibility (< 50)
        assert response.status_code == 200
        content = json.loads(response.body)
        assert content["launched"] is False
        assert content["reason"] == "insufficient_verified_buyers"
        assert content["verified_matched"] == 30
        assert content["required"] == 50

    @pytest.mark.asyncio
    @patch.object(campaigns_router, "find_top_matches_for_deal")
    @patch.object(campaigns_router, "assess_buyer_eligibility")
    @patch.object(campaigns_router, "check_fatigue_protection")
    @patch.object(campaigns_router, "settings")
    @patch.object(campaigns_router, "audit")
    async def test_configurable_threshold(
        self,
        mock_audit,
        mock_settings,
        mock_check_fatigue,
        mock_assess_eligibility,
        mock_find_matches,
        mock_deal,
        deal_id,
    ):
        """Should use the configurable threshold from settings."""
        # Only 5 matched buyers, threshold set to 10
        matches = [_make_buyer_match(i) for i in range(5)]
        buyers = [_make_buyer_object(m) for m in matches]

        match_result = MagicMock()
        match_result.matches = matches
        mock_find_matches.return_value = match_result

        mock_settings.min_verified_buyers_to_launch = 10  # Custom low threshold

        mock_assess_eligibility.return_value = (True, None)
        mock_check_fatigue.return_value = (True, None)
        mock_audit.log = AsyncMock()

        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        deal_result = FakeScalarResult(mock_deal)
        no_campaigns = FakeScalarResult(None)
        buyer_records = FakeRowsResult(buyers)

        db.execute = AsyncMock(side_effect=[
            deal_result,        # 1: fetch deal
            no_campaigns,       # 2: idempotency check
            buyer_records,      # 3: fetch buyer records
        ])

        response = await campaigns_router.launch_campaign(
            deal_id=deal_id,
            match_limit=10,
            db=db,
        )

        assert response.status_code == 200
        content = json.loads(response.body)
        assert content["launched"] is False
        assert content["verified_matched"] == 5
        assert content["required"] == 10

    @pytest.mark.asyncio
    @patch.object(campaigns_router, "find_top_matches_for_deal")
    @patch.object(campaigns_router, "assess_buyer_eligibility")
    @patch.object(campaigns_router, "check_fatigue_protection")
    @patch.object(campaigns_router, "settings")
    @patch.object(campaigns_router, "audit")
    async def test_one_below_threshold(
        self,
        mock_audit,
        mock_settings,
        mock_check_fatigue,
        mock_assess_eligibility,
        mock_find_matches,
        mock_deal,
        deal_id,
    ):
        """Should block even when just 1 below the threshold (49 < 50)."""
        matches = [_make_buyer_match(i) for i in range(49)]
        buyers = [_make_buyer_object(m) for m in matches]

        match_result = MagicMock()
        match_result.matches = matches
        mock_find_matches.return_value = match_result

        mock_settings.min_verified_buyers_to_launch = 50

        mock_assess_eligibility.return_value = (True, None)
        mock_check_fatigue.return_value = (True, None)
        mock_audit.log = AsyncMock()

        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        deal_result = FakeScalarResult(mock_deal)
        no_campaigns = FakeScalarResult(None)
        buyer_records = FakeRowsResult(buyers)

        db.execute = AsyncMock(side_effect=[
            deal_result,        # 1: fetch deal
            no_campaigns,       # 2: idempotency check
            buyer_records,      # 3: fetch buyer records
        ])

        response = await campaigns_router.launch_campaign(
            deal_id=deal_id,
            match_limit=49,
            db=db,
        )

        # Should be blocked (49 < 50)
        assert response.status_code == 200
        content = json.loads(response.body)
        assert content["launched"] is False
        assert content["verified_matched"] == 49
        assert content["required"] == 50
