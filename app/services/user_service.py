"""
app/services/user_service.py — CRUD операции с пользователями.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User


async def get_or_create_user(
    session: AsyncSession,
    tg_id: int,
    username: Optional[str],
    first_name: str,
) -> tuple[User, bool]:
    """Upsert: получить или создать юзера. Возвращает (user, is_new)."""
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_active_at=datetime.utcnow(),
        )
        session.add(user)
        await session.flush()
        return user, True
    else:
        user.last_active_at = datetime.utcnow()
        if username:
            user.username = username
        if first_name:
            user.first_name = first_name
        return user, False


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


async def activate_verified(session: AsyncSession, user: User) -> User:
    """Активировать подписку Verified 18+ на N дней."""
    now = datetime.utcnow()
    # Если подписка ещё активна — продлеваем от текущей даты окончания
    if user.verified_expires_at and user.verified_expires_at > now:
        user.verified_expires_at += timedelta(days=settings.verified_duration_days)
    else:
        user.verified_expires_at = now + timedelta(days=settings.verified_duration_days)
    user.is_verified = True
    await session.flush()
    return user


async def add_stars(session: AsyncSession, user: User, amount: int) -> User:
    user.stars_balance += amount
    await session.flush()
    return user


async def deduct_stars(session: AsyncSession, user: User, amount: int) -> bool:
    """Снять Stars с баланса. Возвращает False если недостаточно."""
    if user.stars_balance < amount:
        return False
    user.stars_balance -= amount
    await session.flush()
    return True


async def ban_user_db(
    session: AsyncSession,
    user: User,
    reason: str,
) -> User:
    user.is_banned = True
    user.ban_reason = reason
    await session.flush()
    return user


async def unban_user_db(
    session: AsyncSession,
    user: User,
) -> User:
    user.is_banned = False
    user.ban_reason = None
    await session.flush()
    return user
