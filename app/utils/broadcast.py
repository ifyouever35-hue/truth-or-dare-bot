"""
app/utils/broadcast.py — Безопасная массовая рассылка.

Проблема: при 10 игроках в комнате 10 параллельных send_message
одновременно могут пробить лимит Telegram (30 msg/sec на бота).

Решение: отправляем с задержкой 35ms между сообщениями + retry при 429.
"""
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Telegram лимит: 30 сообщений/сек глобально, 1 msg/сек в один чат
BROADCAST_DELAY = 0.035  # 35ms между сообщениями = ~28 msg/сек


async def send_safe(bot, chat_id: int, **kwargs) -> bool:
    """
    Отправка с защитой от FloodWait (429).
    Возвращает True при успехе.
    """
    from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
    try:
        await bot.send_message(chat_id=chat_id, **kwargs)
        return True
    except TelegramRetryAfter as e:
        logger.warning("FloodWait %ds for chat %d", e.retry_after, chat_id)
        await asyncio.sleep(e.retry_after + 1)
        try:
            await bot.send_message(chat_id=chat_id, **kwargs)
            return True
        except Exception:
            return False
    except TelegramForbiddenError:
        # Пользователь заблокировал бота
        logger.info("Bot blocked by user %d", chat_id)
        return False
    except Exception as e:
        logger.warning("Failed to send to %d: %s", chat_id, e)
        return False


async def broadcast(bot, recipients: list[dict], delay: float = BROADCAST_DELAY) -> int:
    """
    Рассылка группе пользователей с задержкой между отправками.
    
    recipients: [{"chat_id": int, "text": str, "parse_mode": ..., "reply_markup": ...}]
    Возвращает кол-во успешных отправок.
    """
    success = 0
    for i, msg_kwargs in enumerate(recipients):
        if i > 0:
            await asyncio.sleep(delay)
        chat_id = msg_kwargs.pop("chat_id")
        ok = await send_safe(bot, chat_id, **msg_kwargs)
        if ok:
            success += 1
    return success
