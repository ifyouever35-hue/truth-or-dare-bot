"""
run_local.py — Запуск бота локально в режиме polling.

Что делает:
  1. Ждёт пока PostgreSQL и Redis будут готовы
  2. Создаёт все таблицы в БД (аналог alembic upgrade head для локалки)
  3. Заполняет пул заданий если он пуст
  4. Запускает бота — он сам опрашивает Telegram каждые несколько секунд

Требования:
  - Docker Desktop запущен (для PostgreSQL и Redis)
  - .env заполнен (минимум BOT_TOKEN)
  - pip install -r requirements.txt выполнен
"""
import asyncio
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def wait_for_postgres(max_tries: int = 30) -> bool:
    """Ждём пока PostgreSQL примет соединения."""
    import asyncpg
    from app.config import settings

    logger.info("⏳ Ожидание PostgreSQL...")
    for attempt in range(1, max_tries + 1):
        try:
            conn = await asyncpg.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                user=settings.postgres_user,
                password=settings.postgres_password,
                database=settings.postgres_db,
            )
            await conn.close()
            logger.info("✅ PostgreSQL готов")
            return True
        except Exception as e:
            if attempt == max_tries:
                logger.error("❌ PostgreSQL не ответил за %d попыток: %s", max_tries, e)
                return False
            logger.info("   PostgreSQL не готов (попытка %d/%d)...", attempt, max_tries)
            await asyncio.sleep(2)
    return False


async def wait_for_redis(max_tries: int = 15) -> bool:
    """Ждём пока Redis примет соединения."""
    import redis.asyncio as aioredis
    from app.config import settings

    logger.info("⏳ Ожидание Redis...")
    for attempt in range(1, max_tries + 1):
        try:
            r = aioredis.from_url(settings.redis_url)
            await r.ping()
            await r.aclose()
            logger.info("✅ Redis готов")
            return True
        except Exception as e:
            if attempt == max_tries:
                logger.error("❌ Redis не ответил за %d попыток: %s", max_tries, e)
                return False
            logger.info("   Redis не готов (попытка %d/%d)...", attempt, max_tries)
            await asyncio.sleep(2)
    return False


async def setup_database() -> None:
    """Создаём таблицы если их нет."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.config import settings
    from app.database.models import Base

    logger.info("🗄️  Настройка базы данных...")
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    logger.info("✅ Таблицы созданы / уже существуют")


async def seed_if_empty() -> None:
    """Заполняем задания если пул пуст."""
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.config import settings
    from app.database.models import TasksPool

    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(select(func.count(TasksPool.id)))
        count = result.scalar()

    await engine.dispose()

    if count == 0 or count < 200:
        info(f"📋 Заданий в базе: {count} — добавляем полный пул...")
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "seed_tasks",
            os.path.join(os.path.dirname(__file__), "scripts", "seed_tasks.py")
        )
        seed_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(seed_module)
        await seed_module.seed()
    else:
        ok(f"✅ В базе уже {count} заданий")


async def main():
    # ── 1. Проверяем .env ─────────────────────────────────────────────────────
    from app.config import settings

    if not settings.bot_token or settings.bot_token.startswith("7123456789"):
        logger.error("❌ BOT_TOKEN не заполнен в .env файле!")
        logger.error("   Получите токен у @BotFather в Telegram и вставьте в .env")
        sys.exit(1)

    logger.info("🤖 BOT_TOKEN: %s...%s", settings.bot_token[:10], settings.bot_token[-5:])

    # ── 2. Ждём инфраструктуру ────────────────────────────────────────────────
    if not await wait_for_postgres():
        logger.error("Запустите: docker compose -f docker-compose.local.yml up -d")
        sys.exit(1)

    if not await wait_for_redis():
        logger.error("Запустите: docker compose -f docker-compose.local.yml up -d")
        sys.exit(1)

    # ── 3. База данных ────────────────────────────────────────────────────────
    await setup_database()
    await seed_if_empty()

    # ── 4. Запускаем бота ─────────────────────────────────────────────────────
    from app.utils.redis_client import redis_client
    from app.database.session import engine
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

    # Информация для пользователя
    me = await bot.get_me()
    logger.info("")
    logger.info("=" * 50)
    logger.info("✅ БОТ ЗАПУЩЕН!")
    logger.info("   Имя: %s", me.full_name)
    logger.info("   Username: @%s", me.username)
    logger.info("   Ссылка: https://t.me/%s", me.username)
    logger.info("")
    logger.info("   Откройте Telegram и напишите боту /start")
    logger.info("=" * 50)
    logger.info("")
    logger.info("   Для остановки нажмите Ctrl+C")
    logger.info("")

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,   # игнорируем старые сообщения
        )
    except KeyboardInterrupt:
        logger.info("Остановка...")
    finally:
        sched.shutdown(wait=False)
        await bot.session.close()
        await redis_client.disconnect()
        await engine.dispose()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
