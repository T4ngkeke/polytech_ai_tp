"""
database.py — SQLAlchemy async engine, session factory, and base declarative class.

Uses asyncpg driver for async PostgreSQL access.
DATABASE_URL must follow the pattern:
    postgresql+asyncpg://user:password@host:port/dbname
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.app.config import settings

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,          # Set to True to log SQL in development
    pool_pre_ping=True,  # Recycle dead connections automatically
    pool_size=10,
    max_overflow=20,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ---------------------------------------------------------------------------
# Declarative base shared by all ORM models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# FastAPI dependency — yields a DB session per request
# ---------------------------------------------------------------------------


async def get_db() -> AsyncSession:  # type: ignore[return]
    """
    Dependency that provides a transactional AsyncSession.

    Usage::

        @router.get("/")
        async def endpoint(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
