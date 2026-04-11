"""
app/utils/scheduler.py — Фоновые задачи через APScheduler.

Задачи:
  1. job_cleanup_media        — каждые 7 дней удаляет старые медиафайлы.
  2. job_expire_verified      — ежедневно деактивирует просроченные подписки.
  3. job_close_stuck_lobbies  — каждые 30 минут закрывает зависшие лобби (>3ч).
  4. job_cleanup_db           — каждую ночь чистит мусор из БД:
                                  • closed лобби старше 7 дней
                                  • members выбывших лобби
                                  • media_archive удалённых файлов
                                  • payments со статусом pending > 24ч
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, delete, update, func

from app.database.models import Lobby, LobbyMember, LobbyStatus, MediaArchive, Payment, User
from app.database.session import get_db_context
from app.utils.media import cleanup_old_media

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")


async def job_cleanup_media():
    """Удаляем устаревшие медиафайлы с диска."""
    try:
        async with get_db_context() as db:
            count = await cleanup_old_media(db)
        logger.info("Scheduler: удалено %d медиафайлов", count)
    except Exception as e:
        logger.exception("Scheduler: ошибка очистки медиа: %s", e)


async def job_expire_verified():
    """Деактивируем просроченные Verified подписки."""
    try:
        async with get_db_context() as db:
            now = datetime.utcnow()
            result = await db.execute(
                select(User).where(
                    User.is_verified == True,  # noqa: E712
                    User.verified_expires_at < now,
                )
            )
            expired = result.scalars().all()
            for user in expired:
                user.is_verified = False
            await db.flush()
            if expired:
                logger.info("Scheduler: истекло %d подписок Verified", len(expired))
    except Exception as e:
        logger.exception("Scheduler: ошибка проверки Verified: %s", e)


async def job_close_stuck_lobbies():
    """Закрываем лобби без активности более 3 часов."""
    try:
        async with get_db_context() as db:
            cutoff = datetime.utcnow() - timedelta(hours=3)
            result = await db.execute(
                select(Lobby).where(
                    Lobby.status.in_([LobbyStatus.WAITING, LobbyStatus.ACTIVE]),
                    Lobby.created_at < cutoff,
                )
            )
            stuck = result.scalars().all()
            for lobby in stuck:
                lobby.status = LobbyStatus.CLOSED
                lobby.closed_at = datetime.utcnow()
                try:
                    from app.utils.redis_client import redis_client
                    await redis_client.delete_lobby_state(str(lobby.id))
                except Exception:
                    pass
            await db.flush()
            if stuck:
                logger.info("Scheduler: закрыто %d зависших лобби", len(stuck))
    except Exception as e:
        logger.exception("Scheduler: ошибка закрытия лобби: %s", e)


async def job_cleanup_db():
    """
    Ночная очистка БД от мусора.

    Что удаляем:
      • Closed лобби старше 30 дней — они уже не нужны
      • LobbyMember строки закрытых лобби старше 30 дней
      • MediaArchive записи с is_deleted=True старше 7 дней
      • Payment записи со статусом 'pending' старше 24 часов (брошенные)

    Что НЕ удаляем:
      • Пользователей (нужны для статистики)
      • Payments success/failed (нужны для отчётности)
      • MediaArchive с is_reported=True (нужны для модерации)
    """
    try:
        async with get_db_context() as db:
            now = datetime.utcnow()

            # 1. Удаляем members закрытых лобби старше 30 дней
            old_cutoff = now - timedelta(days=30)
            old_lobby_ids_result = await db.execute(
                select(Lobby.id).where(
                    Lobby.status == LobbyStatus.CLOSED,
                    Lobby.closed_at < old_cutoff,
                )
            )
            old_lobby_ids = [row[0] for row in old_lobby_ids_result.all()]

            members_deleted = 0
            lobbies_deleted = 0
            if old_lobby_ids:
                # Удаляем участников
                del_members = delete(LobbyMember).where(
                    LobbyMember.lobby_id.in_(old_lobby_ids)
                )
                result = await db.execute(del_members)
                members_deleted = result.rowcount

                # Удаляем сами лобби
                del_lobbies = delete(Lobby).where(Lobby.id.in_(old_lobby_ids))
                result = await db.execute(del_lobbies)
                lobbies_deleted = result.rowcount

            # 2. Удаляем MediaArchive с is_deleted=True старше 7 дней
            media_cutoff = now - timedelta(days=7)
            del_media = delete(MediaArchive).where(
                MediaArchive.is_deleted == True,  # noqa: E712
                MediaArchive.is_reported == False,  # noqa: E712 — не трогаем жалобы
                MediaArchive.deleted_at < media_cutoff,
            )
            result = await db.execute(del_media)
            media_deleted = result.rowcount

            # 3. Помечаем брошенные pending payments как failed
            payment_cutoff = now - timedelta(hours=24)
            result = await db.execute(
                select(Payment).where(
                    Payment.status == 'pending',
                    Payment.created_at < payment_cutoff,
                )
            )
            stale_payments = result.scalars().all()
            for p in stale_payments:
                p.status = 'failed'
            await db.flush()

            logger.info(
                "Scheduler: DB cleanup — лобби: %d, участников: %d, "
                "медиа: %d, платежей: %d",
                lobbies_deleted, members_deleted, media_deleted, len(stale_payments)
            )
    except Exception as e:
        logger.exception("Scheduler: ошибка очистки БД: %s", e)


def setup_scheduler() -> AsyncIOScheduler:
    # Очистка медиа — каждое воскресенье в 3:00 UTC
    scheduler.add_job(
        job_cleanup_media,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="job_cleanup_media",
        replace_existing=True,
    )
    # Проверка Verified — каждый день в 00:05 UTC
    scheduler.add_job(
        job_expire_verified,
        trigger=CronTrigger(hour=0, minute=5),
        id="job_expire_verified",
        replace_existing=True,
    )
    # Зависшие лобби — каждые 30 минут
    scheduler.add_job(
        job_close_stuck_lobbies,
        trigger=IntervalTrigger(minutes=30),
        id="job_close_stuck_lobbies",
        replace_existing=True,
    )
    # Ночная очистка БД — каждую ночь в 04:00 UTC
    scheduler.add_job(
        job_cleanup_db,
        trigger=CronTrigger(hour=4, minute=0),
        id="job_cleanup_db",
        replace_existing=True,
    )
    return scheduler
