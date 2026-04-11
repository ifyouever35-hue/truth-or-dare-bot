"""
app/database/session.py — Фабрика сессий SQLAlchemy (async).

Паттерн: один engine на весь процесс, сессия создаётся per-request
через async context manager или DI в FastAPI.
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# ─── Engine ───────────────────────────────────────────────────────────────────
# pool_size + max_overflow: при 10 воркерах и 20 соединений у каждого
# суммарно не превышаем стандартный лимит PostgreSQL (100).
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,      # проверяем соединение перед использованием
    pool_recycle=1800,        # сбрасываем соединения старше 30 минут
    echo=False,               # True — логировать SQL в dev
)

# ─── Session factory ──────────────────────────────────────────────────────────
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # объекты доступны после commit() без re-fetch
    autoflush=False,
)


# ─── Dependency ───────────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — inject сессию в роут."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager для использования вне FastAPI (handlers, services)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
