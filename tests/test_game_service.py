"""
tests/test_game_service.py — Тесты игровой логики.

Что тестируем:
  • get_next_task  — фильтр по 18+, взвешенность (редкие задания берутся чаще)
  • complete_task  — очки, продвижение хода
  • surrender_task — потеря жизни, выбывание при 0 жизнях
  • buyout_task    — списание Stars, недостаточно Stars
"""
import pytest

from app.database.models import LobbyStatus, LobbyMember, TaskType, MediaRequired
from app.services.game_service import (
    complete_task,
    get_next_task,
    surrender_task,
    buyout_task,
)
from app.services.lobby_service import join_lobby, start_game
from app.config import settings


class TestGetNextTask:
    async def test_returns_task_for_regular_mode(self, db_session, make_task):
        """В обычном режиме возвращаем только не-18+ задания."""
        regular_task = await make_task(task_type=TaskType.DARE, is_18_plus=False)
        adult_task = await make_task(task_type=TaskType.DARE, is_18_plus=True)

        task = await get_next_task(db_session, is_18_plus=False, task_type=TaskType.DARE)
        assert task is not None
        assert task.is_18_plus is False

    async def test_returns_any_task_for_18plus_mode(self, db_session, make_task):
        """В 18+ режиме возвращаем любые задания (обычные тоже допустимы)."""
        await make_task(task_type=TaskType.TRUTH, is_18_plus=True)
        task = await get_next_task(db_session, is_18_plus=True, task_type=TaskType.TRUTH)
        assert task is not None

    async def test_returns_none_when_no_tasks(self, db_session):
        """Если пул пуст — возвращаем None без ошибки."""
        task = await get_next_task(db_session, is_18_plus=False, task_type=TaskType.TRUTH)
        assert task is None

    async def test_increments_times_used(self, db_session, make_task):
        """Счётчик использования увеличивается при каждом вызове."""
        task = await make_task(task_type=TaskType.DARE)
        assert task.times_used == 0

        await get_next_task(db_session, is_18_plus=False, task_type=TaskType.DARE)
        assert task.times_used == 1

    async def test_less_used_tasks_get_priority(self, db_session, make_task):
        """
        Задания с меньшим times_used получают больший вес.
        Проверяем статистически: из 100 запросов свежая задача должна
        встречаться чаще, чем 'старая' (times_used=50).
        """
        fresh_task = await make_task(task_type=TaskType.DARE, text="Свежее")
        old_task = await make_task(task_type=TaskType.DARE, text="Старое")
        old_task.times_used = 50
        await db_session.flush()

        fresh_count = 0
        for _ in range(100):
            t = await get_next_task(db_session, is_18_plus=False, task_type=TaskType.DARE)
            if t and t.text == "Свежее":
                fresh_count += 1

        # Свежая задача должна встречаться значительно чаще (ожидаем > 70%)
        assert fresh_count > 70


class TestCompleteTask:
    async def _setup_active_game(self, db_session, make_user, make_lobby, mock_redis):
        """Вспомогательный метод: создаём активную игру с 2 игроками."""
        lobby, host = await make_lobby()
        player = await make_user(tg_id=70001)
        await join_lobby(db_session, player, lobby.join_hash)
        await start_game(db_session, lobby, host)

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        result = await db_session.execute(
            select(LobbyMember)
            .where(LobbyMember.lobby_id == lobby.id, LobbyMember.user_id == host.id)
            .options(selectinload(LobbyMember.user))
        )
        host_member = result.scalar_one()
        return lobby, host_member, player

    async def test_complete_without_media_gives_2_points(self, db_session, make_user, make_lobby, mock_redis):
        lobby, host_member, _ = await self._setup_active_game(db_session, make_user, make_lobby, mock_redis)
        result = await complete_task(db_session, lobby, host_member, with_media=False)
        assert result["points_earned"] == 2
        assert host_member.score == 2

    async def test_complete_with_media_gives_3_points(self, db_session, make_user, make_lobby, mock_redis):
        lobby, host_member, _ = await self._setup_active_game(db_session, make_user, make_lobby, mock_redis)
        result = await complete_task(db_session, lobby, host_member, with_media=True)
        assert result["points_earned"] == 3
        assert host_member.score == 3

    async def test_complete_advances_turn(self, db_session, make_user, make_lobby, mock_redis):
        """После выполнения задания счётчик раунда увеличивается."""
        lobby, host_member, _ = await self._setup_active_game(db_session, make_user, make_lobby, mock_redis)
        initial_round = lobby.current_round
        await complete_task(db_session, lobby, host_member, with_media=False)
        assert lobby.current_round > initial_round


class TestSurrenderTask:
    async def _make_member(self, db_session, make_user, make_lobby, mock_redis, lives=3):
        lobby, host = await make_lobby()
        player = await make_user(tg_id=80001)
        await join_lobby(db_session, player, lobby.join_hash)
        await start_game(db_session, lobby, host)

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        result = await db_session.execute(
            select(LobbyMember)
            .where(LobbyMember.lobby_id == lobby.id, LobbyMember.user_id == host.id)
            .options(selectinload(LobbyMember.user))
        )
        member = result.scalar_one()
        member.lives = lives
        return lobby, member

    async def test_surrender_decrements_lives(self, db_session, make_user, make_lobby, mock_redis):
        lobby, member = await self._make_member(db_session, make_user, make_lobby, mock_redis, lives=3)
        result = await surrender_task(db_session, lobby, member)
        assert member.lives == 2
        assert result["eliminated"] is False

    async def test_surrender_eliminates_at_zero_lives(self, db_session, make_user, make_lobby, mock_redis):
        """При 1 жизни — сдача выбивает из игры."""
        lobby, member = await self._make_member(db_session, make_user, make_lobby, mock_redis, lives=1)
        result = await surrender_task(db_session, lobby, member)
        assert member.lives == 0
        assert result["eliminated"] is True
        assert member.is_active is False

    async def test_game_over_when_one_player_remains(self, db_session, make_user, make_lobby, mock_redis):
        """Если выбывает предпоследний — game_over."""
        lobby, member = await self._make_member(db_session, make_user, make_lobby, mock_redis, lives=1)
        result = await surrender_task(db_session, lobby, member)
        assert result["game_over"] is True


class TestBuyoutTask:
    async def _setup_game_with_member(self, db_session, make_user, make_lobby, mock_redis, stars=10):
        lobby, host = await make_lobby()
        player = await make_user(tg_id=90001)
        await join_lobby(db_session, player, lobby.join_hash)
        await start_game(db_session, lobby, host)

        host.stars_balance = stars
        await db_session.flush()

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        result = await db_session.execute(
            select(LobbyMember)
            .where(LobbyMember.lobby_id == lobby.id, LobbyMember.user_id == host.id)
            .options(selectinload(LobbyMember.user))
        )
        member = result.scalar_one()
        return lobby, member

    async def test_buyout_deducts_stars(self, db_session, make_user, make_lobby, mock_redis):
        lobby, member = await self._setup_game_with_member(
            db_session, make_user, make_lobby, mock_redis, stars=100
        )
        initial_balance = member.user.stars_balance
        success, _ = await buyout_task(db_session, lobby, member)

        assert success is True
        assert member.user.stars_balance == initial_balance - settings.buyout_cost_stars

    async def test_buyout_fails_if_not_enough_stars(self, db_session, make_user, make_lobby, mock_redis):
        """Нет Stars — откуп не проходит, баланс не меняется."""
        lobby, member = await self._setup_game_with_member(
            db_session, make_user, make_lobby, mock_redis, stars=0
        )
        success, error = await buyout_task(db_session, lobby, member)

        assert success is False
        assert "Недостаточно" in error
        assert member.user.stars_balance == 0
