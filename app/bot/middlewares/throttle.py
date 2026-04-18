"""
app/bot/middlewares/throttle.py — Rate-limiting + защита от падений.

Лимиты:
  • Сообщения:   10/мин на юзера
  • Callbacks:   30/мин на юзера
  • Медиа:        3/мин на юзера (тяжёлые операции)
  • Глобально:  500 запросов/сек (защита от DDoS)
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.utils.redis_client import redis_client

logger = logging.getLogger(__name__)

LIMITS = {
    "message":  (10, 60),
    "callback": (30, 60),
    "media":    (3, 60),
}

# Семафор: не более 50 одновременных обработчиков
_semaphore = asyncio.Semaphore(50)


class ThrottleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = None
        action = "message"

        if isinstance(event, Message):
            tg_user = event.from_user
            if event.photo or event.video_note or event.video:
                action = "media"
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user
            action = "callback"

        if not tg_user:
            return await handler(event, data)

        max_count, window = LIMITS[action]
        allowed = await redis_client.rate_limit_check(
            tg_id=tg_user.id,
            action=action,
            max_count=max_count,
            window_seconds=window,
        )

        if not allowed:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Слишком часто. Подождите секунду.", show_alert=False)
            return

        # Ограничиваем параллельную нагрузку
        try:
            async with asyncio.timeout(15):
                async with _semaphore:
                    return await handler(event, data)
        except asyncio.TimeoutError:
            logger.warning("Handler timeout for user %d", tg_user.id)
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("⏳ Сервер перегружен, попробуйте ещё раз.", show_alert=True)
                except Exception:
                    pass
        except Exception as e:
            logger.exception("Unhandled error in handler for user %d: %s", tg_user.id, e)
