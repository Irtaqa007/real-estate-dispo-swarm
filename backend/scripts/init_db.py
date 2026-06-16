"""
Initialize all database tables in Supabase.

Usage:
    cd backend && python -m scripts.init_db
    # or from the project root:
    python backend/scripts/init_db.py
"""

import asyncio
import sys
from pathlib import Path

# Ensure the backend directory is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.config import settings
from app.database import Base, engine, register_pgvector_extension


async def init_database():
    """Create all tables and register required extensions."""
    print(f"Connecting to: {settings.database_url}")
    print()

    # 1. Register pgvector extension
    print("Step 1/3: Registering pgvector extension...")
    await register_pgvector_extension()
    print("  [OK] pgvector extension ready")
    print()

    # 2. Test basic connectivity
    print("Step 2/3: Testing database connectivity...")
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version()"))
        version = result.scalar()
        print(f"  [OK] Connected to: {version}")
    print()

    # 3. Create all tables
    print("Step 3/3: Creating all tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # List all tables
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
        )
        tables = [row[0] for row in result]
        print(f"  [OK] Created {len(tables)} table(s):")
        for table in tables:
            print(f"     - {table}")

    print()
    print("Database initialization complete!")


if __name__ == "__main__":
    asyncio.run(init_database())
