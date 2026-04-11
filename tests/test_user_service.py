"""
tests/test_user_service.py — Тесты сервиса пользователей.

Что тестируем:
  • get_or_create_user — создание нового, обновление существующего
  • activate_verified — правильный расчёт срока подписки
  • deduct_stars — граничный случай (недостаточно Stars)
  • ban_user_db — флаги выставляются корректно
"""
from datetime import datetime, timedelta

import pytest

from app.services.user_service import (
    activate_verified,
    add_stars,
    ban_user_db,
    deduct_stars,
    get_or_create_user,
    get_user_by_tg_id,
)
from app.config import settings


class TestGetOrCreateUser:
    async def test_creates_new_user(self, db_session):
        """Новый пользователь создаётся с правильными данными."""
        user = await get_or_create_user(
            session=db_session,
            tg_id=12345,
            username="alice",
            first_name="Alice",
        )
        assert user.tg_id == 12345
        assert user.username == "alice"
        assert user.first_name == "Alice"
        assert user.is_banned is False
        assert user.stars_balance == 0
        assert user.id is not None

    async def test_returns_existing_user(self, db_session):
        """Повторный вызов возвращает того же пользователя, не создаёт нового."""
        user1 = await get_or_create_user(db_session, tg_id=111, username="bob", first_name="Bob")
        user2 = await get_or_create_user(db_session, tg_id=111, username="bob", first_name="Bob")
        assert user1.id == user2.id

    async def test_updates_username_on_second_call(self, db_session):
        """Если пользователь сменил username — обновляем при следующем заходе."""
        await get_or_create_user(db_session, tg_id=222, username="old_name", first_name="Carl")
        updated = await get_or_create_user(db_session, tg_id=222, username="new_name", first_name="Carl")
        assert updated.username == "new_name"

    async def test_updates_last_active_at(self, db_session):
        """last_active_at всегда обновляется."""
        user = await get_or_create_user(db_session, tg_id=333, username="dan", first_name="Dan")
        assert user.last_active_at is not None


class TestActivateVerified:
    async def test_sets_verified_flag(self, db_session, make_user):
        """После активации флаг is_verified = True."""
        user = await make_user(tg_id=500)
        assert not user.is_verified

        await activate_verified(db_session, user)
        assert user.is_verified is True

    async def test_sets_expiry_to_30_days_from_now(self, db_session, make_user):
        """Срок подписки = сейчас + VERIFIED_DURATION_DAYS."""
        user = await make_user(tg_id=501)
        before = datetime.utcnow()
        await activate_verified(db_session, user)
        after = datetime.utcnow()

        expected_min = before + timedelta(days=settings.verified_duration_days)
        expected_max = after + timedelta(days=settings.verified_duration_days)

        assert expected_min <= user.verified_expires_at <= expected_max

    async def test_extends_existing_subscription(self, db_session, make_user):
        """
        Если подписка ещё активна и пользователь платит снова —
        срок продлевается от текущей даты окончания, а не от сегодня.
        """
        future_expiry = datetime.utcnow() + timedelta(days=10)
        user = await make_user(tg_id=502, is_verified=True)
        user.verified_expires_at = future_expiry

        await activate_verified(db_session, user)

        # Новый срок должен быть примерно: future_expiry + 30 дней
        expected = future_expiry + timedelta(days=settings.verified_duration_days)
        diff = abs((user.verified_expires_at - expected).total_seconds())
        assert diff < 5  # допуск 5 секунд на выполнение теста


class TestStarsBalance:
    async def test_add_stars(self, db_session, make_user):
        user = await make_user(tg_id=600, stars_balance=10)
        await add_stars(db_session, user, 50)
        assert user.stars_balance == 60

    async def test_deduct_stars_success(self, db_session, make_user):
        user = await make_user(tg_id=601, stars_balance=100)
        result = await deduct_stars(db_session, user, 30)
        assert result is True
        assert user.stars_balance == 70

    async def test_deduct_stars_insufficient_balance(self, db_session, make_user):
        """Если Stars не хватает — возвращаем False, баланс не меняется."""
        user = await make_user(tg_id=602, stars_balance=5)
        result = await deduct_stars(db_session, user, 10)
        assert result is False
        assert user.stars_balance == 5  # не изменился

    async def test_deduct_exactly_available(self, db_session, make_user):
        """Граничный случай: списать ровно столько, сколько есть."""
        user = await make_user(tg_id=603, stars_balance=10)
        result = await deduct_stars(db_session, user, 10)
        assert result is True
        assert user.stars_balance == 0


class TestBanUser:
    async def test_ban_sets_flags(self, db_session, make_user):
        user = await make_user(tg_id=700)
        await ban_user_db(db_session, user, reason="Нарушение правил")
        assert user.is_banned is True
        assert user.ban_reason == "Нарушение правил"

    async def test_get_user_by_tg_id(self, db_session, make_user):
        user = await make_user(tg_id=800, username="findme")
        found = await get_user_by_tg_id(db_session, tg_id=800)
        assert found is not None
        assert found.id == user.id

    async def test_get_nonexistent_user(self, db_session):
        result = await get_user_by_tg_id(db_session, tg_id=999999)
        assert result is None
