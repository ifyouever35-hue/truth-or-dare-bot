"""
app/utils/media.py — Обработка медиафайлов: скачивание, сжатие, удаление.

Логика (по ТЗ):
  1. Скачать файл с серверов Telegram через Bot API.
  2. Сжать: фото → Pillow (quality=40), видео → subprocess + ffmpeg.
  3. Сохранить сжатый файл в MEDIA_DIR/{user_id}/{uuid}.{ext}.
  4. Вернуть путь к файлу для записи в media_archive.
  5. Cron-задача (scheduler.py) вызывает cleanup_old_media() раз в 7 дней.

Важно:
  • Всё async — используем aiofiles для I/O и asyncio.subprocess для FFmpeg.
  • Оригинал сохраняем ТОЛЬКО временно (во время сжатия), потом удаляем.
"""
import asyncio
import io
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

import aiofiles
import aiohttp
from PIL import Image

from app.config import settings


MEDIA_DIR = Path(settings.media_dir)

# Проверяем наличие FFmpeg один раз при старте
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


async def _download_file(file_url: str) -> bytes:
    """Скачиваем файл с Telegram серверов."""
    async with aiohttp.ClientSession() as session:
        async with session.get(file_url) as resp:
            resp.raise_for_status()
            return await resp.read()


async def _compress_photo(raw_bytes: bytes) -> bytes:
    """
    Сжимаем фото через Pillow в отдельном потоке (CPU-bound).
    quality=40 — достаточно для визуального распознавания лицом модератора.
    """
    loop = asyncio.get_event_loop()

    def _sync_compress(data: bytes) -> bytes:
        img = Image.open(io.BytesIO(data))
        # Конвертируем в RGB (PNG с alpha → JPEG не конвертируется без этого)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        # Уменьшаем разрешение до max 800px по большей стороне
        img.thumbnail((800, 800), Image.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=40, optimize=True)
        return output.getvalue()

    return await loop.run_in_executor(None, _sync_compress, raw_bytes)


async def _compress_video(input_path: Path, output_path: Path) -> bool:
    """
    Сжимаем видео через FFmpeg. CRF=40 + scale 320px — минимальное качество.
    Возвращает True при успехе.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", "scale=320:-2",          # ширина 320px, высота кратна 2
        "-c:v", "libx264",
        "-crf", "40",                    # 0=лучшее, 51=худшее. 40 = ~70% сжатие
        "-preset", "ultrafast",          # максимум скорости
        "-c:a", "aac",
        "-b:a", "32k",                   # минимальный аудиобитрейт
        "-movflags", "+faststart",       # MP4: метаданные в начало файла
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        import logging
        logging.getLogger(__name__).error(
            "FFmpeg error: %s", stderr.decode(errors="replace")
        )
        return False
    return True


async def save_and_compress_photo(
    file_url: str,
    user_id: str,
    original_file_id: str,
) -> Optional[dict]:
    """
    Полный pipeline для фото.
    Возвращает dict с путём и метаданными или None при ошибке.
    """
    try:
        raw = await _download_file(file_url)
        if len(raw) > settings.max_photo_size_mb * 1024 * 1024:
            return None  # превышен лимит

        compressed = await _compress_photo(raw)

        # Создаём директорию пользователя
        user_dir = MEDIA_DIR / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{uuid.uuid4().hex}.jpg"
        file_path = user_dir / filename

        async with aiofiles.open(file_path, "wb") as f:
            await f.write(compressed)

        return {
            "file_path": str(file_path),
            "file_type": "photo",
            "file_size_bytes": len(compressed),
            "original_file_id": original_file_id,
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Error saving photo: %s", e)
        return None


async def save_and_compress_video(
    file_url: str,
    user_id: str,
    original_file_id: str,
) -> Optional[dict]:
    """Полный pipeline для video_note (кружочки).
    Если FFmpeg не установлен — сохраняем оригинал без сжатия.
    """
    import tempfile
    import logging
    logger = logging.getLogger(__name__)

    try:
        raw = await _download_file(file_url)
        if len(raw) > settings.max_video_size_mb * 1024 * 1024:
            return None

        user_dir = MEDIA_DIR / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        output_filename = f"{uuid.uuid4().hex}.mp4"
        output_path = user_dir / output_filename

        if not FFMPEG_AVAILABLE:
            # FFmpeg не найден — сохраняем оригинал как есть
            logger.warning(
                "FFmpeg не установлен — видео сохраняется без сжатия. "
                "Для сжатия установите FFmpeg: https://ffmpeg.org/download.html"
            )
            async with aiofiles.open(output_path, "wb") as f:
                await f.write(raw)
            return {
                "file_path": str(output_path),
                "file_type": "video_note",
                "file_size_bytes": len(raw),
                "original_file_id": original_file_id,
            }

        # FFmpeg доступен — сжимаем
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        success = await _compress_video(tmp_path, output_path)
        tmp_path.unlink(missing_ok=True)

        if not success or not output_path.exists():
            # FFmpeg упал — сохраняем оригинал как fallback
            logger.warning("FFmpeg не смог сжать видео — сохраняем оригинал")
            async with aiofiles.open(output_path, "wb") as f:
                await f.write(raw)

        return {
            "file_path": str(output_path),
            "file_type": "video_note",
            "file_size_bytes": output_path.stat().st_size,
            "original_file_id": original_file_id,
        }
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).exception("Error saving video: %s", e)
        return None


async def delete_media_file(file_path: str) -> bool:
    """Физически удаляем файл с сервера."""
    try:
        path = Path(file_path)
        if path.exists():
            path.unlink()
        return True
    except Exception:
        return False


async def cleanup_old_media(db_session) -> int:
    """
    Cron-задача: удаляем медиафайлы старше MEDIA_RETENTION_DAYS дней,
    если на них нет жалоб (is_reported=False).
    Возвращает количество удалённых файлов.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import select, update
    from app.database.models import MediaArchive

    cutoff = datetime.utcnow() - timedelta(days=settings.media_retention_days)

    # Получаем файлы для удаления
    result = await db_session.execute(
        select(MediaArchive).where(
            MediaArchive.created_at < cutoff,
            MediaArchive.is_reported == False,  # noqa: E712
            MediaArchive.is_deleted == False,   # noqa: E712
        )
    )
    files = result.scalars().all()

    deleted_count = 0
    for media in files:
        if await delete_media_file(media.file_path):
            media.is_deleted = True
            media.deleted_at = datetime.utcnow()
            deleted_count += 1

    await db_session.commit()
    return deleted_count
