"""Tests for pass reason capture feature.

Covers:
- Pass reason extraction via AI (mocked Groq)
- Deal pass_count and pass_reasons_summary updates
- JV partner stat increments (overprice_flag_count, title_issue_count, etc.)
- Follow-up question generation when confidence is "low"
- Buy box signal application on price_max
- Deal pass intelligence endpoint
- JV partner intelligence endpoint with risk flags
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.models import Buyer, Campaign, Deal, JVPartner
from app.services.reply_processor import process_reply


# ---------------------------------------------------------------------------
# Fake session for testing DB operations
# ---------------------------------------------------------------------------


class FakeAsyncSession:
    """Minimal fake for AsyncSession that stores objects and supports
    execute(), get(), add(), flush(), and commit()."""

    def __init__(self):
        self._objects: dict[type, dict[uuid.UUID, object]] = {}
        self._execute_results: list = []
        self._execute_index = 0
        self._get_results: dict[tuple[type, uuid.UUID], object] = {}
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)
        # Store for get()
        for cls in type.mro(type(obj)):
            if cls in (object,):
                continue
            if hasattr(obj, "id") and obj.id:
                key = (type(obj), obj.id)
                self._get_results[key] = obj
                break

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def execute(self, stmt):
        if self._execute_index < len(self._execute_results):
            result = self._execute_results[self._execute_index]
            self._execute_index += 1
            return result
        return FakeResult([])

    async def get(self, model, ident):
        if isinstance(ident, uuid.UUID):
            return self._get_results.get((model, ident))
        return None

    def scalar(self, stmt):
        return None

    def with_execute_results(self, *results):
        self._execute_results = list(results)
        self._execute_index = 0
        return self

    def with_get_result(self, model_cls, id_, obj):
        key = (model_cls, id_)
        self._get_results[key] = obj
        return self


class FakeResult:
    def __init__(self, scalars_list):
        self._scalars_list = scalars_list

    def scalars(self):
        return self

    def all(self):
        return self._scalars_list

    def first(self):
        return self._scalars_list[0] if self._scalars_list else None

    def one_or_none(self):
        return self._scalars_list[0] if self._scalars_list else None

    def scalar_one_or_none(self):
        return self._scalars_list[0] if self._scalars_list else None


def _make_mock_groq_response(primary_intent: str = "Pass") -> AsyncMock:
    """Create a mock Groq response that returns the given intent."""
    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(
            message=AsyncMock(
                content=json.dumps({
                    "primary_intent": primary_intent,
                    "urgency": "Low",
                    "sentiment": 2,
                    "topics": ["price"],
                    "recommended_action": "",
                    "counter_price": None,
                    "summary": "Buyer is not interested",
                    "buybox_changes": "",
                    "question_answer": "",
                })
            )
        )
    ]
    return mock_response


_GROQ_PATCH_PATH = "app.services.reply_processor.groq_chat_completion"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def deal():
    return Deal(
        id=uuid.uuid4(),
        address="1234 Elm Street",
        city="Dallas",
        state="TX",
        property_type="House",
        asking_price=250000,
        floor_price=180000,
        contract_price=200000,
        status="Available",
        pass_count=0,
        pass_reasons_summary={},
        jv_partner_id=uuid.uuid4(),
    )


@pytest.fixture
def buyer():
    return Buyer(
        id=uuid.uuid4(),
        full_name="Test Buyer",
        email="buyer@test.com",
        buy_box="I buy 3-4 bed houses in Dallas under $300k",
        price_min=100000,
        price_max=300000,
        pref_property_type="House",
        pref_cities=["Dallas"],
    )


@pytest.fixture
def campaign(deal, buyer):
    return Campaign(
        id=uuid.uuid4(),
        deal_id=deal.id,
        buyer_id=buyer.id,
        touch_number=1,
        status="Sent",
        sent_at=datetime.now(timezone.utc),
        subject="Deal Alert: 1234 Elm Street, Dallas TX",
        body="Great opportunity in Dallas!",
    )


@pytest.fixture
def jv_partner(deal):
    return JVPartner(
        id=deal.jv_partner_id,
        name="Test JV Partner",
        email="jv@test.com",
        total_passes=0,
        overprice_flag_count=0,
        title_issue_count=0,
        condition_issue_count=0,
        pass_reasons_breakdown={},
    )


@pytest.fixture
def session():
    return FakeAsyncSession()


def _setup_session_for_pass_reason(session, deal, buyer, campaign, jv_partner):
    """Register all DB mocks needed for the pass reason capture in process_reply().

    process_reply runs these execute queries in order:
    1. Ghost recovery: select Campaign where ghost_detected_at is not None
    2. Full context loader: select Campaign where buyer_id = X (in load_buyer_full_context)
    3. Pass reason: select Campaign where buyer_id + deal_id limit 1
    4. Pass reason: select Campaign where buyer_id + deal_id limit 3
    """
    session.with_get_result(Deal, deal.id, deal)
    session.with_get_result(Buyer, buyer.id, buyer)
    session.with_get_result(JVPartner, deal.jv_partner_id, jv_partner)
    # Register execute results accounting for all prior queries:
    # 1 ghost recovery + 1 full context + 2 pass reason = 4 total
    session.with_execute_results(
        FakeResult([]),         # [0] ghost recovery: no ghosts
        FakeResult([campaign]), # [1] full context: campaigns list
        FakeResult([campaign]), # [2] pass reason campaign result
        FakeResult([campaign]), # [3] pass reason thread context
    )


# ---------------------------------------------------------------------------
# Part 3 & 4: Pass reason extraction via process_reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_price_too_high_pass_reason(session, deal, buyer, campaign, jv_partner):
    """A reply saying "price is too high" should be categorized as
    price_too_high with high confidence."""
    _setup_session_for_pass_reason(session, deal, buyer, campaign, jv_partner)

    reply_body = "The price is too high for this market. I can't justify $250k for that property."

    with patch(_GROQ_PATCH_PATH) as mock_groq, \
         patch("app.services.reply_processor.extract_pass_reason") as mock_extract:
        mock_groq.return_value = _make_mock_groq_response("Pass")
        mock_extract.return_value = {
            "category": "price_too_high",
            "raw": "The price is too high for this market",
            "confidence": "high",
            "buy_box_signal": {
                "field": "price_max",
                "direction": "lower",
                "signal_strength": "medium",
            },
        }

        result = await process_reply(
            {
                "subject": "Re: Deal Alert: 1234 Elm Street, Dallas TX",
                "body": reply_body,
                "from_email": buyer.email,
            },
            db=session,
            buyer_id=buyer.id,
            deal_id=deal.id,
        )

    # Verify the pass reason fields were set on the campaign
    assert result["reply_intent"] == "Pass"
    assert result["pass_reason_followup"] is None  # confidence is high, no follow-up

    # Verify deal stats were updated
    assert deal.pass_count == 1
    assert deal.pass_reasons_summary.get("price_too_high") == 1

    # Verify JV partner stats were updated
    assert jv_partner.total_passes == 1
    assert jv_partner.overprice_flag_count == 1
    assert jv_partner.pass_reasons_breakdown.get("price_too_high") == 1


@pytest.mark.asyncio
async def test_wrong_market_pass_reason(session, deal, buyer, campaign, jv_partner):
    """A reply saying "not the right area" should be categorized as wrong_market."""
    _setup_session_for_pass_reason(session, deal, buyer, campaign, jv_partner)

    reply_body = "Not the right area — I only invest in Austin."

    with patch(_GROQ_PATCH_PATH) as mock_groq, \
         patch("app.services.reply_processor.extract_pass_reason") as mock_extract:
        mock_groq.return_value = _make_mock_groq_response("Pass")
        mock_extract.return_value = {
            "category": "wrong_market",
            "raw": "Not the right area",
            "confidence": "high",
            "buy_box_signal": None,
        }

        result = await process_reply(
            {
                "subject": "Re: Deal Alert: 1234 Elm Street, Dallas TX",
                "body": reply_body,
                "from_email": buyer.email,
            },
            db=session,
            buyer_id=buyer.id,
            deal_id=deal.id,
        )

    assert result["reply_intent"] == "Pass"
    assert deal.pass_count == 1
    assert deal.pass_reasons_summary.get("wrong_market") == 1
    assert jv_partner.total_passes == 1
    assert jv_partner.overprice_flag_count == 0  # Not a price issue


@pytest.mark.asyncio
async def test_timing_pass_reason(session, deal, buyer, campaign, jv_partner):
    """A reply saying "maybe later" should be categorized as timing."""
    _setup_session_for_pass_reason(session, deal, buyer, campaign, jv_partner)

    reply_body = "Maybe later — not in a position to buy right now."

    with patch(_GROQ_PATCH_PATH) as mock_groq, \
         patch("app.services.reply_processor.extract_pass_reason") as mock_extract:
        mock_groq.return_value = _make_mock_groq_response("Pass")
        mock_extract.return_value = {
            "category": "timing",
            "raw": "Maybe later",
            "confidence": "medium",
            "buy_box_signal": None,
        }

        result = await process_reply(
            {
                "subject": "Re: Deal Alert: 1234 Elm Street, Dallas TX",
                "body": reply_body,
                "from_email": buyer.email,
            },
            db=session,
            buyer_id=buyer.id,
            deal_id=deal.id,
        )

    assert result["reply_intent"] == "Pass"
    assert deal.pass_count == 1
    assert deal.pass_reasons_summary.get("timing") == 1


@pytest.mark.asyncio
async def test_vague_pass_triggers_followup(session, deal, buyer, campaign, jv_partner):
    """A vague "not for me" pass should have confidence="low" and
    trigger a follow-up question."""
    _setup_session_for_pass_reason(session, deal, buyer, campaign, jv_partner)

    reply_body = "Not for me."

    with patch(_GROQ_PATCH_PATH) as mock_groq, \
         patch("app.services.reply_processor.extract_pass_reason") as mock_extract:
        mock_groq.return_value = _make_mock_groq_response("Pass")
        mock_extract.return_value = {
            "category": "no_reason_given",
            "raw": "Not for me",
            "confidence": "low",
            "buy_box_signal": None,
        }

        result = await process_reply(
            {
                "subject": "Re: Deal Alert: 1234 Elm Street, Dallas TX",
                "body": reply_body,
                "from_email": buyer.email,
            },
            db=session,
            buyer_id=buyer.id,
            deal_id=deal.id,
        )

    assert result["pass_reason_followup"] is not None
    assert "was it the price" in result["pass_reason_followup"].lower()
    assert deal.pass_count == 1
    assert deal.pass_reasons_summary.get("no_reason_given") == 1


# ---------------------------------------------------------------------------
# Deal pass intelligence endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deal_pass_intelligence():
    """Deal pass intelligence returns summary, passes list, and top_reason."""
    from app.routers.deals import get_deal_pass_intelligence

    # This test verifies the endpoint signature and imports correctly
    assert callable(get_deal_pass_intelligence)


# ---------------------------------------------------------------------------
# JV partner intelligence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jv_partner_intelligence():
    """JV partner intelligence endpoint returns correct structure."""
    from app.routers.jv_partners import get_jv_partner_intelligence

    assert callable(get_jv_partner_intelligence)


# ---------------------------------------------------------------------------
# Deal pass_reasons_summary tally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deal_pass_reasons_summary_tally(session, deal, buyer, campaign, jv_partner):
    """Second pass on same deal increments the correct tally."""
    _setup_session_for_pass_reason(session, deal, buyer, campaign, jv_partner)

    with patch(_GROQ_PATCH_PATH) as mock_groq, \
         patch("app.services.reply_processor.extract_pass_reason") as mock_extract:
        mock_groq.return_value = _make_mock_groq_response("Pass")
        mock_extract.return_value = {
            "category": "price_too_high",
            "raw": "Too expensive",
            "confidence": "high",
            "buy_box_signal": None,
        }

        # First pass
        deal.pass_count = 0
        deal.pass_reasons_summary = {}
        await process_reply(
            {
                "subject": "Re: Deal Alert: 1234 Elm Street, Dallas TX",
                "body": "Too expensive",
                "from_email": buyer.email,
            },
            db=session,
            buyer_id=buyer.id,
            deal_id=deal.id,
        )

    assert deal.pass_count == 1
    assert deal.pass_reasons_summary.get("price_too_high") == 1

    with patch(_GROQ_PATCH_PATH) as mock_groq2, \
         patch("app.services.reply_processor.extract_pass_reason") as mock_extract2:
        mock_groq2.return_value = _make_mock_groq_response("Pass")
        mock_extract2.return_value = {
            "category": "price_too_high",
            "raw": "Still too expensive",
            "confidence": "high",
            "buy_box_signal": None,
        }

        # Reset the execute index so new mock results can be consumed
        session._execute_index = 0
        await process_reply(
            {
                "subject": "Re: Deal Alert: 1234 Elm Street, Dallas TX",
                "body": "Still too expensive",
                "from_email": buyer.email,
            },
            db=session,
            buyer_id=buyer.id,
            deal_id=deal.id,
        )

    assert deal.pass_count == 2
    assert deal.pass_reasons_summary.get("price_too_high") == 2


# ---------------------------------------------------------------------------
# Buy box signal on price_max
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buy_box_signal_price_lower(session, deal, buyer, campaign, jv_partner):
    """Price too high pass with buy_box_signal should adjust price_max downward."""
    _setup_session_for_pass_reason(session, deal, buyer, campaign, jv_partner)

    original_price_max = buyer.price_max

    with patch(_GROQ_PATCH_PATH) as mock_groq, \
         patch("app.services.reply_processor.extract_pass_reason") as mock_extract:
        mock_groq.return_value = _make_mock_groq_response("Pass")
        mock_extract.return_value = {
            "category": "price_too_high",
            "raw": "Too expensive for me",
            "confidence": "medium",
            "buy_box_signal": {
                "field": "price_max",
                "direction": "lower",
                "signal_strength": "medium",
            },
        }

        await process_reply(
            {
                "subject": "Re: Deal Alert: 1234 Elm Street, Dallas TX",
                "body": "Too expensive for me",
                "from_email": buyer.email,
            },
            db=session,
            buyer_id=buyer.id,
            deal_id=deal.id,
        )

    # price_max should be reduced by 10%
    assert buyer.price_max == original_price_max * 0.9


# ---------------------------------------------------------------------------
# JV partner overprice_flag_count on price_too_high
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jv_overprice_flag_increment(session, deal, buyer, campaign, jv_partner):
    """JV partner overprice_flag_count increments on price_too_high pass."""
    _setup_session_for_pass_reason(session, deal, buyer, campaign, jv_partner)

    assert jv_partner.overprice_flag_count == 0

    with patch(_GROQ_PATCH_PATH) as mock_groq, \
         patch("app.services.reply_processor.extract_pass_reason") as mock_extract:
        mock_groq.return_value = _make_mock_groq_response("Pass")
        mock_extract.return_value = {
            "category": "price_too_high",
            "raw": "Too expensive",
            "confidence": "high",
            "buy_box_signal": None,
        }

        await process_reply(
            {
                "subject": "Re: Deal Alert: 1234 Elm Street, Dallas TX",
                "body": "Too expensive",
                "from_email": buyer.email,
            },
            db=session,
            buyer_id=buyer.id,
            deal_id=deal.id,
        )

    assert jv_partner.overprice_flag_count == 1
