"""
app/database/session.py — Connection pool оптимизированный для высокой нагрузки.
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

# ─── Engine — настроен для 500+ параллельных пользователей ───────────────────
engine: AsyncEngine = create_async_engine(
    settings.database_url,

    # Pool: 20 постоянных + 40 временных = 60 max соединений
    # PostgreSQL default limit = 100, оставляем запас для admin/alembic
    pool_size=20,
    max_overflow=40,

    pool_pre_ping=True,       # проверяем соединение перед использованием
    pool_recycle=1800,         # пересоздаём соединения старше 30 мин
    pool_timeout=10,           # ждём свободное соединение max 10 сек
    pool_reset_on_return="rollback",  # откатываем незакоммиченное при возврате

    # Производительность
    echo=False,
    future=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager для использования вне FastAPI."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
