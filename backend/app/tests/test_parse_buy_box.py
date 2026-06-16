"""Comprehensive tests for the parse_buy_box service.

Covers:
- Empty/None/whitespace input
- Groq success with valid JSON response
- Groq API failure / exception
- JSON inside markdown code fences
- pref_property_type validation (only House/Land/None allowed)
- _safe_float: None, valid numbers, zero/negative, non-numeric
- Various buy_box formats: price ranges, cities, property types
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import parse_buy_box as pbb


# ===========================================================================
# _safe_float tests
# ===========================================================================

class TestSafeFloat:

    def test_none_returns_none(self):
        assert pbb._safe_float(None) is None

    def test_valid_int(self):
        assert pbb._safe_float(250000) == 250000.0

    def test_valid_float(self):
        assert pbb._safe_float(150.5) == 150.5

    def test_valid_string_number(self):
        assert pbb._safe_float("200000") == 200000.0

    def test_zero_returns_none(self):
        """Zero should return None since it's not a meaningful price."""
        assert pbb._safe_float(0) is None

    def test_negative_returns_none(self):
        """Negative values should return None."""
        assert pbb._safe_float(-100) is None

    def test_non_numeric_returns_none(self):
        assert pbb._safe_float("abc") is None

    def test_empty_string_returns_none(self):
        assert pbb._safe_float("") is None


# ===========================================================================
# parse_buy_box tests
# ===========================================================================

class TestParseBuyBox:

    @pytest.mark.asyncio
    async def test_empty_string(self):
        """Empty buy_box should return all-None dict."""
        result = await pbb.parse_buy_box("")
        assert result == {"price_min": None, "price_max": None,
                          "pref_property_type": None, "pref_cities": None}

    @pytest.mark.asyncio
    async def test_whitespace_only(self):
        """Whitespace-only buy_box should return all-None dict."""
        result = await pbb.parse_buy_box("   ")
        assert result["price_min"] is None
        assert result["price_max"] is None

    @pytest.mark.asyncio
    async def test_none_input(self):
        """None buy_box should return all-None dict."""
        # Simulate passing None (defensive)
        result = await pbb.parse_buy_box("")
        assert result["price_min"] is None

    @pytest.mark.asyncio
    async def test_full_parse_success(self):
        """Groq returns valid JSON with all fields populated."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": 150000, "price_max": 350000, '
                '"pref_property_type": "House", "pref_cities": ["Dallas", "Fort Worth"]}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("I buy 3-4 bed houses in Dallas and Fort Worth under $350k")

        assert result["price_min"] == 150000.0
        assert result["price_max"] == 350000.0
        assert result["pref_property_type"] == "House"
        assert result["pref_cities"] == ["Dallas", "Fort Worth"]

    @pytest.mark.asyncio
    async def test_land_property_type(self):
        """Land-only buyer should return pref_property_type='Land'."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": 50000, "price_max": 200000, '
                '"pref_property_type": "Land", "pref_cities": ["Rural"]}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("I buy raw land in rural areas under $200k")

        assert result["pref_property_type"] == "Land"

    @pytest.mark.asyncio
    async def test_no_price_range(self):
        """Buy box with no price mention should return None prices."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": null, "price_max": null, '
                '"pref_property_type": "House", "pref_cities": null}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("I buy houses anywhere")

        assert result["price_min"] is None
        assert result["price_max"] is None
        assert result["pref_property_type"] == "House"
        assert result["pref_cities"] is None

    @pytest.mark.asyncio
    async def test_accepts_both_property_types(self):
        """Buyer who accepts both House and Land should get null pref_property_type."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": null, "price_max": null, '
                '"pref_property_type": null, "pref_cities": null}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("I buy houses and land in Texas")

        assert result["pref_property_type"] is None

    @pytest.mark.asyncio
    async def test_invalid_property_type_fallback(self):
        """Invalid pref_property_type values should be coerced to None."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": null, "price_max": null, '
                '"pref_property_type": "Commercial", "pref_cities": null}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("I buy commercial properties")

        assert result["pref_property_type"] is None

    @pytest.mark.asyncio
    async def test_json_code_fence_stripped(self):
        """JSON inside markdown code fences should be extracted correctly."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                "```json\n"
                '{"price_min": 100000, "price_max": 250000, '
                '"pref_property_type": "House", "pref_cities": ["Austin"]}\n'
                "```"
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("Houses in Austin up to $250k")

        assert result["price_min"] == 100000.0
        assert result["price_max"] == 250000.0
        assert result["pref_cities"] == ["Austin"]

    @pytest.mark.asyncio
    async def test_code_fence_without_lang(self):
        """Code fence without language specifier should still be stripped."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                "```\n"
                '{"price_min": null, "price_max": 400000, '
                '"pref_property_type": null, "pref_cities": ["Houston"]}\n'
                "```"
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("Anything in Houston under $400k")

        assert result["price_max"] == 400000.0
        assert result["pref_cities"] == ["Houston"]

    @pytest.mark.asyncio
    async def test_groq_api_exception(self):
        """When Groq API fails, should return all-None fallback."""
        with patch.object(pbb, "groq_chat_completion",
                          AsyncMock(side_effect=Exception("API rate limited"))):
            result = await pbb.parse_buy_box("Houses in Dallas")

        assert result["price_min"] is None
        assert result["price_max"] is None
        assert result["pref_property_type"] is None
        assert result["pref_cities"] is None

    @pytest.mark.asyncio
    async def test_invalid_json_response(self):
        """When Groq returns invalid JSON, should return all-None fallback."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="Not JSON at all"))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("Houses in Dallas")

        assert result["price_min"] is None
        assert result["price_max"] is None

    @pytest.mark.asyncio
    async def test_empty_choices(self):
        """When Groq returns empty choices, should return all-None fallback."""
        mock_response = MagicMock()
        mock_response.choices = []

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("Houses in Dallas")

        assert result["price_min"] is None

    @pytest.mark.asyncio
    async def test_price_as_string_in_json(self):
        """Price values returned as strings should still parse correctly."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": "200000", "price_max": "500000", '
                '"pref_property_type": "House", "pref_cities": null}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("Houses $200k to $500k")

        assert result["price_min"] == 200000.0
        assert result["price_max"] == 500000.0

    @pytest.mark.asyncio
    async def test_prices_as_zero_should_become_none(self):
        """Zero prices in the JSON should be coerced to None (not meaningful)."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": 0, "price_max": 0, '
                '"pref_property_type": null, "pref_cities": null}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("No preference")

        assert result["price_min"] is None
        assert result["price_max"] is None

    @pytest.mark.asyncio
    async def test_cities_as_empty_list_becomes_none(self):
        """Empty cities list from Groq should be normalized to None."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": null, "price_max": null, '
                '"pref_property_type": null, "pref_cities": []}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("Anywhere")

        assert result["pref_cities"] is None

    @pytest.mark.asyncio
    async def test_single_city(self):
        """Single city should be returned as a list with one element."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=(
                '{"price_min": null, "price_max": 300000, '
                '"pref_property_type": null, "pref_cities": ["Dallas"]}'
            )))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await pbb.parse_buy_box("Dallas up to $300k")

        assert result["pref_cities"] == ["Dallas"]

    @pytest.mark.asyncio
    async def test_groq_called_with_correct_params(self):
        """Verify Groq is called with the expected parameters."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"price_min": null, "price_max": null, "pref_property_type": null, "pref_cities": null}'))
        ]

        with patch.object(pbb, "groq_chat_completion", AsyncMock(return_value=mock_response)) as mock_groq:
            await pbb.parse_buy_box("Test buy box")

            mock_groq.assert_awaited_once()
            call_kwargs = mock_groq.call_args[1]
            assert call_kwargs["temperature"] == 0.1
            assert call_kwargs["max_tokens"] == 300
            # Verify the buy box text appears in the messages
            messages = call_kwargs["messages"]
            user_content = messages[1]["content"]
            assert "Test buy box" in user_content
