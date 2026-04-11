#!/bin/bash
# stop.sh — Остановить PostgreSQL и Redis
echo "Останавливаем контейнеры..."
docker compose -f docker-compose.local.yml down
echo "✅ Готово. Данные сохранены (тома Docker не удалены)."
