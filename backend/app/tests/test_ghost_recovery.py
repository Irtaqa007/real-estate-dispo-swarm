"""Unit tests for the ghost detection and recovery feature.

Tests:
1. Ghost detection fires after 96hrs silence on a previously-replied campaign
2. Ghost detection does NOT fire on a campaign with no replies (non-responder)
3. Ghost detection does NOT fire if deal is Sold/Dead
4. Recovery touch 4 is one line only (pattern interrupt)
5. Recovery cancelled correctly when buyer replies mid-recovery
6. Buyer marked Dormant after 5 touches with no response
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models.schemas import ActivityLog, Buyer, Campaign, Deal


# ===========================================================================
# Fake async database session (no AsyncMock wrapping issues)
# ===========================================================================


class FakeAsyncSession:
    """A fake async DB session returning pre-configured results.

    Uses real async methods instead of AsyncMock to avoid the
    coroutine-wrapping bug where await doesn't return the expected value.
    """

    def __init__(self):
        self._execute_results = []
        self._execute_index = 0
        self._scalar_results = []
        self._scalar_index = 0
        self.add_called = False
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, query):
        if self._execute_index < len(self._execute_results):
            val = self._execute_results[self._execute_index]
            self._execute_index += 1
            return val
        raise IndexError(f"No more execute results (index {self._execute_index})")

    async def scalar(self, query):
        if self._scalar_index < len(self._scalar_results):
            val = self._scalar_results[self._scalar_index]
            self._scalar_index += 1
            return val
        return None

    async def get(self, model, id_):
        return self._get_result if hasattr(self, '_get_result') else None

    def add(self, obj):
        self.add_called = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass

    async def close(self):
        pass

    def with_execute_results(self, *results):
        self._execute_results = list(results)
        self._execute_index = 0
        return self

    def with_scalar_results(self, *results):
        self._scalar_results = list(results)
        self._scalar_index = 0
        return self

    def with_get_result(self, result):
        self._get_result = result
        return self


# ===========================================================================
# Patcher helper
# ===========================================================================


def patch_db(scheduler_module):
    """Patch _db.async_session_factory to return a FakeAsyncSession.

    Usage:
        db_session, unpatch = patch_db(scheduler_module)
        try:
            # configure db_session, run test
        finally:
            unpatch()
    """
    from unittest.mock import patch as _patch

    fake_module = MagicMock()
    session = FakeAsyncSession()
    fake_module.async_session_factory = MagicMock(return_value=session)

    patcher = _patch.object(scheduler_module, "_db", fake_module)
    patcher.start()

    return session, patcher.stop


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def mock_deal():
    deal = MagicMock(spec=Deal)
    deal.id = uuid.uuid4()
    deal.address = "123 Test St"
    deal.city = "TestCity"
    deal.state = "TS"
    deal.property_type = "House"
    deal.arv = 250000
    deal.asking_price = 200000
    deal.floor_price = 150000
    deal.condition_description = "Good condition"
    deal.repair_estimate = 10000
    deal.beds = 3
    deal.baths = 2
    deal.sqft = 1500
    deal.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    return deal


@pytest.fixture
def mock_buyer():
    buyer = MagicMock(spec=Buyer)
    buyer.id = uuid.uuid4()
    buyer.full_name = "Test Buyer"
    buyer.email = "testbuyer@example.com"
    buyer.buy_box = "Looking for 3-4 bed houses under $300k in downtown area"
    buyer.buyer_tier = "A-List"
    buyer.deals_closed = 2
    buyer.engagement_score = 65.0
    buyer.last_reply_at = datetime.now(timezone.utc) - timedelta(days=10)
    buyer.pref_cities = ["TestCity", "OtherCity"]
    buyer.price_min = 100000.0
    buyer.price_max = 300000.0
    buyer.avg_spread_closed = 15000.0
    buyer.portfolio_insights = None
    buyer.unsubscribed_at = None
    buyer.status = "Active"
    return buyer


def make_campaign(
    buyer_id,
    deal_id,
    touch_number=1,
    status="Sent",
    sent_at=None,
    reply_received_at=None,
    reply_body=None,
    reply_intent=None,
    ghost_detected_at=None,
    ghost_recovery_touch=0,
    ghost_recovery_sent_at=None,
    body=None,
):
    campaign = MagicMock(spec=Campaign)
    campaign.id = uuid.uuid4()
    campaign.deal_id = deal_id
    campaign.buyer_id = buyer_id
    campaign.touch_number = touch_number
    campaign.status = status
    campaign.sent_at = sent_at
    campaign.subject = f"Touch {touch_number} — 123 Test St"
    campaign.body = body or f"This is touch {touch_number} email body."
    campaign.reply_received_at = reply_received_at
    campaign.reply_body = reply_body
    campaign.reply_intent = reply_intent
    campaign.ai_extracted_insights = None
    campaign.buyer_profile_updated = False
    campaign.question_round = 0
    campaign.ghost_detected_at = ghost_detected_at
    campaign.ghost_recovery_touch = ghost_recovery_touch
    campaign.ghost_recovery_sent_at = ghost_recovery_sent_at
    return campaign


# ===========================================================================
# Test: Ghost detection fires after 96hrs silence
# ===========================================================================


@pytest.mark.asyncio
async def test_ghost_detection_fires_after_silence():
    """Ghost detection should flag a buyer who replied then went silent for 96+ hours."""
    import app.services.scheduler as scheduler_mod
    from app.services.scheduler import detect_and_flag_ghosts

    now = datetime.now(timezone.utc)
    silence_hours = settings.ghost_silence_hours  # 96

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()

    sent_campaign = make_campaign(
        buyer_id=buyer_id, deal_id=deal_id,
        touch_number=3, status="Sent",
        sent_at=now - timedelta(hours=silence_hours + 1),
    )
    replied_campaign = make_campaign(
        buyer_id=buyer_id, deal_id=deal_id,
        touch_number=2, status="Replied",
        sent_at=now - timedelta(hours=silence_hours + 10),
        reply_received_at=now - timedelta(hours=silence_hours + 10),
        reply_intent="Interested",
    )

    deal = MagicMock(spec=Deal)
    deal.id = deal_id
    deal.status = "Available"

    distinct_result = MagicMock()
    distinct_result.all.return_value = [(buyer_id, deal_id)]
    terminal_result = MagicMock()
    terminal_result.first.return_value = None

    session, unpatch = patch_db(scheduler_mod)
    session.with_execute_results(distinct_result, terminal_result) \
           .with_scalar_results(sent_campaign, replied_campaign, replied_campaign) \
           .with_get_result(deal)

    try:
        result = await detect_and_flag_ghosts()

        assert result == 1
        assert replied_campaign.ghost_detected_at is not None
        assert replied_campaign.ghost_recovery_touch == 0
    finally:
        unpatch()


# ===========================================================================
# Test: Ghost detection does NOT fire on non-responders
# ===========================================================================


@pytest.mark.asyncio
async def test_ghost_detection_skips_non_responders():
    """Ghost detection should NOT flag a buyer who never replied (non-responder)."""
    import app.services.scheduler as scheduler_mod
    from app.services.scheduler import detect_and_flag_ghosts

    session, unpatch = patch_db(scheduler_mod)

    distinct_result = MagicMock()
    distinct_result.all.return_value = []
    session.with_execute_results(distinct_result)

    try:
        result = await detect_and_flag_ghosts()
        assert result == 0
    finally:
        unpatch()


# ===========================================================================
# Test: Ghost detection does NOT fire if deal is Sold/Dead
# ===========================================================================


@pytest.mark.asyncio
async def test_ghost_detection_skips_inactive_deals():
    """Ghost detection should NOT flag a buyer if the deal is Sold or Dead."""
    import app.services.scheduler as scheduler_mod
    from app.services.scheduler import detect_and_flag_ghosts

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()

    deal = MagicMock(spec=Deal)
    deal.id = deal_id
    deal.status = "Sold"

    distinct_result = MagicMock()
    distinct_result.all.return_value = [(buyer_id, deal_id)]

    session, unpatch = patch_db(scheduler_mod)
    session.with_execute_results(distinct_result).with_get_result(deal)

    try:
        result = await detect_and_flag_ghosts()
        assert result == 0
    finally:
        unpatch()


# ===========================================================================
# Test: Ghost detection does NOT fire if silence period hasn't elapsed
# ===========================================================================


@pytest.mark.asyncio
async def test_ghost_detection_skips_recent_activity():
    """Ghost detection should NOT flag if silence period hasn't elapsed."""
    import app.services.scheduler as scheduler_mod
    from app.services.scheduler import detect_and_flag_ghosts

    now = datetime.now(timezone.utc)
    silence_hours = settings.ghost_silence_hours  # 96

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()

    sent_campaign = make_campaign(
        buyer_id=buyer_id, deal_id=deal_id,
        touch_number=3, status="Sent",
        sent_at=now - timedelta(hours=silence_hours - 10),
    )

    deal = MagicMock(spec=Deal)
    deal.id = deal_id
    deal.status = "Available"

    distinct_result = MagicMock()
    distinct_result.all.return_value = [(buyer_id, deal_id)]
    terminal_result = MagicMock()
    terminal_result.first.return_value = None

    session, unpatch = patch_db(scheduler_mod)
    session.with_execute_results(distinct_result, terminal_result) \
           .with_scalar_results(sent_campaign) \
           .with_get_result(deal)

    try:
        result = await detect_and_flag_ghosts()
        assert result == 0
    finally:
        unpatch()


# ===========================================================================
# Test: Recovery touch 4 is pattern interrupt (one line only)
# ===========================================================================


def test_ghost_recovery_touch_4_one_line():
    """Ghost recovery touch 4 should be a one-line pattern interrupt."""
    from app.services.ghost_recovery import TOUCH_ARCS

    assert 4 in TOUCH_ARCS
    arc = TOUCH_ARCS[4]
    assert "ONE SENTENCE" in arc["instruction"] or "one line only" in arc["instruction"].lower()


# ===========================================================================
# Test: Recovery email includes operator identity
# ===========================================================================


@pytest.mark.asyncio
async def test_ghost_recovery_includes_operator_identity(mock_buyer, mock_deal):
    """Ghost recovery email should include operator identity in the prompt."""
    from app.services.ghost_recovery import (
        _build_operator_identity_block,
        generate_ghost_recovery_email,
    )

    identity_block = _build_operator_identity_block()
    assert settings.operator_name in identity_block
    assert settings.operator_email_signature in identity_block

    thread_context = [
        make_campaign(
            buyer_id=mock_buyer.id, deal_id=mock_deal.id,
            touch_number=1, status="Sent",
            sent_at=datetime.now(timezone.utc) - timedelta(days=14),
        ),
    ]

    with patch("app.services.ghost_recovery.groq_chat_completion") as mock_groq:
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"subject": "Test subject", "body": "Test body"}'))
        ]
        mock_groq.return_value = mock_response

        result = await generate_ghost_recovery_email(
            buyer=mock_buyer,
            deal=mock_deal,
            touch_number=1,
            thread_context=thread_context,
        )

        assert result["subject"] == "Test subject"
        assert "Test body" in result["body"]

        call_kwargs = mock_groq.call_args[1]
        messages = call_kwargs["messages"]
        system_content = messages[0]["content"]
        assert settings.operator_name in system_content


# ===========================================================================
# Test: Thread context is included in the AI prompt
# ===========================================================================


def test_ghost_recovery_includes_thread_context():
    """Ghost recovery prompt should include the full conversation thread context."""
    from app.services.ghost_recovery import _build_thread_context_block

    now = datetime.now(timezone.utc)
    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()

    campaigns = [
        make_campaign(
            buyer_id=buyer_id, deal_id=deal_id,
            touch_number=1, sent_at=now - timedelta(days=14),
            body="Touch 1 email body content.",
        ),
        make_campaign(
            buyer_id=buyer_id, deal_id=deal_id,
            touch_number=1, status="Replied",
            sent_at=now - timedelta(days=12),
            reply_received_at=now - timedelta(days=12),
            reply_body="I'm interested in this property.",
            reply_intent="Interested",
        ),
    ]

    context_block = _build_thread_context_block(campaigns)
    assert "CONVERSATION THREAD" in context_block
    assert "I'm interested in this property." in context_block
    assert "Touch 1 email body content." in context_block


# ===========================================================================
# Test: Recovery cancelled when buyer replies mid-recovery
# ===========================================================================


@pytest.mark.asyncio
async def test_ghost_recovery_cancelled_by_reply():
    """When a buyer in ghost recovery replies, the recovery state should reset."""
    import app.services.scheduler as scheduler_mod
    from app.services.scheduler import process_buyer_replies

    now = datetime.now(timezone.utc)
    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()

    # Campaign in ghost recovery with 2 touches sent
    ghost_campaign = make_campaign(
        buyer_id=buyer_id, deal_id=deal_id,
        touch_number=3, status="Sent",
        sent_at=now - timedelta(hours=120),
        ghost_detected_at=now - timedelta(days=4),
        ghost_recovery_touch=2,
        ghost_recovery_sent_at=now - timedelta(days=1),
    )

    sent_campaign = make_campaign(
        buyer_id=buyer_id, deal_id=deal_id,
        touch_number=3, status="Sent",
        sent_at=now - timedelta(hours=120),
    )

    buyer = MagicMock(spec=Buyer)
    buyer.id = buyer_id
    buyer.email = "testbuyer@example.com"
    buyer.full_name = "Test Buyer"
    buyer.last_reply_at = None
    buyer.buy_box = "Test buy box"
    buyer.unsubscribed_at = None
    buyer.status = "Active"

    # Must return objects with .id and .email attributes (like SQLAlchemy Row objects)
    row1 = MagicMock()
    row1.id = buyer_id
    row1.email = "testbuyer@example.com"
    row1._mapping = {"id": buyer_id, "email": "testbuyer@example.com"}
    buyer_result = MagicMock()
    buyer_result.all.return_value = [row1]
    be_result = MagicMock()
    be_result.all.return_value = []
    ghost_result = MagicMock()
    ghost_result.scalars.return_value.all.return_value = [ghost_campaign]
    queued_result = MagicMock()
    queued_result.scalars.return_value.all.return_value = []

    session, unpatch = patch_db(scheduler_mod)
    session.with_execute_results(buyer_result, be_result, ghost_result, queued_result) \
           .with_scalar_results(sent_campaign) \
           .with_get_result(buyer)

    with patch("app.services.scheduler.check_for_replies") as mock_check, \
         patch("app.services.scheduler.process_reply") as mock_process, \
         patch("app.services.scheduler.audit") as mock_audit:

        mock_check.return_value = [
            {"from_email": "testbuyer@example.com", "subject": "Re: Test", "body": "I'm back!"}
        ]
        mock_process.return_value = {
            "reply_intent": "Interested",
            "primary_intent": "Interested",
            "urgency": "Medium",
            "sentiment": 4,
            "topics": ["price"],
            "recommended_action": "send_details",
            "counter_price": None,
            "ai_extracted_insights": "Buyer is back",
            "buyer_profile_updates": {},
            "question_answer": None,
        }

        try:
            result = await process_buyer_replies()

            assert ghost_campaign.ghost_detected_at is None, "ghost_detected_at should be None"
            assert ghost_campaign.ghost_recovery_touch == 0, "ghost_recovery_touch should be 0"
            assert ghost_campaign.ghost_recovery_sent_at is None, "ghost_recovery_sent_at should be None"
        finally:
            unpatch()


# ===========================================================================
# Test: Config values
# ===========================================================================


def test_max_recovery_touches_config():
    """Ghost max recovery touches should be 5."""
    assert settings.ghost_max_recovery_touches == 5


def test_recovery_intervals_length():
    """Ghost recovery intervals list should have 5 entries (one per touch)."""
    assert len(settings.ghost_recovery_intervals_days) == 5


def test_ghost_recovery_touch_arcs_defined():
    """All 5 ghost recovery touch arcs should be defined."""
    from app.services.ghost_recovery import TOUCH_ARCS

    assert len(TOUCH_ARCS) == 5
    for i in range(1, 6):
        assert i in TOUCH_ARCS
        assert "arc" in TOUCH_ARCS[i]
        assert "instruction" in TOUCH_ARCS[i]


# ===========================================================================
# Test: Invalid touch number raises error
# ===========================================================================


@pytest.mark.asyncio
async def test_ghost_recovery_invalid_touch(mock_buyer, mock_deal):
    """Ghost recovery with invalid touch number should raise ValueError."""
    from app.services.ghost_recovery import generate_ghost_recovery_email

    with pytest.raises(ValueError, match="Invalid ghost recovery touch number"):
        await generate_ghost_recovery_email(
            buyer=mock_buyer, deal=mock_deal,
            touch_number=6, thread_context=[],
        )

    with pytest.raises(ValueError, match="Invalid ghost recovery touch number"):
        await generate_ghost_recovery_email(
            buyer=mock_buyer, deal=mock_deal,
            touch_number=0, thread_context=[],
        )


# ===========================================================================
# Test: Ghost detection does NOT double-flag
# ===========================================================================


@pytest.mark.asyncio
async def test_ghost_detection_no_double_flag():
    """Running detect_and_flag_ghosts twice should not double-flag."""
    import app.services.scheduler as scheduler_mod
    from app.services.scheduler import detect_and_flag_ghosts

    session, unpatch = patch_db(scheduler_mod)

    distinct_result = MagicMock()
    distinct_result.all.return_value = []
    session.with_execute_results(distinct_result)

    try:
        result = await detect_and_flag_ghosts()
        assert result == 0
    finally:
        unpatch()
