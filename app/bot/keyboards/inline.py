"""
app/bot/keyboards/inline.py — Все Inline-клавиатуры бота.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


ANON_BOT_USERNAME = "neverland_anon_chatbot"


def _anon_chat_url(tg_id: int | None = None) -> str:
    base = f"https://t.me/{ANON_BOT_USERNAME}"
    if tg_id is not None:
        return f"{base}?start=fromtod_{tg_id}"
    return base


def main_menu_kb(user=None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tg_id = getattr(user, "tg_id", None)
    builder.row(
        InlineKeyboardButton(text="🎭 Анонимный чат — общение по интересам", url=_anon_chat_url(tg_id)),
    )
    builder.row(
        InlineKeyboardButton(text="🎲 Создать комнату", callback_data="menu:create_lobby"),
        InlineKeyboardButton(text="🔑 Войти по коду", callback_data="menu:join_lobby"),
    )
    builder.row(
        InlineKeyboardButton(text="🔍 Быстрый поиск", callback_data="menu:quickmatch"),
        InlineKeyboardButton(text="🔞 Поиск 18+", callback_data="menu:quickmatch18"),
    )
    builder.row(
        InlineKeyboardButton(text="💎 Verified 18+", callback_data="menu:get_verified"),
        InlineKeyboardButton(text="⭐ Магазин Stars", callback_data="menu:shop"),
    )
    builder.row(
        InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile"),
        InlineKeyboardButton(text="🏆 Топ игроков", callback_data="menu:leaderboard"),
    )
    builder.row(
        InlineKeyboardButton(text="ℹ️ О боте", callback_data="menu:about"),
    )
    return builder.as_markup()


def lobby_mode_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👨‍👩‍👧 Обычный (0+)", callback_data="lobby:mode:regular"))
    builder.row(InlineKeyboardButton(text="🔥 Только для взрослых (18+)", callback_data="lobby:mode:18plus"))
    builder.row(InlineKeyboardButton(text="« Назад", callback_data="menu:back"))
    return builder.as_markup()


def lobby_created_kb(join_hash: str, lobby_id: str, bot_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="▶️ Начать игру", callback_data=f"lobby:start:{lobby_id}"))
    if bot_username:
        invite_url = f"https://t.me/{bot_username}?start=join_{join_hash}"
        builder.row(InlineKeyboardButton(text="🔗 Пригласить друзей", url=invite_url))
    builder.row(InlineKeyboardButton(text="❌ Закрыть лобби", callback_data=f"lobby:close:{lobby_id}"))
    return builder.as_markup()


def lobby_waiting_kb(lobby_id: str, is_host: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_host:
        builder.row(InlineKeyboardButton(text="▶️ Начать игру", callback_data=f"lobby:start:{lobby_id}"))
    builder.row(InlineKeyboardButton(text="🚪 Покинуть комнату", callback_data=f"lobby:leave:{lobby_id}"))
    return builder.as_markup()


def task_choice_kb(lobby_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗣 Правда", callback_data=f"game:pick:truth:{lobby_id}"),
        InlineKeyboardButton(text="⚡ Действие", callback_data=f"game:pick:dare:{lobby_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Своё задание", callback_data=f"game:custom:{lobby_id}"),
    )
    builder.row(InlineKeyboardButton(text="🚪 Покинуть игру", callback_data=f"game:leave:{lobby_id}"))
    return builder.as_markup()


def task_active_kb(lobby_id: str, media_required: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if media_required != "none":
        builder.row(InlineKeyboardButton(text="📎 Отправить подтверждение", callback_data=f"game:upload_media:{lobby_id}"))
    else:
        builder.row(InlineKeyboardButton(text="✅ Выполнил", callback_data=f"game:done:{lobby_id}"))
    builder.row(
        InlineKeyboardButton(text="❌ Сдаться (-1 жизнь)", callback_data=f"game:surrender:{lobby_id}"),
        InlineKeyboardButton(text="💰 Откупиться", callback_data=f"game:buyout:{lobby_id}"),
    )
    builder.row(InlineKeyboardButton(text="🚪 Покинуть игру", callback_data=f"game:leave:{lobby_id}"))
    return builder.as_markup()


def report_media_kb(lobby_id: str, media_id: str) -> InlineKeyboardMarkup:
    # media_id НЕ передаём в callback_data — Telegram лимит 64 байта
    # UUID(36) + UUID(36) + разделители = 85 байт > 64 = ошибка при нажатии
    # media_id хранится в Redis ключ lobby:{lobby_id}:current_media
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👁 Просмотрел", callback_data=f"game:viewed:{lobby_id}"),
        InlineKeyboardButton(text="🚩 Жалоба", callback_data=f"game:report:{lobby_id}"),
    )
    return builder.as_markup()


def buyout_confirm_kb(lobby_id: str, cost: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"💫 Откупиться за {cost} ⭐", callback_data=f"game:buyout_confirm:{lobby_id}"),
        InlineKeyboardButton(text="« Назад", callback_data=f"game:buyout_cancel:{lobby_id}"),
    )
    return builder.as_markup()


def shop_kb() -> InlineKeyboardMarkup:
    """Магазин Stars — пакеты с бонусом."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⭐ 50 Stars — $0.65", callback_data="shop:stars:small"))
    builder.row(InlineKeyboardButton(text="⭐ 170 Stars — $1.95  🔥 +13%", callback_data="shop:stars:medium"))
    builder.row(InlineKeyboardButton(text="⭐ 420 Stars — $4.55  💎 +20%", callback_data="shop:stars:large"))
    builder.row(InlineKeyboardButton(text="💎 Verified 18+ — 99 ⭐/мес", callback_data="menu:get_verified"))
    builder.row(InlineKeyboardButton(text="« Назад", callback_data="menu:main"))
    return builder.as_markup()


def paywall_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 Оформить Verified 18+", callback_data="menu:get_verified"))
    builder.row(InlineKeyboardButton(text="« Назад", callback_data="menu:back"))
    return builder.as_markup()


def verified_buy_kb(is_renewal: bool = False) -> InlineKeyboardMarkup:
    from app.config import settings
    price = settings.verified_stars_price
    if is_renewal:
        price = max(price - settings.verified_renewal_discount, 1)
        label = f"⭐ Продлить за {price} Stars (скидка {settings.verified_renewal_discount} ⭐)"
    else:
        label = f"⭐ Купить за {price} Stars"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=label, callback_data="payment:verified:stars"))
    builder.row(InlineKeyboardButton(text="« Назад", callback_data="menu:main"))
    return builder.as_markup()


def profile_kb(has_active_lobby: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_active_lobby:
        builder.row(InlineKeyboardButton(text="🎮 Вернуться в игру", callback_data="menu:rejoin"))
    builder.row(
        InlineKeyboardButton(text="⭐ Магазин Stars", callback_data="menu:shop"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"),
    )
    return builder.as_markup()


def game_over_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 В главное меню", callback_data="menu:main"),
    )
    return builder.as_markup()


def task_show_kb(lobby_id: str) -> InlineKeyboardMarkup:
    """Клавиатура для показа Действия без медиа — БЕЗ кнопки Выполнил.

    Выполнил появится отдельным сообщением снизу.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Сдаться (-1 жизнь)", callback_data=f"game:surrender:{lobby_id}"),
        InlineKeyboardButton(text="💰 Откупиться", callback_data=f"game:buyout:{lobby_id}"),
    )
    builder.row(InlineKeyboardButton(text="🚪 Покинуть игру", callback_data=f"game:leave:{lobby_id}"))
    return builder.as_markup()


def task_confirm_kb(lobby_id: str) -> InlineKeyboardMarkup:
    """Кнопка подтверждения выполнения — отправляется отдельно."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Я выполнил(а)!", callback_data=f"game:done:{lobby_id}"),
    )
    return builder.as_markup()


def ready_kb(lobby_id: str) -> InlineKeyboardMarkup:
    """Кнопка готовности перед стартом игры."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Я готов!", callback_data=f"game:ready:{lobby_id}"))
    builder.row(InlineKeyboardButton(text="🚪 Выйти", callback_data=f"lobby:leave:{lobby_id}"))
    return builder.as_markup()


def truth_answer_kb(lobby_id: str) -> InlineKeyboardMarkup:
    """Кнопки для ответа на правду."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Я ответил(а)", callback_data=f"game:truth_done:{lobby_id}"))
    builder.row(
        InlineKeyboardButton(text="💰 Откупиться", callback_data=f"game:buyout:{lobby_id}"),
    )
    return builder.as_markup()


def vote_kb(lobby_id: str) -> InlineKeyboardMarkup:
    """Голосование — выполнил или нет."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Выполнил(а)!", callback_data=f"game:vote:yes:{lobby_id}"),
        InlineKeyboardButton(text="❌ Не выполнил(а)", callback_data=f"game:vote:no:{lobby_id}"),
    )
    return builder.as_markup()


def redo_or_buyout_kb(lobby_id: str) -> InlineKeyboardMarkup:
    """Переделать или откупиться после провала голосования."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Переделать", callback_data=f"game:redo:{lobby_id}"))
    builder.row(InlineKeyboardButton(text="💸 Откупиться", callback_data=f"game:buyout:{lobby_id}"))
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="« Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def quickmatch_kb(mode: str = "regular") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="❌ Отменить поиск",
        callback_data=f"quickmatch:cancel:{mode}",
    ))
    return builder.as_markup()
