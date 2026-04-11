"""
tests/test_lobby_service.py — Тесты жизненного цикла лобби.

Что тестируем:
  • create_lobby — успех, дубль (уже в лобби)
  • join_lobby — успех, лобби полное, 18+ без верификации, paywall
  • start_game — мало игроков, не хост
  • leave_lobby — уход хоста закрывает лобби
  • close_lobby — статус и Redis
"""
import pytest
from datetime import datetime, timedelta

from app.database.models import LobbyStatus
from app.services.lobby_service import (
    close_lobby,
    create_lobby,
    get_lobby_by_hash,
    get_lobby_members,
    join_lobby,
    leave_lobby,
    start_game,
)
from app.config import settings


class TestCreateLobby:
    async def test_creates_lobby_successfully(self, db_session, make_user, mock_redis):
        host = await make_user(tg_id=1001)
        lobby = await create_lobby(db_session, host, is_18_plus=False)

        assert lobby.id is not None
        assert lobby.host_id == host.id
        assert lobby.is_18_plus is False
        assert lobby.status == LobbyStatus.WAITING
        assert len(lobby.join_hash) == 6

    async def test_host_added_as_member(self, db_session, make_user, mock_redis):
        """После создания лобби хост автоматически становится участником."""
        host = await make_user(tg_id=1002)
        lobby = await create_lobby(db_session, host, is_18_plus=False)

        members = await get_lobby_members(db_session, lobby.id)
        assert len(members) == 1
        assert str(members[0].user_id) == str(host.id)

    async def test_creates_18plus_lobby(self, db_session, make_user, mock_redis):
        host = await make_user(tg_id=1003)
        lobby = await create_lobby(db_session, host, is_18_plus=True)
        assert lobby.is_18_plus is True

    async def test_join_hash_is_unique_uppercase(self, db_session, make_user, mock_redis):
        host = await make_user(tg_id=1004)
        lobby = await create_lobby(db_session, host, is_18_plus=False)
        assert lobby.join_hash == lobby.join_hash.upper()

    async def test_raises_if_already_in_lobby(self, db_session, make_lobby, mock_redis):
        """Нельзя создать второе лобби, если уже в одном."""
        lobby, host = await make_lobby()
        with pytest.raises(ValueError, match="уже находитесь"):
            await create_lobby(db_session, host, is_18_plus=False)

    async def test_syncs_state_to_redis(self, db_session, make_user, mock_redis):
        """После создания состояние сохраняется в Redis."""
        host = await make_user(tg_id=1005)
        lobby = await create_lobby(db_session, host, is_18_plus=False)

        state = await mock_redis.get_lobby_state(str(lobby.id))
        assert state is not None
        assert state["join_hash"] == lobby.join_hash


class TestJoinLobby:
    async def test_join_regular_lobby(self, db_session, make_user, make_lobby, mock_redis):
        lobby, host = await make_lobby()
        player = await make_user(tg_id=2001)

        result_lobby, error = await join_lobby(db_session, player, lobby.join_hash)
        assert error == ""
        members = await get_lobby_members(db_session, lobby.id)
        assert len(members) == 2

    async def test_join_nonexistent_lobby(self, db_session, make_user, mock_redis):
        player = await make_user(tg_id=2002)
        _, error = await join_lobby(db_session, player, "XXXXXX")
        assert "не найдена" in error

    async def test_join_18plus_without_verification(self, db_session, make_user, mock_redis):
        """Незаверифицированный пользователь получает PAYWALL при входе в 18+ комнату."""
        host = await make_user(tg_id=2003, is_verified=True)
        player = await make_user(tg_id=2004, is_verified=False)

        # Хост создаёт 18+ лобби
        lobby = await create_lobby(db_session, host, is_18_plus=True)

        _, error = await join_lobby(db_session, player, lobby.join_hash)
        assert error == "PAYWALL"

    async def test_join_18plus_with_valid_verification(self, db_session, make_user, mock_redis):
        """Верифицированный пользователь может войти в 18+ комнату."""
        host = await make_user(tg_id=2005, is_verified=True)
        player = await make_user(tg_id=2006, is_verified=True)

        lobby = await create_lobby(db_session, host, is_18_plus=True)
        _, error = await join_lobby(db_session, player, lobby.join_hash)
        assert error == ""

    async def test_join_full_lobby(self, db_session, make_user, make_lobby, mock_redis):
        """При превышении max_lobby_size — ошибка."""
        lobby, host = await make_lobby()

        # Заполняем лобби до максимума
        for i in range(settings.max_lobby_size - 1):  # -1 т.к. хост уже внутри
            player = await make_user(tg_id=3000 + i)
            await join_lobby(db_session, player, lobby.join_hash)

        # Следующий должен получить ошибку
        extra = await make_user(tg_id=9999)
        _, error = await join_lobby(db_session, extra, lobby.join_hash)
        assert "заполнена" in error

    async def test_join_already_in_lobby(self, db_session, make_user, make_lobby, mock_redis):
        """Попытка войти в лобби, где уже состоишь — special code."""
        lobby, host = await make_lobby()
        player = await make_user(tg_id=2010)
        await join_lobby(db_session, player, lobby.join_hash)

        # Вторая попытка
        _, error = await join_lobby(db_session, player, lobby.join_hash)
        assert error == "already_joined"


class TestStartGame:
    async def test_start_requires_minimum_2_players(self, db_session, make_lobby, mock_redis):
        """Игра не стартует если только один игрок (хост)."""
        lobby, host = await make_lobby()
        success, error = await start_game(db_session, lobby, host)
        assert success is False
        assert "2 игрока" in error

    async def test_start_succeeds_with_2_players(self, db_session, make_user, make_lobby, mock_redis):
        lobby, host = await make_lobby()
        player = await make_user(tg_id=4001)
        await join_lobby(db_session, player, lobby.join_hash)

        success, error = await start_game(db_session, lobby, host)
        assert success is True
        assert error == ""
        assert lobby.status == LobbyStatus.ACTIVE

    async def test_only_host_can_start(self, db_session, make_user, make_lobby, mock_redis):
        lobby, host = await make_lobby()
        player = await make_user(tg_id=4002)
        await join_lobby(db_session, player, lobby.join_hash)

        # Игрок (не хост) пытается стартовать
        success, error = await start_game(db_session, lobby, player)
        assert success is False
        assert "создатель" in error


class TestLeaveLobby:
    async def test_host_leaving_closes_lobby(self, db_session, make_user, make_lobby, mock_redis):
        """Когда хост уходит — лобби закрывается."""
        lobby, host = await make_lobby()
        player = await make_user(tg_id=5001)
        await join_lobby(db_session, player, lobby.join_hash)

        closed = await leave_lobby(db_session, lobby, host)
        assert closed is True
        assert lobby.status == LobbyStatus.CLOSED

    async def test_player_leaving_does_not_close_lobby(self, db_session, make_user, make_lobby, mock_redis):
        """Уход обычного игрока не закрывает лобби."""
        lobby, host = await make_lobby()
        player1 = await make_user(tg_id=5002)
        player2 = await make_user(tg_id=5003)
        await join_lobby(db_session, player1, lobby.join_hash)
        await join_lobby(db_session, player2, lobby.join_hash)

        closed = await leave_lobby(db_session, lobby, player1)
        assert closed is False
        assert lobby.status == LobbyStatus.WAITING

    async def test_last_player_leaving_closes_lobby(self, db_session, make_user, make_lobby, mock_redis):
        """Если активная игра и вышел предпоследний — лобби закрывается."""
        lobby, host = await make_lobby(status=LobbyStatus.ACTIVE)
        player = await make_user(tg_id=5004)
        await join_lobby(db_session, player, lobby.join_hash)
        lobby.status = LobbyStatus.ACTIVE

        closed = await leave_lobby(db_session, lobby, player)
        assert closed is True


class TestCloseLobby:
    async def test_close_sets_status_and_timestamp(self, db_session, make_lobby, mock_redis):
        lobby, _ = await make_lobby()
        await close_lobby(db_session, lobby)

        assert lobby.status == LobbyStatus.CLOSED
        assert lobby.closed_at is not None

    async def test_close_removes_redis_state(self, db_session, make_lobby, mock_redis):
        lobby, _ = await make_lobby()
        # Убеждаемся что состояние есть в Redis
        state = await mock_redis.get_lobby_state(str(lobby.id))
        assert state is not None

        await close_lobby(db_session, lobby)

        # После закрытия состояние удалено
        state_after = await mock_redis.get_lobby_state(str(lobby.id))
        assert state_after is None
