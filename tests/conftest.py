"""
tests/conftest.py — Общие фикстуры для всех тестов.

Что здесь:
  • async_engine — SQLite в памяти. Не нужен реальный PostgreSQL.
  • db_session   — чистая сессия для каждого теста (rollback после).
  • mock_redis   — заглушка RedisClient. Хранит данные в обычном dict.
  • make_user    — фабрика: создать User с любыми параметрами.
  • make_lobby   — фабрика: создать Lobby.
"""
import uuid
from datetime import datetime, timedelta
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Используем SQLite для тестов — PostgreSQL-специфичные типы (UUID, ENUM)
# заменяем на совместимые через render_as_batch
from app.database.models import Base, Lobby, LobbyMember, LobbyStatus, TasksPool, TaskType, MediaRequired, User
from app.config import settings


# ─── Database ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def async_engine():
    """
    SQLite в памяти — пересоздаётся для каждого теста.
    UUID автоматически адаптируется под SQLite через TypeDecorator в models.py.
    Благодаря этому тесты изолированы: изменения одного не влияют на другой.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Сессия с автоматическим rollback после каждого теста.
    Это гарантирует чистое состояние БД для каждого теста.
    """
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


# ─── Redis mock ───────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis(monkeypatch):
    """
    Заменяем реальный RedisClient на заглушку.
    Данные хранятся в dict — быстро, без подключения к Redis.
    """
    storage: dict = {}
    sets: dict = {}

    mock = MagicMock()

    async def _set(key, value, ttl=None):
        storage[key] = value

    async def _get(key):
        return storage.get(key)

    async def _delete(*keys):
        for k in keys:
            storage.pop(k, None)
            sets.pop(k, None)

    async def _is_banned(tg_id):
        return storage.get(f"blacklist:{tg_id}") is not None

    async def _ban_user(tg_id, ttl_seconds=None):
        storage[f"blacklist:{tg_id}"] = "1"

    async def _set_lobby_state(lobby_id, state, ttl=86400):
        storage[f"lobby:{lobby_id}:state"] = state

    async def _get_lobby_state(lobby_id):
        return storage.get(f"lobby:{lobby_id}:state")

    async def _delete_lobby_state(lobby_id):
        storage.pop(f"lobby:{lobby_id}:state", None)
        sets.pop(f"lobby:{lobby_id}:viewed", None)

    async def _add_viewed(lobby_id, user_tg_id):
        key = f"lobby:{lobby_id}:viewed"
        if key not in sets:
            sets[key] = set()
        sets[key].add(str(user_tg_id))
        return len(sets[key])

    async def _get_viewed_count(lobby_id):
        return len(sets.get(f"lobby:{lobby_id}:viewed", set()))

    async def _reset_viewed(lobby_id):
        sets.pop(f"lobby:{lobby_id}:viewed", None)

    async def _rate_limit_check(tg_id, action, max_count, window_seconds):
        return True  # в тестах всегда разрешаем

    mock.set = _set
    mock.get = _get
    mock.delete = _delete
    mock.is_banned = _is_banned
    mock.ban_user = _ban_user
    mock.set_lobby_state = _set_lobby_state
    mock.get_lobby_state = _get_lobby_state
    mock.delete_lobby_state = _delete_lobby_state
    mock.add_viewed = _add_viewed
    mock.get_viewed_count = _get_viewed_count
    mock.reset_viewed = _reset_viewed
    mock.rate_limit_check = _rate_limit_check
    mock._storage = storage  # для проверок в тестах

    # Патчим везде где используется redis_client
    monkeypatch.setattr("app.utils.redis_client.redis_client", mock)
    monkeypatch.setattr("app.services.lobby_service.redis_client", mock)
    monkeypatch.setattr("app.services.game_service.redis_client", mock)

    return mock


# ─── Factories ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def make_user(db_session):
    """
    Фабрика пользователей.
    Использование:
        user = await make_user()
        admin = await make_user(tg_id=999, is_verified=True)
    """
    created_users = []

    async def _factory(
        tg_id: int = None,
        username: str = "testuser",
        first_name: str = "Test",
        is_verified: bool = False,
        stars_balance: int = 0,
        is_banned: bool = False,
        games_played: int = 0,
        games_won: int = 0,
        dares_completed: int = 0,
        truths_answered: int = 0,
    ) -> User:
        if tg_id is None:
            tg_id = 100000 + len(created_users)

        user = User(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            is_verified=is_verified,
            verified_expires_at=(
                datetime.utcnow() + timedelta(days=30) if is_verified else None
            ),
            stars_balance=stars_balance,
            is_banned=is_banned,
            games_played=games_played,
            games_won=games_won,
            dares_completed=dares_completed,
            truths_answered=truths_answered,
        )
        db_session.add(user)
        await db_session.flush()
        created_users.append(user)
        return user

    return _factory


@pytest_asyncio.fixture
async def make_lobby(db_session, make_user, mock_redis):
    """Фабрика лобби."""
    async def _factory(
        host=None,
        is_18_plus: bool = False,
        status: LobbyStatus = LobbyStatus.WAITING,
        join_hash: str = None,
    ) -> tuple[Lobby, User]:
        if host is None:
            host = await make_user()

        lobby = Lobby(
            host_id=host.id,
            is_18_plus=is_18_plus,
            join_hash=join_hash or uuid.uuid4().hex[:6].upper(),
            status=status,
            current_round=0 if status == LobbyStatus.WAITING else 1,
        )
        db_session.add(lobby)
        await db_session.flush()

        # Добавляем хоста как участника
        member = LobbyMember(
            lobby_id=lobby.id,
            user_id=host.id,
            lives=settings.default_lives,
        )
        db_session.add(member)
        await db_session.flush()

        return lobby, host

    return _factory


@pytest_asyncio.fixture
async def make_task(db_session):
    """Фабрика заданий."""
    async def _factory(
        task_type: TaskType = TaskType.DARE,
        is_18_plus: bool = False,
        text: str = "Сделай что-нибудь",
        media_required: MediaRequired = MediaRequired.NONE,
    ) -> TasksPool:
        task = TasksPool(
            type=task_type,
            is_18_plus=is_18_plus,
            text=text,
            media_required=media_required,
        )
        db_session.add(task)
        await db_session.flush()
        return task

    return _factory
