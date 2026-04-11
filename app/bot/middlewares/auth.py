"""
app/bot/middlewares/auth.py — Middleware проверки бана и регистрации юзера.

Порядок выполнения для каждого апдейта:
  1. Проверяем Redis blacklist (O(1), быстро).
  2. Получаем/создаём пользователя в PostgreSQL (upsert).
  3. Если юзер в PREMIUM_USER_IDS — ставим Verified навсегда.
  4. Прокидываем объект User в handler через data["user"].
"""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.database.session import AsyncSessionLocal
from app.utils.redis_client import redis_client
from app.services.user_service import get_or_create_user
from app.config_premium import is_permanent_premium


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = None

        if isinstance(event, Update):
            if event.message and event.message.from_user:
                tg_user = event.message.from_user
            elif event.callback_query and event.callback_query.from_user:
                tg_user = event.callback_query.from_user
            elif event.inline_query and event.inline_query.from_user:
                tg_user = event.inline_query.from_user
            elif event.my_chat_member and event.my_chat_member.from_user:
                tg_user = event.my_chat_member.from_user
        elif hasattr(event, "from_user") and event.from_user:
            tg_user = event.from_user

        if not tg_user:
            return await handler(event, data)

        # ── 1. Проверка бана ──────────────────────────────────────────────────
        if await redis_client.is_banned(tg_user.id):
            return

        async with AsyncSessionLocal() as session:
            # ── 2. Upsert пользователя ────────────────────────────────────────
            user, is_new = await get_or_create_user(
                session=session,
                tg_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name or "",
            )

            if user.is_banned:
                await redis_client.ban_user(tg_user.id)
                return

            # ── 3. Перманентный Premium ───────────────────────────────────────
            if is_permanent_premium(tg_user.id):
                # Verified 18+ всегда активен — без срока и оплаты
                if not user.is_verified:
                    from datetime import datetime, timedelta
                    user.is_verified = True
                    user.verified_expires_at = datetime(2099, 12, 31)
                    await session.flush()
                # Stars не ограничены — пополняем если меньше 9999
                if user.stars_balance < 9999:
                    user.stars_balance = 9999
                    await session.flush()

            # ── 4. Прокидываем в handler ──────────────────────────────────────
            data["user"] = user
            data["db"] = session
            data["is_new_user"] = is_new

            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
