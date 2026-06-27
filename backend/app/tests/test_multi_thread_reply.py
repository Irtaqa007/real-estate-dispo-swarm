"""Unit tests for the multi-thread reply matching feature.

Tests:
1. Header matching correctly identifies campaign from Message-ID in In-Reply-To header
2. Subject matching correctly identifies deal from address in subject line
3. Body matching correctly identifies deal from address mentioned in body
4. Fallback fires when all 3 methods fail and logs warning
5. load_buyer_full_context returns correct structure for a buyer with 2 active deals
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.models.models import Buyer, Campaign, Deal


# ===========================================================================
# Fake async database session
# ===========================================================================


class FakeAsyncSession:
    """A fake async DB session returning pre-configured results."""

    def __init__(self):
        self._execute_results = []
        self._execute_index = 0
        self._scalar_results = []
        self._scalar_index = 0
        self._get_results = {}
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
        return self._get_results.get((model, id_))

    def add(self, obj):
        self.add_called = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass

    async def close(self):
        pass

    async def flush(self):
        pass

    def with_execute_results(self, *results):
        self._execute_results = list(results)
        self._execute_index = 0
        return self

    def with_scalar_results(self, *results):
        self._scalar_results = list(results)
        self._scalar_index = 0
        return self

    def with_get_result(self, model_cls, id_, result):
        self._get_results[(model_cls, id_)] = result
        return self


# ===========================================================================
# Factory helpers
# ===========================================================================


def make_campaign(
    campaign_id=None,
    buyer_id=None,
    deal_id=None,
    touch_number=1,
    status="Sent",
    sent_at=None,
):
    camp = MagicMock(spec=Campaign)
    camp.id = campaign_id or uuid.uuid4()
    camp.deal_id = deal_id or uuid.uuid4()
    camp.buyer_id = buyer_id or uuid.uuid4()
    camp.touch_number = touch_number
    camp.status = status
    camp.sent_at = sent_at or datetime.now(timezone.utc) - timedelta(hours=1)
    camp.subject = f"Touch {touch_number}"
    camp.body = f"Body for touch {touch_number}"
    return camp


def make_deal(deal_id=None, address="123 Test St", city="TestCity", state="TS", zip_code="12345", property_type="House", asking_price=200000):
    deal = MagicMock(spec=Deal)
    deal.id = deal_id or uuid.uuid4()
    deal.address = address
    deal.city = city
    deal.state = state
    deal.zip = zip_code
    deal.property_type = property_type
    deal.asking_price = asking_price
    deal.status = "Available"
    return deal


def make_buyer(buyer_id=None, email="buyer@example.com"):
    buyer = MagicMock(spec=Buyer)
    buyer.id = buyer_id or uuid.uuid4()
    buyer.email = email
    buyer.full_name = "Test Buyer"
    buyer.buy_box = "Looking for 3-4 bed houses under $300k"
    return buyer


# ===========================================================================
# Test: Header matching
# ===========================================================================


@pytest.mark.asyncio
async def test_header_matching_identifies_campaign():
    """Header matching should identify campaign from Message-ID in In-Reply-To."""
    from app.services.reply_processor import match_reply_to_campaign

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    campaign_id = uuid.uuid4()

    campaign = make_campaign(
        campaign_id=campaign_id,
        buyer_id=buyer_id,
        deal_id=deal_id,
    )

    session = FakeAsyncSession()
    session.with_get_result(Campaign, campaign_id, campaign)

    reply = {
        "subject": "Re: 123 Test St",
        "body": "I'm interested!",
        "from_email": "buyer@example.com",
        "headers": {
            "In-Reply-To": f"<campaign-{campaign_id}@dispo.local>",
            "References": f"<campaign-{campaign_id}@dispo.local> <some-other@email.com>",
        },
    }

    matched_campaign, confidence = await match_reply_to_campaign(session, buyer_id, reply)

    assert confidence == "header"
    assert matched_campaign.id == campaign_id


@pytest.mark.asyncio
async def test_header_matching_from_references():
    """Header matching should work from References header when In-Reply-To is empty."""
    from app.services.reply_processor import match_reply_to_campaign

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    campaign_id = uuid.uuid4()

    campaign = make_campaign(
        campaign_id=campaign_id,
        buyer_id=buyer_id,
        deal_id=deal_id,
    )

    session = FakeAsyncSession()
    session.with_get_result(Campaign, campaign_id, campaign)

    reply = {
        "subject": "Re: 123 Test St",
        "body": "I'm interested!",
        "from_email": "buyer@example.com",
        "headers": {
            "In-Reply-To": "",
            "References": f"<campaign-{campaign_id}@dispo.local>",
        },
    }

    matched_campaign, confidence = await match_reply_to_campaign(session, buyer_id, reply)

    assert confidence == "header"
    assert matched_campaign.id == campaign_id


@pytest.mark.asyncio
async def test_header_matching_wrong_buyer_ignored():
    """Header match should be ignored if campaign belongs to a different buyer."""
    from app.services.reply_processor import match_reply_to_campaign

    buyer_id = uuid.uuid4()
    wrong_buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    campaign_id = uuid.uuid4()

    campaign = make_campaign(
        campaign_id=campaign_id,
        buyer_id=wrong_buyer_id,  # Different buyer!
        deal_id=deal_id,
    )

    deal = make_deal(deal_id=deal_id)

    session = FakeAsyncSession()
    session.with_get_result(Campaign, campaign_id, campaign)
    session.with_get_result(Deal, deal_id, deal)

    # Mock execute to return the campaign for method 2/3/4 fallback
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [campaign]
    session._execute_results = [result_mock]

    reply = {
        "subject": "Re: Something else",
        "body": "Hello",
        "from_email": "buyer@example.com",
        "headers": {
            "In-Reply-To": f"<campaign-{campaign_id}@dispo.local>",
            "References": "",
        },
    }

    # Should fall through since campaign doesn't belong to buyer_id
    matched_campaign, confidence = await match_reply_to_campaign(session, buyer_id, reply)

    # Since the campaign doesn't match by buyer, and there are no other campaigns,
    # it should fallback
    assert confidence == "fallback"
    assert matched_campaign.id == campaign_id


# ===========================================================================
# Test: Subject matching
# ===========================================================================


@pytest.mark.asyncio
async def test_subject_matching_identifies_deal():
    """Subject matching should identify deal from address in subject line."""
    from app.services.reply_processor import match_reply_to_campaign

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    campaign_id = uuid.uuid4()

    campaign = make_campaign(
        campaign_id=campaign_id,
        buyer_id=buyer_id,
        deal_id=deal_id,
    )
    deal = make_deal(deal_id=deal_id, address="1234 Oak Avenue")

    session = FakeAsyncSession()
    session.with_get_result(Campaign, campaign_id, campaign)  # For method 1 header match (will fail)
    session.with_get_result(Deal, deal_id, deal)  # For method 2 subject match

    # Mock execute for active campaigns query
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [campaign]
    session._execute_results = [result_mock]

    reply = {
        "subject": "RE: 1234 Oak Avenue - great deal!",
        "body": "Tell me more",
        "from_email": "buyer@example.com",
        "headers": {
            "In-Reply-To": "",
            "References": "",
        },
    }

    matched_campaign, confidence = await match_reply_to_campaign(session, buyer_id, reply)

    assert confidence == "subject"
    assert matched_campaign.id == campaign_id


# ===========================================================================
# Test: Body matching
# ===========================================================================


@pytest.mark.asyncio
async def test_body_matching_identifies_deal():
    """Body matching should identify deal from address mentioned in body."""
    from app.services.reply_processor import match_reply_to_campaign

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    campaign_id = uuid.uuid4()

    campaign = make_campaign(
        campaign_id=campaign_id,
        buyer_id=buyer_id,
        deal_id=deal_id,
    )
    deal = make_deal(deal_id=deal_id, address="789 Pine Road", city="Dallas", property_type="House", asking_price=250000)

    session = FakeAsyncSession()
    session.with_get_result(Campaign, campaign_id, campaign)  # For method 1 header match (will fail)
    session.with_get_result(Deal, deal_id, deal)  # For method 2/3

    # Mock execute for active campaigns query
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [campaign]
    session._execute_results = [result_mock]

    reply = {
        "subject": "Re: Question about properties",
        "body": "I was looking at 789 Pine Road in Dallas. Is this a House? Also the asking price of $250000 seems reasonable.",
        "from_email": "buyer@example.com",
        "headers": {
            "In-Reply-To": "",
            "References": "",
        },
    }

    matched_campaign, confidence = await match_reply_to_campaign(session, buyer_id, reply)

    assert confidence == "body"
    assert matched_campaign.id == campaign_id


# ===========================================================================
# Test: Fallback
# ===========================================================================


@pytest.mark.asyncio
async def test_fallback_fires_when_no_match():
    """Fallback should fire when all 3 methods fail and return most recent sent campaign."""
    from app.services.reply_processor import match_reply_to_campaign

    buyer_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    campaign_id = uuid.uuid4()

    campaign = make_campaign(
        campaign_id=campaign_id,
        buyer_id=buyer_id,
        deal_id=deal_id,
        status="Sent",
        sent_at=datetime.now(timezone.utc),
    )
    deal = make_deal(deal_id=deal_id, address="999 Unknown Lane", city="Nowhere")

    session = FakeAsyncSession()
    session.with_get_result(Deal, deal_id, deal)

    # Mock execute for active campaigns query
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [campaign]
    session._execute_results = [result_mock]

    reply = {
        "subject": "Hello there",
        "body": "Just checking in",
        "from_email": "buyer@example.com",
        "headers": {
            "In-Reply-To": "",
            "References": "",
        },
    }

    matched_campaign, confidence = await match_reply_to_campaign(session, buyer_id, reply)

    assert confidence == "fallback"
    assert matched_campaign.id == campaign_id


# ===========================================================================
# Test: load_buyer_full_context
# ===========================================================================


@pytest.mark.asyncio
async def test_load_buyer_full_context_two_active_deals():
    """load_buyer_full_context should return correct structure for buyer with 2 active deals."""
    from app.services.reply_processor import load_buyer_full_context

    buyer_id = uuid.uuid4()
    primary_deal_id = uuid.uuid4()
    other_deal_id = uuid.uuid4()

    buyer = make_buyer(buyer_id=buyer_id)
    primary_deal = make_deal(deal_id=primary_deal_id, address="100 Main St")
    other_deal = make_deal(deal_id=other_deal_id, address="200 Second Ave")

    # Create campaigns for both deals
    primary_camps = [
        make_campaign(buyer_id=buyer_id, deal_id=primary_deal_id, touch_number=1, sent_at=datetime.now(timezone.utc) - timedelta(days=10)),
        make_campaign(buyer_id=buyer_id, deal_id=primary_deal_id, touch_number=2, sent_at=datetime.now(timezone.utc) - timedelta(days=8)),
    ]
    other_camps = [
        make_campaign(buyer_id=buyer_id, deal_id=other_deal_id, touch_number=1, sent_at=datetime.now(timezone.utc) - timedelta(days=5)),
    ]
    all_campaigns = primary_camps + other_camps

    session = FakeAsyncSession()
    session.with_get_result(Buyer, buyer_id, buyer)
    session.with_get_result(Deal, primary_deal_id, primary_deal)
    session.with_get_result(Deal, other_deal_id, other_deal)

    # Mock execute for all campaigns query
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = all_campaigns
    session._execute_results = [result_mock]

    context = await load_buyer_full_context(session, buyer_id, primary_deal_id)

    assert context["buyer"] is not None
    assert context["primary_deal"].id == primary_deal_id
    assert len(context["primary_thread"]) == 2
    assert len(context["other_active_deals"]) == 1
    assert context["other_active_deals"][0]["deal"].id == other_deal_id
    assert len(context["other_active_deals"][0]["thread"]) == 1
    assert context["total_active_deals"] == 2


# ===========================================================================
# Test: No active campaigns returns None
# ===========================================================================


@pytest.mark.asyncio
async def test_match_reply_no_campaigns_returns_none():
    """match_reply_to_campaign should return None when buyer has no active campaigns."""
    from app.services.reply_processor import match_reply_to_campaign

    buyer_id = uuid.uuid4()

    session = FakeAsyncSession()

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    session._execute_results = [result_mock]

    reply = {
        "subject": "Re: Something",
        "body": "Body text",
        "from_email": "buyer@example.com",
        "headers": {
            "In-Reply-To": "",
            "References": "",
        },
    }

    matched_campaign, confidence = await match_reply_to_campaign(session, buyer_id, reply)

    assert matched_campaign is None
    assert confidence == "fallback"


# ===========================================================================
# Test: Multiple deals - subject picks correct one
# ===========================================================================


@pytest.mark.asyncio
async def test_subject_matching_picks_correct_deal_among_multiple():
    """Subject matching should pick the correct deal when buyer has multiple active deals."""
    from app.services.reply_processor import match_reply_to_campaign

    buyer_id = uuid.uuid4()
    deal_a_id = uuid.uuid4()
    deal_b_id = uuid.uuid4()
    camp_a_id = uuid.uuid4()
    camp_b_id = uuid.uuid4()

    camp_a = make_campaign(campaign_id=camp_a_id, buyer_id=buyer_id, deal_id=deal_a_id, sent_at=datetime.now(timezone.utc) - timedelta(days=3))
    camp_b = make_campaign(campaign_id=camp_b_id, buyer_id=buyer_id, deal_id=deal_b_id, sent_at=datetime.now(timezone.utc) - timedelta(days=1))

    deal_a = make_deal(deal_id=deal_a_id, address="555 Alpha Blvd", city="Austin")
    deal_b = make_deal(deal_id=deal_b_id, address="777 Beta Street", city="Houston")

    session = FakeAsyncSession()
    session.with_get_result(Deal, deal_a_id, deal_a)
    session.with_get_result(Deal, deal_b_id, deal_b)

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [camp_a, camp_b]
    session._execute_results = [result_mock]

    reply = {
        "subject": "Re: 555 Alpha Blvd - I want this one",
        "body": "Please send more details about this property",
        "from_email": "buyer@example.com",
        "headers": {
            "In-Reply-To": "",
            "References": "",
        },
    }

    matched_campaign, confidence = await match_reply_to_campaign(session, buyer_id, reply)

    assert confidence == "subject"
    assert matched_campaign.id == camp_a_id  # Should match deal A, not deal B
