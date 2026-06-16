"""Deal similarity deduplication using address normalization and embedding cosine similarity.

Before creating a new deal, normalize the address, generate an embedding,
and check cosine similarity against all Available deals. If similarity > 0.95,
flag as duplicate.

Frontend: Show warning "This deal is 95% similar to existing deal at {address}. Create anyway?"
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import Deal
from app.services.embeddings import generate_embedding

logger = logging.getLogger(__name__)

# Similarity threshold for flagging as duplicate
DUPLICATE_SIMILARITY_THRESHOLD = 0.95

# ---------------------------------------------------------------------------
# Address normalization
# ---------------------------------------------------------------------------

_STREET_SUFFIXES = {
    "st": "street", "rd": "road", "ave": "avenue", "dr": "drive",
    "ln": "lane", "ct": "court", "cir": "circle", "blvd": "boulevard",
    "wy": "way", "pl": "place", "ter": "terrace", "trl": "trail",
    "hwy": "highway", "pkwy": "parkway",
}

_DIRECTIONS = {
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
}


def normalize_address(address: str) -> str:
    """Normalize an address for comparison.

    Steps:
    1. Lowercase
    2. Remove punctuation (except hyphens in numbers)
    3. Expand common abbreviations (St → Street, Rd → Road, etc.)
    4. Expand direction abbreviations (N → North, etc.)
    5. Collapse whitespace
    6. Strip leading/trailing whitespace

    Args:
        address: The raw address string.

    Returns:
        Normalized address string.
    """
    if not address:
        return ""

    addr = address.lower().strip()

    # Remove punctuation except hyphens within numbers
    addr = re.sub(r"[^\w\s\'-]", " ", addr)
    addr = re.sub(r"(?<!\d)-(?!\d)", " ", addr)  # Remove standalone hyphens

    # Tokenize and expand
    tokens = addr.split()
    expanded = []
    for token in tokens:
        # Remove trailing punctuation from tokens
        cleaned = token.strip(".,;:'\"")
        if not cleaned:
            continue
        # Check if it's a street suffix
        if cleaned in _STREET_SUFFIXES:
            expanded.append(_STREET_SUFFIXES[cleaned])
        # Check if it's a direction
        elif cleaned in _DIRECTIONS:
            expanded.append(_DIRECTIONS[cleaned])
        else:
            expanded.append(cleaned)

    return " ".join(expanded)


# ---------------------------------------------------------------------------
# Deal normalization (build a canonical text string for embedding)
# ---------------------------------------------------------------------------


def build_deal_normalized_text(address: str, city: Optional[str], state: Optional[str],
                               property_type: str, condition_description: str,
                               beds: Optional[int] = None, baths: Optional[float] = None,
                               sqft: Optional[int] = None,
                               lot_size: Optional[str] = None, zoning: Optional[str] = None) -> str:
    """Build a canonical text string for a new deal to generate a comparison embedding.

    This is used for dedup checks (not the same as the full narrative for matching).
    Focuses on location + property type + size.
    """
    normalized_addr = normalize_address(address)
    parts = [normalized_addr]
    if city:
        parts.append(city.lower())
    if state:
        parts.append(state.lower())
    parts.append(property_type.lower())

    if property_type == "House":
        if beds:
            parts.append(f"{beds} bedroom")
        if baths:
            parts.append(f"{baths} bathroom")
        if sqft:
            parts.append(f"{sqft} square feet")
    elif property_type == "Land":
        if lot_size:
            parts.append(lot_size.lower())
        if zoning:
            parts.append(zoning.lower())

    if condition_description:
        # Just the first 100 chars for location/property focus
        parts.append(condition_description.lower()[:100])

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Dedup check
# ---------------------------------------------------------------------------


async def check_deal_duplicate(
    db: AsyncSession,
    address: str,
    city: Optional[str],
    state: Optional[str],
    property_type: str,
    condition_description: str,
    beds: Optional[int] = None,
    baths: Optional[float] = None,
    sqft: Optional[int] = None,
    lot_size: Optional[str] = None,
    zoning: Optional[str] = None,
    deal_id_to_exclude: Optional[str] = None,
) -> Tuple[bool, Optional[Dict]]:
    """Check if a deal is a duplicate of an existing Available deal.

    Generates a comparison embedding for the new deal, then checks cosine
    similarity against all deals with status='Available'. If any match has
    similarity > 0.95, flags as duplicate.

    Args:
        db: Database session.
        address: Deal address.
        city: Deal city.
        state: Deal state.
        property_type: 'House' or 'Land'.
        condition_description: Deal condition description.
        beds: Number of bedrooms (House only).
        baths: Number of bathrooms (House only).
        sqft: Square footage (House only).
        lot_size: Lot size (Land only).
        zoning: Zoning (Land only).
        deal_id_to_exclude: Optional deal ID to exclude (for update flows).

    Returns:
        Tuple of (is_duplicate: bool, match_info: dict or None).
        match_info contains: matched_deal_id, address, similarity_score.
    """
    # Build normalized text for the new deal
    normalized_text = build_deal_normalized_text(
        address=address, city=city, state=state,
        property_type=property_type, condition_description=condition_description,
        beds=beds, baths=baths, sqft=sqft,
        lot_size=lot_size, zoning=zoning,
    )

    if not normalized_text.strip():
        logger.warning("Cannot check dedup: empty normalized deal text")
        return False, None

    try:
        # Generate embedding for the new deal (use search_document type to match deal embeddings)
        new_embedding = await generate_embedding(normalized_text, input_type="search_document")
    except Exception as e:
        logger.warning("Failed to generate embedding for dedup check: %s", e, exc_info=True)
        return False, None

    # Build SQL to find similar deals using cosine similarity
    clean_embedding = [float(x) for x in new_embedding]
    embedding_str = str(clean_embedding)

    exclude_clause = ""
    if deal_id_to_exclude:
        exclude_clause = f"AND d.id != '{deal_id_to_exclude}'"

    sql = text(f"""
        SELECT
            d.id,
            d.address,
            d.city,
            d.state,
            GREATEST(0, 1 - (d.deal_embedding <=> :embedding)) AS similarity
        FROM deals d
        WHERE d.status IN ('Available', 'Campaign Launched')
          AND d.deal_embedding IS NOT NULL
          {exclude_clause}
        ORDER BY d.deal_embedding <=> CAST(:embedding AS vector)
        LIMIT 5
    """)

    try:
        rows = await db.execute(sql, {"embedding": embedding_str})
        matches = rows.fetchall()

        for row in matches:
            similarity = float(row.similarity)
            if similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
                location = f"{row.address}"
                if row.city:
                    location += f", {row.city}"
                if row.state:
                    location += f", {row.state}"

                logger.info(
                    "Potential duplicate deal detected: similarity=%.3f with deal %s (%s)",
                    similarity, row.id, location,
                )

                return True, {
                    "matched_deal_id": str(row.id),
                    "address": location,
                    "similarity_score": round(similarity, 4),
                }

        return False, None

    except Exception as e:
        logger.warning("Dedup similarity check failed: %s", e, exc_info=True)
        return False, None


async def get_similar_deals(
    db: AsyncSession,
    deal_embedding: List[float],
    limit: int = 5,
) -> List[Dict]:
    """Find deals similar to a given embedding.

    Used for display purposes or bulk dedup.

    Args:
        db: Database session.
        deal_embedding: The deal's embedding vector.
        limit: Max results.

    Returns:
        List of dicts with keys: id, address, similarity.
    """
    clean_embedding = [float(x) for x in deal_embedding]
    embedding_str = str(clean_embedding)

    sql = text("""
        SELECT
            d.id,
            d.address,
            d.city,
            d.state,
            GREATEST(0, 1 - (d.deal_embedding <=> :embedding)) AS similarity
        FROM deals d
        WHERE d.status IN ('Available', 'Campaign Launched')
          AND d.deal_embedding IS NOT NULL
        ORDER BY d.deal_embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """)

    rows = await db.execute(sql, {"embedding": embedding_str, "limit": limit})
    return [
        {
            "id": str(r.id),
            "address": r.address,
            "city": r.city,
            "state": r.state,
            "similarity": round(float(r.similarity), 4),
        }
        for r in rows
    ]
