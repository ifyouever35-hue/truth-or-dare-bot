"""
app/bot/instance.py — Синглтон бота и диспетчера.

Вынесено в отдельный файл, чтобы импортировать bot из webhook-обработчиков
(например, из webhooks.py при уведомлении пользователя).
"""
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.config import settings
from app.utils.redis_client import redis_client

# Инициализируем после подключения Redis (в lifespan)
bot: Bot = None  # type: ignore[assignment]
dp: Dispatcher = None  # type: ignore[assignment]


def create_bot() -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    storage = RedisStorage(redis=redis_client.client)
    return Dispatcher(storage=storage)
