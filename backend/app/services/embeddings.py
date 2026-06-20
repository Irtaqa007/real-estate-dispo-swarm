"""Local embedding service using sentence-transformers.

Generates 1024-dimension embeddings using mxbai-embed-large-v1 running
locally on CPU. No external API calls required.

Replaces the previous Cohere embed-english-v3.0 integration.
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy-loaded model — avoids importing sentence_transformers at module level
# so the import cost is paid only on first embedding call.
_model = None
_model_name = "mixedbread-ai/mxbai-embed-large-v1"
_embedding_dim = 1024


def _get_model():
    """Load the sentence-transformers model on first call."""
    global _model
    if _model is None:
        logger.info("Loading embedding model: %s (first call, may take ~30s)", _model_name)
        from sentence_transformers import SentenceTransformer
        token = settings.hf_token or None  # pass None if not set so no auth is attempted
        _model = SentenceTransformer(_model_name, token=token)
        logger.info("Embedding model loaded (dim=%d)", _embedding_dim)
    return _model


async def generate_embedding(
    text: str,
    input_type: str = "search_document",
) -> list[float]:
    """Generate a 1024-dim embedding vector using the local mxbai model.

    Args:
        text: The text to embed.
        input_type: Kept for API compatibility with callers. The local model
                    does not differentiate between document and query inputs.

    Returns:
        A list of 1024 floats representing the embedding vector.

    Raises:
        RuntimeError: If the model fails to generate an embedding.
    """
    if not text or not text.strip():
        logger.warning("Empty text provided for embedding, returning zero vector")
        return [0.0] * _embedding_dim

    try:
        model = _get_model()
        # sentence_transformers encode is synchronous — run in executor
        import asyncio
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(
            None,
            lambda: model.encode(text, normalize_embeddings=True).tolist(),
        )
        logger.debug(
            "Generated embedding (dim=%d) for text[:60]=%r",
            len(embedding), text[:60],
        )
        return embedding
    except Exception as e:
        logger.error("Local embedding failed: %s", e, exc_info=True)
        raise RuntimeError(f"Failed to generate embedding: {e}") from e


async def check_embedding_health() -> dict:
    """Check that the local embedding model is loaded and functional.

    Returns:
        dict with keys:
            configured (bool): Always True (local model, no API key needed).
            reachable (bool): Whether the model produced a valid embedding.
            latency_ms (float|None): Response time in milliseconds.
            error (str|None): Error message if the check failed.
    """
    import time

    try:
        start = time.monotonic()
        embedding = await generate_embedding("health check")
        elapsed = (time.monotonic() - start) * 1000

        if embedding and len(embedding) == _embedding_dim:
            logger.debug("Embedding health check passed (%.1fms)", elapsed)
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
                "error": f"Unexpected embedding dimensions: {len(embedding)}",
            }
    except Exception as e:
        logger.warning("Embedding health check failed: %s", e, exc_info=True)
        return {
            "configured": True,
            "reachable": False,
            "latency_ms": None,
            "error": str(e)[:200],
        }


# Keep old name as alias for backward compatibility
check_cohere_health = check_embedding_health
