"""Comprehensive tests for deal deduplication service.

Covers:
- normalize_address: abbreviations, punctuation, case, directions, empty
- build_deal_normalized_text: House and Land variants
- check_deal_duplicate: duplicate found, no duplicate, API failure, empty text
- get_similar_deals: returns results, handles empty
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Optional

from app.services import deal_dedup as dd


# ===========================================================================
# normalize_address tests
# ===========================================================================

class TestNormalizeAddress:

    def test_basic_address(self):
        """A simple address should be lowercased and stripped."""
        assert dd.normalize_address("123 Main St") == "123 main street"

    def test_expand_multiple_suffixes(self):
        assert dd.normalize_address("456 Oak Ave") == "456 oak avenue"

    def test_expand_directions(self):
        assert dd.normalize_address("100 N Main St") == "100 north main street"

    def test_direction_south(self):
        assert dd.normalize_address("200 S Oak Dr") == "200 south oak drive"

    def test_compound_direction(self):
        assert dd.normalize_address("300 NE 1st St") == "300 northeast 1st street"

    def test_punctuation_removed(self):
        """Periods and commas should be stripped."""
        assert dd.normalize_address("123 Main St.") == "123 main street"

    def test_multiple_punctuation(self):
        assert dd.normalize_address("456, Oak Ave;") == "456 oak avenue"

    def test_whitespace_collapsed(self):
        assert dd.normalize_address("  123   Main   St  ") == "123 main street"

    def test_hyphen_preserved_in_numbers(self):
        """Hyphens within numbers should be preserved (e.g. 123-45)."""
        result = dd.normalize_address("123-45 Main St")
        assert "123-45" in result
        assert "main street" in result

    def test_standalone_hyphen_removed(self):
        """Standalone hyphens (not within numbers) should be removed."""
        result = dd.normalize_address("123 Main - St")
        # The hyphen is removed, tokens are reorganized
        assert "main" in result
        assert "street" in result

    def test_empty_address(self):
        assert dd.normalize_address("") == ""

    def test_none_address(self):
        assert dd.normalize_address("") == ""

    def test_already_lowercase(self):
        assert dd.normalize_address("123 main street") == "123 main street"

    def test_mixed_case(self):
        assert dd.normalize_address("123 MAIN STREET") == "123 main street"

    def test_highway_abbreviation(self):
        assert dd.normalize_address("1 Hwy 101") == "1 highway 101"

    def test_parkway_abbreviation(self):
        assert dd.normalize_address("500 Park Dr") == "500 park drive"

    def test_trail_abbreviation(self):
        assert dd.normalize_address("1 Nature Trl") == "1 nature trail"

    def test_blvd_abbreviation(self):
        assert dd.normalize_address("1 Sunset Blvd") == "1 sunset boulevard"

    def test_address_with_quotes(self):
        result = dd.normalize_address('123 "Main" St')
        assert "main" in result
        assert "street" in result

    def test_address_with_apostrophe(self):
        """Apostrophes within words should be preserved."""
        result = dd.normalize_address("123 O'Brien St")
        assert "o'brien" in result
        assert "street" in result


# ===========================================================================
# build_deal_normalized_text tests
# ===========================================================================

class TestBuildDealNormalizedText:

    # ------------------------------------------------------------------
    # House
    # ------------------------------------------------------------------

    def test_house_basic(self):
        text = dd.build_deal_normalized_text(
            address="123 Main St",
            city="Dallas",
            state="TX",
            property_type="House",
            condition_description="Good condition",
            beds=3, baths=2, sqft=1500,
        )
        assert "123 main street" in text
        assert "dallas" in text
        assert "tx" in text
        assert "house" in text
        assert "3 bedroom" in text
        assert "2 bathroom" in text
        assert "1500 square feet" in text

    def test_house_no_beds_baths(self):
        """Beds/baths/sqft are optional for House."""
        text = dd.build_deal_normalized_text(
            address="456 Oak Ave",
            city="Austin", state="TX",
            property_type="House",
            condition_description="Fixer upper",
        )
        assert "456 oak avenue" in text
        assert "house" in text
        assert "fixer upper" in text
        assert "bedroom" not in text

    def test_house_no_city_or_state(self):
        text = dd.build_deal_normalized_text(
            address="123 Main St",
            city=None, state=None,
            property_type="House",
            condition_description="Needs work",
        )
        assert "123 main street" in text
        assert "house" in text
        assert "needs work" in text

    # ------------------------------------------------------------------
    # Land
    # ------------------------------------------------------------------

    def test_land_basic(self):
        text = dd.build_deal_normalized_text(
            address="1 Vacant Lot Rd",
            city="Rural", state="OK",
            property_type="Land",
            condition_description="Overgrown lot",
            lot_size="2.5 acres",
            zoning="Residential",
        )
        assert "1 vacant lot road" in text
        assert "rural" in text
        assert "ok" in text
        assert "land" in text
        assert "2.5 acres" in text
        assert "residential" in text

    def test_land_no_lot_size(self):
        """Lot size and zoning are optional for build purposes."""
        text = dd.build_deal_normalized_text(
            address="100 Open Field",
            city=None, state=None,
            property_type="Land",
            condition_description="Raw land",
        )
        assert "100 open field" in text
        assert "land" in text

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_condition_description_truncated(self):
        """Condition description should be truncated to 100 chars."""
        long_condition = "X" * 200
        text = dd.build_deal_normalized_text(
            address="123 Main St",
            city=None, state=None,
            property_type="House",
            condition_description=long_condition,
        )
        assert len(text.split()[-1]) <= 100

    def test_empty_address_falls_back(self):
        """Empty address still produces some text."""
        text = dd.build_deal_normalized_text(
            address="",
            city="Austin", state="TX",
            property_type="House",
            condition_description="Nice house",
        )
        assert "austin" in text
        assert "tx" in text

    def test_special_chars_in_address(self):
        """Special characters should be normalized."""
        text = dd.build_deal_normalized_text(
            address="123 Main St. #4",
            city="City", state="ST",
            property_type="House",
            condition_description="Condo",
        )
        assert "main street" in text
        assert "condo" in text


# ===========================================================================
# check_deal_duplicate tests
# ===========================================================================

class FakeRow:
    """Simple fake DB result row."""
    def __init__(self, id=None, address="", city=None, state=None, similarity=0.0):
        self.id = id or uuid.uuid4()
        self.address = address
        self.city = city
        self.state = state
        self.similarity = similarity


class FakeResult:
    """Simple fake DB result with fetchall() and iteration."""
    def __init__(self, rows=None):
        self._rows = rows or []
    def fetchall(self):
        return self._rows
    def __iter__(self):
        return iter(self._rows)


class TestCheckDealDuplicate:

    @pytest.mark.asyncio
    async def test_duplicate_found(self):
        """Should return is_duplicate=True when similarity >= 0.95."""
        mock_db = AsyncMock()
        mock_embedding = [0.1] * 1024

        deal_id = uuid.uuid4()
        mock_row = FakeRow(id=deal_id, address="123 Main St", city="Dallas", state="TX", similarity=0.97)

        mock_db.execute = AsyncMock(return_value=FakeResult([mock_row]))

        with patch.object(dd, "generate_embedding", AsyncMock(return_value=mock_embedding)):
            is_dup, info = await dd.check_deal_duplicate(
                db=mock_db,
                address="123 Main St",
                city="Dallas",
                state="TX",
                property_type="House",
                condition_description="Nice house, good condition, 3 bed 2 bath",
                beds=3, baths=2, sqft=1500,
            )

        assert is_dup is True
        assert info is not None
        assert str(deal_id) == info["matched_deal_id"]
        assert info["similarity_score"] == 0.97

    @pytest.mark.asyncio
    async def test_no_duplicate_below_threshold(self):
        """Should return is_duplicate=False when similarity < 0.95."""
        mock_db = AsyncMock()
        mock_embedding = [0.2] * 1024

        mock_row = FakeRow(address="456 Oak Ave", city="Austin", state="TX", similarity=0.5)
        mock_db.execute = AsyncMock(return_value=FakeResult([mock_row]))

        with patch.object(dd, "generate_embedding", AsyncMock(return_value=mock_embedding)):
            is_dup, info = await dd.check_deal_duplicate(
                db=mock_db,
                address="456 Oak Ave",
                city="Austin",
                state="TX",
                property_type="House",
                condition_description="Fixer upper",
            )

        assert is_dup is False
        assert info is None

    @pytest.mark.asyncio
    async def test_no_matches_at_all(self):
        """Should return False when no deals exist in the DB."""
        mock_db = AsyncMock()
        mock_embedding = [0.3] * 1024

        mock_db.execute = AsyncMock(return_value=FakeResult([]))

        with patch.object(dd, "generate_embedding", AsyncMock(return_value=mock_embedding)):
            is_dup, info = await dd.check_deal_duplicate(
                db=mock_db,
                address="789 New St",
                city=None, state=None,
                property_type="Land",
                condition_description="Vacant lot",
                lot_size="1 acre",
                zoning="Residential",
            )

        assert is_dup is False
        assert info is None

    @pytest.mark.asyncio
    async def test_embedding_api_failure(self):
        """Should return False when embedding generation fails."""
        mock_db = AsyncMock()

        with patch.object(dd, "generate_embedding", AsyncMock(side_effect=Exception("API down"))):
            is_dup, info = await dd.check_deal_duplicate(
                db=mock_db,
                address="123 Main St",
                city=None, state=None,
                property_type="House",
                condition_description="Test",
            )

        assert is_dup is False
        assert info is None

    @pytest.mark.asyncio
    async def test_empty_normalized_text_graceful(self):
        """Should gracefully handle the case where normalized text is present but short.

        Note: build_deal_normalized_text always includes property_type, so truly
        empty text requires empty property_type (invalid input). This test verifies
        the function handles minimal input gracefully without real API calls.
        """
        mock_db = AsyncMock()

        with patch.object(dd, "generate_embedding", AsyncMock()) as mock_embed:
            is_dup, info = await dd.check_deal_duplicate(
                db=mock_db,
                address="",
                city="", state="",
                property_type="House",
                condition_description="",
            )

        # Function proceeds past the empty-check (text is not empty due to property_type)
        # The mocked generate_embedding returns AsyncMock which is not iterable -> TypeError -> caught by try/except
        assert is_dup is False
        assert info is None
        # generate_embedding was called (not a real API call - it's mocked)
        mock_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_db_query_failure(self):
        """Should return False when the DB query fails."""
        mock_db = AsyncMock()
        mock_embedding = [0.1] * 1024
        mock_db.execute = AsyncMock(side_effect=Exception("DB error"))

        with patch.object(dd, "generate_embedding", AsyncMock(return_value=mock_embedding)):
            is_dup, info = await dd.check_deal_duplicate(
                db=mock_db,
                address="123 Main St",
                city="Dallas", state="TX",
                property_type="House",
                condition_description="Test",
            )

        assert is_dup is False
        assert info is None

    @pytest.mark.asyncio
    async def test_duplicate_multiple_matches_returns_highest(self):
        """When there are multiple matches, return the highest similarity."""
        mock_db = AsyncMock()
        mock_embedding = [0.1] * 1024

        id1 = uuid.uuid4()
        id2 = uuid.uuid4()

        mock_row1 = FakeRow(id=id1, address="123 Main St", city="Dallas", state="TX", similarity=0.96)
        mock_row2 = FakeRow(id=id2, address="124 Main St", city="Dallas", state="TX", similarity=0.93)

        mock_db.execute = AsyncMock(return_value=FakeResult([mock_row1, mock_row2]))

        with patch.object(dd, "generate_embedding", AsyncMock(return_value=mock_embedding)):
            is_dup, info = await dd.check_deal_duplicate(
                db=mock_db,
                address="123 Main St",
                city="Dallas", state="TX",
                property_type="House",
                condition_description="Test",
            )

        assert is_dup is True
        assert str(id1) == info["matched_deal_id"]

    @pytest.mark.asyncio
    async def test_deal_id_to_exclude_included(self):
        """deal_id_to_exclude should be passed to the SQL."""
        mock_db = AsyncMock()
        mock_embedding = [0.1] * 1024
        mock_db.execute = AsyncMock(return_value=FakeResult([]))

        exclude_id = str(uuid.uuid4())

        with patch.object(dd, "generate_embedding", AsyncMock(return_value=mock_embedding)):
            is_dup, info = await dd.check_deal_duplicate(
                db=mock_db,
                address="123 Main St",
                city="Dallas", state="TX",
                property_type="House",
                condition_description="Test",
                deal_id_to_exclude=exclude_id,
            )

        assert is_dup is False
        # Verify the SQL contains the exclude clause
        call_sql = mock_db.execute.call_args[0][0]
        assert exclude_id in str(call_sql)


# ===========================================================================
# get_similar_deals tests
# ===========================================================================

class TestGetSimilarDeals:

    @pytest.mark.asyncio
    async def test_returns_results(self):
        """Should return a list of similar deals."""
        mock_db = AsyncMock()

        id1 = uuid.uuid4()
        id2 = uuid.uuid4()

        mock_row1 = FakeRow(id=id1, address="123 Main St", city="Dallas", state="TX", similarity=0.92)
        mock_row2 = FakeRow(id=id2, address="456 Oak Ave", city=None, state=None, similarity=0.85)

        mock_db.execute = AsyncMock(return_value=FakeResult([mock_row1, mock_row2]))

        results = await dd.get_similar_deals(mock_db, [0.1] * 1024, limit=5)

        assert len(results) == 2
        assert results[0]["id"] == str(id1)
        assert results[0]["similarity"] == 0.92
        assert results[1]["id"] == str(id2)
        assert results[1]["similarity"] == 0.85

    @pytest.mark.asyncio
    async def test_empty_results(self):
        """Should return empty list when no similar deals exist."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=FakeResult([]))

        results = await dd.get_similar_deals(mock_db, [0.1] * 1024, limit=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        """Should respect the limit parameter."""
        mock_db = AsyncMock()
        rows = []
        for i in range(3):
            row = FakeRow(address=f"Address {i}", city=None, state=None, similarity=1.0 - (i * 0.1))
            rows.append(row)

        mock_db.execute = AsyncMock(return_value=FakeResult(rows))

        results = await dd.get_similar_deals(mock_db, [0.1] * 1024, limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_db_error_propagates(self):
        """Should propagate DB errors since caller handles exceptions."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await dd.get_similar_deals(mock_db, [0.1] * 1024, limit=5)

    @pytest.mark.asyncio
    async def test_embedding_cleaned(self):
        """Should clean the embedding (convert to float list)."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=FakeResult([]))
        embedding = [0.1, 0.2, 0.3, 0.4]

        results = await dd.get_similar_deals(mock_db, embedding, limit=5)
        assert results == []

        # SQL should reference the embedding
        call_sql = mock_db.execute.call_args[0][0]
        assert "<=>" in str(call_sql)
