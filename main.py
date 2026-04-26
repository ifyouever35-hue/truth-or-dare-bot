"""
main.py — Точка входа приложения.

Архитектура:
  • FastAPI запускает один ASGI-сервер (uvicorn).
  • aiogram работает в режиме Webhook — все апдейты поступают через POST /webhook.
  • Преимущество перед polling: меньше нагрузки, работает за nginx/cloudflare.
  • При локальной разработке можно переключиться на polling (см. комментарий ниже).

Lifespan порядок запуска:
  1. Redis connect
  2. Database engine warm-up
  3. Bot + Dispatcher init
  4. Register middlewares, routers
  5. Set webhook
  6. Start APScheduler
"""
import logging
from contextlib import asynccontextmanager

import uvicorn
from aiogram.types import Update
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.bot.instance import create_bot, create_dispatcher
from app.bot.middlewares.auth import AuthMiddleware
from app.bot.middlewares.throttle import ThrottleMiddleware
from app.bot.handlers import start, lobby, game, payment
from app.admin.routes import dashboard as admin_dashboard
from app.admin.routes import webhooks as payment_webhooks
from app.config import settings
from app.database.session import engine
from app.database.models import Base
from app.utils.redis_client import redis_client
from app.utils.scheduler import setup_scheduler
import app.bot.instance as bot_instance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения."""
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("Starting up...")

    # 1. Redis
    await redis_client.connect()
    logger.info("Redis connected")

    # 2. Database — проверяем соединение (таблицы создаёт Alembic, не мы)
    # ❌ НИКОГДА не используй create_all в продакшне — он пропускает ALTER TABLE
    # ✅ Правильно: alembic upgrade head (запускается в Dockerfile/entrypoint)
    async with engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    logger.info("Database connection OK")

    # 3. Bot + Dispatcher
    bot = create_bot()
    dp = create_dispatcher()
    bot_instance.bot = bot
    bot_instance.dp = dp

    # 4. Middlewares
    dp.update.outer_middleware(AuthMiddleware())
    dp.message.outer_middleware(ThrottleMiddleware())
    dp.callback_query.outer_middleware(ThrottleMiddleware())

    # 5. Routers
    dp.include_router(start.router)
    dp.include_router(lobby.router)
    dp.include_router(game.router)
    dp.include_router(payment.router)
    logger.info("Routers registered")

    # 6. Webhook ИЛИ polling-fallback
    polling_task = None
    if settings.webhook_host:
        webhook_url = f"{settings.webhook_host}{settings.webhook_path}"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret,
            drop_pending_updates=True,
        )
        logger.info("Webhook set: %s", webhook_url)
    else:
        # Без домена — запускаем polling в фоне, в том же event loop, что и FastAPI.
        # Это нужно для деплоя на VM, у которой пока нет публичного HTTPS.
        logger.warning("WEBHOOK_HOST not set — starting polling fallback in background.")
        await bot.delete_webhook(drop_pending_updates=True)
        import asyncio as _asyncio

        async def _polling_runner():
            try:
                await dp.start_polling(
                    bot,
                    allowed_updates=dp.resolve_used_update_types(),
                )
            except _asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Polling loop crashed")

        polling_task = _asyncio.create_task(_polling_runner(), name="aiogram-polling")

    # 7. Scheduler
    sched = setup_scheduler()
    sched.start()
    logger.info("Scheduler started")

    app.state.bot = bot
    app.state.dp = dp
    app.state.scheduler = sched
    app.state.polling_task = polling_task

    yield  # ── приложение работает ──────────────────────────────────────────

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("Shutting down...")
    sched.shutdown(wait=False)
    if polling_task is not None:
        await dp.stop_polling()
        polling_task.cancel()
        try:
            await polling_task
        except BaseException:
            pass
    if settings.webhook_host:
        await bot.delete_webhook()
    await bot.session.close()
    await redis_client.disconnect()
    await engine.dispose()
    logger.info("Shutdown complete")


# ─── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Truth or Dare Bot",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,     # отключаем Swagger в prod
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus метрики на /metrics
Instrumentator().instrument(app).expose(app)

# Admin panel
app.include_router(admin_dashboard.router)
app.include_router(payment_webhooks.router)


# ─── Webhook endpoint ─────────────────────────────────────────────────────────

@app.post(settings.webhook_path)
async def telegram_webhook(request: Request) -> Response:
    """Получаем апдейты от Telegram."""
    # Проверяем секретный токен
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if settings.webhook_secret and token != settings.webhook_secret:
        return Response(status_code=403)

    bot = request.app.state.bot
    dp = request.app.state.dp

    update_data = await request.json()
    update = Update.model_validate(update_data, context={"bot": bot})
    await dp.feed_update(bot=bot, update=update)
    return Response(status_code=200)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "truth-or-dare-bot"}


@app.get("/")
async def root():
    return {"message": "Truth or Dare Bot API"}


# ─── Local development polling ────────────────────────────────────────────────

async def run_polling():
    """
    Для локальной разработки без домена — запускаем polling.
    Используй: python -c "import asyncio; from main import run_polling; asyncio.run(run_polling())"
    """
    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.redis import RedisStorage

    await redis_client.connect()
    # В режиме polling (локалка) — можно создать таблицы для удобства разработки
    from app.database.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured (dev mode)")

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

    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        sched.shutdown()
        await bot.session.close()
        await redis_client.disconnect()
        await engine.dispose()


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,      # aiogram не поддерживает multi-worker без Redis lock
    )
