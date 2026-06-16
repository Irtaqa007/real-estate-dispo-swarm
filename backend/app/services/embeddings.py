"""Cohere embedding service for semantic vector search.

Generates 1024-dimension embeddings using Cohere's embed API.
- search_document input type for deal narratives
- search_query input type for buyer buy_boxes
"""

import logging
from typing import Optional

import cohere

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy-init client so we don't fail import if COHERE_API_KEY is not set
_client: Optional[cohere.AsyncClient] = None


def _get_client() -> cohere.AsyncClient:
    """Get or create the Cohere async client."""
    global _client
    if _client is None:
        api_key = settings.cohere_api_key
        if not api_key:
            raise ValueError(
                "COHERE_API_KEY is not set. Add it to your .env file."
            )
        _client = cohere.AsyncClient(api_key=api_key, timeout=30)
    return _client


async def generate_embedding(
    text: str,
    input_type: str = "search_document",
) -> list[float]:
    """Generate a 1024-dim embedding vector using Cohere's embed API.

    Args:
        text: The text to embed.
        input_type: One of "search_document" (for deal narratives) or
                    "search_query" (for buyer buy_boxes).

    Returns:
        A list of 1024 floats representing the embedding vector.

    Raises:
        ValueError: If COHERE_API_KEY is not configured.
        RuntimeError: If the Cohere API call fails.
    """
    if not text or not text.strip():
        logger.warning("Empty text provided for embedding, returning zero vector")
        return [0.0] * 1024

    client = _get_client()

    try:
        response = await client.embed(
            texts=[text],
            model="embed-english-v3.0",
            input_type=input_type,
        )
        embedding = [float(x) for x in response.embeddings[0]]
        logger.debug(
            "Generated embedding (dim=%d) for text[:60]=%r",
            len(embedding),
            text[:60],
        )
        return embedding
    except Exception as e:
        logger.error("Cohere embedding failed: %s", e, exc_info=True)
        raise RuntimeError(f"Failed to generate embedding: {e}") from e


async def check_cohere_health() -> dict:
    """Check Cohere API connectivity.

    Makes a lightweight embedding call with a short test string to verify
    the API key is valid and the service is responsive. The cost is
    negligible (a few tokens).

    Returns:
        dict with keys:
            configured (bool): Whether COHERE_API_KEY is set.
            reachable (bool): Whether the API responded successfully.
            latency_ms (float|None): Response time in milliseconds.
            error (str|None): Error message if the check failed.
    """
    if not settings.cohere_api_key:
        return {
            "configured": False,
            "reachable": False,
            "latency_ms": None,
            "error": "COHERE_API_KEY is not configured",
        }

    import time

    try:
        start = time.monotonic()
        client = _get_client()
        response = await client.embed(
            texts=["health check"],
            model="embed-english-v3.0",
            input_type="search_document",
        )
        elapsed = (time.monotonic() - start) * 1000

        if response and response.embeddings:
            logger.debug("Cohere health check passed (%.1fms)", elapsed)
            return {
                "configured": True,
                "reachable": True,
                "latency_ms": round(elapsed, 1),
                "error": None,
            }
        else:
            return {
                "configured": True,
                "reachable": False,
                "latency_ms": round(elapsed, 1),
                "error": "Empty response from Cohere API",
            }
    except Exception as e:
        logger.warning("Cohere health check failed: %s", e, exc_info=True)
        return {
            "configured": True,
            "reachable": False,
            "latency_ms": None,
            "error": str(e)[:200],
        }
