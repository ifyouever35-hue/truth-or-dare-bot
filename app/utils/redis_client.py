"""
app/utils/redis_client.py — Redis-клиент + хелперы для состояний лобби.

Почему Redis для состояний лобби, а не только PostgreSQL?
  • Таймеры заданий, список "кто уже просмотрел медиа" — данные живут
    секунды/минуты и нам не нужна их долговечность.
  • Blacklist забаненных юзеров — O(1) проверка через SET.
  • FSM-состояния aiogram — стандартная практика.

Структура ключей:
  lobby:{lobby_id}:state       — JSON snapshot состояния лобби в реальном времени
  lobby:{lobby_id}:viewed      — SET user_id, которые просмотрели текущее медиа
  user:{tg_id}:fsm             — FSM state aiogram
  blacklist:{tg_id}            — флаг бана (TTL = бессрочно или до expiry)
  rate:{tg_id}:{action}        — счётчик для rate-limit
"""
import json
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config import settings


class RedisClient:
    def __init__(self) -> None:
        self._pool: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        self._pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await self._pool.ping()

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.aclose()

    @property
    def client(self) -> aioredis.Redis:
        if not self._pool:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._pool

    # ── Lobby state ───────────────────────────────────────────────────────────

    async def set_lobby_state(
        self, lobby_id: str, state: dict, ttl: int = 86400
    ) -> None:
        """Сохраняем состояние лобби в Redis. TTL = 24 ч."""
        key = f"lobby:{lobby_id}:state"
        await self.client.set(key, json.dumps(state), ex=ttl)

    async def get_lobby_state(self, lobby_id: str) -> Optional[dict]:
        key = f"lobby:{lobby_id}:state"
        data = await self.client.get(key)
        return json.loads(data) if data else None

    async def delete_lobby_state(self, lobby_id: str) -> None:
        await self.client.delete(
            f"lobby:{lobby_id}:state",
            f"lobby:{lobby_id}:viewed",
        )

    # ── "Просмотрел медиа" ────────────────────────────────────────────────────

    async def add_viewed(self, lobby_id: str, user_tg_id: int) -> int:
        """Добавляем юзера в SET просмотревших. Возвращает размер SET."""
        key = f"lobby:{lobby_id}:viewed"
        await self.client.sadd(key, str(user_tg_id))
        await self.client.expire(key, 600)  # 10 минут хватит
        return await self.client.scard(key)

    async def get_viewed_count(self, lobby_id: str) -> int:
        return await self.client.scard(f"lobby:{lobby_id}:viewed")

    async def reset_viewed(self, lobby_id: str) -> None:
        await self.client.delete(f"lobby:{lobby_id}:viewed")

    # ── Current media (для жалоб) ─────────────────────────────────────────────

    async def set_current_media(self, lobby_id: str, media_id: str) -> None:
        """Запоминаем media_id текущего хода — для обработки жалобы."""
        await self.client.set(f"lobby:{lobby_id}:current_media", media_id, ex=600)

    async def get_current_media(self, lobby_id: str) -> str | None:
        return await self.client.get(f"lobby:{lobby_id}:current_media")

    # ── Blacklist ──────────────────────────────────────────────────────────────

    async def ban_user(
        self, tg_id: int, ttl_seconds: Optional[int] = None
    ) -> None:
        """Добавить юзера в blacklist. None = перманентно."""
        key = f"blacklist:{tg_id}"
        if ttl_seconds:
            await self.client.set(key, "1", ex=ttl_seconds)
        else:
            await self.client.set(key, "1")

    async def is_banned(self, tg_id: int) -> bool:
        return bool(await self.client.exists(f"blacklist:{tg_id}"))

    async def unban_user(self, tg_id: int) -> None:
        await self.client.delete(f"blacklist:{tg_id}")

    # ── Rate limiting ──────────────────────────────────────────────────────────

    async def rate_limit_check(
        self, tg_id: int, action: str, max_count: int, window_seconds: int
    ) -> bool:
        """
        Возвращает True если лимит НЕ превышен (можно выполнять действие).
        Использует скользящее окно через INCR + EXPIRE.
        """
        key = f"rate:{tg_id}:{action}"
        count = await self.client.incr(key)
        if count == 1:
            await self.client.expire(key, window_seconds)
        return count <= max_count

    # ── Система "Готов" ───────────────────────────────────────────────────────

    async def ready_add(self, lobby_id: str, tg_id: int) -> int:
        """Игрок нажал Готов. Возвращает кол-во готовых."""
        key = f"ready:{lobby_id}"
        await self.client.sadd(key, str(tg_id))
        await self.client.expire(key, 600)
        return await self.client.scard(key)

    async def ready_count(self, lobby_id: str) -> int:
        return await self.client.scard(f"ready:{lobby_id}")

    async def ready_clear(self, lobby_id: str) -> None:
        await self.client.delete(f"ready:{lobby_id}")

    # ── Голосование за выполнение задания ─────────────────────────────────────

    async def vote_yes(self, lobby_id: str, tg_id: int) -> None:
        await self.client.sadd(f"vote:{lobby_id}:yes", str(tg_id))
        await self.client.srem(f"vote:{lobby_id}:no",  str(tg_id))
        await self.client.expire(f"vote:{lobby_id}:yes", 300)

    async def vote_no(self, lobby_id: str, tg_id: int) -> None:
        await self.client.sadd(f"vote:{lobby_id}:no",  str(tg_id))
        await self.client.srem(f"vote:{lobby_id}:yes", str(tg_id))
        await self.client.expire(f"vote:{lobby_id}:no", 300)

    async def vote_counts(self, lobby_id: str) -> tuple[int, int]:
        """Возвращает (yes, no)."""
        yes = await self.client.scard(f"vote:{lobby_id}:yes")
        no  = await self.client.scard(f"vote:{lobby_id}:no")
        return yes, no

    async def vote_clear(self, lobby_id: str) -> None:
        await self.client.delete(f"vote:{lobby_id}:yes", f"vote:{lobby_id}:no")

    async def vote_has_voted(self, lobby_id: str, tg_id: int) -> bool:
        yes = await self.client.sismember(f"vote:{lobby_id}:yes", str(tg_id))
        no  = await self.client.sismember(f"vote:{lobby_id}:no",  str(tg_id))
        return bool(yes or no)

    async def matchmaking_join(self, tg_id: int, mode: str = "regular") -> int:
        """Добавить юзера в очередь поиска. Возвращает размер очереди."""
        key = f"matchmaking:{mode}"
        await self.client.sadd(key, str(tg_id))
        await self.client.expire(key, 300)  # очередь живёт 5 минут
        return await self.client.scard(key)

    async def matchmaking_leave(self, tg_id: int, mode: str = "regular") -> None:
        """Убрать юзера из очереди поиска."""
        await self.client.srem(f"matchmaking:{mode}", str(tg_id))

    async def matchmaking_get_all(self, mode: str = "regular") -> list[int]:
        """Получить всех в очереди."""
        members = await self.client.smembers(f"matchmaking:{mode}")
        return [int(m) for m in members]

    async def matchmaking_clear(self, mode: str = "regular") -> None:
        """Очистить очередь (после старта игры)."""
        await self.client.delete(f"matchmaking:{mode}")

    async def matchmaking_is_searching(self, tg_id: int, mode: str = "regular") -> bool:
        """Проверить — юзер в очереди поиска?"""
        return bool(await self.client.sismember(f"matchmaking:{mode}", str(tg_id)))

    async def set_last_message(self, tg_id: int, message_id: int) -> None:
        """Запоминаем ID последнего сообщения бота юзеру. TTL = 48ч."""
        await self.client.set(f"last_msg:{tg_id}", str(message_id), ex=172800)

    async def get_last_message(self, tg_id: int) -> Optional[int]:
        val = await self.client.get(f"last_msg:{tg_id}")
        return int(val) if val else None

    async def del_last_message(self, tg_id: int) -> None:
        await self.client.delete(f"last_msg:{tg_id}")

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        serialized = json.dumps(value) if not isinstance(value, str) else value
        if ttl:
            await self.client.set(key, serialized, ex=ttl)
        else:
            await self.client.set(key, serialized)

    async def get(self, key: str) -> Optional[str]:
        return await self.client.get(key)

    async def delete(self, *keys: str) -> None:
        await self.client.delete(*keys)


# Синглтон
redis_client = RedisClient()
