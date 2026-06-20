import logging
import asyncio
import socket
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


logger = logging.getLogger(__name__)


async def _create_engine_with_retry(max_retries=10, retry_delay=5):
    """Create async engine with retry logic for initial connection.

    Parses the DATABASE_URL, optionally forces IPv4 resolution (to work around
    WSL2/Docker IPv6 routing issues), and retries with exponential backoff.
    Raises on final exhaustion rather than silently creating an unusable engine.

    Args:
        max_retries: Maximum number of connection attempts (default 10).
        retry_delay: Base delay in seconds between retries (default 5).
    """
    database_url = settings.database_url

    # Parse the URL to extract host and port
    clean_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(clean_url)
    host = parsed.hostname
    port = parsed.port or 6543

    logger.info(f"Target database: {host}:{port}")

    # Ensure asyncpg driver prefix
    if "+asyncpg" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")

    # Force IPv4 resolution if enabled (Option B: resolve hostname → IPv4 address)
    loop = asyncio.get_running_loop()
    if settings.force_ipv4 and host:
        try:
            addr_info = await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
            if addr_info:
                resolved_ip = addr_info[0][4][0]
                logger.info(f"Resolved {host} to IPv4: {resolved_ip}")
                database_url = database_url.replace(host, resolved_ip)
            else:
                logger.warning(f"No IPv4 address found for {host}, using hostname directly")
        except Exception as e:
            logger.warning(f"Could not resolve IPv4 for {host}: {e}, using hostname directly")

    # Connection arguments for asyncpg.
    # statement_cache_size=0 is the ONLY valid asyncpg param for this --
    # there is no "prepared_statement_cache_size" in asyncpg's API, so a
    # prior attempt at that key was silently ignored, not actually applied.
    connect_args = {
        "ssl": settings.database_ssl_mode or "require",
        "statement_cache_size": 0,
        "timeout": settings.database_connect_timeout,   # Connection handshake timeout
        "command_timeout": settings.database_command_timeout,  # Query timeout
        "server_settings": {
            "application_name": "real_estate_dispo_swarm",
        },
    }

    for attempt in range(max_retries):
        try:
            logger.info(f"Connection attempt {attempt + 1}/{max_retries}")

            engine = create_async_engine(
                database_url,
                # Supabase's pooler (port 6543) IS pgbouncer in transaction
                # mode. SQLAlchemy's own QueuePool maintaining 20 persistent
                # connections on top of pgbouncer's pooling is what causes
                # the prepared-statement-name collisions: SQLAlchemy keeps a
                # connection open and asyncpg's per-connection prepare cache
                # (even disabled client-side) can still race against
                # pgbouncer silently swapping the underlying backend
                # connection beneath a "stable" client connection on
                # transaction boundaries. NullPool makes SQLAlchemy open a
                # fresh DBAPI connection per checkout and close it
                # immediately after, which is the documented-correct mode
                # when sitting behind an external pooler like pgbouncer.
                poolclass=NullPool,
                echo=settings.debug,
                connect_args=connect_args,
                # SQLAlchemy's own compiled-statement cache, separate from
                # asyncpg's. Must also be disabled for the same reason.
                query_cache_size=0,
            )

            # Test connection with a SELECT 1
            # NOTE: conn.execute() is the awaitable call. The CursorResult it
            # returns is already resolved -- calling .fetchone() on it is a
            # synchronous call, NOT an awaitable. Awaiting it raises:
            # TypeError: object Row can't be used in 'await' expression
            async with engine.begin() as conn:
                result = await conn.execute(text("SELECT 1"))
                result.fetchone()

            logger.info(f"✅ Database engine created successfully on attempt {attempt + 1}")
            return engine

        except Exception as e:
            logger.error(f"Connection attempt {attempt + 1} failed: {type(e).__name__}: {e}")


            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                logger.info(f"Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.critical(f"All {max_retries} connection attempts exhausted")
                raise


# Lazy-initialized globals
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
        engine = await _create_engine_with_retry(max_retries=10, retry_delay=5)
        logger.info("Engine created: %s", engine is not None)

    if async_session_factory is None:
        logger.info("Creating session factory with engine: %s", engine)
        async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("Database session factory initialized: %s", async_session_factory is not None)
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
        logger.warning("Failed to register pgvector extension: %s", e, exc_info=True)


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
        logger.error(
            "Database connection failed: %s. Host: %s",
            e,
            settings.database_host or "unknown",
            exc_info=True,
        )
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