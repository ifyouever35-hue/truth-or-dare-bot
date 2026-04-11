"""
app/services/media_service.py — Сохранение, получение, жалобы на медиа.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MediaArchive, User, Lobby


async def save_media_record(
    session: AsyncSession,
    user: User,
    lobby: Lobby,
    task_id: Optional[uuid.UUID],
    file_info: dict,
) -> MediaArchive:
    """
    Записать метаданные медиафайла в архив после сжатия.
    file_info = {"file_path", "file_type", "file_size_bytes", "original_file_id"}
    """
    record = MediaArchive(
        user_id=user.id,
        lobby_id=lobby.id,
        task_id=task_id,
        file_path=file_info["file_path"],
        original_file_id=file_info["original_file_id"],
        file_type=file_info["file_type"],
        file_size_bytes=file_info["file_size_bytes"],
    )
    session.add(record)
    await session.flush()
    return record


async def report_media(
    session: AsyncSession,
    media_id: str,
    reason: str = "",
) -> Optional[MediaArchive]:
    """Зафиксировать жалобу на медиафайл."""
    result = await session.execute(
        select(MediaArchive).where(MediaArchive.id == media_id)
    )
    media = result.scalar_one_or_none()
    if not media:
        return None

    media.is_reported = True
    media.report_count += 1
    if reason:
        media.report_reason = reason
    await session.flush()
    return media


async def get_reported_media(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[MediaArchive]:
    """Список медиа с жалобами для модератора."""
    result = await session.execute(
        select(MediaArchive)
        .where(
            MediaArchive.is_reported == True,  # noqa: E712
            MediaArchive.is_deleted == False,  # noqa: E712
        )
        .order_by(MediaArchive.report_count.desc(), MediaArchive.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()
