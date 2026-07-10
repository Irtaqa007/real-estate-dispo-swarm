"""Tests for the manual reply endpoint.

Covers:
- Successful manual reply sends email and returns success
- Campaign reply_body updated with "MANUAL:" prefix
- ActivityLog entry created with action="manual_reply_sent"
- Non-existent campaign returns 404
- Empty message returns 422/400
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.models import ActivityLog, Buyer, Campaign
from app.routers import campaigns as campaigns_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def campaign_id():
    return uuid.uuid4()


@pytest.fixture
def buyer_id():
    return uuid.uuid4()


@pytest.fixture
def mock_campaign(campaign_id, buyer_id):
    c = MagicMock(spec=Campaign)
    c.id = campaign_id
    c.buyer_id = buyer_id
    c.deal_id = uuid.uuid4()
    c.touch_number = 1
    c.status = "Sent"
    c.subject = "Great deal in Austin"
    c.body = "Check out this property..."
    c.conversation_stage = "pitching"
    c.reply_body = None
    return c


@pytest.fixture
def mock_buyer(buyer_id):
    b = MagicMock(spec=Buyer)
    b.id = buyer_id
    b.full_name = "Jane Investor"
    b.email = "jane@example.com"
    return b


@pytest.fixture
def mock_db():
    return AsyncMock()


# ===========================================================================
# Tests
# ===========================================================================


class TestManualReply:

    @pytest.mark.asyncio
    async def test_manual_reply_success(self, campaign_id, mock_campaign, mock_buyer, mock_db):
        """Successful manual reply returns success=True and sent_to=buyer email."""
        # Mock campaign query
        campaign_result = MagicMock()
        campaign_result.scalar_one_or_none.return_value = mock_campaign
        # Mock buyer query
        buyer_result = MagicMock()
        buyer_result.scalar_one_or_none.return_value = mock_buyer

        mock_db.execute = AsyncMock(side_effect=[campaign_result, buyer_result])
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with patch.object(campaigns_router, "send_email", AsyncMock(return_value={
            "status": "sent", "message_id": "msg_123", "sent_at": datetime.now(timezone.utc).isoformat(),
        })):
            result = await campaigns_router.manual_reply(
                campaign_id=campaign_id,
                body={"message": "Thanks for your interest! Let me know if you have questions."},
                db=mock_db,
            )

        assert result["success"] is True
        assert result["sent_to"] == "jane@example.com"

    @pytest.mark.asyncio
    async def test_manual_reply_updates_campaign(self, campaign_id, mock_campaign, mock_buyer, mock_db):
        """After reply, campaign.reply_body should start with 'MANAL:'."""
        campaign_result = MagicMock()
        campaign_result.scalar_one_or_none.return_value = mock_campaign
        buyer_result = MagicMock()
        buyer_result.scalar_one_or_none.return_value = mock_buyer

        mock_db.execute = AsyncMock(side_effect=[campaign_result, buyer_result])
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with patch.object(campaigns_router, "send_email", AsyncMock(return_value={
            "status": "sent", "message_id": "msg_123", "sent_at": datetime.now(timezone.utc).isoformat(),
        })):
            await campaigns_router.manual_reply(
                campaign_id=campaign_id,
                body={"message": "Let's schedule a walkthrough."},
                db=mock_db,
            )

        assert mock_campaign.reply_body.startswith("MANUAL:")
        assert "walkthrough" in mock_campaign.reply_body

    @pytest.mark.asyncio
    async def test_manual_reply_logs_activity(self, campaign_id, mock_campaign, mock_buyer, mock_db):
        """ActivityLog entry should be created with action='manual_reply_sent'."""
        campaign_result = MagicMock()
        campaign_result.scalar_one_or_none.return_value = mock_campaign
        buyer_result = MagicMock()
        buyer_result.scalar_one_or_none.return_value = mock_buyer

        mock_db.execute = AsyncMock(side_effect=[campaign_result, buyer_result])
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with patch.object(campaigns_router, "send_email", AsyncMock(return_value={
            "status": "sent", "message_id": "msg_123", "sent_at": datetime.now(timezone.utc).isoformat(),
        })):
            await campaigns_router.manual_reply(
                campaign_id=campaign_id,
                body={"message": "Let's move forward."},
                db=mock_db,
            )

        # Verify an ActivityLog was added to the session
        log_added = None
        for call_args in mock_db.add.call_args_list:
            arg = call_args[0][0]
            if isinstance(arg, ActivityLog):
                log_added = arg
                break

        assert log_added is not None
        assert log_added.action == "manual_reply_sent"
        assert log_added.entity_type == "campaign"
        assert log_added.entity_id == campaign_id

    @pytest.mark.asyncio
    async def test_manual_reply_invalid_campaign(self, mock_db):
        """POST to non-existent campaign_id should raise 404."""
        campaign_result = MagicMock()
        campaign_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=campaign_result)

        with pytest.raises(HTTPException) as exc_info:
            await campaigns_router.manual_reply(
                campaign_id=uuid.uuid4(),
                body={"message": "Hello"},
                db=mock_db,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_manual_reply_empty_message(self, mock_db, campaign_id):
        """POST with empty message should raise 400."""
        with pytest.raises(HTTPException) as exc_info:
            await campaigns_router.manual_reply(
                campaign_id=campaign_id,
                body={"message": "   "},
                db=mock_db,
            )

        assert exc_info.value.status_code == 400
        assert "message" in exc_info.value.detail.lower()
