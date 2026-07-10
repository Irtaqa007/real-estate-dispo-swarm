"""Comprehensive tests for the matching service.

Covers:
- MatchResult class
- find_top_matches_for_deal: no embedding, no candidates, full flow, capped buyers,
  hard filters (price, property type, geography), duplicate queue prevention, custom threshold
- get_active_deal_count_for_buyer: zero, one, two, multiple distinct deals
- process_queued_matches: no waiting, still capped, released, invalidated (various reasons)
- invalidate_queued_matches_for_buyer: found and invalidated, none to invalidate
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from uuid import UUID

from app.models.models import Buyer, Campaign, Deal, QueuedDealMatch
from app.services import matching_service as ms


# ---------------------------------------------------------------------------
# Fake row helpers — mimic SQLAlchemy result rows with named attributes
# ---------------------------------------------------------------------------

class FakeBuyerRow:
    """Mimics a SQL result row for a buyer candidate from the matching query."""
    def __init__(self, id=None, full_name="Test Buyer", email="test@example.com",
                 buy_box="Test buy box", affiliation="Test Co",
                 buyer_tier="C-List", similarity=0.85):
        self.id = id or uuid.uuid4()
        self.full_name = full_name
        self.email = email
        self.buy_box = buy_box
        self.affiliation = affiliation
        self.buyer_tier = buyer_tier
        self.similarity = similarity


class FakeCountRow:
    """Mimics a SQL result row for active deal counts."""
    def __init__(self, buyer_id, active_deal_count):
        self.buyer_id = buyer_id
        self.active_deal_count = active_deal_count


class FakeScalarResult:
    """Mimics a DB result that returns a single scalar via scalar_one_or_none()."""
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def fetchall(self):
        if self._value is None:
            return []
        if isinstance(self._value, list):
            return self._value
        return [self._value]

    def __iter__(self):
        if self._value is None:
            return iter([])
        if isinstance(self._value, list):
            return iter(self._value)
        return iter([self._value])


class FakeResult:
    """Mimics a DB result with fetchall() and iteration."""
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


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
def mock_deal(deal_id):
    """A mock Deal with an embedding and price/property/city data."""
    deal = MagicMock(spec=Deal)
    deal.id = deal_id
    deal.address = "123 Main St"
    deal.city = "Dallas"
    deal.state = "TX"
    deal.property_type = "House"
    deal.asking_price = 250000.0
    deal.deal_embedding = [0.1] * 1024
    return deal


@pytest.fixture
def mock_db():
    """A mock DB session with standard async methods."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.get = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_buyer(buyer_id):
    """A mock Buyer with structured filter fields."""
    buyer = MagicMock(spec=Buyer)
    buyer.id = buyer_id
    buyer.full_name = "Test Buyer"
    buyer.email = "test@example.com"
    buyer.buy_box = "Houses in Dallas under $300k"
    buyer.price_min = 100000.0
    buyer.price_max = 300000.0
    buyer.pref_property_type = "House"
    buyer.pref_cities = ["Dallas"]
    buyer.status = "Active"
    buyer.buy_box_embedding = [0.1] * 1024
    return buyer


@pytest.fixture
def mock_queued_match(buyer_id, deal_id):
    """A mock QueuedDealMatch in 'waiting' status."""
    match = MagicMock(spec=QueuedDealMatch)
    match.id = uuid.uuid4()
    match.buyer_id = buyer_id
    match.deal_id = deal_id
    match.status = "waiting"
    match.similarity_score = 0.85
    match.queued_at = datetime.now(timezone.utc)
    match.released_at = None
    return match


# ===========================================================================
# MatchResult class tests
# ===========================================================================

class TestMatchResult:

    def test_initialization(self, deal_id):
        from app.schemas import BuyerMatchResult
        match = BuyerMatchResult(
            id=uuid.uuid4(), full_name="Test", email="t@t.com",
            buy_box="test", similarity=0.9,
        )
        result = ms.MatchResult(
            deal_id=deal_id,
            deal_address="123 Main St",
            matches=[match],
            skipped_due_to_cap=1,
            queued_count=1,
        )
        assert result.deal_id == deal_id
        assert result.deal_address == "123 Main St"
        assert len(result.matches) == 1
        assert result.skipped_due_to_cap == 1
        assert result.queued_count == 1

    def test_defaults_zero(self, deal_id):
        result = ms.MatchResult(
            deal_id=deal_id,
            deal_address="456 Oak Ave",
            matches=[],
        )
        assert result.skipped_due_to_cap == 0
        assert result.queued_count == 0


# ===========================================================================
# find_top_matches_for_deal tests
# ===========================================================================

class TestFindTopMatchesForDeal:

    @pytest.mark.asyncio
    async def test_no_embedding_returns_empty(self, mock_db, deal_id):
        """When deal has no embedding, should return an empty MatchResult."""
        deal = MagicMock(spec=Deal)
        deal.id = deal_id
        deal.address = "123 Main St"
        deal.deal_embedding = None

        result = await ms.find_top_matches_for_deal(mock_db, deal)

        assert len(result.matches) == 0
        assert result.deal_id == deal_id
        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_candidates_after_filters(self, mock_db, mock_deal):
        """When the SQL returns no rows, should return empty MatchResult."""
        mock_db.execute = AsyncMock(return_value=FakeResult([]))

        with patch.object(ms.settings, "match_similarity_threshold", 0.65):
            result = await ms.find_top_matches_for_deal(mock_db, mock_deal, limit=20)

        assert len(result.matches) == 0
        assert result.skipped_due_to_cap == 0

    @pytest.mark.asyncio
    async def test_full_flow_with_eligible_buyers(self, mock_db, mock_deal):
        """Normal flow: multiple eligible buyers, some capped, correct matches returned.
        Uses the real QueuedDealMatch class (not a mock) — db.execute/db.add are mocked.
        """
        id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        candidate_rows = [
            FakeBuyerRow(id=id1, email="buyer1@test.com", similarity=0.92),
            FakeBuyerRow(id=id2, email="buyer2@test.com", similarity=0.85),
            FakeBuyerRow(id=id3, email="buyer3@test.com", similarity=0.78),
        ]
        count_rows = [
            FakeCountRow(buyer_id=id1, active_deal_count=2),  # Capped
            FakeCountRow(buyer_id=id2, active_deal_count=1),  # Eligible
            FakeCountRow(buyer_id=id3, active_deal_count=0),  # Eligible
        ]
        no_queue_result = FakeScalarResult(None)

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult(candidate_rows),  # Main SQL
            FakeResult(count_rows),      # Active counts SQL
            no_queue_result,             # Queue check for id1
        ])

        with patch.object(ms.settings, "match_similarity_threshold", 0.65):
            result = await ms.find_top_matches_for_deal(mock_db, mock_deal, limit=20)

        # Should return 2 eligible buyers (not the capped one)
        assert len(result.matches) == 2
        assert result.matches[0].id == id2  # Highest similarity among eligible
        assert result.matches[1].id == id3
        assert result.skipped_due_to_cap == 1
        assert result.queued_count == 1

        # Verify a QueuedDealMatch was added for the capped buyer
        added_matches = [call[0][0] for call in mock_db.add.call_args_list
                         if isinstance(call[0][0], QueuedDealMatch)]
        assert len(added_matches) == 1
        assert added_matches[0].buyer_id == id1
        assert added_matches[0].status == "waiting"

    @pytest.mark.asyncio
    async def test_duplicate_queue_prevention(self, mock_db, mock_deal):
        """If a buyer is already queued for the same deal, don't queue again."""
        id1 = uuid.uuid4()
        candidate_rows = [
            FakeBuyerRow(id=id1, email="buyer1@test.com", similarity=0.90),
        ]
        count_rows = [
            FakeCountRow(buyer_id=id1, active_deal_count=2),
        ]

        existing_match = MagicMock(spec=QueuedDealMatch)
        existing_match.id = uuid.uuid4()
        existing_match.status = "waiting"

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult(candidate_rows),
            FakeResult(count_rows),
            FakeScalarResult(existing_match),  # Already queued
        ])

        with patch.object(ms.settings, "match_similarity_threshold", 0.65):
            result = await ms.find_top_matches_for_deal(mock_db, mock_deal, limit=20)

        assert result.skipped_due_to_cap == 1
        assert result.queued_count == 1
        # No new QueuedDealMatch added — only existing match was found
        qdm_calls = [call for call in mock_db.add.call_args_list
                     if isinstance(call[0][0], QueuedDealMatch)]
        assert len(qdm_calls) == 0

    @pytest.mark.asyncio
    async def test_all_buyers_capped(self, mock_db, mock_deal):
        """When all buyers are at cap, return no matches but queue them all."""
        id1, id2 = uuid.uuid4(), uuid.uuid4()
        candidate_rows = [
            FakeBuyerRow(id=id1, email="b1@test.com", similarity=0.88),
            FakeBuyerRow(id=id2, email="b2@test.com", similarity=0.82),
        ]
        count_rows = [
            FakeCountRow(buyer_id=id1, active_deal_count=3),
            FakeCountRow(buyer_id=id2, active_deal_count=2),
        ]

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult(candidate_rows),
            FakeResult(count_rows),
            FakeScalarResult(None),  # Queue check for id1
            FakeScalarResult(None),  # Queue check for id2
        ])

        with patch.object(ms.settings, "match_similarity_threshold", 0.65):
            result = await ms.find_top_matches_for_deal(mock_db, mock_deal, limit=20)

        assert len(result.matches) == 0
        assert result.skipped_due_to_cap == 2
        assert result.queued_count == 2
        qdm_calls = [call for call in mock_db.add.call_args_list
                     if isinstance(call[0][0], QueuedDealMatch)]
        assert len(qdm_calls) == 2

    @pytest.mark.asyncio
    async def test_respects_limit(self, mock_db, mock_deal):
        """Should only return up to `limit` matches."""
        ids = [uuid.uuid4() for _ in range(5)]
        candidate_rows = [
            FakeBuyerRow(id=ids[i], email=f"b{i}@test.com", similarity=0.95 - i * 0.05)
            for i in range(5)
        ]
        count_rows = [
            FakeCountRow(buyer_id=ids[i], active_deal_count=0)
            for i in range(5)
        ]

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult(candidate_rows),
            FakeResult(count_rows),
        ])

        with patch.object(ms.settings, "match_similarity_threshold", 0.65):
            result = await ms.find_top_matches_for_deal(mock_db, mock_deal, limit=3)

        assert len(result.matches) == 3

    @pytest.mark.asyncio
    async def test_custom_threshold_passed_to_sql(self, mock_db, mock_deal):
        """Custom match_threshold should be passed as a SQL parameter."""
        mock_db.execute = AsyncMock(return_value=FakeResult([]))

        with patch.object(ms.settings, "match_similarity_threshold", 0.65):
            await ms.find_top_matches_for_deal(mock_db, mock_deal, limit=20, match_threshold=0.9)

        # Verify threshold was passed to the SQL execute call
        call_args = mock_db.execute.call_args
        assert call_args is not None
        _, params = call_args[0]
        assert params["threshold"] == 0.9

    @pytest.mark.asyncio
    async def test_default_threshold_from_settings(self, mock_db, mock_deal):
        """When no threshold given, should use settings.match_similarity_threshold."""
        mock_db.execute = AsyncMock(return_value=FakeResult([]))

        with patch.object(ms.settings, "match_similarity_threshold", 0.42):
            await ms.find_top_matches_for_deal(mock_db, mock_deal, limit=20)

        call_args = mock_db.execute.call_args
        assert call_args is not None
        _, params = call_args[0]
        assert params["threshold"] == 0.42

    @pytest.mark.asyncio
    async def test_buyer_with_no_structured_filters_passes_all(self, mock_db, mock_deal):
        """Buyer with NULL structured fields should pass all hard filters."""
        buyer_id = uuid.uuid4()
        candidate_rows = [
            FakeBuyerRow(id=buyer_id, email="nofilter@test.com", similarity=0.87),
        ]
        count_rows = [
            FakeCountRow(buyer_id=buyer_id, active_deal_count=0),
        ]

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult(candidate_rows),
            FakeResult(count_rows),
        ])

        with patch.object(ms.settings, "match_similarity_threshold", 0.65):
            result = await ms.find_top_matches_for_deal(mock_db, mock_deal, limit=20)

        assert len(result.matches) == 1
        assert result.matches[0].email == "nofilter@test.com"


# ===========================================================================
# get_active_deal_count_for_buyer tests
# ===========================================================================

class TestGetActiveDealCountForBuyer:

    @pytest.mark.asyncio
    async def test_zero_active_deals(self, mock_db, buyer_id):
        """Buyer with no active deals should return 0."""
        mock_db.execute.return_value = FakeResult([])
        count = await ms.get_active_deal_count_for_buyer(mock_db, buyer_id)
        assert count == 0

    @pytest.mark.asyncio
    async def test_one_active_deal(self, mock_db, buyer_id):
        """Buyer with one active deal should return 1."""
        mock_db.execute.return_value = FakeResult([MagicMock(deal_id=uuid.uuid4())])
        count = await ms.get_active_deal_count_for_buyer(mock_db, buyer_id)
        assert count == 1

    @pytest.mark.asyncio
    async def test_two_active_deals(self, mock_db, buyer_id):
        """Buyer with two active deals should return 2."""
        mock_db.execute.return_value = FakeResult([
            MagicMock(deal_id=uuid.uuid4()),
            MagicMock(deal_id=uuid.uuid4()),
        ])
        count = await ms.get_active_deal_count_for_buyer(mock_db, buyer_id)
        assert count == 2

    @pytest.mark.asyncio
    async def test_three_active_deals_exceeds_cap(self, mock_db, buyer_id):
        """More than 2 active deals should be caught by the cap check."""
        mock_db.execute.return_value = FakeResult([
            MagicMock(deal_id=uuid.uuid4()),
            MagicMock(deal_id=uuid.uuid4()),
            MagicMock(deal_id=uuid.uuid4()),
        ])
        count = await ms.get_active_deal_count_for_buyer(mock_db, buyer_id)
        assert count == 3
        assert count >= 2  # Would be excluded by cap


# ===========================================================================
# process_queued_matches tests
# ===========================================================================

class TestProcessQueuedMatches:

    @pytest.mark.asyncio
    async def test_no_waiting_matches(self, mock_db):
        """When no buyers have waiting matches, should return 0."""
        mock_db.execute.return_value = FakeResult([])
        count = await ms.process_queued_matches(mock_db)
        assert count == 0

    @pytest.mark.asyncio
    async def test_buyer_still_at_cap(self, mock_db, buyer_id):
        """Buyer still at 2+ active deals should not be released."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)
        mock_db.execute.return_value = FakeResult([waiting_buyer])

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=2)):
            count = await ms.process_queued_matches(mock_db)

        assert count == 0
        # Should not have fetched the oldest match (skipped due to cap)
        assert mock_db.get.await_count == 0

    @pytest.mark.asyncio
    async def test_buyer_released(self, mock_db, buyer_id, deal_id, mock_deal, mock_buyer, mock_queued_match):
        """Buyer below cap should have their oldest match released and campaign launched."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)

        # The oldest match query returns mock_queued_match
        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),    # 1st: waiting buyers
            match_result,                    # 2nd: oldest match (inside loop)
        ])
        mock_db.get = AsyncMock(side_effect=[mock_deal, mock_buyer])

        mock_launch = AsyncMock(return_value={
            "success": True, "touches_created": 6, "reason": "ok",
            "campaign_ids": [str(uuid.uuid4())] * 6,
        })

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=1)):
            with patch.object(ms, "launch_campaign_for_buyer", mock_launch):
                with patch.object(ms.logger, "info"):
                    count = await ms.process_queued_matches(mock_db)

        assert count == 1
        assert mock_queued_match.status == "released"
        assert mock_queued_match.released_at is not None
        mock_db.add.assert_called_with(mock_queued_match)
        mock_launch.assert_awaited_once()
        call_kwargs = mock_launch.call_args[1]
        assert call_kwargs["buyer"] == mock_buyer
        assert call_kwargs["deal"] == mock_deal
        assert call_kwargs["similarity_score"] == mock_queued_match.similarity_score

    @pytest.mark.asyncio
    async def test_invalidated_deal_gone(self, mock_db, buyer_id, mock_queued_match):
        """Queued match should be invalidated if deal no longer exists."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)

        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),
            match_result,
        ])
        mock_db.get = AsyncMock(return_value=None)  # Deal not found

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=1)):
            count = await ms.process_queued_matches(mock_db)

        assert count == 0
        assert mock_queued_match.status == "invalidated"
        mock_db.add.assert_called_with(mock_queued_match)

    @pytest.mark.asyncio
    async def test_invalidated_buyer_inactive(self, mock_db, buyer_id, deal_id, mock_deal, mock_queued_match):
        """Queued match should be invalidated if buyer is no longer active."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)
        inactive_buyer = MagicMock(spec=Buyer)
        inactive_buyer.status = "Do Not Contact"
        inactive_buyer.buy_box_embedding = None

        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),
            match_result,
        ])
        mock_db.get = AsyncMock(side_effect=[mock_deal, inactive_buyer])

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=1)):
            count = await ms.process_queued_matches(mock_db)

        assert count == 0
        assert mock_queued_match.status == "invalidated"

    @pytest.mark.asyncio
    async def test_invalidated_price_mismatch(self, mock_db, buyer_id, deal_id, mock_deal, mock_queued_match):
        """Queued match should be invalidated if deal price doesn't match buyer's range."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)
        buyer_too_low = MagicMock(spec=Buyer)
        buyer_too_low.id = buyer_id
        buyer_too_low.status = "Active"
        buyer_too_low.buy_box_embedding = [0.1] * 1024
        buyer_too_low.price_min = 500000.0  # Above deal price of 250k
        buyer_too_low.price_max = 1000000.0
        buyer_too_low.pref_property_type = "House"
        buyer_too_low.pref_cities = ["Dallas"]

        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),
            match_result,
        ])
        mock_db.get = AsyncMock(side_effect=[mock_deal, buyer_too_low])

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=1)):
            count = await ms.process_queued_matches(mock_db)

        assert count == 0
        assert mock_queued_match.status == "invalidated"

    @pytest.mark.asyncio
    async def test_invalidated_property_type_mismatch(self, mock_db, buyer_id, deal_id, mock_deal, mock_queued_match):
        """Queued match should be invalidated if property types don't match."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)
        buyer_land_only = MagicMock(spec=Buyer)
        buyer_land_only.id = buyer_id
        buyer_land_only.status = "Active"
        buyer_land_only.buy_box_embedding = [0.1] * 1024
        buyer_land_only.price_min = None
        buyer_land_only.price_max = None
        buyer_land_only.pref_property_type = "Land"  # Deal is House
        buyer_land_only.pref_cities = None

        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),
            match_result,
        ])
        mock_db.get = AsyncMock(side_effect=[mock_deal, buyer_land_only])

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=1)):
            count = await ms.process_queued_matches(mock_db)

        assert count == 0
        assert mock_queued_match.status == "invalidated"

    @pytest.mark.asyncio
    async def test_invalidated_city_mismatch(self, mock_db, buyer_id, deal_id, mock_deal, mock_queued_match):
        """Queued match should be invalidated if city not in buyer's preferences."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)
        buyer_different_city = MagicMock(spec=Buyer)
        buyer_different_city.id = buyer_id
        buyer_different_city.status = "Active"
        buyer_different_city.buy_box_embedding = [0.1] * 1024
        buyer_different_city.price_min = None
        buyer_different_city.price_max = None
        buyer_different_city.pref_property_type = None
        buyer_different_city.pref_cities = ["Austin"]  # Deal is in Dallas

        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),
            match_result,
        ])
        mock_db.get = AsyncMock(side_effect=[mock_deal, buyer_different_city])

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=1)):
            count = await ms.process_queued_matches(mock_db)

        assert count == 0
        assert mock_queued_match.status == "invalidated"

    @pytest.mark.asyncio
    async def test_multiple_buyers_some_released(self, mock_db):
        """Multiple buyers in queue: some get released, some stay capped."""
        buyer1_id, buyer2_id, buyer3_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        waiting_buyers = [
            MagicMock(buyer_id=buyer1_id),
            MagicMock(buyer_id=buyer2_id),
            MagicMock(buyer_id=buyer3_id),
        ]

        match2 = MagicMock(spec=QueuedDealMatch)
        match2.id = uuid.uuid4()
        match2.buyer_id = buyer2_id
        match2.deal_id = uuid.uuid4()
        match2.status = "waiting"
        match2.similarity_score = 0.85

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult(waiting_buyers),   # 1st: find waiting buyers
        ])

        mock_launch = AsyncMock(return_value={
            "success": True, "touches_created": 6, "reason": "ok",
            "campaign_ids": [str(uuid.uuid4())] * 6,
        })

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(side_effect=[2, 1, 3])):
            with patch.object(ms.logger, "info"):
                match2_result = MagicMock()
                match2_result.scalar_one_or_none.return_value = match2
                # Second execute call is for buyer2's oldest match query
                mock_db.execute = AsyncMock(side_effect=[
                    FakeResult(waiting_buyers),   # 1st: waiting buyers (reset)
                    match2_result,                 # 2nd: oldest match for buyer2
                ])

                deal2 = MagicMock(spec=Deal)
                deal2.id = match2.deal_id
                deal2.asking_price = 200000.0
                deal2.property_type = "House"
                deal2.city = "Dallas"

                buyer2 = MagicMock(spec=Buyer)
                buyer2.id = buyer2_id
                buyer2.status = "Active"
                buyer2.buy_box_embedding = [0.1] * 1024
                buyer2.price_min = None
                buyer2.price_max = None
                buyer2.pref_property_type = None
                buyer2.pref_cities = None

                mock_db.get = AsyncMock(side_effect=[deal2, buyer2])

                with patch.object(ms, "launch_campaign_for_buyer", mock_launch):
                    count = await ms.process_queued_matches(mock_db)

                assert count == 1
                assert match2.status == "released"
                assert match2.released_at is not None
                mock_launch.assert_awaited_once()


# ===========================================================================
# invalidate_queued_matches_for_buyer tests
# ===========================================================================

class TestInvalidateQueuedMatchesForBuyer:

    @pytest.mark.asyncio
    async def test_no_waiting_matches(self, mock_db, buyer_id):
        """When buyer has no waiting matches, should return 0."""
        mock_db.execute.return_value = FakeResult([])
        count = await ms.invalidate_queued_matches_for_buyer(mock_db, buyer_id)
        assert count == 0
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidates_waiting_matches(self, mock_db, buyer_id, mock_queued_match):
        """Should invalidate all waiting matches for the buyer."""
        mock_db.execute.return_value = FakeResult([mock_queued_match])
        count = await ms.invalidate_queued_matches_for_buyer(mock_db, buyer_id)
        assert count == 1
        assert mock_queued_match.status == "invalidated"
        mock_db.add.assert_called_once_with(mock_queued_match)

    @pytest.mark.asyncio
    async def test_does_not_commit(self, mock_db, buyer_id, mock_queued_match):
        """Should NOT call db.commit (caller is responsible)."""
        mock_db.execute.return_value = FakeResult([mock_queued_match])
        count = await ms.invalidate_queued_matches_for_buyer(mock_db, buyer_id)
        assert count == 1
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multiple_matches_invalidated(self, mock_db, buyer_id):
        """Should invalidate all waiting matches (not just one)."""
        match1 = MagicMock(spec=QueuedDealMatch)
        match1.id = uuid.uuid4()
        match1.status = "waiting"
        match2 = MagicMock(spec=QueuedDealMatch)
        match2.id = uuid.uuid4()
        match2.status = "waiting"
        match3 = MagicMock(spec=QueuedDealMatch)
        match3.id = uuid.uuid4()
        match3.status = "waiting"

        mock_db.execute.return_value = FakeResult([match1, match2, match3])
        count = await ms.invalidate_queued_matches_for_buyer(mock_db, buyer_id)
        assert count == 3
        assert match1.status == "invalidated"
        assert match2.status == "invalidated"
        assert match3.status == "invalidated"
        assert mock_db.add.call_count == 3

    @pytest.mark.asyncio
    async def test_non_waiting_matches_not_touched(self, mock_db, buyer_id):
        """Should only invalidate 'waiting' matches, not 'released' or 'invalidated'."""
        waiting = MagicMock(spec=QueuedDealMatch)
        waiting.id = uuid.uuid4()
        waiting.status = "waiting"

        mock_db.execute.return_value = FakeResult([waiting])
        count = await ms.invalidate_queued_matches_for_buyer(mock_db, buyer_id)
        assert count == 1
        assert waiting.status == "invalidated"


# ===========================================================================
# process_queued_matches + campaign launch integration tests
# ===========================================================================

class TestProcessQueuedMatchesCampaignLaunch:
    """Tests that verify campaign launcher is called on release."""

    @pytest.mark.asyncio
    async def test_campaign_launcher_called_on_release(self, mock_db, buyer_id, deal_id, mock_deal, mock_buyer, mock_queued_match):
        """When a match is released, launch_campaign_for_buyer should be called."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)
        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),
            match_result,
        ])
        mock_db.get = AsyncMock(side_effect=[mock_deal, mock_buyer])

        mock_launch = AsyncMock(return_value={
            "success": True, "touches_created": 6, "reason": "ok",
            "campaign_ids": [str(uuid.uuid4())] * 6,
        })

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=0)):
            with patch.object(ms, "launch_campaign_for_buyer", mock_launch):
                count = await ms.process_queued_matches(mock_db)

        assert count == 1
        mock_launch.assert_awaited_once()
        launch_args = mock_launch.call_args[1]
        assert launch_args["buyer"] == mock_buyer
        assert launch_args["deal"] == mock_deal

    @pytest.mark.asyncio
    async def test_campaign_launch_failure_keeps_match_waiting(self, mock_db, buyer_id, deal_id, mock_deal, mock_buyer, mock_queued_match):
        """If campaign launch raises, the match should stay 'waiting' for retry."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)
        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),
            match_result,
        ])
        mock_db.get = AsyncMock(side_effect=[mock_deal, mock_buyer])

        mock_launch = AsyncMock(side_effect=RuntimeError("Groq API timeout"))

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=1)):
            with patch.object(ms, "launch_campaign_for_buyer", mock_launch):
                count = await ms.process_queued_matches(mock_db)

        # Match stays 'waiting' — will retry next scheduler cycle
        assert count == 0
        assert mock_queued_match.status == "waiting"
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_campaign_launch_skipped_keeps_match_waiting(self, mock_db, buyer_id, deal_id, mock_deal, mock_buyer, mock_queued_match):
        """If campaign launch returns success=False (ineligible), match stays 'waiting'."""
        waiting_buyer = MagicMock(buyer_id=buyer_id)
        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = mock_queued_match

        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([waiting_buyer]),
            match_result,
        ])
        mock_db.get = AsyncMock(side_effect=[mock_deal, mock_buyer])

        mock_launch = AsyncMock(return_value={
            "success": False, "touches_created": 0,
            "reason": "fatigued: too many pitches",
            "campaign_ids": [],
        })

        with patch.object(ms, "get_active_deal_count_for_buyer", AsyncMock(return_value=1)):
            with patch.object(ms, "launch_campaign_for_buyer", mock_launch):
                count = await ms.process_queued_matches(mock_db)

        # Match stays 'waiting' — will retry next scheduler cycle
        assert count == 0
        assert mock_queued_match.status == "waiting"
        mock_launch.assert_awaited_once()
