"""
app/utils/backup.py — Автоматический бэкап PostgreSQL.

Что делает:
  1. Каждую ночь в 3:30 UTC запускает pg_dump
  2. Сохраняет в папку backups/ с датой в имени
  3. Хранит последние 7 бэкапов (старые удаляет)
  4. Логирует результат

Для работы pg_dump должен быть доступен в PATH:
  Windows: входит в состав установки PostgreSQL
  Linux:   apt install postgresql-client
"""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)
BACKUP_DIR = Path("backups")
KEEP_LAST = 7   # хранить последних N бэкапов


async def run_backup() -> bool:
    """
    Запускает pg_dump и сохраняет дамп.
    Возвращает True при успехе.
    """
    BACKUP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    filename = BACKUP_DIR / f"backup_{timestamp}.sql.gz"

    # Команда pg_dump с gzip-сжатием
    cmd = [
        "pg_dump",
        f"--host={settings.postgres_host}",
        f"--port={settings.postgres_port}",
        f"--username={settings.postgres_user}",
        f"--dbname={settings.postgres_db}",
        "--no-password",
        "--format=custom",      # бинарный формат, лучше сжимается
        f"--file={filename}",
    ]

    env = os.environ.copy()
    env["PGPASSWORD"] = settings.postgres_password

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode != 0:
            logger.error("pg_dump failed: %s", stderr.decode(errors="replace"))
            return False

        size_kb = filename.stat().st_size // 1024
        logger.info("Backup created: %s (%d KB)", filename.name, size_kb)

        # Удаляем старые бэкапы
        _cleanup_old_backups()
        return True

    except asyncio.TimeoutError:
        logger.error("pg_dump timeout (>120s)")
        return False
    except FileNotFoundError:
        logger.error("pg_dump not found in PATH. Install PostgreSQL client tools.")
        return False
    except Exception as e:
        logger.exception("Backup error: %s", e)
        return False


def _cleanup_old_backups() -> None:
    """Удаляем бэкапы старше последних KEEP_LAST штук."""
    backups = sorted(BACKUP_DIR.glob("backup_*.sql.gz"), key=lambda p: p.stat().st_mtime)
    to_delete = backups[:-KEEP_LAST] if len(backups) > KEEP_LAST else []
    for old in to_delete:
        old.unlink()
        logger.info("Deleted old backup: %s", old.name)


def restore_instructions() -> str:
    """Инструкция по восстановлению для README."""
    return """
Восстановление из бэкапа:
  pg_restore --host=localhost --port=5432 \\
             --username=tod_user --dbname=truth_or_dare \\
             --no-password backups/backup_YYYY-MM-DD_HH-MM.sql.gz
"""
