"""
app/config_premium.py — Перманентные Premium пользователи.

Как добавить себя:
  1. Узнай свой Telegram ID: напиши @userinfobot в Telegram
  2. Добавь ID в список PREMIUM_USER_IDS ниже
  3. Перезапусти бота

Что даёт Premium навсегда:
  • Verified 18+ не требует оплаты (никогда не истекает)
  • Безлимитный баланс Stars (откуп всегда доступен)
  • Значок 👑 в профиле
  • Доступ к /admin команде в боте
"""

# ── Добавь свой Telegram ID сюда ──────────────────────────────────────────────
# Узнать ID: написать @userinfobot или @getmyid_bot в Telegram
PREMIUM_USER_IDS: set[int] = {
    # 123456789,   # Пример: замени на свой реальный ID
}

# ── Admins — могут использовать /admin команду в боте ─────────────────────────
ADMIN_USER_IDS: set[int] = {
    # 123456789,   # Тот же список обычно
}


def is_permanent_premium(tg_id: int) -> bool:
    """Проверить — является ли юзер перманентным Premium."""
    return tg_id in PREMIUM_USER_IDS


def is_admin(tg_id: int) -> bool:
    """Проверить — является ли юзер администратором бота."""
    return tg_id in ADMIN_USER_IDS or tg_id in PREMIUM_USER_IDS
