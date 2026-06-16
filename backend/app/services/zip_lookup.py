"""ZIP code geo-lookup service.

Fetches city, state, and county from a US ZIP code using the free
Zippopotam.us API (no API key required).

API: http://api.zippopotam.us/us/{zip}
Response example:
  {
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

County is not provided by Zippopotam.us, so we default to the place name.
For more accurate county data we could use a paid API, but this is sufficient
for auto-fill purposes.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ZIPPOPOTAM_URL = "http://api.zippopotam.us/us/{zip}"

# Request timeout in seconds
TIMEOUT = 10


class ZipLookupResult:
    """Result of a ZIP code lookup."""

    def __init__(self, city: str, state: str, state_abbr: str) -> None:
        self.city = city
        self.state = state
        self.state_abbr = state_abbr

    def to_dict(self) -> dict:
        return {
            "city": self.city,
            "state": self.state_abbr,  # Return abbreviation (e.g. "CA")
            "state_full": self.state,
            "county": self.city,  # Fallback — Zippopotam.us doesn't provide county
        }


async def lookup_zip(zip_code: str) -> Optional[ZipLookupResult]:
    """Look up city/state/county for a US ZIP code.

    Args:
        zip_code: 5-digit US ZIP code string.

    Returns:
        ZipLookupResult with city, state, state_abbr fields, or None if not found.
    """
    zip_code = zip_code.strip()[:5]
    if not zip_code.isdigit() or len(zip_code) != 5:
        logger.warning("Invalid ZIP code format: %s", zip_code)
        return None

    url = ZIPPOPOTAM_URL.format(zip=zip_code)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url)

        if response.status_code != 200:
            logger.info("ZIP lookup returned %d for %s", response.status_code, zip_code)
            return None

        data = response.json()
        places = data.get("places", [])
        if not places:
            logger.info("ZIP lookup returned no places for %s", zip_code)
            return None

        place = places[0]
        city = place.get("place name", "")
        state = place.get("state", "")
        state_abbr = place.get("state abbreviation", "")

        if not city or not state_abbr:
            logger.warning("ZIP lookup incomplete response for %s: %s", zip_code, data)
            return None

        return ZipLookupResult(city=city, state=state, state_abbr=state_abbr)

    except httpx.TimeoutException:
        logger.warning("ZIP lookup timed out for %s", zip_code)
        return None
    except httpx.RequestError as e:
        logger.warning("ZIP lookup request failed for %s: %s", zip_code, e)
        return None
    except (ValueError, KeyError, IndexError) as e:
        logger.warning("ZIP lookup parse error for %s: %s", zip_code, e)
        return None
