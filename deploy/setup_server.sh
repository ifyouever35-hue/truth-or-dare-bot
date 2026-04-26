#!/bin/bash
# =============================================================================
# deploy/setup_server.sh — минимальная подготовка чистой Ubuntu VM.
#
# Что делает:
#   1. Устанавливает Docker + docker compose plugin (если ещё нет)
#   2. Проверяет, что .env существует и заполнен
#   3. Собирает и запускает стек: docker compose up -d --build
#
# Что НЕ делает (намеренно):
#   - Не ставит nginx/certbot (см. deploy/setup_nginx_ssl.sh — отдельный шаг)
#   - Не клонирует репозиторий (предполагается, что код уже на сервере)
#
# Использование:
#   sudo bash deploy/setup_server.sh
#
# Требования:
#   - Ubuntu 22.04 / 24.04 (или Debian 11/12)
#   - Запуск из корня проекта
#   - Файл .env уже создан и заполнен (BOT_TOKEN, POSTGRES_PASSWORD, ...)
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=============================================="
echo " Truth or Dare Bot — Server Setup"
echo "=============================================="

# ── 0. sudo? ─────────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}Запусти через sudo:${NC} sudo bash deploy/setup_server.sh"
    exit 1
fi

# ── 1. .env есть? ────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo -e "${RED}Файл .env не найден.${NC}"
    echo "Скопируй шаблон и заполни:"
    echo "  cp .env.example .env"
    echo "  nano .env   # заполни BOT_TOKEN, POSTGRES_PASSWORD и пр."
    exit 1
fi

# Проверяем хотя бы BOT_TOKEN
if ! grep -qE '^BOT_TOKEN=.{20,}' .env; then
    echo -e "${RED}BOT_TOKEN в .env не заполнен.${NC}"
    echo "Получи токен у @BotFather и впиши его в .env."
    exit 1
fi

echo -e "${GREEN}✓ .env найден.${NC}"

# ── 2. Docker ────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo -e "${YELLOW}→ Устанавливаю Docker...${NC}"
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl
    curl -fsSL https://get.docker.com | sh
    # Добавляем sudo-юзера в группу docker (если запускали через sudo)
    if [ -n "${SUDO_USER:-}" ]; then
        usermod -aG docker "$SUDO_USER" || true
        echo "    Пользователь $SUDO_USER добавлен в группу docker."
        echo "    Чтобы это применилось — выйди и зайди заново (или 'newgrp docker')."
    fi
fi
echo -e "${GREEN}✓ Docker: $(docker --version)${NC}"

# Docker Compose plugin (на свежих установках уже идёт с docker-ce)
if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y -qq docker-compose-plugin
fi
echo -e "${GREEN}✓ Docker Compose: $(docker compose version --short)${NC}"

# ── 3. Сборка и запуск ───────────────────────────────────────────────────────
echo -e "${YELLOW}→ Сборка образа и запуск контейнеров...${NC}"
docker compose build
docker compose up -d

echo ""
echo -e "${GREEN}=============================================="
echo " ✅ Готово."
echo -e "==============================================${NC}"
echo ""
echo " Полезные команды:"
echo "   docker compose ps           — статус контейнеров"
echo "   docker compose logs -f app  — логи бота"
echo "   docker compose restart app  — перезапуск"
echo "   docker compose down         — остановить всё"
echo ""
echo " Приложение слушает:  http://127.0.0.1:8000"
echo "   /health      — health-check"
echo "   /admin/      — админ-панель"
echo "   /webhook     — Telegram webhook"
echo ""
echo " Дальнейшие шаги:"
echo "   • Если нужен публичный домен + HTTPS:"
echo "       sudo bash deploy/setup_nginx_ssl.sh yourdomain.com you@example.com"
echo "   • Если нужен только polling (без домена/webhook):"
echo "       оставь WEBHOOK_HOST пустым в .env — бот сам перейдёт в polling."
echo ""
