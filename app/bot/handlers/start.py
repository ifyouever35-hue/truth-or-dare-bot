"""
app/bot/handlers/start.py — /start, меню, профиль, магазин, лидерборд.
"""
from aiogram import F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import (
    back_to_menu_kb,
    main_menu_kb,
    profile_kb,
    shop_kb,
    verified_buy_kb,
)
from app.config import settings
from app.database.models import User
from app.utils.redis_client import redis_client

router = Router()


def _verified_badge(user: User) -> str:
    from app.config_premium import is_permanent_premium
    if is_permanent_premium(user.tg_id):
        return "👑 Premium навсегда"
    if user.is_verification_active:
        exp = user.verified_expires_at.strftime("%d.%m.%Y")
        return f"💎 Verified 18+ · до {exp}"
    return "🔓 Обычный аккаунт"


async def _replace_card(message: Message, user: User, text: str, kb) -> None:
    """Удаляет предыдущую карточку бота, отправляет новую."""
    last_id = await redis_client.get_last_message(user.tg_id)
    if last_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=last_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass
    sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await redis_client.set_last_message(user.tg_id, sent.message_id)


# ─── Онбординг для новых пользователей ───────────────────────────────────────

ONBOARDING_TEXT = """\
🎲 <b>Добро пожаловать в Правда или Действие!</b>

Это бот для весёлой игры с друзьями — онлайн, в любом месте.

<b>Как играть:</b>
1️⃣ Создай комнату или найди игру через быстрый поиск
2️⃣ Позови друзей — поделись кодом или ссылкой
3️⃣ Выбирай: 🗣 <b>Правда</b> или ⚡ <b>Действие</b>
4️⃣ Выполняй задания, зарабатывай очки, не теряй жизни ❤️

<b>Особенности:</b>
🔞 Режим 18+ с более смелыми заданиями
💰 Откупись от задания за Stars если совсем не хочешь
🔍 Быстрый поиск — находим тебе партнёров автоматически
📸 Некоторые задания требуют фото/видео подтверждения

<b>Получай каждый день:</b>
🎁 +3 ⭐ Stars просто за вход в бота

Нажми <b>Начать игру</b> и погнали! 🚀\
"""


async def _check_daily_bonus(user: User) -> str:
    """Ежедневный бонус +3 Stars. Возвращает текст если выдан."""
    from datetime import date
    key = f"daily_bonus:{user.tg_id}:{date.today().isoformat()}"
    if await redis_client.get(key):
        return ""
    from app.database.session import get_db_context
    from app.services.user_service import add_stars
    async with get_db_context() as db:
        from sqlalchemy import select
        from app.database.models import User as UserModel
        result = await db.execute(select(UserModel).where(UserModel.tg_id == user.tg_id))
        u = result.scalar_one_or_none()
        if u:
            await add_stars(db, u, 3)
    await redis_client.set(key, "1", ttl=86400)
    return "🎁 <b>+3 ⭐ ежедневный бонус!</b>"


def _menu_text(user: User, extra: str = "") -> str:
    text = (
        f"🏠 <b>Главное меню</b>\n\n"
        f"{_verified_badge(user)}\n"
        f"⭐ Stars: <b>{user.stars_balance}</b>"
    )
    if extra:
        text += f"\n\n{extra}"
    return text


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(
    message: Message,
    user: User,
    state: FSMContext,
    is_new_user: bool = False,
) -> None:
    await state.clear()

    # Deeplink: /start join_XXXXXX
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("join_"):
        join_hash = args[1][5:]
        from app.bot.handlers.lobby import process_join_by_hash
        await process_join_by_hash(message, user, join_hash, state)
        return

    # Новый пользователь — онбординг
    if is_new_user:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🚀 Начать игру!", callback_data="menu:main"))
        try:
            await message.delete()
        except Exception:
            pass
        last_id = await redis_client.get_last_message(user.tg_id)
        if last_id:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=last_id)
            except Exception:
                pass
        sent = await message.answer(ONBOARDING_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")
        await redis_client.set_last_message(user.tg_id, sent.message_id)
        return

    bonus = await _check_daily_bonus(user)
    await _replace_card(message, user, _menu_text(user, bonus), main_menu_kb())


# ─── Меню ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:main")
async def cb_main_menu(call: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(_menu_text(user), reply_markup=main_menu_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "menu:back")
async def cb_back(call: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(_menu_text(user), reply_markup=main_menu_kb(), parse_mode="HTML")
    await call.answer()


# ─── Профиль ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:profile")
async def cb_profile(call: CallbackQuery, user: User, db: AsyncSession) -> None:
    win_rate = round(user.games_won / user.games_played * 100) if user.games_played > 0 else 0

    from sqlalchemy import select
    from app.database.models import Lobby, LobbyMember, LobbyStatus
    result = await db.execute(
        select(Lobby)
        .join(LobbyMember, LobbyMember.lobby_id == Lobby.id)
        .where(
            LobbyMember.user_id == user.id,
            LobbyMember.is_active == True,  # noqa: E712
            Lobby.status.in_([LobbyStatus.WAITING, LobbyStatus.ACTIVE]),
        )
        .limit(1)
    )
    has_active = result.scalars().first() is not None

    # Уровень активности
    if user.games_played == 0:
        rank = "🌱 Новичок"
    elif user.games_played < 5:
        rank = "🎮 Игрок"
    elif user.games_played < 20:
        rank = "⚡ Бывалый"
    elif user.games_played < 50:
        rank = "🔥 Ветеран"
    else:
        rank = "👑 Легенда"

    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"{'@' + user.username if user.username else user.first_name}\n"
        f"{rank}\n\n"
        f"🎮 Игр сыграно: <b>{user.games_played}</b>\n"
        f"🏆 Побед: <b>{user.games_won}</b> ({win_rate}%)\n"
        f"✅ Действий: <b>{user.dares_completed}</b>\n"
        f"🗣 Правд: <b>{user.truths_answered}</b>\n\n"
        f"⭐ Stars: <b>{user.stars_balance}</b>\n"
        f"{_verified_badge(user)}"
    )
    await call.message.edit_text(text, reply_markup=profile_kb(has_active), parse_mode="HTML")
    await call.answer()


# ─── Вернуться в игру ────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:rejoin")
async def cb_rejoin(call: CallbackQuery, user: User, db: AsyncSession) -> None:
    from sqlalchemy import select
    from app.database.models import Lobby, LobbyMember, LobbyStatus
    from app.bot.keyboards.inline import lobby_waiting_kb

    result = await db.execute(
        select(Lobby)
        .join(LobbyMember, LobbyMember.lobby_id == Lobby.id)
        .where(
            LobbyMember.user_id == user.id,
            LobbyMember.is_active == True,  # noqa: E712
            Lobby.status.in_([LobbyStatus.WAITING, LobbyStatus.ACTIVE]),
        )
        .limit(1)
    )
    lobby = result.scalars().first()

    if not lobby:
        await call.message.edit_text("У вас нет активной комнаты.", reply_markup=main_menu_kb())
        await call.answer()
        return

    is_host = str(lobby.host_id) == str(user.id)
    status = "🎮 Игра идёт" if lobby.status == LobbyStatus.ACTIVE else "⏳ Ожидание игроков"
    await call.message.edit_text(
        f"{status}\n🔑 Код: <code>{lobby.join_hash}</code>",
        reply_markup=lobby_waiting_kb(str(lobby.id), is_host),
        parse_mode="HTML",
    )
    await call.answer()


# ─── О боте ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:about")
async def cb_about(call: CallbackQuery) -> None:
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Написать разработчику", url="https://t.me/"))
    builder.row(InlineKeyboardButton(text="« Назад", callback_data="menu:main"))

    await call.message.edit_text(
        "ℹ️ <b>О боте</b>\n\n"
        "<b>Правда или Действие</b> — бот для игры с друзьями.\n\n"
        f"📋 Заданий в базе: 245+\n"
        f"🔞 Режим 18+ с отдельным пулом\n"
        f"🔍 Быстрый поиск партнёров\n"
        f"📸 Медиа-подтверждения заданий\n"
        f"⭐ Система Stars и откупов\n\n"
        f"<b>Правила:</b>\n"
        f"• Уважай других игроков\n"
        f"• Не отправляй неприемлемый контент\n"
        f"• Не используй бота для спама\n\n"
        f"Нашли баг или есть идея? Пишите разработчику 👇",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Verified 18+ ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:get_verified")
async def cb_get_verified(call: CallbackQuery, user: User) -> None:
    is_renewal = user.is_verification_active
    price = settings.verified_stars_price
    if is_renewal:
        price = max(price - settings.verified_renewal_discount, 1)
        exp = user.verified_expires_at.strftime("%d.%m.%Y")
        header = f"💎 <b>Verified 18+</b>\n\n✅ Активна до {exp}\n\n🔄 Продлить со скидкой:\n"
    else:
        header = (
            "💎 <b>Verified 18+</b>\n\n"
            "<b>Что даёт:</b>\n"
            "• Доступ к комнатам 18+\n"
            "• Взрослые задания и правда\n"
            "• Быстрый поиск 18+ партнёров\n\n"
        )

    text = (
        f"{header}"
        f"💰 Стоимость: <b>{price} ⭐ Stars</b> (~$1.29)\n"
        f"📅 Срок: 30 дней\n\n"
        f"⭐ Ваш баланс: {user.stars_balance} Stars"
    )
    await call.message.edit_text(text, reply_markup=verified_buy_kb(is_renewal), parse_mode="HTML")
    await call.answer()


# ─── Магазин Stars ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:shop")
async def cb_shop(call: CallbackQuery, user: User) -> None:
    text = (
        f"⭐ <b>Магазин Stars</b>\n\n"
        f"Stars — внутренняя валюта бота.\n"
        f"Используются для <b>откупа</b> от заданий ({settings.buyout_cost_stars} ⭐ за ход).\n\n"
        f"💡 <i>Чем больше пакет — тем выгоднее бонус!</i>\n\n"
        f"Ваш баланс: <b>{user.stars_balance} ⭐</b>\n\n"
        f"Выберите пакет:"
    )
    await call.message.edit_text(text, reply_markup=shop_kb(), parse_mode="HTML")
    await call.answer()


# ─── Топ игроков ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:leaderboard")
async def cb_leaderboard(call: CallbackQuery, db: AsyncSession) -> None:
    from sqlalchemy import select, desc
    from app.database.models import User as UserModel

    rating_expr = (
        UserModel.games_won * 3
        + UserModel.dares_completed
        + UserModel.truths_answered
    ).label("rating")

    result = await db.execute(
        select(
            UserModel.first_name, UserModel.username,
            UserModel.games_played, UserModel.games_won,
            UserModel.dares_completed, UserModel.truths_answered,
            rating_expr,
        )
        .where(UserModel.games_played > 0, UserModel.is_banned == False)  # noqa: E712
        .order_by(desc("rating"))
        .limit(10)
    )
    rows = result.all()

    if not rows:
        await call.message.edit_text(
            "🏆 <b>Топ игроков</b>\n\nПока никто не сыграл ни одной игры.\nБудь первым!",
            reply_markup=back_to_menu_kb(),
            parse_mode="HTML",
        )
        await call.answer()
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = ["🏆 <b>Топ-10 игроков</b>\n"]
    for i, row in enumerate(rows):
        uname = f" @{row.username}" if row.username else ""
        lines.append(
            f"{medals[i]} <b>{row.first_name}</b>{uname}\n"
            f"   ⭐ {row.rating} · 🎮 {row.games_played} · 🏆 {row.games_won}"
        )

    await call.message.edit_text(
        "\n".join(lines), reply_markup=back_to_menu_kb(), parse_mode="HTML",
    )
    await call.answer()


# ─── /help и /about ───────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📖 <b>Как играть</b>\n\n"
        "1. Создай комнату или найди через <b>Быстрый поиск</b>\n"
        "2. Позови друзей — поделись кодом или ссылкой\n"
        "3. Дождись минимум 2 игроков → нажми ▶️ Начать\n"
        "4. Выбирай: 🗣 Правда или ⚡ Действие\n"
        "5. Выполняй задания, зарабатывай очки\n"
        "6. Потерял все жизни — выбываешь!\n\n"
        f"⏱ Время: {settings.task_timer_seconds} сек · ❤️ Жизней: {settings.default_lives}\n"
        f"💰 Откуп: {settings.buyout_cost_stars} ⭐ · 🎁 Бонус: +3 ⭐/день\n\n"
        "Команды:\n"
        "/start — главное меню\n"
        "/profile — твой профиль\n"
        "/shop — магазин Stars\n"
        "/help — эта справка"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb())


@router.message(Command("profile"))
async def cmd_profile(message: Message, user: User) -> None:
    """Быстрый доступ к профилю через команду."""
    win_rate = round(user.games_won / user.games_played * 100) if user.games_played > 0 else 0
    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"{'@' + user.username if user.username else user.first_name}\n\n"
        f"🎮 Игр: {user.games_played} · 🏆 Побед: {user.games_won} ({win_rate}%)\n"
        f"⭐ Stars: {user.stars_balance} · {_verified_badge(user)}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=profile_kb())


@router.message(Command("top"))
async def cmd_top(message: Message) -> None:
    """Быстрый доступ к топу через команду."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Открыть топ", callback_data="menu:leaderboard"))
    await message.answer("🏆 Нажми чтобы открыть топ игроков:", reply_markup=builder.as_markup())


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    """Быстрый доступ к разделу о боте."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="ℹ️ Открыть", callback_data="menu:about"))
    await message.answer("ℹ️ Информация о боте:", reply_markup=builder.as_markup())
    """Быстрый доступ к магазину."""
    text = (
        f"⭐ <b>Магазин Stars</b>\n\n"
        f"Баланс: <b>{user.stars_balance} ⭐</b>\n"
        f"Откуп за ход: {settings.buyout_cost_stars} ⭐"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=shop_kb())
