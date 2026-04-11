@echo off
echo Останавливаем контейнеры...
docker compose -f docker-compose.local.yml down
echo Готово. Данные сохранены.
pause
