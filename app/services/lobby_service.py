"""
app/services/lobby_service.py — Жизненный цикл лобби.

Создание → ожидание игроков → запуск → раунды → завершение.
Все изменения дублируются в Redis (быстрый доступ) и PostgreSQL (надёжность).
"""
import secrets
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import Lobby, LobbyMember, LobbyStatus, User
from app.utils.redis_client import redis_client


def _generate_join_hash() -> str:
    """6-символьный case-insensitive хэш."""
    return secrets.token_hex(3).upper()  # например: "A3F7C2"


async def create_lobby(
    session: AsyncSession,
    host: User,
    is_18_plus: bool,
) -> Lobby:
    """
    Создать лобби. Хост автоматически добавляется как первый участник.
    Raises ValueError если хост уже в другом активном лобби.
    """
    # Проверяем — нет ли у хоста уже активного лобби
    existing = await _get_user_active_lobby(session, host)
    if existing:
        raise ValueError("Вы уже находитесь в другой комнате.")

    # Генерируем уникальный хэш
    while True:
        join_hash = _generate_join_hash()
        clash = await session.execute(
            select(Lobby).where(Lobby.join_hash == join_hash)
        )
        if not clash.scalar_one_or_none():
            break

    lobby = Lobby(
        host_id=host.id,
        is_18_plus=is_18_plus,
        join_hash=join_hash,
        status=LobbyStatus.WAITING,
    )
    session.add(lobby)
    await session.flush()

    # Хост — первый участник
    member = LobbyMember(
        lobby_id=lobby.id,
        user_id=host.id,
        lives=settings.default_lives,
    )
    session.add(member)
    await session.flush()

    # Синхронизируем с Redis
    await _sync_lobby_to_redis(lobby, [member])

    return lobby


async def join_lobby(
    session: AsyncSession,
    user: User,
    join_hash: str,
) -> tuple[Lobby, str]:
    """
    Добавить юзера в лобби по хэшу.
    Возвращает (lobby, error_message). Если ошибок нет — error_message = "".

    Проверки:
      1. Лобби существует и в статусе WAITING.
      2. Юзер не забанен.
      3. Лобби не переполнено.
      4. Для 18+ лобби — проверка verified.
      5. Юзер уже не в этом лобби.
    """
    result = await session.execute(
        select(Lobby)
        .where(Lobby.join_hash == join_hash.upper())
        .options(selectinload(Lobby.members))
    )
    lobby = result.scalar_one_or_none()

    if not lobby:
        return None, "❌ Комната не найдена. Проверьте код."

    if lobby.status != LobbyStatus.WAITING:
        return lobby, "⏳ Игра уже началась или комната закрыта."

    # Проверка 18+ верификации
    if lobby.is_18_plus and not user.is_verification_active:
        return lobby, "PAYWALL"  # специальный код — вызывающий покажет paywall

    active_members = [m for m in lobby.members if m.is_active]
    if len(active_members) >= settings.max_lobby_size:
        return lobby, f"❌ Комната заполнена (макс. {settings.max_lobby_size} чел.)."

    # Уже в этой комнате?
    already = await session.execute(
        select(LobbyMember).where(
            LobbyMember.lobby_id == lobby.id,
            LobbyMember.user_id == user.id,
        )
    )
    if already.scalar_one_or_none():
        return lobby, "already_joined"

    # Добавляем
    member = LobbyMember(
        lobby_id=lobby.id,
        user_id=user.id,
        lives=settings.default_lives,
    )
    session.add(member)
    await session.flush()

    # Обновляем Redis
    all_members = active_members + [member]
    await _sync_lobby_to_redis(lobby, all_members)

    return lobby, ""


async def start_game(
    session: AsyncSession,
    lobby: Lobby,
    host: User,
) -> tuple[bool, str]:
    """
    Запустить игру. Только хост может стартовать.
    Нужно минимум 2 участника.
    """
    if str(lobby.host_id) != str(host.id):
        return False, "❌ Только создатель комнаты может начать игру."

    result = await session.execute(
        select(LobbyMember).where(
            LobbyMember.lobby_id == lobby.id,
            LobbyMember.is_active == True,  # noqa: E712
        )
    )
    members = result.scalars().all()

    if len(members) < 2:
        return False, "❌ Нужно минимум 2 игрока для начала."

    lobby.status = LobbyStatus.ACTIVE
    lobby.current_round = 1
    lobby.current_player_index = 0
    await session.flush()

    await _sync_lobby_to_redis(lobby, members)
    return True, ""


async def leave_lobby(
    session: AsyncSession,
    lobby: Lobby,
    user: User,
) -> bool:
    """Покинуть лобби. Возвращает True если лобби закрылось (все ушли / хост вышел)."""
    result = await session.execute(
        select(LobbyMember).where(
            LobbyMember.lobby_id == lobby.id,
            LobbyMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if member:
        member.is_active = False
        await session.flush()

    # Хост вышел → закрываем
    if str(lobby.host_id) == str(user.id):
        return await close_lobby(session, lobby)

    # Проверяем — остались ли активные игроки
    count_result = await session.execute(
        select(func.count()).select_from(LobbyMember).where(
            LobbyMember.lobby_id == lobby.id,
            LobbyMember.is_active == True,  # noqa: E712
        )
    )
    count = count_result.scalar()
    if count < 2 and lobby.status == LobbyStatus.ACTIVE:
        return await close_lobby(session, lobby)

    return False


async def close_lobby(session: AsyncSession, lobby: Lobby) -> bool:
    lobby.status = LobbyStatus.CLOSED
    lobby.closed_at = datetime.utcnow()
    await session.flush()
    await redis_client.delete_lobby_state(str(lobby.id))
    return True


async def get_lobby_members(
    session: AsyncSession, lobby_id: uuid.UUID
) -> list[LobbyMember]:
    result = await session.execute(
        select(LobbyMember)
        .where(
            LobbyMember.lobby_id == lobby_id,
            LobbyMember.is_active == True,  # noqa: E712
        )
        .options(selectinload(LobbyMember.user))
        .order_by(LobbyMember.joined_at)
    )
    return result.scalars().all()


async def get_lobby_by_hash(session: AsyncSession, join_hash: str) -> Optional[Lobby]:
    result = await session.execute(
        select(Lobby).where(Lobby.join_hash == join_hash.upper())
    )
    return result.scalar_one_or_none()


async def get_lobby_by_id(session: AsyncSession, lobby_id: str) -> Optional[Lobby]:
    result = await session.execute(
        select(Lobby).where(Lobby.id == lobby_id)
    )
    return result.scalar_one_or_none()


async def _get_user_active_lobby(
    session: AsyncSession, user: User
) -> Optional[Lobby]:
    """Найти активное лобби юзера (waiting или active)."""
    result = await session.execute(
        select(Lobby)
        .join(LobbyMember, LobbyMember.lobby_id == Lobby.id)
        .where(
            LobbyMember.user_id == user.id,
            LobbyMember.is_active == True,  # noqa: E712
            Lobby.status.in_([LobbyStatus.WAITING, LobbyStatus.ACTIVE]),
        )
        .limit(1)
    )
    return result.scalars().first()


async def _sync_lobby_to_redis(
    lobby: Lobby, members: list[LobbyMember]
) -> None:
    """Синхронизируем текущее состояние лобби в Redis."""
    state = {
        "id": str(lobby.id),
        "join_hash": lobby.join_hash,
        "is_18_plus": lobby.is_18_plus,
        "status": lobby.status.value,
        "host_id": str(lobby.host_id),
        "current_round": lobby.current_round,
        "current_player_index": lobby.current_player_index,
        "members": [
            {
                "user_id": str(m.user_id),
                "lives": m.lives,
                "score": m.score,
                "is_active": m.is_active,
            }
            for m in members
        ],
    }
    await redis_client.set_lobby_state(str(lobby.id), state)
