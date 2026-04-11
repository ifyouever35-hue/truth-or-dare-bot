#!/bin/bash
# ╔══════════════════════════════════════════════════════╗
# ║  start.sh — Запуск бота на Mac / Linux               ║
# ║  Использование: ./start.sh                           ║
# ╚══════════════════════════════════════════════════════╝

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       Правда или Действие — Бот          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Шаг 1: Проверяем .env ─────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚙️  Создаём .env из шаблона...${NC}"
    cp .env.local .env
    echo ""
    echo -e "${RED}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║  НУЖНО ЗАПОЛНИТЬ BOT_TOKEN!                  ║${NC}"
    echo -e "${RED}║                                              ║${NC}"
    echo -e "${RED}║  1. Откройте файл .env в любом редакторе     ║${NC}"
    echo -e "${RED}║  2. Замените ВСТАВЬТЕ_ТОКЕН_СЮДА             ║${NC}"
    echo -e "${RED}║     на токен от @BotFather                   ║${NC}"
    echo -e "${RED}║  3. Запустите ./start.sh снова               ║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════╝${NC}"
    echo ""
    exit 1
fi

# Читаем BOT_TOKEN из .env
BOT_TOKEN=$(grep "^BOT_TOKEN=" .env | cut -d'=' -f2 | tr -d ' ')
if [ -z "$BOT_TOKEN" ] || [ "$BOT_TOKEN" = "ВСТАВЬТЕ_ТОКЕН_СЮДА" ]; then
    echo -e "${RED}❌ BOT_TOKEN не заполнен в .env${NC}"
    echo ""
    echo "  Откройте .env и замените ВСТАВЬТЕ_ТОКЕН_СЮДА на реальный токен."
    echo "  Токен выглядит так: 7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    echo "  Получить у @BotFather → /newbot"
    echo ""
    exit 1
fi
echo -e "${GREEN}✅ BOT_TOKEN найден${NC}"

# ── Шаг 2: Проверяем Docker ───────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo ""
    echo -e "${RED}❌ Docker не установлен!${NC}"
    echo ""
    echo "  Скачайте Docker Desktop:"
    echo "  Mac:   https://docs.docker.com/desktop/mac/install/"
    echo "  Linux: https://docs.docker.com/engine/install/"
    echo ""
    exit 1
fi

if ! docker info &>/dev/null 2>&1; then
    echo ""
    echo -e "${RED}❌ Docker не запущен!${NC}"
    echo ""
    echo "  Запустите Docker Desktop и попробуйте снова."
    echo ""
    exit 1
fi
echo -e "${GREEN}✅ Docker запущен${NC}"

# ── Шаг 3: Проверяем Python ───────────────────────────────────────────────────
PYTHON=""
for cmd in python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VERSION=$("$cmd" -c "import sys; print(sys.version_info[:2])")
        if [[ "$VERSION" == "(3, 11)"* ]] || [[ "$VERSION" == "(3, 12)"* ]] || [[ "$VERSION" == "(3, 13)"* ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo -e "${RED}❌ Python 3.11+ не найден!${NC}"
    echo ""
    echo "  Скачайте с https://python.org/downloads/"
    echo "  Нужна версия 3.11 или новее."
    echo ""
    exit 1
fi
echo -e "${GREEN}✅ Python: $($PYTHON --version)${NC}"

# ── Шаг 4: Виртуальное окружение ─────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}📦 Создаём виртуальное окружение...${NC}"
    $PYTHON -m venv venv
fi

# Активируем
source venv/bin/activate
echo -e "${GREEN}✅ Виртуальное окружение активировано${NC}"

# ── Шаг 5: Зависимости ────────────────────────────────────────────────────────
echo -e "${YELLOW}📦 Установка зависимостей...${NC}"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo -e "${GREEN}✅ Зависимости установлены${NC}"

# ── Шаг 6: PostgreSQL и Redis через Docker ────────────────────────────────────
echo -e "${YELLOW}🐳 Запускаем PostgreSQL и Redis...${NC}"
docker compose -f docker-compose.local.yml up -d

echo -e "${GREEN}✅ Контейнеры запущены${NC}"

# ── Шаг 7: Запускаем бота ─────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}🚀 Запускаем бота...${NC}"
echo ""

$PYTHON run_local.py
