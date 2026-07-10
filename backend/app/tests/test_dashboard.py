"""Tests for the dashboard stats endpoint.

Covers:
- Empty DB returns all zeros
- Deals grouped by status
- Today's email and reply counts
- Conversion rate calculation
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.models import Campaign, Deal
from app.routers import dashboard as dashboard_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_deal(id: uuid.UUID, status: str) -> MagicMock:
    deal = MagicMock(spec=Deal)
    deal.id = id
    deal.status = status
    deal.address = "123 Test St"
    deal.created_at = datetime.now(timezone.utc)
    return deal


def _make_campaign(
    deal_id: uuid.UUID,
    sent_at=None,
    reply_received_at=None,
    status: str = "Queued",
    conversation_stage: str = "pitching",
) -> MagicMock:
    c = MagicMock(spec=Campaign)
    c.id = uuid.uuid4()
    c.deal_id = deal_id
    c.buyer_id = uuid.uuid4()
    c.status = status
    c.conversation_stage = conversation_stage
    c.sent_at = sent_at
    c.reply_received_at = reply_received_at
    return c


# ===========================================================================
# Tests
# ===========================================================================


class TestDashboardStats:

    @pytest.mark.asyncio
    async def test_dashboard_stats_empty_db(self):
        """All counts = 0 when no data exists."""
        db = AsyncMock()
        # Mock all queries to return empty results
        db.execute = AsyncMock()

        # _count_deals_by_status returns empty dict
        # _count_today_campaigns returns 0
        # etc.

        # We need to mock each helper call differently since the endpoint
        # calls them in sequence. We'll patch the helpers themselves.
        with patch.object(dashboard_router, "_count_deals_by_status", AsyncMock(return_value={})):
            with patch.object(dashboard_router, "_count_today_campaigns", AsyncMock(return_value=0)):
                with patch.object(dashboard_router, "_count_today_replies", AsyncMock(return_value=0)):
                    with patch.object(dashboard_router, "_count_active_conversations", AsyncMock(return_value=0)):
                        with patch.object(dashboard_router, "_count_contract_ready", AsyncMock(return_value=0)):
                            with patch.object(dashboard_router, "_count_total_pitched", AsyncMock(return_value=0)):
                                with patch.object(dashboard_router, "_count_total_contracts", AsyncMock(return_value=0)):
                                    result = await dashboard_router.dashboard_stats(db=db)

        assert result["deals"]["available"] == 0
        assert result["deals"]["launched"] == 0
        assert result["deals"]["under_contract"] == 0
        assert result["deals"]["closed"] == 0
        assert result["today"]["emails_sent"] == 0
        assert result["today"]["replies_received"] == 0
        assert result["active"]["conversations"] == 0
        assert result["active"]["contract_ready"] == 0
        assert result["conversion"]["total_pitched"] == 0
        assert result["conversion"]["total_contracts"] == 0
        assert result["conversion"]["rate_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_dashboard_stats_with_deals(self):
        """Deal counts by status are correctly returned."""
        db = AsyncMock()

        deal_counts = {
            "Available": 1,
            "Campaign Launched": 1,
            "Under Contract": 0,
            "Sold": 0,
        }

        with patch.object(dashboard_router, "_count_deals_by_status", AsyncMock(return_value=deal_counts)):
            with patch.object(dashboard_router, "_count_today_campaigns", AsyncMock(return_value=0)):
                with patch.object(dashboard_router, "_count_today_replies", AsyncMock(return_value=0)):
                    with patch.object(dashboard_router, "_count_active_conversations", AsyncMock(return_value=0)):
                        with patch.object(dashboard_router, "_count_contract_ready", AsyncMock(return_value=0)):
                            with patch.object(dashboard_router, "_count_total_pitched", AsyncMock(return_value=0)):
                                with patch.object(dashboard_router, "_count_total_contracts", AsyncMock(return_value=0)):
                                    result = await dashboard_router.dashboard_stats(db=db)

        assert result["deals"]["available"] == 1
        assert result["deals"]["launched"] == 1
        assert result["deals"]["under_contract"] == 0
        assert result["deals"]["closed"] == 0

    @pytest.mark.asyncio
    async def test_dashboard_today_emails(self):
        """Emails sent today count is returned."""
        db = AsyncMock()

        with patch.object(dashboard_router, "_count_deals_by_status", AsyncMock(return_value={})):
            with patch.object(dashboard_router, "_count_today_campaigns", AsyncMock(return_value=5)):
                with patch.object(dashboard_router, "_count_today_replies", AsyncMock(return_value=0)):
                    with patch.object(dashboard_router, "_count_active_conversations", AsyncMock(return_value=0)):
                        with patch.object(dashboard_router, "_count_contract_ready", AsyncMock(return_value=0)):
                            with patch.object(dashboard_router, "_count_total_pitched", AsyncMock(return_value=0)):
                                with patch.object(dashboard_router, "_count_total_contracts", AsyncMock(return_value=0)):
                                    result = await dashboard_router.dashboard_stats(db=db)

        assert result["today"]["emails_sent"] == 5

    @pytest.mark.asyncio
    async def test_dashboard_today_replies(self):
        """Replies received today count is returned."""
        db = AsyncMock()

        with patch.object(dashboard_router, "_count_deals_by_status", AsyncMock(return_value={})):
            with patch.object(dashboard_router, "_count_today_campaigns", AsyncMock(return_value=0)):
                with patch.object(dashboard_router, "_count_today_replies", AsyncMock(return_value=3)):
                    with patch.object(dashboard_router, "_count_active_conversations", AsyncMock(return_value=0)):
                        with patch.object(dashboard_router, "_count_contract_ready", AsyncMock(return_value=0)):
                            with patch.object(dashboard_router, "_count_total_pitched", AsyncMock(return_value=0)):
                                with patch.object(dashboard_router, "_count_total_contracts", AsyncMock(return_value=0)):
                                    result = await dashboard_router.dashboard_stats(db=db)

        assert result["today"]["replies_received"] == 3

    @pytest.mark.asyncio
    async def test_dashboard_conversion_rate(self):
        """Conversion rate is correctly calculated as total_contracts / total_pitched * 100."""
        db = AsyncMock()

        with patch.object(dashboard_router, "_count_deals_by_status", AsyncMock(return_value={})):
            with patch.object(dashboard_router, "_count_today_campaigns", AsyncMock(return_value=0)):
                with patch.object(dashboard_router, "_count_today_replies", AsyncMock(return_value=0)):
                    with patch.object(dashboard_router, "_count_active_conversations", AsyncMock(return_value=0)):
                        with patch.object(dashboard_router, "_count_contract_ready", AsyncMock(return_value=0)):
                            with patch.object(dashboard_router, "_count_total_pitched", AsyncMock(return_value=10)):
                                with patch.object(dashboard_router, "_count_total_contracts", AsyncMock(return_value=2)):
                                    result = await dashboard_router.dashboard_stats(db=db)

        assert result["conversion"]["total_pitched"] == 10
        assert result["conversion"]["total_contracts"] == 2
        assert result["conversion"]["rate_pct"] == 20.0
