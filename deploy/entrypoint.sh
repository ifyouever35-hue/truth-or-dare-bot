#!/bin/sh
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
            ssl='disable',
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
        echo "ОШИБКА: PostgreSQL не ответил за ${MAX_TRIES} попыток."
        exit 1
    fi
    echo "   Попытка $COUNT/$MAX_TRIES — ждём 2 секунды..."
    sleep 2
done

echo "==> Запуск бота..."
exec python bot.py
