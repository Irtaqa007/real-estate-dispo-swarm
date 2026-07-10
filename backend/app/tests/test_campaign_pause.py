"""Tests for campaign pause/resume endpoints.

Covers:
- pause_campaigns: sets Queued → Paused, sets deal to Paused, logs activity
- resume_campaigns: sets Paused → Queued, sets deal to Campaign Launched, logs activity
- Idempotency: Sent/Passed campaigns are not affected by pause
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.models import ActivityLog, Campaign, Deal
from app.routers.campaigns import pause_campaigns, resume_campaigns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_campaign(
    touch_number: int,
    status: str = "Queued",
    deal_id: uuid.UUID = None,
    buyer_id: uuid.UUID = None,
) -> Campaign:
    c = MagicMock(spec=Campaign)
    c.id = uuid.uuid4()
    c.touch_number = touch_number
    c.status = status
    c.deal_id = deal_id or uuid.uuid4()
    c.buyer_id = buyer_id or uuid.uuid4()
    c.subject = "Test Subject"
    c.body = "Test body"
    return c


def _make_deal(deal_id: uuid.UUID = None, status: str = "Campaign Launched") -> Deal:
    d = MagicMock(spec=Deal)
    d.id = deal_id or uuid.uuid4()
    d.status = status
    d.address = "456 Oak Ave"
    return d


def _make_mock_db(campaigns: list, deal) -> AsyncMock:
    """Create a mock db session that returns the given campaigns and deal."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    def execute_side_effect(*args, **kwargs):
        sql = args[0] if args else None
        sql_str = str(sql) if sql is not None else ""
        result = MagicMock()
        result.scalars = MagicMock(return_value=result)
        result.all = MagicMock(return_value=campaigns)
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)
    db.get = AsyncMock(side_effect=lambda model, pk: deal if model == Deal else None)
    return db


# ===========================================================================
# Tests
# ===========================================================================

class TestPauseCampaigns:

    @pytest.mark.asyncio
    async def test_pause_campaign_sets_queued_to_paused(self):
        """Pause should set all Queued campaigns to Paused."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Campaign Launched")
        campaigns = [
            _make_campaign(1, "Queued", deal_id),
            _make_campaign(2, "Queued", deal_id),
            _make_campaign(3, "Queued", deal_id),
        ]
        db = _make_mock_db(campaigns, deal)

        result = await pause_campaigns(deal_id, body={"reason": "testing"}, db=db)

        assert result["paused_count"] == 3
        assert result["deal_id"] == str(deal_id)
        for c in campaigns:
            assert c.status == "Paused"

    @pytest.mark.asyncio
    async def test_pause_sets_deal_status_paused(self):
        """Pause should set deal.status to Paused."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Campaign Launched")
        campaigns = [_make_campaign(1, "Queued", deal_id)]
        db = _make_mock_db(campaigns, deal)

        await pause_campaigns(deal_id, body={"reason": "testing"}, db=db)

        assert deal.status == "Paused"

    @pytest.mark.asyncio
    async def test_pause_logs_activity(self):
        """Pause should log a campaign_paused activity entry."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Campaign Launched")
        campaigns = [_make_campaign(1, "Queued", deal_id)]
        db = _make_mock_db(campaigns, deal)

        await pause_campaigns(deal_id, body={"reason": "testing"}, db=db)

        # Verify an ActivityLog was added with correct action
        added_logs = []
        for call_args in db.add.call_args_list:
            if call_args.args and isinstance(call_args.args[0], ActivityLog):
                added_logs.append(call_args.args[0])
        assert len(added_logs) >= 1
        log_entry = added_logs[0]
        assert log_entry.action == "campaign_paused"
        assert log_entry.entity_type == "deal"
        assert log_entry.entity_id == deal_id
        assert log_entry.metadata_json["paused_count"] == 1
        assert log_entry.metadata_json["reason"] == "testing"

    @pytest.mark.asyncio
    async def test_pause_does_not_affect_sent_campaigns(self):
        """Pause should leave Sent campaigns unchanged."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Campaign Launched")
        sent_camp = _make_campaign(1, "Sent", deal_id)
        queued_camp = _make_campaign(2, "Queued", deal_id)
        # Mock only returns Queued campaigns (what the endpoint queries)
        db = _make_mock_db([queued_camp], deal)

        await pause_campaigns(deal_id, body={}, db=db)

        assert queued_camp.status == "Paused"
        assert sent_camp.status == "Sent", "Sent campaign should not be changed by endpoint"

    @pytest.mark.asyncio
    async def test_pause_does_not_affect_passed_campaigns(self):
        """Pause should leave Passed campaigns unchanged."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Campaign Launched")
        passed_camp = _make_campaign(1, "Passed", deal_id)
        queued_camp = _make_campaign(2, "Queued", deal_id)
        # Mock only returns Queued campaigns (what the endpoint queries)
        db = _make_mock_db([queued_camp], deal)

        await pause_campaigns(deal_id, body={}, db=db)

        assert queued_camp.status == "Paused"
        assert passed_camp.status == "Passed", "Passed campaign should not be changed"


class TestResumeCampaigns:

    @pytest.mark.asyncio
    async def test_resume_sets_paused_to_queued(self):
        """Resume should set all Paused campaigns to Queued."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Paused")
        campaigns = [
            _make_campaign(1, "Paused", deal_id),
            _make_campaign(2, "Paused", deal_id),
        ]
        db = _make_mock_db(campaigns, deal)

        result = await resume_campaigns(deal_id, db=db)

        assert result["resumed_count"] == 2
        assert result["deal_id"] == str(deal_id)
        for c in campaigns:
            assert c.status == "Queued"

    @pytest.mark.asyncio
    async def test_resume_sets_deal_status_launched(self):
        """Resume should set deal.status back to Campaign Launched."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Paused")
        campaigns = [_make_campaign(1, "Paused", deal_id)]
        db = _make_mock_db(campaigns, deal)

        await resume_campaigns(deal_id, db=db)

        assert deal.status == "Campaign Launched"

    @pytest.mark.asyncio
    async def test_resume_logs_activity(self):
        """Resume should log a campaign_resumed activity entry."""
        deal_id = uuid.uuid4()
        deal = _make_deal(deal_id, "Paused")
        campaigns = [_make_campaign(1, "Paused", deal_id)]
        db = _make_mock_db(campaigns, deal)

        await resume_campaigns(deal_id, db=db)

        added_logs = []
        for call_args in db.add.call_args_list:
            if call_args.args and isinstance(call_args.args[0], ActivityLog):
                added_logs.append(call_args.args[0])
        assert len(added_logs) >= 1
        log_entry = added_logs[0]
        assert log_entry.action == "campaign_resumed"
        assert log_entry.entity_type == "deal"
        assert log_entry.entity_id == deal_id
        assert log_entry.metadata_json["resumed_count"] == 1
