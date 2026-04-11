"""
app/bot/middlewares/throttle.py — Rate-limiting через Redis.

Защищаем бота от спам-апдейтов:
  • Обычные сообщения: не более 10 в минуту на пользователя.
  • Callback-кнопки: не более 30 в минуту (кликеры).
  • Медиа-загрузки: не более 5 в минуту.
"""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.utils.redis_client import redis_client


LIMITS = {
    "message": (10, 60),      # max_count, window_seconds
    "callback": (30, 60),
    "media": (5, 60),
}


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
            if event.photo or event.video_note:
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
                await event.answer("⏳ Слишком часто. Подождите немного.", show_alert=False)
            return  # молча игнорируем

        return await handler(event, data)
