"""Tests for deal expiry auto-stop and expiry urgency in email prompts.

Covers:
- check_deal_auto_stops: expired deals pause campaigns and set status to Expired
- _build_prompt: expiry urgency text is injected for close-expiry deals
- _build_prompt: no urgency text when no expiry date

Follows the same test class pattern as test_campaign_pause.py.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.models import Campaign, Deal


# ===========================================================================
# Test: check_deal_auto_stops
# ===========================================================================

class TestDealAutoStop:

    @pytest.mark.asyncio
    async def test_expired_deal_pauses_campaigns(self):
        """Auto-stop should pause all Queued campaigns for an expired deal."""
        from app.services.scheduler.auto_stops import check_deal_auto_stops
        import app.database as _db_real

        now = datetime.now(timezone.utc)
        deal_id = uuid.uuid4()
        deal = MagicMock(spec=Deal)
        deal.id = deal_id
        deal.address = "123 Test St"
        deal.status = "Campaign Launched"
        deal.expiry_date = now - timedelta(days=1)
        deal.payment_confirmed = False

        queued_camp = MagicMock(spec=Campaign)
        queued_camp.id = uuid.uuid4()
        queued_camp.deal_id = deal_id
        queued_camp.status = "Queued"

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        deal_result = MagicMock()
        deal_result.scalars.return_value.all.return_value = [deal]
        camp_result = MagicMock()
        camp_result.scalars.return_value.all.return_value = [queued_camp]

        db.execute = AsyncMock(side_effect=[deal_result, camp_result])
        db.get = AsyncMock(return_value=None)

        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(return_value=db)
        session_mock.__aexit__ = AsyncMock(return_value=None)

        with patch.object(_db_real, "async_session_factory", return_value=session_mock):
            affected = await check_deal_auto_stops()

        assert affected == 1
        assert queued_camp.status == "Paused"
        assert deal.status == "Expired"

    @pytest.mark.asyncio
    async def test_expired_deal_sets_status_expired(self):
        """Auto-stop should set deal.status to Expired when expiry_date has passed."""
        from app.services.scheduler.auto_stops import check_deal_auto_stops
        import app.database as _db_real

        now = datetime.now(timezone.utc)
        deal_id = uuid.uuid4()
        deal = MagicMock(spec=Deal)
        deal.id = deal_id
        deal.address = "456 Oak Ave"
        deal.status = "Campaign Launched"
        deal.expiry_date = now - timedelta(hours=1)
        deal.payment_confirmed = False

        queued_camp = MagicMock(spec=Campaign)
        queued_camp.id = uuid.uuid4()
        queued_camp.deal_id = deal_id
        queued_camp.status = "Queued"

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        deal_result = MagicMock()
        deal_result.scalars.return_value.all.return_value = [deal]
        camp_result = MagicMock()
        camp_result.scalars.return_value.all.return_value = [queued_camp]

        db.execute = AsyncMock(side_effect=[deal_result, camp_result])
        db.get = AsyncMock(return_value=None)

        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(return_value=db)
        session_mock.__aexit__ = AsyncMock(return_value=None)

        with patch.object(_db_real, "async_session_factory", return_value=session_mock):
            await check_deal_auto_stops()

        assert deal.status == "Expired"

    @pytest.mark.asyncio
    async def test_closed_deal_pauses_queued_campaigns(self):
        """Auto-stop should pause campaigns when deal.status is Closed."""
        from app.services.scheduler.auto_stops import check_deal_auto_stops
        import app.database as _db_real

        deal_id = uuid.uuid4()
        deal = MagicMock(spec=Deal)
        deal.id = deal_id
        deal.address = "789 Pine St"
        deal.status = "Closed"
        deal.expiry_date = None
        deal.payment_confirmed = False

        queued_camp = MagicMock(spec=Campaign)
        queued_camp.id = uuid.uuid4()
        queued_camp.deal_id = deal_id
        queued_camp.status = "Queued"

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        deal_result = MagicMock()
        deal_result.scalars.return_value.all.return_value = [deal]
        camp_result = MagicMock()
        camp_result.scalars.return_value.all.return_value = [queued_camp]

        db.execute = AsyncMock(side_effect=[deal_result, camp_result])
        db.get = AsyncMock(return_value=None)

        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(return_value=db)
        session_mock.__aexit__ = AsyncMock(return_value=None)

        with patch.object(_db_real, "async_session_factory", return_value=session_mock):
            await check_deal_auto_stops()

        assert queued_camp.status == "Paused"


# ===========================================================================
# Test: _build_prompt expiry urgency
# ===========================================================================

class TestExpiryUrgency:

    def test_expiry_urgency_in_prompt_3_days(self):
        """When expiry is <=3 days away, 'URGENT' should appear in the prompt."""
        from app.services.email_generator import _build_prompt

        expiry = datetime.now(timezone.utc) + timedelta(days=2)
        messages = _build_prompt(
            touch=1,
            buyer_name="Test Buyer",
            buyer_email="buyer@test.com",
            buy_box="Looking for 3BR houses",
            buyer_tier="A-List",
            address="123 Test St",
            city="TestCity",
            state="TS",
            property_type="House",
            arv=250000.0,
            asking_price=200000.0,
            spread=50000.0,
            condition_description="Good condition",
            expiry_date=expiry,
        )

        user_content = messages[1]["content"]
        assert "URGENT" in user_content, "URGENT should be in the prompt for <=3 days"

    def test_expiry_urgency_in_prompt_7_days(self):
        """When expiry is 3-7 days away, 'DEADLINE' should appear in the prompt."""
        from app.services.email_generator import _build_prompt

        expiry = datetime.now(timezone.utc) + timedelta(days=5)
        messages = _build_prompt(
            touch=1,
            buyer_name="Test Buyer",
            buyer_email="buyer@test.com",
            buy_box="Looking for 3BR houses",
            buyer_tier="A-List",
            address="123 Test St",
            city="TestCity",
            state="TS",
            property_type="House",
            arv=250000.0,
            asking_price=200000.0,
            spread=50000.0,
            condition_description="Good condition",
            expiry_date=expiry,
        )

        user_content = messages[1]["content"]
        assert "DEADLINE" in user_content, "DEADLINE should be in prompt for 3-7 days"
        assert "URGENT" not in user_content, "URGENT should not be in prompt for 3-7 days"

    def test_no_urgency_when_no_expiry_date(self):
        """When expiry_date is None, no urgency text should appear."""
        from app.services.email_generator import _build_prompt

        messages = _build_prompt(
            touch=1,
            buyer_name="Test Buyer",
            buyer_email="buyer@test.com",
            buy_box="Looking for 3BR houses",
            buyer_tier="A-List",
            address="123 Test St",
            city="TestCity",
            state="TS",
            property_type="House",
            arv=250000.0,
            asking_price=200000.0,
            spread=50000.0,
            condition_description="Good condition",
            expiry_date=None,
        )

        user_content = messages[1]["content"]
        assert "URGENT" not in user_content, "URGENT should not appear when no expiry"
        assert "DEADLINE" not in user_content, "DEADLINE should not appear when no expiry"
