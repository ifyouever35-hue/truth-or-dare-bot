#!/bin/sh
# =============================================================================
# entrypoint.sh — запускается при старте контейнера app.
# Шаги:
#   1. Ждём PostgreSQL
#   2. Ждём Redis
#   3. alembic upgrade head  (миграции)
#   4. seed_tasks.py         (идемпотентно — сам проверяет, есть ли задания)
#   5. exec uvicorn main:app (PID 1, корректные сигналы)
# =============================================================================
set -e

PG_HOST="${POSTGRES_HOST:-db}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-tod_user}"
PG_DB="${POSTGRES_DB:-truth_or_dare}"

REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"

# ── 1. PostgreSQL ────────────────────────────────────────────────────────────
echo "==> Ожидание PostgreSQL ($PG_HOST:$PG_PORT)..."
MAX_TRIES=60
COUNT=0
until python -c "
import asyncio, asyncpg, os, sys
async def check():
    conn = await asyncpg.connect(
        host=os.environ.get('POSTGRES_HOST', 'db'),
        port=int(os.environ.get('POSTGRES_PORT', 5432)),
        user=os.environ.get('POSTGRES_USER', 'tod_user'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
        database=os.environ.get('POSTGRES_DB', 'truth_or_dare'),
        ssl='disable',
    )
    await conn.close()
asyncio.run(check())
" 2>/dev/null; do
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -ge "$MAX_TRIES" ]; then
        echo "ОШИБКА: PostgreSQL не ответил за ${MAX_TRIES} попыток." >&2
        exit 1
    fi
    sleep 2
done
echo "    PostgreSQL готов."

# ── 2. Redis ─────────────────────────────────────────────────────────────────
echo "==> Ожидание Redis ($REDIS_HOST:$REDIS_PORT)..."
COUNT=0
until python -c "
import asyncio, os
import redis.asyncio as r
async def check():
    c = r.from_url(f\"redis://{os.environ.get('REDIS_HOST','redis')}:{os.environ.get('REDIS_PORT','6379')}\")
    await c.ping()
    await c.aclose()
asyncio.run(check())
" 2>/dev/null; do
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -ge "$MAX_TRIES" ]; then
        echo "ОШИБКА: Redis не ответил за ${MAX_TRIES} попыток." >&2
        exit 1
    fi
    sleep 2
done
echo "    Redis готов."

# ── 3. Миграции ──────────────────────────────────────────────────────────────
echo "==> Применение миграций (alembic upgrade head)..."
alembic upgrade head
echo "    Миграции применены."

# ── 4. Seed заданий (skip если уже есть) ────────────────────────────────────
echo "==> Загрузка пула заданий (seed_tasks.py)..."
# seed_tasks.py сам логирует "уже есть N заданий — пропускаю" и выходит.
# Если падает (редко: например, на CI без env) — не останавливаем приложение.
PYTHONPATH=/app python scripts/seed_tasks.py || echo "    [warn] seed_tasks завершился с ошибкой, пропускаем."

# ── 5. Запуск приложения ─────────────────────────────────────────────────────
echo "==> Запуск uvicorn main:app на 0.0.0.0:8000..."
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips='*'
