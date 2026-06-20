"""
One-time migration: re-embed all buyers and deals with the new local model.

After switching from Cohere embed-english-v3.0 to mxbai-embed-large-v1,
existing pgvector embeddings are incompatible. This script regenerates
all buy_box_embedding (buyers) and deal_embedding (deals) vectors using
the new local model.

Usage:
    cd backend && python -m scripts.reembed_all
    # or from the project root:
    python backend/scripts/reembed_all.py

The script:
  1. Loads the local mxbai-embed-large-v1 model (~670MB, first run only)
  2. Fetches all buyers with non-empty buy_box text
  3. Generates new 1024-dim embeddings for each buyer's buy_box
  4. Fetches all deals and builds a narrative for each
  5. Generates new 1024-dim embeddings for each deal narrative
  6. Updates the database in batches of 50

Safe to run multiple times — idempotent (re-embeds everything).
"""

import asyncio
import sys
import time
from pathlib import Path

# Ensure the backend directory is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.database import initialize_db, async_session_factory
from app.services.embeddings import _get_model, _embedding_dim


def _build_deal_narrative(row) -> str:
    """Build a narrative string from a deal row for embedding.
    Mirrors the logic in routers/deals.py._build_deal_narrative."""
    property_type = row.property_type

    if property_type == "House":
        parts = [
            f"Single family {row.beds}bed/{row.baths}bath",
        ]
        if row.city:
            parts.append(f"in {row.city}")
        if row.sqft:
            parts.append(f"{row.sqft}sqft")
        if row.year_built:
            parts.append(f"built {row.year_built}")
        if row.condition_description:
            parts.append(f"{row.condition_description}")
        if row.arv:
            parts.append(f"ARV ${float(row.arv):,.0f}")
        if row.asking_price:
            parts.append(f"asking ${float(row.asking_price):,.0f}")
        return ". ".join(parts) + "." if parts else ""

    elif property_type == "Land":
        parts = []
        if row.lot_size:
            parts.append(f"{row.lot_size}")
        if row.zoning:
            parts.append(f"{row.zoning} lot")
        if row.city:
            parts.append(f"in {row.city}")
        if row.condition_description:
            parts.append(f"{row.condition_description}")
        if row.arv:
            parts.append(f"ARV ${float(row.arv):,.0f}")
        if row.asking_price:
            parts.append(f"asking ${float(row.asking_price):,.0f}")
        return ". ".join(parts) + "." if parts else ""

    return ""


async def reembed_buyers(model, batch_size: int = 50) -> int:
    """Re-embed all buyers with non-empty buy_box text."""
    async with async_session_factory() as db:
        # Count total
        result = await db.execute(
            text("SELECT COUNT(*) FROM buyers WHERE buy_box IS NOT NULL AND buy_box != ''")
        )
        total = result.scalar()
        print(f"  Found {total} buyers with buy_box text")

        if total == 0:
            return 0

        # Fetch in batches
        offset = 0
        updated = 0
        start_time = time.time()

        while offset < total:
            result = await db.execute(
                text(
                    "SELECT id, buy_box FROM buyers "
                    "WHERE buy_box IS NOT NULL AND buy_box != '' "
                    "ORDER BY created_at ASC "
                    "LIMIT :limit OFFSET :offset"
                ),
                {"limit": batch_size, "offset": offset},
            )
            rows = result.fetchall()

            if not rows:
                break

            # Batch embed
            texts = [row.buy_box for row in rows]
            embeddings = model.encode(texts, normalize_embeddings=True, batch_size=batch_size)

            # Batch update
            for row, embedding in zip(rows, embeddings):
                await db.execute(
                    text("UPDATE buyers SET buy_box_embedding = :embedding WHERE id = :id"),
                    {"id": row.id, "embedding": embedding.tolist()},
                )

            await db.commit()
            updated += len(rows)
            offset += batch_size

            elapsed = time.time() - start_time
            rate = updated / elapsed if elapsed > 0 else 0
            eta = (total - updated) / rate if rate > 0 else 0
            print(
                f"  [{updated}/{total}] buyers re-embedded "
                f"({rate:.1f}/s, ETA {eta:.0f}s)"
            )

        return updated


async def reembed_deals(model, batch_size: int = 50) -> int:
    """Re-embed all deals with non-empty narratives."""
    async with async_session_factory() as db:
        # Count total
        result = await db.execute(text("SELECT COUNT(*) FROM deals"))
        total = result.scalar()
        print(f"  Found {total} deals")

        if total == 0:
            return 0

        # Fetch in batches (need all columns to build narrative)
        offset = 0
        updated = 0
        skipped = 0
        start_time = time.time()

        while offset < total:
            result = await db.execute(
                text(
                    "SELECT id, property_type, beds, baths, sqft, year_built, "
                    "city, condition_description, arv, asking_price, "
                    "lot_size, zoning "
                    "FROM deals ORDER BY created_at ASC LIMIT :limit OFFSET :offset"
                ),
                {"limit": batch_size, "offset": offset},
            )
            rows = result.fetchall()

            if not rows:
                break

            # Build narratives and filter empty ones
            pairs = []
            for row in rows:
                narrative = _build_deal_narrative(row)
                if narrative.strip():
                    pairs.append((row.id, narrative))
                else:
                    skipped += 1

            if pairs:
                ids, texts = zip(*pairs)
                embeddings = model.encode(
                    list(texts), normalize_embeddings=True, batch_size=batch_size
                )
                for deal_id, embedding in zip(ids, embeddings):
                    await db.execute(
                        text("UPDATE deals SET deal_embedding = :embedding WHERE id = :id"),
                        {"id": deal_id, "embedding": embedding.tolist()},
                    )
                await db.commit()

            updated += len(pairs)
            offset += batch_size

            elapsed = time.time() - start_time
            rate = updated / elapsed if elapsed > 0 else 0
            eta = (total - offset) / rate if rate > 0 else 0
            print(
                f"  [{updated}/{total}] deals re-embedded "
                f"({skipped} skipped, {rate:.1f}/s, ETA {eta:.0f}s)"
            )

        return updated


async def main():
    print("=" * 60)
    print("Re-embedding Migration: Cohere → mxbai-embed-large-v1")
    print("=" * 60)
    print()

    # 1. Initialize database
    print("Step 1/4: Connecting to database...")
    await initialize_db()
    print("  [OK] Database connected")
    print()

    # 2. Load the embedding model
    print("Step 2/4: Loading mxbai-embed-large-v1 model...")
    print("  (First run downloads ~670MB, subsequent runs use cache)")
    load_start = time.time()
    model = _get_model()
    load_time = time.time() - load_start
    print(f"  [OK] Model loaded in {load_time:.1f}s (dim={_embedding_dim})")
    print()

    # 3. Re-embed buyers
    print("Step 3/4: Re-embedding buyers...")
    buyer_count = await reembed_buyers(model)
    print(f"  [OK] {buyer_count} buyers re-embedded")
    print()

    # 4. Re-embed deals
    print("Step 4/4: Re-embedding deals...")
    deal_count = await reembed_deals(model)
    print(f"  [OK] {deal_count} deals re-embedded")
    print()

    # Summary
    print("=" * 60)
    print(f"Migration complete!")
    print(f"  Buyers re-embedded: {buyer_count}")
    print(f"  Deals re-embedded:  {deal_count}")
    print(f"  Model used:         mxbai-embed-large-v1 ({_embedding_dim}d)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
