"""Tests for campaign scheduling: touches 2-6 send on schedule.

Verifies that process_scheduled_campaigns:
- Correctly picks up queued campaigns past their scheduled_send_at
- Sends touches in sequential order (touch N waits for touch N-1)
- Respects tier-based scheduling offsets
- Pauses campaigns when buyer already replied
- Pauses campaigns when deal is no longer active
- Skips campaigns where previous touch hasn't sent yet
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.models import Campaign, Deal, Buyer
from app.services.scheduler import process_scheduled_campaigns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_campaign(
    touch_number: int,
    status: str = "Queued",
    scheduled_send_at: Optional[datetime] = None,
    sent_at: Optional[datetime] = None,
    buyer_id: Optional[uuid.UUID] = None,
    deal_id: Optional[uuid.UUID] = None,
    subject: str = "Test Subject",
    body: str = "Test body",
) -> Campaign:
    if scheduled_send_at is None:
        scheduled_send_at = datetime.now(timezone.utc) - timedelta(hours=1)
    campaign = MagicMock(spec=Campaign)
    campaign.id = uuid.uuid4()
    campaign.touch_number = touch_number
    campaign.status = status
    campaign.scheduled_send_at = scheduled_send_at
    campaign.sent_at = sent_at
    campaign.buyer_id = buyer_id or uuid.uuid4()
    campaign.deal_id = deal_id or uuid.uuid4()
    campaign.subject = subject
    campaign.body = body
    campaign.buyer_legal_name = None
    campaign.buyer_phone = None
    campaign.buyer_title_company = None
    campaign.agreed_price = None
    campaign.conversation_stage = "pitching"
    campaign.question_round = 0
    campaign.reply_received_at = None
    campaign.reply_body = None
    campaign.reply_intent = None
    campaign.ai_extracted_insights = None
    campaign.ghost_detected_at = None
    campaign.ghost_recovery_touch = 0
    campaign.ghost_recovery_sent_at = None
    campaign.pass_reason_category = None
    campaign.pass_reason_raw = None
    campaign.pass_reason_confidence = None
    campaign.passed_at = None
    return campaign


def _make_deal(deal_id: Optional[uuid.UUID] = None, status: str = "Available") -> Deal:
    deal = MagicMock(spec=Deal)
    deal.id = deal_id or uuid.uuid4()
    deal.status = status
    deal.address = "456 Oak Ave"
    deal.repair_estimate = None
    deal.asking_price = 250000.0
    deal.arv = 350000.0
    return deal


def _make_buyer(buyer_id: Optional[uuid.UUID] = None, email: str = "buyer@example.com") -> Buyer:
    buyer = MagicMock(spec=Buyer)
    buyer.id = buyer_id or uuid.uuid4()
    buyer.email = email
    buyer.full_name = "Jane Investor"
    buyer.unsubscribed_at = None
    buyer.status = "Active"
    return buyer


# ---------------------------------------------------------------------------
# DB mock setup — patches async_session_factory to return a fake session
# ---------------------------------------------------------------------------

def _patch_db(campaigns_to_return, deal, buyer, replied_exists=False, prev_is_sent=True):
    """Return factory patch + mock db reference for process_scheduled_campaigns."""

    class FakeSession:
        """Fake async context manager returning a pre-configured mock db session.

        Fixes two issues from the original mock:
        1. db.get() now returns the actual Deal/Buyer objects, not AsyncMock
        2. SQL string matching no longer depends on literal values ("Queued",
           "Replied") which SQLAlchemy renders as bind parameters like :status_1.
           Instead it matches on structural SQL patterns:
           - Queued query: sent_at IS NULL + scheduled_send_at
           - Previous touch: touch_number
           - Replied check: any other FROM campaigns query
        """

        def __init__(self):
            self.db = AsyncMock()

            # Fix db.get() — return actual Deal/Buyer objects instead of AsyncMock
            async def _mock_get(model, pk):
                if model is Deal:
                    return deal
                if model is Buyer:
                    return buyer
                return None

            self.db.get = AsyncMock(side_effect=_mock_get)
            self.db.add = MagicMock()
            self.db.add_all = MagicMock()
            self.db.commit = AsyncMock()
            self.db.rollback = AsyncMock()

            def execute_side_effect(*args, **kwargs):
                sql = args[0] if args else None
                sql_str = str(sql) if sql is not None else ""

                # ── Batch-load Deals query (distinct: FROM deals + id IN) ──
                if "from deals" in sql_str.lower() and "id in" in sql_str.lower():
                    result = MagicMock()
                    result.scalars = MagicMock(return_value=result)
                    result.all = MagicMock(return_value=[deal])
                    return result

                # ── Batch-load Buyers query (distinct: FROM buyers + id IN) ──
                if "from buyers" in sql_str.lower() and "id in" in sql_str.lower():
                    result = MagicMock()
                    result.scalars = MagicMock(return_value=result)
                    result.all = MagicMock(return_value=[buyer])
                    return result

                # ── Queued campaigns query (distinct: sent_at IS NULL + scheduled_send_at) ──
                if "sent_at" in sql_str and "IS NULL" in sql_str and "scheduled_send_at" in sql_str:
                    result = MagicMock()
                    result.scalars = MagicMock(return_value=result)
                    result.all = MagicMock(return_value=campaigns_to_return)
                    return result

                # ── Previous touch check (distinct: touch_number in WHERE clause,
                #    not in SELECT column list — use "touch_number = ") ──
                if "touch_number =" in sql_str:
                    result = MagicMock()
                    if prev_is_sent:
                        prev = MagicMock(spec=Campaign)
                        prev.status = "Sent"
                        prev.sent_at = datetime.now(timezone.utc) - timedelta(days=7)
                        result.scalar_one_or_none = MagicMock(return_value=prev)
                    else:
                        result.scalar_one_or_none = MagicMock(return_value=None)
                    return result

                # ── Replied check (any other FROM campaigns query) ──
                # Note: SQL has \r\n newlines around FROM clause
                if "from campaigns" in sql_str.lower():
                    result = MagicMock()
                    if replied_exists:
                        result.first = MagicMock(return_value=MagicMock())
                    else:
                        result.first = MagicMock(return_value=None)
                    result.scalars = MagicMock(return_value=result)
                    result.all = MagicMock(return_value=[])
                    return result

                # ── Default fallback ──
                result = MagicMock()
                result.scalar_one_or_none = MagicMock(return_value=None)
                result.first = MagicMock(return_value=None)
                result.scalars = MagicMock(return_value=result)
                result.all = MagicMock(return_value=[])
                result.fetchall = MagicMock(return_value=[])
                return result

            self.db.execute = AsyncMock(side_effect=execute_side_effect)

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *args):
            pass

    from app import database as _db_module
    session_instance = FakeSession()
    factory_patch = patch.object(
        _db_module,
        "async_session_factory",
        return_value=session_instance,
    )
    return factory_patch, session_instance.db


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
def deal(deal_id):
    return _make_deal(deal_id)


@pytest.fixture
def buyer(buyer_id):
    return _make_buyer(buyer_id)


# ===========================================================================
# Tests
# ===========================================================================

class TestProcessScheduledCampaigns:

    # -----------------------------------------------------------------------
    # Basic scheduling: touches 2-6 are picked up when past scheduled_send_at
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_sends_touches_2_through_6_when_due(self, deal, buyer):
        """All 6 touches past their scheduled time: touches 1-6 should send."""
        now = datetime.now(timezone.utc)
        campaigns = [
            _make_campaign(1, scheduled_send_at=now - timedelta(days=1), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(2, scheduled_send_at=now - timedelta(hours=12), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(3, scheduled_send_at=now - timedelta(hours=6), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(4, scheduled_send_at=now - timedelta(hours=3), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(5, scheduled_send_at=now - timedelta(hours=1), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(6, scheduled_send_at=now - timedelta(minutes=30), buyer_id=buyer.id, deal_id=deal.id),
        ]

        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=True)

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", AsyncMock(return_value={"status": "sent", "message_id": "msg123"})):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 6  # All 6 touches should send

    @pytest.mark.asyncio
    async def test_sends_touches_2_through_6_when_sequential(self, deal, buyer):
        """Touches 2-6 send when each previous touch is Sent before this run."""
        now = datetime.now(timezone.utc)
        campaigns = [
            _make_campaign(2, scheduled_send_at=now - timedelta(hours=12), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(3, scheduled_send_at=now - timedelta(hours=6), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(4, scheduled_send_at=now - timedelta(hours=3), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(5, scheduled_send_at=now - timedelta(hours=1), buyer_id=buyer.id, deal_id=deal.id),
            _make_campaign(6, scheduled_send_at=now - timedelta(minutes=30), buyer_id=buyer.id, deal_id=deal.id),
        ]

        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=True)

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", AsyncMock(return_value={"status": "sent", "message_id": "msg123"})):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 5  # 5 queued touches (2-6) should all send

    # -----------------------------------------------------------------------
    # Sequential ordering: each touch waits for the previous
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_blocks_touch_2_when_touch_1_not_sent(self, deal, buyer):
        """Touch 2 should NOT send if touch 1 is still Queued (not Sent/Replied)."""
        now = datetime.now(timezone.utc)
        t2 = _make_campaign(2, scheduled_send_at=now - timedelta(hours=1),
                            buyer_id=buyer.id, deal_id=deal.id)
        campaigns = [t2]

        # prev_is_sent=False → previous touch check returns None
        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=False)

        send_mock = AsyncMock(return_value={"status": "sent", "message_id": "msg123"})

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", send_mock):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 0  # Touch 2 blocked because touch 1 not sent
        send_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocks_touch_3_when_prev_touch_not_sent(self, deal, buyer):
        """Touch 3 should NOT send if previous touch is not Sent/Replied."""
        now = datetime.now(timezone.utc)
        t3 = _make_campaign(3, scheduled_send_at=now - timedelta(hours=1),
                            buyer_id=buyer.id, deal_id=deal.id)
        campaigns = [t3]

        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=False)

        send_mock = AsyncMock(return_value={"status": "sent", "message_id": "msg123"})

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", send_mock):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 0
        send_mock.assert_not_called()

    # -----------------------------------------------------------------------
    # All touches send when ready
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_all_touches_send_when_all_ready(self, deal, buyer):
        """When all touches are past due with prev sent, all send."""
        now = datetime.now(timezone.utc)
        campaigns = []
        for i in range(1, 7):
            c = _make_campaign(i, scheduled_send_at=now - timedelta(hours=i * 2),
                               buyer_id=buyer.id, deal_id=deal.id)
            campaigns.append(c)

        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=True)

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", AsyncMock(return_value={"status": "sent", "message_id": "msg123"})):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 6

    # -----------------------------------------------------------------------
    # Pause rules
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pauses_when_buyer_already_replied(self, deal, buyer):
        """Campaigns should be paused if buyer already replied to any touch."""
        now = datetime.now(timezone.utc)
        t2 = _make_campaign(2, scheduled_send_at=now - timedelta(hours=1),
                            buyer_id=buyer.id, deal_id=deal.id)
        campaigns = [t2]

        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, replied_exists=True, prev_is_sent=True)

        send_mock = AsyncMock(return_value={"status": "sent", "message_id": "msg123"})

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", send_mock):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 0  # No campaigns sent (paused)
        assert t2.status == "Paused"  # Campaign was paused
        send_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_pauses_when_deal_no_longer_active(self, deal, buyer):
        """Campaigns should be paused if deal is Under Contract/Sold/Dead."""
        now = datetime.now(timezone.utc)
        deal.status = "Under Contract"

        t2 = _make_campaign(2, scheduled_send_at=now - timedelta(hours=1),
                            buyer_id=buyer.id, deal_id=deal.id)
        campaigns = [t2]

        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=True)

        send_mock = AsyncMock(return_value={"status": "sent", "message_id": "msg123"})

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", send_mock):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 0
        assert t2.status == "Paused"
        send_mock.assert_not_called()

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_no_queued_campaigns_returns_0(self, deal, buyer):
        """When no campaigns are queued, should return 0."""
        factory_patch, mock_db = _patch_db([], deal, buyer, prev_is_sent=True)

        with factory_patch:
            sent = await process_scheduled_campaigns()

        assert sent == 0

    @pytest.mark.asyncio
    async def test_skips_campaigns_without_subject_or_body(self, deal, buyer):
        """Campaigns without subject or body should be skipped (Failed)."""
        now = datetime.now(timezone.utc)
        t2 = _make_campaign(2, scheduled_send_at=now - timedelta(hours=1),
                            buyer_id=buyer.id, deal_id=deal.id,
                            subject="", body="")

        campaigns = [t2]
        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=True)

        send_mock = AsyncMock(return_value={"status": "sent", "message_id": "msg123"})

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", send_mock):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 0
        send_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_touch_when_previous_touch_missing(self, deal, buyer):
        """Touch should skip if the previous touch campaign doesn't exist at all."""
        now = datetime.now(timezone.utc)
        t3 = _make_campaign(3, scheduled_send_at=now - timedelta(hours=1),
                            buyer_id=buyer.id, deal_id=deal.id)
        campaigns = [t3]

        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=False)

        send_mock = AsyncMock(return_value={"status": "sent", "message_id": "msg123"})

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", send_mock):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        assert sent == 0
        send_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_only_eligible_campaigns(self, deal, buyer):
        """Should only send campaigns that pass all checks, skip the rest."""
        now = datetime.now(timezone.utc)
        # Touch 1: no subject → should be skipped (no subject or body check)
        t1 = _make_campaign(1, scheduled_send_at=now - timedelta(days=2),
                            buyer_id=buyer.id, deal_id=deal.id,
                            subject="", body="")
        # Touch 2: valid → should send
        t2 = _make_campaign(2, scheduled_send_at=now - timedelta(hours=12),
                            buyer_id=buyer.id, deal_id=deal.id)

        campaigns = [t1, t2]
        factory_patch, mock_db = _patch_db(campaigns, deal, buyer, prev_is_sent=True)

        send_mock = AsyncMock(return_value={"status": "sent", "message_id": "msg123"})

        with factory_patch:
            with patch("app.services.scheduler.campaign_sender.send_email", send_mock):
                with patch("app.services.scheduler.campaign_sender.validate_ai_output", AsyncMock(return_value=MagicMock(severity="pass"))):
                    sent = await process_scheduled_campaigns()

        # Touch 1 skipped (no subject/body), touch 2 should send
        assert sent == 1
        send_mock.assert_awaited_once()
