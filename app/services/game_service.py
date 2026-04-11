"""
app/services/game_service.py — Игровая логика: выдача заданий, таймер, очки.

Поток одного хода:
  1. get_next_task() — получить случайное задание из tasks_pool.
  2. Бот отправляет задание активному игроку.
  3. Запускается asyncio.Task с таймером (task_timer_seconds).
  4. Игрок нажимает «Выполнил» / «Сдаться» / «Откупиться»:
       complete_task() / surrender_task() / buyout_task()
  5. Таймер отменяется, ход переходит следующему игроку.

Важно: таймер — это asyncio.Task, который хранится в dict {lobby_id: Task}.
При перезапуске бота таймеры теряются — это нормально для MVP.
В продакшне можно хранить expires_at в Redis и восстанавливать при старте.
"""
import asyncio
import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Lobby, LobbyMember, TasksPool, TaskType
from app.database.session import get_db_context
from app.utils.redis_client import redis_client


# Словарь активных таймеров: lobby_id → asyncio.Task
_active_timers: dict[str, asyncio.Task] = {}


async def get_next_task(
    session: AsyncSession,
    is_18_plus: bool,
    task_type: TaskType,
) -> Optional[TasksPool]:
    """
    Получить случайное задание из пула.
    Для обычного режима возвращаем только !is_18_plus задания.
    """
    query = select(TasksPool).where(
        TasksPool.type == task_type,
        TasksPool.is_active == True,  # noqa: E712
    )
    if not is_18_plus:
        query = query.where(TasksPool.is_18_plus == False)  # noqa: E712

    result = await session.execute(query)
    tasks = result.scalars().all()

    if not tasks:
        return None

    # Взвешенная случайность: задания с меньшим times_used получают приоритет
    # Это предотвращает повторение одних и тех же заданий в одной сессии
    weights = [1 / (t.times_used + 1) for t in tasks]
    chosen = random.choices(tasks, weights=weights, k=1)[0]

    # Увеличиваем счётчик использования
    chosen.times_used += 1
    await session.flush()

    return chosen


async def complete_task(
    session: AsyncSession,
    lobby: Lobby,
    member: LobbyMember,
    with_media: bool = False,
) -> dict:
    """
    Игрок выполнил задание.
    Очки: +3 за выполнение с медиа, +2 за обычное выполнение.
    Переход хода к следующему игроку.
    """
    points = 3 if with_media else 2
    member.score += points

    # Обновляем статистику пользователя
    if lobby.current_task_id:
        task_result = await session.execute(
            select(TasksPool).where(TasksPool.id == lobby.current_task_id)
        )
        task = task_result.scalar_one_or_none()
        if task:
            if task.type == TaskType.TRUTH:
                member.user.truths_answered += 1
            else:
                member.user.dares_completed += 1

    await session.flush()
    _cancel_timer(str(lobby.id))

    turn = await _advance_turn(session, lobby)
    return {
        "points_earned": points,
        "new_score": member.score,
        "game_over": turn["game_over"],
        "winner": turn.get("winner"),
        "next_player": turn.get("next_player"),
    }


async def surrender_task(
    session: AsyncSession,
    lobby: Lobby,
    member: LobbyMember,
) -> dict:
    """Игрок сдался — теряет жизнь."""
    member.lives -= 1
    is_eliminated = member.lives <= 0

    if is_eliminated:
        member.is_active = False

    await session.flush()
    _cancel_timer(str(lobby.id))

    # Проверяем — не осталось ли один игрок (конец игры)
    active_result = await session.execute(
        select(LobbyMember).where(
            LobbyMember.lobby_id == lobby.id,
            LobbyMember.is_active == True,  # noqa: E712
        )
    )
    active_members = active_result.scalars().all()

    if len(active_members) <= 1:
        return {"eliminated": is_eliminated, "game_over": True, "winner": active_members[0] if active_members else None}

    turn = await _advance_turn(session, lobby)
    return {
        "eliminated": is_eliminated,
        "game_over": turn["game_over"],
        "winner": turn.get("winner"),
        "next_player": turn.get("next_player"),
    }


async def buyout_task(
    session: AsyncSession,
    lobby: Lobby,
    member: LobbyMember,
) -> tuple[bool, str]:
    """
    Откупиться за Stars.
    Проверяет баланс → списывает → переводит ход.
    Возвращает (success, error_message).
    """
    from app.services.user_service import deduct_stars

    if member.user.stars_balance < settings.buyout_cost_stars:
        return False, f"❌ Недостаточно Stars. Нужно {settings.buyout_cost_stars}, у вас {member.user.stars_balance}."

    success = await deduct_stars(session, member.user, settings.buyout_cost_stars)
    if not success:
        return False, "❌ Ошибка списания Stars."

    _cancel_timer(str(lobby.id))
    turn = await _advance_turn(session, lobby)
    return True, "", turn


async def start_task_timer(
    lobby_id: str,
    message_id: int,
    chat_id: int,
    bot,
) -> None:
    """
    Таймер задания. Каждые 30 секунд отправляет напоминание.
    По истечении — засчитываем сдачу (-1 жизнь).
    """
    _cancel_timer(lobby_id)

    async def _timer_task():
        total = settings.task_timer_seconds
        try:
            # Напоминания на 60 сек и 30 сек до конца
            checkpoints = []
            if total > 60:
                checkpoints.append(total - 60)   # за 60 сек до конца
            if total > 30:
                checkpoints.append(total - 30)   # за 30 сек до конца

            elapsed = 0
            for checkpoint in checkpoints:
                wait = checkpoint - elapsed
                if wait > 0:
                    await asyncio.sleep(wait)
                    elapsed = checkpoint
                remaining = total - elapsed
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"⏰ Осталось <b>{remaining} сек.</b> на выполнение задания!",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

            # Ждём оставшееся время
            await asyncio.sleep(total - elapsed)

            # Время вышло
            async with get_db_context() as session:
                from app.database.models import Lobby, LobbyStatus
                result = await session.execute(
                    select(Lobby).where(Lobby.id == lobby_id)
                )
                lobby = result.scalar_one_or_none()
                if lobby and lobby.status == LobbyStatus.ACTIVE:
                    members = await _get_ordered_members(session, lobby)
                    if members:
                        current_member = members[lobby.current_player_index % len(members)]
                        surrender_result = await surrender_task(session, lobby, current_member)
                        try:
                            await bot.send_message(
                                chat_id=chat_id,
                                text=f"⏰ Время вышло! <b>{current_member.user.first_name}</b> потерял жизнь (-1 ❤️).",
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                        # Если игра не закончилась — уведомляем следующего игрока
                        if not surrender_result.get("game_over"):
                            from app.services.lobby_service import get_lobby_members
                            from app.bot.handlers.game import send_turn_notification
                            updated_members = await get_lobby_members(session, lobby.id)
                            await send_turn_notification(bot, lobby, updated_members)

        except asyncio.CancelledError:
            pass  # Нормально — таймер отменён игроком

    task = asyncio.create_task(_timer_task())
    _active_timers[lobby_id] = task


def _cancel_timer(lobby_id: str) -> None:
    """Отменяем активный таймер лобби."""
    task = _active_timers.pop(lobby_id, None)
    if task and not task.done():
        task.cancel()


async def _advance_turn(session: AsyncSession, lobby: Lobby) -> dict:
    """
    Переходим к следующему активному игроку.
    Возвращает dict: {'next_player': ..., 'game_over': bool, 'winner': ...}
    """
    members = await _get_ordered_members(session, lobby)
    if not members:
        return {"next_player": None, "game_over": True, "winner": None}

    next_index = (lobby.current_player_index + 1) % len(members)
    lobby.current_player_index = next_index
    lobby.current_round += 1
    lobby.current_task_id = None
    lobby.task_expires_at = None
    await session.flush()

    # Проверяем лимит раундов (если задан)
    if settings.max_rounds > 0 and lobby.current_round > settings.max_rounds * len(members):
        # Определяем победителя по очкам
        winner = max(members, key=lambda m: m.score, default=None)
        return {"next_player": None, "game_over": True, "winner": winner}

    from app.services.lobby_service import _sync_lobby_to_redis
    await _sync_lobby_to_redis(lobby, members)

    return {"next_player": members[next_index], "game_over": False, "winner": None}


async def _get_ordered_members(
    session: AsyncSession, lobby: Lobby
) -> list[LobbyMember]:
    """Активные игроки в порядке присоединения."""
    from sqlalchemy.orm import selectinload
    result = await session.execute(
        select(LobbyMember)
        .where(
            LobbyMember.lobby_id == lobby.id,
            LobbyMember.is_active == True,  # noqa: E712
        )
        .options(selectinload(LobbyMember.user))
        .order_by(LobbyMember.joined_at)
    )
    return result.scalars().all()


def get_current_player_index(lobby: Lobby, members_count: int) -> int:
    return lobby.current_player_index % max(members_count, 1)
