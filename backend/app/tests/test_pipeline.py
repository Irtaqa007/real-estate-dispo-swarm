"""Tests for the deal pipeline endpoint.

Covers:
- Empty DB returns empty list
- Deals grouped by status → correct pipeline stage
- Campaign counts (sent, replied, passed) per deal
- Ordering by last activity
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.models import Campaign, Deal
from app.routers import deals as deals_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_deal(
    id: uuid.UUID,
    status: str,
    address: str = "123 Test St",
    created_at: datetime = None,
) -> MagicMock:
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    deal = MagicMock(spec=Deal)
    deal.id = id
    deal.status = status
    deal.address = address
    deal.city = "Austin"
    deal.state = "TX"
    deal.property_type = "House"
    deal.asking_price = 250000.0
    deal.arv = 300000.0
    deal.created_at = created_at
    return deal


# ===========================================================================
# Tests
# ===========================================================================


class TestDealPipeline:

    @pytest.mark.asyncio
    async def test_pipeline_empty(self):
        """Returns empty list when no deals exist."""
        db = AsyncMock()
        # No deals and no campaigns
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        empty_result.all.return_value = []
        db.execute = AsyncMock(return_value=empty_result)

        result = await deals_router.deal_pipeline(db=db)
        assert result == []

    @pytest.mark.asyncio
    async def test_pipeline_groups_by_status(self):
        """Deals with different statuses appear in correct stage."""
        available_id = uuid.uuid4()
        launched_id = uuid.uuid4()

        deals = [
            _make_deal(available_id, "Available", "123 Available St"),
            _make_deal(launched_id, "Campaign Launched", "456 Launched St"),
        ]

        # Mock deal query
        deal_result = MagicMock()
        deal_result.scalars.return_value.all.return_value = deals
        deal_result.all.return_value = []

        # Mock campaign agg query (no campaigns)
        empty_agg = MagicMock()
        empty_agg.all.return_value = []

        # Mock stage query (no conversation stages)
        empty_stages = MagicMock()
        empty_stages.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[deal_result, empty_agg, empty_stages])

        result = await deals_router.deal_pipeline(db=db)

        assert len(result) == 2

        by_address = {r["address"]: r for r in result}
        assert by_address["123 Available St"]["stage"] == "Available"
        assert by_address["456 Launched St"]["stage"] == "Launched"

    @pytest.mark.asyncio
    async def test_pipeline_campaign_counts(self):
        """Campaign counts (sent, replied, passed) are aggregated correctly."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Campaign Launched", "123 Test St")

        deal_result = MagicMock()
        deal_result.scalars.return_value.all.return_value = [deal]
        deal_result.all.return_value = []

        # Mock campaign aggregation: 1 total, 1 sent, 1 replied, 1 passed, 0 contract
        class FakeRow:
            def __init__(self, d_id):
                self.deal_id = d_id
                self.total = 4
                self.sent = 1
                self.replied = 1
                self.passed = 1
                self.contract = 0
                self.last_sent_at = datetime.now(timezone.utc) - timedelta(hours=2)
                self.last_reply_at = None

        agg_result = MagicMock()
        agg_result.all.return_value = [FakeRow(deal_id)]

        # Mock conversation stages (empty - no stages to keep it simple)
        stages_result = MagicMock()
        stages_result.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[deal_result, agg_result, stages_result])

        result = await deals_router.deal_pipeline(db=db)

        assert len(result) == 1
        assert result[0]["campaigns_total"] == 4
        assert result[0]["campaigns_sent"] == 1
        assert result[0]["campaigns_replied"] == 1
        assert result[0]["campaigns_passed"] == 1
        assert result[0]["campaigns_contract"] == 0

    @pytest.mark.asyncio
    async def test_pipeline_orders_by_last_activity(self):
        """Most recently active deal appears first."""
        now = datetime.now(timezone.utc)
        old_id = uuid.uuid4()
        recent_id = uuid.uuid4()

        old_deal = _make_deal(old_id, "Available", "Old Deal", created_at=now - timedelta(days=10))
        recent_deal = _make_deal(recent_id, "Available", "Recent Deal", created_at=now - timedelta(days=1))

        deal_result = MagicMock()
        deal_result.scalars.return_value.all.return_value = [recent_deal, old_deal]
        deal_result.all.return_value = []

        empty = MagicMock()
        empty.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[deal_result, empty, empty])

        result = await deals_router.deal_pipeline(db=db)

        assert len(result) == 2
        # Most recently created deal should appear first
        assert result[0]["address"] == "Recent Deal"
        assert result[1]["address"] == "Old Deal"
