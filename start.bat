@echo off
chcp 65001 >nul
title Правда или Действие — Бот

echo.
echo ╔══════════════════════════════════════════╗
echo ║       Правда или Действие — Бот          ║
echo ╚══════════════════════════════════════════╝
echo.

:: ── Шаг 1: Проверяем .env ────────────────────────────────────────────────────
if not exist ".env" (
    echo Создаём .env из шаблона...
    copy .env.local .env >nul
    echo.
    echo ╔══════════════════════════════════════════════╗
    echo ║  НУЖНО ЗАПОЛНИТЬ BOT_TOKEN!                  ║
    echo ║                                              ║
    echo ║  1. Откройте файл .env блокнотом             ║
    echo ║  2. Замените ВСТАВЬТЕ_ТОКЕН_СЮДА             ║
    echo ║     на токен от @BotFather                   ║
    echo ║  3. Запустите start.bat снова                ║
    echo ╚══════════════════════════════════════════════╝
    echo.
    notepad .env
    pause
    exit /b 1
)

:: Проверяем что токен заполнен
findstr /C:"ВСТАВЬТЕ_ТОКЕН_СЮДА" .env >nul 2>&1
if not errorlevel 1 (
    echo.
    echo ❌ BOT_TOKEN не заполнен в .env
    echo.
    echo    Откройте .env и замените ВСТАВЬТЕ_ТОКЕН_СЮДА на реальный токен.
    echo    Токен выглядит так: 7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    echo    Получить у @BotFather → /newbot
    echo.
    notepad .env
    pause
    exit /b 1
)
echo ✅ BOT_TOKEN найден

:: ── Шаг 2: Проверяем Docker ──────────────────────────────────────────────────
docker --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ❌ Docker не установлен!
    echo.
    echo    Скачайте Docker Desktop:
    echo    https://www.docker.com/products/docker-desktop/
    echo.
    start https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo.
    echo ❌ Docker не запущен!
    echo.
    echo    Запустите Docker Desktop из меню Пуск и попробуйте снова.
    echo.
    pause
    exit /b 1
)
echo ✅ Docker запущен

:: ── Шаг 3: Проверяем Python ──────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ❌ Python не найден!
    echo.
    echo    Скачайте с https://python.org/downloads/
    echo    Нужна версия 3.11 или новее.
    echo    При установке поставьте галочку "Add Python to PATH"!
    echo.
    start https://python.org/downloads/
    pause
    exit /b 1
)
echo ✅ Python найден

:: ── Шаг 4: Виртуальное окружение ─────────────────────────────────────────────
if not exist "venv" (
    echo Создаём виртуальное окружение...
    python -m venv venv
)
call venv\Scripts\activate.bat
echo ✅ Виртуальное окружение активировано

:: ── Шаг 5: Зависимости ────────────────────────────────────────────────────────
echo Установка зависимостей (может занять пару минут при первом запуске)...
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo ✅ Зависимости установлены

:: ── Шаг 6: PostgreSQL и Redis ─────────────────────────────────────────────────
echo Запускаем PostgreSQL и Redis...
docker compose -f docker-compose.local.yml up -d
echo ✅ Контейнеры запущены

:: ── Шаг 7: Запускаем бота ─────────────────────────────────────────────────────
echo.
echo 🚀 Запускаем бота...
echo.

python run_local.py

echo.
echo Бот остановлен.
pause
