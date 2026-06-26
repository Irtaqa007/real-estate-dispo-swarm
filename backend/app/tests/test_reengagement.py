"""Tests for future buying window detection and re-engagement scheduler.

Covers:
- detect_future_buying_window() in reply_processor.py
- fire_buyer_reengagements() in scheduler.py
- Edge cases: vague signals, inactive buyers, 2-deal cap, no matching deal
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ==========================================================================
# detect_future_buying_window tests
# ==========================================================================


class MockGroqResponse:
    """Mock Groq API response."""

    def __init__(self, content: str):
        self.choices = [MagicMock()]
        self.choices[0].message.content = content


@pytest.mark.asyncio
async def test_detect_september_window():
    """'I'll be ready in September' should create a waiting record with Sept target."""
    from app.services.reply_processor import detect_future_buying_window
    from app.models.schemas import BuyerReengagementSchedule

    mock_db = AsyncMock()
    mock_db.add.return_value = None
    mock_db.flush.return_value = None

    mock_groq_response = MockGroqResponse(
        '{"has_future_signal": true, "stated_window_raw": "I will be ready in September", '
        '"target_date": "2026-09-01", "target_month": "2026-09", "confidence": "high"}'
    )

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()

    with patch(
        "app.services.reply_processor.groq_chat_completion",
        AsyncMock(return_value=mock_groq_response),
    ):
        result = await detect_future_buying_window(
            reply_body="I will be ready in September.",
            buyer_id=buyer_id,
            deal_id=deal_id,
            db=mock_db,
        )

    assert result is not None
    assert result["stated_window_raw"] == "I will be ready in September"
    assert result["confidence"] == "high"
    assert result["target_date"].year == 2026
    assert result["target_date"].month == 9
    assert result["target_date"].day == 1

    assert mock_db.add.called
    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, BuyerReengagementSchedule)
    assert added_obj.buyer_id == buyer_id
    assert added_obj.deal_id == deal_id
    assert added_obj.stated_window_raw == "I will be ready in September"
    assert added_obj.target_date.year == 2026
    assert added_obj.target_date.month == 9
    assert added_obj.status == "waiting"


@pytest.mark.asyncio
async def test_detect_three_months_window():
    """'Check back in 3 months' should set target_date ~90 days from now."""
    from app.services.reply_processor import detect_future_buying_window
    from app.models.schemas import BuyerReengagementSchedule

    mock_db = AsyncMock()
    mock_db.add.return_value = None
    mock_db.flush.return_value = None

    mock_groq_response = MockGroqResponse(
        '{"has_future_signal": true, "stated_window_raw": "check back in 3 months", '
        '"target_date": null, "target_month": null, "confidence": "medium"}'
    )

    buyer_id = uuid.uuid4()

    with patch(
        "app.services.reply_processor.groq_chat_completion",
        AsyncMock(return_value=mock_groq_response),
    ):
        result = await detect_future_buying_window(
            reply_body="check back in 3 months",
            buyer_id=buyer_id,
            db=mock_db,
        )

    assert result is not None
    assert result["confidence"] == "medium"
    now = datetime.now(timezone.utc)
    expected_min = now + timedelta(days=60)
    expected_max = now + timedelta(days=120)
    assert expected_min <= result["target_date"] <= expected_max, (
        f"Expected target_date between {expected_min} and {expected_max}, "
        f"got {result['target_date']}"
    )

    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, BuyerReengagementSchedule)


@pytest.mark.asyncio
async def test_vague_signal_no_record():
    """'maybe later' with low confidence should NOT create a record."""
    from app.services.reply_processor import detect_future_buying_window

    mock_db = AsyncMock()

    mock_groq_response = MockGroqResponse(
        '{"has_future_signal": true, "stated_window_raw": "maybe later", '
        '"target_date": null, "target_month": null, "confidence": "low"}'
    )

    with patch(
        "app.services.reply_processor.groq_chat_completion",
        AsyncMock(return_value=mock_groq_response),
    ):
        result = await detect_future_buying_window(
            reply_body="maybe later",
            buyer_id=uuid.uuid4(),
            db=mock_db,
        )

    assert result is None
    assert not mock_db.add.called


@pytest.mark.asyncio
async def test_no_signal_no_record():
    """Reply with no future signal should return None and create no record."""
    from app.services.reply_processor import detect_future_buying_window

    mock_db = AsyncMock()

    mock_groq_response = MockGroqResponse(
        '{"has_future_signal": false}'
    )

    with patch(
        "app.services.reply_processor.groq_chat_completion",
        AsyncMock(return_value=mock_groq_response),
    ):
        result = await detect_future_buying_window(
            reply_body="I'm interested, let's do this deal.",
            buyer_id=uuid.uuid4(),
            db=mock_db,
        )

    assert result is None
    assert not mock_db.add.called


@pytest.mark.asyncio
async def test_empty_body_no_call():
    """Empty reply body should skip Groq call entirely."""
    from app.services.reply_processor import detect_future_buying_window

    mock_groq = AsyncMock()

    with patch(
        "app.services.reply_processor.groq_chat_completion",
        mock_groq,
    ):
        result = await detect_future_buying_window(
            reply_body="",
            buyer_id=uuid.uuid4(),
            db=AsyncMock(),
        )

    assert result is None
    mock_groq.assert_not_called()


# ==========================================================================
# fire_buyer_reengagements tests
# ==========================================================================


def _make_async_session_mock(db_mock: MagicMock) -> MagicMock:
    """Create a mock for `_db.async_session_factory()` that returns a session.

    The pattern is:
        async with _db.async_session_factory() as db:

    This helper builds the nested mock chain to make that work.
    """
    session_mock = MagicMock()
    session_mock.__aenter__ = AsyncMock(return_value=db_mock)
    session_mock.__aexit__ = AsyncMock(return_value=None)
    factory_mock = MagicMock(return_value=session_mock)
    return factory_mock


@pytest.mark.asyncio
async def test_fire_skips_inactive_buyer():
    """fire_buyer_reengagements should skip buyers who are inactive."""
    from app.services.scheduler import fire_buyer_reengagements
    from app.models.schemas import Buyer, BuyerReengagementSchedule, Deal

    buyer_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Mock due schedule
    mock_schedule = MagicMock(spec=BuyerReengagementSchedule)
    mock_schedule.id = schedule_id
    mock_schedule.buyer_id = buyer_id
    mock_schedule.deal_id = deal_id
    mock_schedule.stated_window_raw = "I will be ready in September"
    mock_schedule.target_date = now - timedelta(days=1)
    mock_schedule.status = "waiting"

    # Mock unsubscribed buyer
    mock_buyer = MagicMock(spec=Buyer)
    mock_buyer.id = buyer_id
    mock_buyer.email = "buyer@test.com"
    mock_buyer.full_name = "Test Buyer"
    mock_buyer.unsubscribed_at = now
    mock_buyer.status = "Do Not Contact"

    # Build mock DB
    mock_db = MagicMock()

    # Mock `db.execute().scalars().all()` → [mock_schedule]
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_schedule]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_db.execute = AsyncMock(return_value=mock_result)

    # Mock `db.get(Buyer, ...)` → inactive buyer
    async def mock_get(model, pk):
        if model == Buyer and pk == buyer_id:
            return mock_buyer
        if model == Deal and pk == deal_id:
            return None
        return None

    mock_db.get = mock_get

    factory = _make_async_session_mock(mock_db)

    with patch("app.services.scheduler._db.async_session_factory", factory):
        count = await fire_buyer_reengagements()

    assert count == 0
    assert mock_schedule.status == "cancelled"
    assert mock_schedule.cancellation_reason == "buyer_inactive"


@pytest.mark.asyncio
async def test_fire_creates_no_deal_found():
    """fire_buyer_reengagements should mark 'no_deal_found' when no active deal exists."""
    from app.services.scheduler import fire_buyer_reengagements
    from app.models.schemas import Buyer, BuyerReengagementSchedule, Deal

    buyer_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Mock due schedule
    mock_schedule = MagicMock(spec=BuyerReengagementSchedule)
    mock_schedule.id = schedule_id
    mock_schedule.buyer_id = buyer_id
    mock_schedule.deal_id = deal_id
    mock_schedule.stated_window_raw = "I will be ready in September"
    mock_schedule.target_date = now - timedelta(days=1)
    mock_schedule.status = "waiting"

    # Mock active buyer
    mock_buyer = MagicMock(spec=Buyer)
    mock_buyer.id = buyer_id
    mock_buyer.email = "buyer@test.com"
    mock_buyer.full_name = "Test Buyer"
    mock_buyer.unsubscribed_at = None
    mock_buyer.status = "Active"
    mock_buyer.buy_box = "Looking for 3BR houses"
    mock_buyer.buyer_tier = "B-List"

    # Build mock DB
    mock_db = MagicMock()

    # First execute() returns the due schedules
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_schedule]
    result1 = MagicMock()
    result1.scalars.return_value = mock_scalars

    # Second execute() is for finding alternative deal — returns None
    result2 = MagicMock()
    result2.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(side_effect=[result1, result2])

    async def mock_get(model, pk):
        # Original deal (from schedule.deal_id) returns None (not available)
        if model == Buyer and pk == buyer_id:
            return mock_buyer
        return None

    mock_db.get = mock_get

    factory = _make_async_session_mock(mock_db)

    with patch("app.services.scheduler._db.async_session_factory", factory):
        count = await fire_buyer_reengagements()

    assert count == 0
    assert mock_schedule.status == "no_deal_found"


@pytest.mark.asyncio
async def test_fire_queues_at_cap():
    """fire_buyer_reengagements should queue as QueuedDealMatch when buyer is at 2-deal cap."""
    from app.services.scheduler import fire_buyer_reengagements
    from app.models.schemas import Buyer, BuyerReengagementSchedule, Deal, QueuedDealMatch

    buyer_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_schedule = MagicMock(spec=BuyerReengagementSchedule)
    mock_schedule.id = schedule_id
    mock_schedule.buyer_id = buyer_id
    mock_schedule.deal_id = deal_id
    mock_schedule.stated_window_raw = "ready in Sept"
    mock_schedule.target_date = now - timedelta(days=1)
    mock_schedule.status = "waiting"

    mock_buyer = MagicMock(spec=Buyer)
    mock_buyer.id = buyer_id
    mock_buyer.email = "buyer@test.com"
    mock_buyer.full_name = "Test Buyer"
    mock_buyer.unsubscribed_at = None
    mock_buyer.status = "Active"
    mock_buyer.buy_box = "Looking for 3BR houses"
    mock_buyer.buyer_tier = "B-List"

    mock_deal = MagicMock(spec=Deal)
    mock_deal.id = deal_id
    mock_deal.address = "123 Test St"
    mock_deal.status = "Available"

    mock_db = MagicMock()

    # Execute returns:
    # 1. BuyerReengagementSchedule query → scalars().all() → [mock_schedule]
    # 2. Campaign existence check → scalar_one_or_none() → None (no existing campaign)
    # 3. QueuedDealMatch check → scalar_one_or_none() → None (not already queued)
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_schedule]

    result1 = MagicMock()
    result1.scalars.return_value = mock_scalars

    result2 = MagicMock()
    result2.scalar_one_or_none.return_value = None

    result3 = MagicMock()
    result3.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(side_effect=[result1, result2, result3])

    async def mock_get(model, pk):
        if model == Buyer and pk == buyer_id:
            return mock_buyer
        if model == Deal and pk == deal_id:
            return mock_deal
        return None

    mock_db.get = mock_get

    factory = _make_async_session_mock(mock_db)

    with patch("app.services.scheduler._db.async_session_factory", factory):
        with patch(
            "app.services.matching_service.get_active_deal_count_for_buyer",
            AsyncMock(return_value=2),
        ):
            count = await fire_buyer_reengagements()

    assert count == 0

    # Verify a QueuedDealMatch was added
    added_objs = []
    for call_args in mock_db.add.call_args_list:
        arg = call_args[0][0]
        added_objs.append(arg)
    assert any(isinstance(obj, QueuedDealMatch) for obj in added_objs)


@pytest.mark.asyncio
async def test_idempotency_skip_existing_campaign():
    """Running twice should only fire once — existing campaign check catches it."""
    from app.services.scheduler import fire_buyer_reengagements
    from app.models.schemas import Buyer, BuyerReengagementSchedule, Deal, Campaign

    buyer_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_schedule = MagicMock(spec=BuyerReengagementSchedule)
    mock_schedule.id = schedule_id
    mock_schedule.buyer_id = buyer_id
    mock_schedule.deal_id = deal_id
    mock_schedule.stated_window_raw = "ready in Sept"
    mock_schedule.target_date = now - timedelta(days=1)
    mock_schedule.status = "waiting"

    mock_buyer = MagicMock(spec=Buyer)
    mock_buyer.id = buyer_id
    mock_buyer.email = "buyer@test.com"
    mock_buyer.full_name = "Buyer"
    mock_buyer.unsubscribed_at = None
    mock_buyer.status = "Active"
    mock_buyer.buy_box = "Looking for 3BR"
    mock_buyer.buyer_tier = "B-List"

    mock_deal = MagicMock(spec=Deal)
    mock_deal.id = deal_id
    mock_deal.address = "123 Test St"
    mock_deal.city = "Test City"
    mock_deal.state = "TS"
    mock_deal.property_type = "House"
    mock_deal.arv = 250000
    mock_deal.asking_price = 200000
    mock_deal.contract_price = 160000
    mock_deal.floor_price = 180000
    mock_deal.spread = 40000
    mock_deal.condition_description = "Good condition"
    mock_deal.beds = 3
    mock_deal.baths = 2
    mock_deal.sqft = 1500
    mock_deal.status = "Available"

    mock_existing_campaign = MagicMock(spec=Campaign)

    mock_db = MagicMock()

    # Execute returns for each query:
    # Call 1: due schedules query → [mock_schedule]
    # Call 2: existing campaign check → mock_existing_campaign found
    scalars_all = MagicMock()
    scalars_all.all.return_value = [mock_schedule]
    first_result = MagicMock()
    first_result.scalars.return_value = scalars_all

    scalar_one = MagicMock()
    scalar_one.scalar_one_or_none.return_value = mock_existing_campaign

    # We need to return different results for different execute calls
    mock_db.execute = AsyncMock()
    mock_db.execute.side_effect = [first_result, scalar_one]

    async def mock_get(model, pk):
        if model == Buyer and pk == buyer_id:
            return mock_buyer
        if model == Deal and pk == deal_id:
            return mock_deal
        return None

    mock_db.get = mock_get

    factory = _make_async_session_mock(mock_db)

    with patch("app.services.scheduler._db.async_session_factory", factory):
        count = await fire_buyer_reengagements()

    assert count == 0
    assert mock_schedule.status == "cancelled"
    assert mock_schedule.cancellation_reason == "campaign_already_exists"
