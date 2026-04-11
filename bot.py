"""
bot.py — ЗАПУСК БОТА

Просто запусти:  python bot.py

Скрипт сам:
  - Проверит .env и токен
  - Запустит PostgreSQL и Redis (через Docker)
  - Установит зависимости если нужно
  - Создаст таблицы в БД
  - Добавит задания если их нет
  - Запустит бота
"""
import asyncio
import os
import subprocess
import sys
import time

# ─── Цвета для Windows и Mac/Linux ───────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"{GREEN}  ✓ {msg}{RESET}")
def err(msg):   print(f"{RED}  ✗ {msg}{RESET}")
def info(msg):  print(f"{BLUE}  → {msg}{RESET}")
def warn(msg):  print(f"{YELLOW}  ! {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")

# ─── Шаг 1: .env ─────────────────────────────────────────────────────────────
header("Шаг 1/5  Проверка конфига")

if not os.path.exists(".env"):
    if os.path.exists(".env.local"):
        import shutil
        shutil.copy(".env.local", ".env")
        ok("Создан .env из .env.local")
    else:
        err(".env файл не найден!")
        print("\n  Создайте .env файл с содержимым из .env.local")
        sys.exit(1)

# Читаем токен
token = ""
with open(".env", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line.startswith("BOT_TOKEN="):
            token = line.split("=", 1)[1].strip()
            break

if not token or token == "ВСТАВЬТЕ_ТОКЕН_СЮДА" or len(token) < 20:
    err("BOT_TOKEN не заполнен в .env")
    print()
    print("  1. Откройте файл .env")
    print("  2. Найдите строку:  BOT_TOKEN=ВСТАВЬТЕ_ТОКЕН_СЮДА")
    print("  3. Замените на токен от @BotFather")
    print("     Пример: BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxx")
    print()
    input("  Нажмите Enter после исправления, затем запустите bot.py снова...")
    sys.exit(1)

ok(f"BOT_TOKEN найден ({token[:10]}...{token[-4:]})")

# ─── Шаг 2: Docker ───────────────────────────────────────────────────────────
header("Шаг 2/5  Запуск базы данных")

# Проверяем Docker
try:
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True, timeout=10
    )
    if result.returncode != 0:
        raise Exception("not running")
    ok("Docker запущен")
except FileNotFoundError:
    err("Docker не установлен!")
    print()
    print("  Скачайте Docker Desktop:")
    print("  https://www.docker.com/products/docker-desktop/")
    sys.exit(1)
except Exception:
    err("Docker Desktop не запущен!")
    print()
    print("  Откройте Docker Desktop и дождитесь иконки кита в трее")
    input("  Нажмите Enter когда Docker будет готов...")

# Запускаем контейнеры
info("Запускаем PostgreSQL и Redis...")
result = subprocess.run(
    ["docker", "compose", "-f", "docker-compose.local.yml", "up", "-d"],
    capture_output=True, text=True
)
if result.returncode != 0:
    err(f"Ошибка запуска контейнеров:\n{result.stderr}")
    sys.exit(1)
ok("PostgreSQL и Redis запущены")

# ─── Шаг 3: Зависимости ──────────────────────────────────────────────────────
header("Шаг 3/5  Зависимости Python")

# Проверяем версию Python
py_ver = sys.version_info
print(f"  Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}")

try:
    import aiogram
    import sqlalchemy
    import asyncpg
    import redis
    ok("Все зависимости уже установлены")
except ImportError:
    info("Устанавливаем зависимости (первый раз — до 5 минут)...")
    info("Для Python 3.14 некоторые пакеты собираются из исходников — это нормально")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt",
         "--prefer-binary",   # сначала ищем готовые wheels
         "-q"],
        capture_output=False
    )
    if result.returncode != 0:
        err("Ошибка установки зависимостей")
        print()
        print("  Попробуйте установить вручную:")
        print(f"  {sys.executable} -m pip install -r requirements.txt --prefer-binary")
        sys.exit(1)
    ok("Зависимости установлены")

# ─── Шаг 4–5: Запуск бота ────────────────────────────────────────────────────
header("Шаг 4-5/5  Запуск бота")

async def run():
    # Ждём БД
    import asyncpg as pg
    info("Ожидание PostgreSQL...")

    # Читаем параметры из .env
    env = {}
    with open(".env", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    pg_host = env.get("POSTGRES_HOST", "localhost")
    pg_port = int(env.get("POSTGRES_PORT", "5432"))
    pg_db   = env.get("POSTGRES_DB", "truth_or_dare")
    pg_user = env.get("POSTGRES_USER", "tod_user")
    pg_pass = env.get("POSTGRES_PASSWORD", "localpassword123")

    for attempt in range(30):
        try:
            conn = await pg.connect(
                host=pg_host, port=pg_port,
                user=pg_user, password=pg_pass, database=pg_db
            )
            await conn.close()
            ok("PostgreSQL готов")
            break
        except Exception:
            if attempt == 29:
                err("PostgreSQL не запустился. Проверьте Docker Desktop.")
                sys.exit(1)
            await asyncio.sleep(2)

    # Ждём Redis
    import redis.asyncio as aioredis
    info("Ожидание Redis...")
    redis_host = env.get("REDIS_HOST", "localhost")
    redis_port = int(env.get("REDIS_PORT", "6379"))
    for attempt in range(15):
        try:
            r = aioredis.from_url(f"redis://{redis_host}:{redis_port}")
            await r.ping()
            await r.aclose()
            ok("Redis готов")
            break
        except Exception:
            if attempt == 14:
                err("Redis не запустился.")
                sys.exit(1)
            await asyncio.sleep(2)

    # Создаём таблицы
    info("Настройка базы данных...")
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.config import settings
    from app.database.models import Base
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    ok("Таблицы готовы")

    # Seed заданий
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from sqlalchemy import select, func
    from app.database.models import TasksPool
    engine2 = create_async_engine(settings.database_url)
    sm = async_sessionmaker(bind=engine2, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        count = (await session.execute(select(func.count(TasksPool.id)))).scalar()
    await engine2.dispose()

    if count < 200:
        info(f"Заданий в базе: {count} — загружаем полный пул...")
        import importlib.util
        spec = importlib.util.spec_from_file_location("seed", "scripts/seed_tasks.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        await mod.seed()
    else:
        ok(f"В базе {count} заданий")

    # ── Запуск бота ───────────────────────────────────────────────────────────
    from app.utils.redis_client import redis_client
    from app.database.session import engine as db_engine
    from app.bot.instance import create_bot, create_dispatcher
    from app.bot.middlewares.auth import AuthMiddleware
    from app.bot.middlewares.throttle import ThrottleMiddleware
    from app.bot.handlers import start, lobby, game, payment
    from app.utils.scheduler import setup_scheduler
    import app.bot.instance as bot_instance

    await redis_client.connect()
    bot = create_bot()
    dp = create_dispatcher()
    bot_instance.bot = bot
    bot_instance.dp = dp

    dp.update.outer_middleware(AuthMiddleware())
    dp.message.outer_middleware(ThrottleMiddleware())
    dp.callback_query.outer_middleware(ThrottleMiddleware())

    dp.include_router(start.router)
    dp.include_router(lobby.router)
    dp.include_router(game.router)
    dp.include_router(payment.router)

    sched = setup_scheduler()
    sched.start()

    # ── Запуск Admin панели как asyncio-задачи (тот же event loop) ───────────
    import uvicorn
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from app.admin.routes import dashboard as admin_dashboard
    from app.admin.routes import webhooks as payment_webhooks

    admin_app = FastAPI(title="ToD Admin", docs_url=None, redoc_url=None)
    admin_app.add_middleware(CORSMiddleware, allow_origins=["*"],
                             allow_methods=["*"], allow_headers=["*"])
    admin_app.include_router(admin_dashboard.router)
    admin_app.include_router(payment_webhooks.router)

    admin_config = uvicorn.Config(
        admin_app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
        access_log=False,
    )
    admin_server = uvicorn.Server(admin_config)

    me = await bot.get_me()

    # Устанавливаем команды в боковом меню Telegram (кнопка / слева от поля ввода)
    from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats
    commands = [
        BotCommand(command="start",   description="🏠 Главное меню"),
        BotCommand(command="help",    description="📖 Как играть"),
        BotCommand(command="profile", description="👤 Профиль и статистика"),
        BotCommand(command="shop",    description="⭐ Купить Stars"),
        BotCommand(command="top",     description="🏆 Топ игроков"),
        BotCommand(command="about",   description="ℹ️ О боте"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())

    print()
    print(f"{GREEN}{BOLD}{'='*50}{RESET}")
    print(f"{GREEN}{BOLD}  ✅  БОТ ЗАПУЩЕН!{RESET}")
    print(f"{GREEN}{'='*50}{RESET}")
    print(f"  Telegram:     https://t.me/{me.username}")
    print(f"  Админ-панель: http://localhost:8000/admin/")
    print(f"  Логин/пароль: admin / admin123")
    print()
    print(f"  Откройте Telegram → @{me.username} → /start")
    print()
    print(f"  Для остановки: {BOLD}Ctrl+C{RESET}")
    print(f"{GREEN}{'='*50}{RESET}")
    print()

    # Запускаем бота и админку параллельно в одном event loop
    async def _poll():
        try:
            await dp.start_polling(bot, drop_pending_updates=True)
        except Exception:
            pass

    async def _admin():
        try:
            await admin_server.serve()
        except Exception:
            pass

    try:
        await asyncio.gather(_poll(), _admin(), return_exceptions=True)
    except KeyboardInterrupt:
        pass
    finally:
        sched.shutdown(wait=False)
        await bot.session.close()
        await redis_client.disconnect()
        await db_engine.dispose()
        print(f"\n{YELLOW}  Бот остановлен.{RESET}")

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
