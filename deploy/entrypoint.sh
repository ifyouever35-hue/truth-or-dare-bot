#!/bin/sh
# deploy/entrypoint.sh — точка входа контейнера
#
# Порядок:
#   1. Ждём пока PostgreSQL примет соединения (до 60 секунд)
#   2. Применяем все новые миграции: alembic upgrade head
#   3. Запускаем uvicorn
#
# Почему это важно:
#   Docker не гарантирует что PostgreSQL полностью готов к запросам
#   в момент когда healthcheck уже прошёл. Цикл ожидания — надёжный способ.

set -e

echo "==> Ожидание PostgreSQL..."
MAX_TRIES=30
COUNT=0

until python -c "
import asyncio, asyncpg, os, sys
async def check():
    try:
        conn = await asyncpg.connect(
            host=os.environ.get('POSTGRES_HOST', 'db'),
            port=int(os.environ.get('POSTGRES_PORT', 5432)),
            user=os.environ.get('POSTGRES_USER', 'tod_user'),
            password=os.environ.get('POSTGRES_PASSWORD', ''),
            database=os.environ.get('POSTGRES_DB', 'truth_or_dare'),
        )
        await conn.close()
        print('PostgreSQL готов')
    except Exception as e:
        print(f'Ещё не готов: {e}', file=sys.stderr)
        sys.exit(1)
asyncio.run(check())
" 2>/dev/null; do
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -ge "$MAX_TRIES" ]; then
        echo "ОШИБКА: PostgreSQL не ответил за ${MAX_TRIES} попыток. Выход."
        exit 1
    fi
    echo "   Попытка $COUNT/$MAX_TRIES — ждём 2 секунды..."
    sleep 2
done

echo "==> Применение миграций Alembic..."
# upgrade head — применяет все миграции которых ещё нет в БД.
# Если миграций нет — ничего не делает. Безопасно запускать при каждом старте.
alembic upgrade head
echo "    Миграции применены."

echo "==> Запуск uvicorn..."
exec python -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    --no-access-log
