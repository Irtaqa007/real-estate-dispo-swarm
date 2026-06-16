import logging
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


logger = logging.getLogger(__name__)


async def _create_engine_with_retry(max_retries=5, retry_delay=2):
    """Create async engine with retry logic for initial connection."""
    for attempt in range(max_retries):
        try:
            engine = create_async_engine(
                settings.database_url.replace("postgresql://", "postgresql+asyncpg://"),
                pool_size=20,
                max_overflow=0,
                echo=settings.debug,
                connect_args={"ssl": settings.database_ssl_mode} if settings.database_ssl_mode else {},
            )
            # Test connection
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info(f"Database engine created successfully on attempt {attempt + 1}")
            return engine
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database connection attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect after {max_retries} attempts: {e}. Creating engine without initial connection.")
                # Return engine anyway so app can start (graceful degradation)
                engine = create_async_engine(
                    settings.database_url.replace("postgresql://", "postgresql+asyncpg://"),
                    pool_size=20,
                    max_overflow=0,
                    echo=settings.debug,
                    connect_args={"ssl": settings.database_ssl_mode} if settings.database_ssl_mode else {},
                )
                logger.info("Engine created (will retry connection on first request)")
                return engine


# Create engine with retry at module load time
engine = None


class Base(DeclarativeBase):
    pass


async_session_factory = None


async def initialize_db():
    """Initialize database engine and session factory. Call this in app startup."""
    global engine, async_session_factory
    
    logger.info("Starting database initialization...")
    
    if engine is None:
        logger.info("Creating engine with retry logic...")
        engine = await _create_engine_with_retry(max_retries=5, retry_delay=2)
        logger.info(f"Engine created: {engine is not None}")
    
    if async_session_factory is None:
        logger.info(f"Creating session factory with engine: {engine}")
        async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info(f"Database session factory initialized: {async_session_factory is not None}")
    else:
        logger.warning("Session factory already initialized")


async def register_pgvector_extension():
    """Register the pgvector extension on connect."""
    if engine is None:
        logger.warning("Engine not initialized, skipping pgvector registration")
        return
    
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
    except Exception as e:
        logger.warning(f"Failed to register pgvector extension: {e}")


async def test_connection():
    """Test database connectivity on startup."""
    if engine is None:
        logger.warning("Engine not initialized, cannot test connection")
        return
    
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            logger.info("Database connection OK")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise


async def get_db() -> AsyncSession:
    """FastAPI dependency that provides an async database session.

    Yields a session that is committed on success or rolled back on error.
    The rollback itself is wrapped in try/except so a rollback failure
    never masks the original exception.
    The async_session_factory context manager handles closing the session
    when the block exits.
    """
    if async_session_factory is None:
        raise RuntimeError("Database not initialized. Call initialize_db() during app startup.")
    
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            try:
                await session.rollback()
            except Exception as rb_err:
                logger.error("Session rollback failed after commit error: %s", rb_err, exc_info=True)
            raise
