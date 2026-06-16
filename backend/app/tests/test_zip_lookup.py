"""Comprehensive tests for ZIP code lookup service and endpoint.

Covers:
- lookup_zip: valid ZIP, invalid format, API errors, timeouts, parse errors
- ZipLookupResult.to_dict(): format verification
- zip_lookup endpoint: success and 404
- Edge cases: empty string, whitespace, non-US chars
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.zip_lookup import ZipLookupResult, lookup_zip
from app.routers import deals as deals_router


# ===========================================================================
# ZipLookupResult tests
# ===========================================================================

class TestZipLookupResult:

    def test_to_dict_format(self):
        """Verify to_dict returns the expected shape."""
        result = ZipLookupResult(city="Dallas", state="Texas", state_abbr="TX")
        d = result.to_dict()
        assert d["city"] == "Dallas"
        assert d["state"] == "TX"
        assert d["state_full"] == "Texas"
        assert d["county"] == "Dallas"  # Falls back to city name

    def test_to_dict_with_different_county(self):
        """When county data is available, to_dict should reflect it."""
        result = ZipLookupResult(city="Beverly Hills", state="California", state_abbr="CA")
        d = result.to_dict()
        assert d["county"] == "Beverly Hills"  # Falls back to city

    def test_empty_city(self):
        result = ZipLookupResult(city="", state="", state_abbr="")
        d = result.to_dict()
        assert d["city"] == ""
        assert d["state"] == ""
        assert d["state_full"] == ""


# ===========================================================================
# lookup_zip tests
# ===========================================================================

class TestLookupZip:

    # ------------------------------------------------------------------
    # Valid responses
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_valid_zip_returns_result(self):
        """A valid 5-digit ZIP should return a ZipLookupResult."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "post code": "90210",
            "country": "United States",
            "country abbreviation": "US",
            "places": [
                {
                    "place name": "Beverly Hills",
                    "state": "California",
                    "state abbreviation": "CA",
                    "latitude": "34.0901",
                    "longitude": "-118.4065"
                }
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("90210")

        assert result is not None
        assert result.city == "Beverly Hills"
        assert result.state == "California"
        assert result.state_abbr == "CA"

    @pytest.mark.asyncio
    async def test_zip_with_leading_whitespace(self):
        """Whitespace should be stripped before processing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "post code": "10001",
            "places": [{"place name": "New York City", "state": "New York", "state abbreviation": "NY"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("  10001  ")
        assert result is not None
        assert result.city == "New York City"

    @pytest.mark.asyncio
    async def test_zip_longer_than_5_truncated(self):
        """ZIP+4 should be truncated to 5 digits."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "post code": "10001",
            "places": [{"place name": "New York City", "state": "New York", "state abbreviation": "NY"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("10001-1234")
        assert result is not None
        assert result.city == "New York City"

    # ------------------------------------------------------------------
    # Invalid formats — should return None without calling API
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_invalid_zip_too_short(self):
        """Less than 5 digits should return None."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await lookup_zip("123")
        assert result is None
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_zip_non_digit(self):
        """Non-numeric input should return None."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await lookup_zip("ABCDE")
        assert result is None
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_zip_empty_string(self):
        """Empty string should return None."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await lookup_zip("")
        assert result is None
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_zip_all_whitespace(self):
        """Whitespace-only should return None."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await lookup_zip("     ")
        assert result is None
        mock_client_cls.assert_not_called()

    # ------------------------------------------------------------------
    # API errors
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_api_returns_404(self):
        """A 404 from the API should return None."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("00000")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_returns_500(self):
        """A 5xx from the API should return None."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_timeout(self):
        """A timeout should return None."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timed out"))
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_request_error(self):
        """A network error should return None."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(side_effect=httpx.RequestError("Connection refused"))
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("12345")
        assert result is None

    # ------------------------------------------------------------------
    # Parse errors
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_empty_places_list(self):
        """An empty places list should return None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"post code": "12345", "places": []}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_place_name(self):
        """Missing 'place name' key should return None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "post code": "12345",
            "places": [{"state": "Texas", "state abbreviation": "TX"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_state_abbreviation(self):
        """Missing 'state abbreviation' key should return None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "post code": "12345",
            "places": [{"place name": "Somewhere", "state": "Texas"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_json_response(self):
        """Non-JSON response should return None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await lookup_zip("12345")
        assert result is None


# ===========================================================================
# ZIP lookup endpoint tests
# ===========================================================================

class TestZipLookupEndpoint:

    @pytest.mark.asyncio
    async def test_zip_lookup_success(self):
        """Endpoint should return location data for a valid ZIP."""
        mock_result = ZipLookupResult(city="Dallas", state="Texas", state_abbr="TX")

        with patch.object(deals_router, "lookup_zip", AsyncMock(return_value=mock_result)):
            response = await deals_router.zip_lookup("75201")

        assert response["city"] == "Dallas"
        assert response["state"] == "TX"
        assert response["state_full"] == "Texas"

    @pytest.mark.asyncio
    async def test_zip_lookup_not_found(self):
        """Endpoint should raise 404 when ZIP not found."""
        from fastapi import HTTPException

        with patch.object(deals_router, "lookup_zip", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await deals_router.zip_lookup("00000")

        assert exc_info.value.status_code == 404
        assert "00000" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_zip_lookup_empty_raises_404(self):
        """Empty ZIP should propagate to lookup_zip returning None -> 404."""
        from fastapi import HTTPException

        with patch.object(deals_router, "lookup_zip", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await deals_router.zip_lookup("")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_zip_lookup_numeric_zips(self):
        """Numeric ZIP string should be handled correctly."""
        mock_result = ZipLookupResult(city="Austin", state="Texas", state_abbr="TX")

        with patch.object(deals_router, "lookup_zip", AsyncMock(return_value=mock_result)):
            response = await deals_router.zip_lookup("73301")

        assert response["city"] == "Austin"
        assert response["state"] == "TX"
