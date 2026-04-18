"""
app/bot/handlers/lobby.py — Создание, вход, запуск, выход из лобби.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import (
    lobby_created_kb,
    lobby_mode_kb,
    lobby_waiting_kb,
    paywall_kb,
    quickmatch_kb,
    ready_kb,
)
from app.bot.states import LobbyCreation, LobbyJoin
from app.database.models import User
from app.services.lobby_service import (
    close_lobby,
    create_lobby,
    get_lobby_by_hash,
    get_lobby_by_id,
    get_lobby_members,
    join_lobby,
    leave_lobby,
    start_game,
)
from app.config import settings
from app.utils.redis_client import redis_client

router = Router()


# ─── Уведомление участников при входе нового игрока ──────────────────────────

async def _notify_members_new_player(bot, lobby, members, new_user: User) -> None:
    """Уведомляем всех в лобби что пришёл новый игрок."""
    member_list = "\n".join(
        f"• {m.user.first_name}{'  👑' if str(m.user_id) == str(lobby.host_id) else ''}"
        for m in members
    )
    is_host_check = lambda uid: str(uid) == str(lobby.host_id)

    for member in members:
        if str(member.user_id) == str(new_user.id):
            continue  # новичку уже показан экран входа
        try:
            await bot.send_message(
                chat_id=member.user.tg_id,
                text=(
                    f"👥 <b>{new_user.first_name}</b> вошёл в комнату!\n\n"
                    f"Игроков: <b>{len(members)}/{settings.max_lobby_size}</b>\n\n"
                    f"{member_list}"
                ),
                reply_markup=lobby_waiting_kb(str(lobby.id), is_host_check(member.user_id)),
                parse_mode="HTML",
            )
        except Exception:
            pass


# ─── Создание лобби ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:create_lobby")
async def cb_create_lobby(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    # Сначала проверяем — нет ли уже активного лобби
    from app.services.lobby_service import _get_user_active_lobby, get_lobby_members
    existing = await _get_user_active_lobby(db, user)
    if existing:
        members = await get_lobby_members(db, existing.id)
        member_list = "\n".join(
            f"• {m.user.first_name}{'  👑' if str(m.user_id) == str(existing.host_id) else ''}"
            for m in members
        )
        is_host = str(existing.host_id) == str(user.id)
        status = "🎮 Игра идёт" if existing.status.value == "active" else "⏳ Ожидание игроков"

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="↩️ Вернуться в комнату",
                callback_data=f"lobby:rejoin:{existing.id}"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="🆕 Покинуть и создать новую",
                callback_data=f"lobby:lac:{existing.id}:regular"
            )
        )
        builder.row(
            InlineKeyboardButton(text="« Главное меню", callback_data="menu:main")
        )
        await call.message.edit_text(
            f"⚠️ <b>Вы уже в комнате!</b>\n\n"
            f"Код: <code>{existing.join_hash}</code>\n"
            f"Статус: {status}\n\n"
            f"<b>Участники:</b>\n{member_list}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        await call.answer()
        return

    await state.set_state(LobbyCreation.choosing_mode)
    await call.message.edit_text(
        "🎲 <b>Новая комната</b>\n\nВыберите режим:",
        reply_markup=lobby_mode_kb(),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("lobby:mode:"), LobbyCreation.choosing_mode)
async def cb_lobby_mode_chosen(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    mode = call.data.split(":")[2]
    is_18_plus = mode == "18plus"

    # 18+ режим требует верификации
    if is_18_plus and not user.is_verification_active:
        await call.message.edit_text(
            "🔞 Для создания комнаты 18+ нужен статус <b>Verified</b>.",
            reply_markup=paywall_kb(),
            parse_mode="HTML",
        )
        await call.answer()
        return

    try:
        lobby = await create_lobby(db, user, is_18_plus)
    except ValueError:
        # Юзер уже в лобби — показываем ЧТО это за лобби и что делать
        from app.services.lobby_service import _get_user_active_lobby, get_lobby_members
        existing = await _get_user_active_lobby(db, user)
        if existing:
            members = await get_lobby_members(db, existing.id)
            member_names = "\n".join(
                f"• {m.user.first_name}{'  👑' if str(m.user_id) == str(existing.host_id) else ''}"
                for m in members
            )
            is_host = str(existing.host_id) == str(user.id)
            status = "🎮 Игра идёт" if existing.status.value == "active" else "⏳ Ожидание игроков"

            from aiogram.utils.keyboard import InlineKeyboardBuilder
            from aiogram.types import InlineKeyboardButton
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text="↩️ Вернуться в комнату",
                    callback_data=f"lobby:rejoin:{existing.id}"
                )
            )
            builder.row(
                InlineKeyboardButton(
                    text="❌ Покинуть старую и создать новую",
                    callback_data=f"lobby:lac:{existing.id}:{mode}"
                )
            )
            builder.row(
                InlineKeyboardButton(text="« Главное меню", callback_data="menu:main")
            )

            await call.message.edit_text(
                f"⚠️ <b>Вы уже в комнате!</b>\n\n"
                f"Код: <code>{existing.join_hash}</code>\n"
                f"Статус: {status}\n"
                f"Игроков: {len(members)}\n\n"
                f"<b>Участники:</b>\n{member_names}\n\n"
                f"Вернуться или покинуть и создать новую?",
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        else:
            await call.answer("Попробуйте ещё раз.", show_alert=True)
        await state.clear()
        await call.answer()
        return

    mode_label = "🔥 18+" if is_18_plus else "👨‍👩‍👧 Обычный"
    bot_me = await call.bot.get_me()
    bot_username = bot_me.username or ""

    text = (
        f"✅ <b>Комната создана!</b>\n\n"
        f"Режим: {mode_label}\n"
        f"🔑 Код: <code>{lobby.join_hash}</code>\n\n"
        f"Поделитесь кодом или ссылкой с друзьями.\n"
        f"Нужно минимум 2 игрока для старта.\n\n"
        f"👥 Игроки (1/{settings.max_lobby_size}):\n"
        f"• {user.first_name} 👑"
    )
    await call.message.edit_text(
        text,
        reply_markup=lobby_created_kb(lobby.join_hash, str(lobby.id), bot_username),
        parse_mode="HTML",
    )
    await state.clear()
    await call.answer()


# ─── Вернуться в существующую комнату ────────────────────────────────────────

@router.callback_query(F.data.startswith("lobby:rejoin:"))
async def cb_rejoin_existing(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
) -> None:
    lobby_id = call.data.split(":")[2]
    lobby = await get_lobby_by_id(db, lobby_id)
    if not lobby:
        from app.bot.keyboards.inline import main_menu_kb
        await call.message.edit_text("❌ Комната уже закрыта.", reply_markup=main_menu_kb())
        await call.answer()
        return

    members = await get_lobby_members(db, lobby.id)
    is_host = str(lobby.host_id) == str(user.id)
    member_list = "\n".join(
        f"• {m.user.first_name}{'  👑' if str(m.user_id) == str(lobby.host_id) else ''}"
        for m in members
    )
    status = "🎮 Игра идёт" if lobby.status.value == "active" else "⏳ Ожидание игроков"

    await call.message.edit_text(
        f"{status}\n"
        f"🔑 Код: <code>{lobby.join_hash}</code>\n\n"
        f"👥 Игроки ({len(members)}/{settings.max_lobby_size}):\n{member_list}",
        reply_markup=lobby_waiting_kb(str(lobby.id), is_host),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Покинуть старую комнату и создать новую ──────────────────────────────────

@router.callback_query(F.data.startswith("lobby:lac:"))
async def cb_leave_and_create(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    parts = call.data.split(":")
    lobby_id = parts[2]
    mode = parts[3] if len(parts) > 3 else "regular"

    # Покидаем старое лобби
    lobby = await get_lobby_by_id(db, lobby_id)
    if lobby:
        await leave_lobby(db, lobby, user)

    # Создаём новое
    is_18_plus = mode == "18plus"
    if is_18_plus and not user.is_verification_active:
        from app.bot.keyboards.inline import paywall_kb
        await call.message.edit_text(
            "🔞 Для создания комнаты 18+ нужен статус <b>Verified</b>.",
            reply_markup=paywall_kb(),
            parse_mode="HTML",
        )
        await call.answer()
        return

    new_lobby = await create_lobby(db, user, is_18_plus)
    bot_me = await call.bot.get_me()
    bot_username = bot_me.username or ""
    mode_label = "🔥 18+" if is_18_plus else "👨‍👩‍👧 Обычный"

    await call.message.edit_text(
        f"✅ <b>Новая комната создана!</b>\n\n"
        f"Режим: {mode_label}\n"
        f"🔑 Код: <code>{new_lobby.join_hash}</code>\n\n"
        f"👥 Игроки (1/{settings.max_lobby_size}):\n"
        f"• {user.first_name} 👑",
        reply_markup=lobby_created_kb(new_lobby.join_hash, str(new_lobby.id), bot_username),
        parse_mode="HTML",
    )
    await state.clear()
    await call.answer()


# ─── Вход по коду ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:join_lobby")
async def cb_join_lobby(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LobbyJoin.entering_code)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="« Отмена", callback_data="menu:main"))

    await call.message.edit_text(
        "🔑 <b>Войти по коду</b>\n\n"
        "Введите 6-символьный код комнаты:\n"
        "Например: <code>A3F7C2</code>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await call.answer()


@router.message(LobbyJoin.entering_code)
async def msg_join_code_entered(
    message: Message,
    user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    join_hash = message.text.strip().upper() if message.text else ""

    if len(join_hash) != 6:
        # Удаляем неверный ввод пользователя
        try:
            await message.delete()
        except Exception:
            pass
        # Редактируем карточку с подсказкой
        from app.utils.redis_client import redis_client
        last_id = await redis_client.get_last_message(user.tg_id)
        if last_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=last_id,
                    text="🔑 Код должен быть <b>ровно 6 символов</b>.\n\n"
                         "Попробуйте ещё раз:\nНапример: <code>A3F7C2</code>",
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        await message.answer("❌ Код должен быть 6 символов. Попробуйте ещё раз.")
        return

    await _do_join(message, user, db, join_hash, state)


async def process_join_by_hash(
    message: Message,
    user: User,
    join_hash: str,
    state: FSMContext,
) -> None:
    """Вызывается из /start deeplink."""
    from app.database.session import get_db_context
    async with get_db_context() as db:
        await _do_join(message, user, db, join_hash, state)


async def _do_join(
    message: Message,
    user: User,
    db: AsyncSession,
    join_hash: str,
    state: FSMContext,
) -> None:
    lobby, error = await join_lobby(db, user, join_hash)

    # Удаляем сообщение с кодом от пользователя
    try:
        await message.delete()
    except Exception:
        pass

    # Редактируем последнюю карточку бота или отправляем новую
    from app.utils.redis_client import redis_client

    async def _reply(text: str, kb):
        last_id = await redis_client.get_last_message(user.tg_id)
        if last_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=last_id,
                    text=text, reply_markup=kb, parse_mode="HTML",
                )
                return
            except Exception:
                pass
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        await redis_client.set_last_message(user.tg_id, sent.message_id)

    if error == "PAYWALL":
        await _reply(
            "🔞 Эта комната только для <b>Verified 18+</b> участников.\n"
            "Оформить пропуск?",
            paywall_kb(),
        )
        await state.clear()
        return

    if error == "already_joined":
        members = await get_lobby_members(db, lobby.id)
        is_host = str(lobby.host_id) == str(user.id)
        await _reply(
            f"✅ Вы уже в этой комнате.\n🔑 Код: <code>{lobby.join_hash}</code>",
            lobby_waiting_kb(str(lobby.id), is_host),
        )
        await state.clear()
        return

    if error:
        await _reply(f"❌ {error}", None)
        return

    members = await get_lobby_members(db, lobby.id)
    is_host = str(lobby.host_id) == str(user.id)
    member_list = "\n".join(
        f"• {m.user.first_name}{'  👑' if str(m.user_id) == str(lobby.host_id) else ''}"
        for m in members
    )

    await _reply(
        f"✅ <b>Вы вошли в комнату!</b>\n\n"
        f"🔑 Код: <code>{lobby.join_hash}</code>\n"
        f"👥 Игроки ({len(members)}/{settings.max_lobby_size}):\n{member_list}\n\n"
        f"Ожидаем начала игры...",
        lobby_waiting_kb(str(lobby.id), is_host),
    )

    # Уведомляем остальных участников что пришёл новый игрок
    await _notify_members_new_player(message.bot, lobby, members, user)

    await state.clear()


# ─── Начало игры ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lobby:start:"))
async def cb_start_game(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
) -> None:
    lobby_id = call.data.split(":")[2]
    if not await redis_client.acquire_action(user.tg_id, "start_game", ttl=5):
        await call.answer("⏳ Обрабатывается...", show_alert=False)
        return
    lobby = await get_lobby_by_id(db, lobby_id)

    if not lobby:
        await call.answer("❌ Комната не найдена.", show_alert=True)
        return

    success, error = await start_game(db, lobby, user)
    if not success:
        await call.answer(error, show_alert=True)
        return

    # Уведомляем всех участников — игра началась
    members = await get_lobby_members(db, lobby.id)
    from app.bot.handlers.game import send_turn_notification
    await send_turn_notification(call.bot, lobby, members)

    await call.message.edit_text(
        "🎮 <b>Игра началась!</b>\nПроверяйте личные сообщения от бота.",
        parse_mode="HTML",
    )
    await call.answer("▶️ Игра началась!")


# ─── Выход из комнаты ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lobby:leave:"))
async def cb_leave_lobby(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    lobby_id = call.data.split(":")[2]
    lobby = await get_lobby_by_id(db, lobby_id)

    if not lobby:
        await call.answer("❌ Комната не найдена.", show_alert=True)
        return

    lobby_closed = await leave_lobby(db, lobby, user)
    await state.clear()

    if lobby_closed:
        await call.message.edit_text("🚪 Комната закрыта.")
    else:
        from app.bot.keyboards.inline import main_menu_kb
        await call.message.edit_text(
            "👋 Вы покинули комнату.",
            reply_markup=main_menu_kb(),
        )
    await call.answer()


# ─── Закрыть лобби (хост) ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lobby:close:"))
async def cb_close_lobby(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
) -> None:
    lobby_id = call.data.split(":")[2]
    lobby = await get_lobby_by_id(db, lobby_id)

    if not lobby or str(lobby.host_id) != str(user.id):
        await call.answer("❌ Нет прав.", show_alert=True)
        return

    await close_lobby(db, lobby)
    from app.bot.keyboards.inline import main_menu_kb
    await call.message.edit_text(
        "✅ Комната закрыта.",
        reply_markup=main_menu_kb(),
    )
    await call.answer()

# ─── Быстрый поиск (Matchmaking) ──────────────────────────────────────────────

QUICKMATCH_MIN_PLAYERS = 2   # минимум игроков для старта
QUICKMATCH_MAX_PLAYERS = 6   # максимум в quickmatch-комнате


@router.callback_query(F.data == "menu:quickmatch")
async def cb_quickmatch(call: CallbackQuery, user: User, db: AsyncSession) -> None:
    """Встаём в очередь быстрого поиска."""
    from app.services.lobby_service import _get_user_active_lobby
    existing = await _get_user_active_lobby(db, user)
    if existing:
        await call.answer("Вы уже в комнате! Сначала выйдите.", show_alert=True)
        return

    if await redis_client.matchmaking_is_searching(user.tg_id):
        await call.answer("Вы уже в очереди поиска.", show_alert=True)
        return

    queue_size = await redis_client.matchmaking_join(user.tg_id)

    await call.message.edit_text(
        f"🔍 <b>Поиск игроков...</b>\n\n"
        f"В очереди: <b>{queue_size}</b> чел.\n"
        f"Нужно минимум {QUICKMATCH_MIN_PLAYERS} для старта.\n\n"
        f"Ожидайте — комната создастся автоматически!",
        reply_markup=quickmatch_kb(),
        parse_mode="HTML",
    )
    await call.answer()

    if queue_size >= QUICKMATCH_MIN_PLAYERS:
        await _try_start_quickmatch(call.bot, db)


@router.callback_query(F.data == "menu:quickmatch18")
async def cb_quickmatch18(call: CallbackQuery, user: User, db: AsyncSession) -> None:
    """Быстрый поиск 18+ — только для Verified."""
    if not user.is_verification_active:
        await call.message.edit_text(
            "🔞 <b>Поиск 18+</b>\n\n"
            "Для участия нужен статус <b>Verified 18+</b>.\n\n"
            "⭐ Стоимость: 99 Stars (~$1.29/мес)",
            reply_markup=paywall_kb(),
            parse_mode="HTML",
        )
        await call.answer()
        return

    from app.services.lobby_service import _get_user_active_lobby
    existing = await _get_user_active_lobby(db, user)
    if existing:
        await call.answer("Вы уже в комнате! Сначала выйдите.", show_alert=True)
        return

    if await redis_client.matchmaking_is_searching(user.tg_id, "18plus"):
        await call.answer("Вы уже в очереди поиска.", show_alert=True)
        return

    queue_size = await redis_client.matchmaking_join(user.tg_id, "18plus")

    await call.message.edit_text(
        f"🔞 <b>Поиск игроков 18+...</b>\n\n"
        f"В очереди: <b>{queue_size}</b> чел.\n"
        f"Нужно минимум {QUICKMATCH_MIN_PLAYERS} для старта.\n\n"
        f"Ожидайте — комната создастся автоматически!",
        reply_markup=quickmatch_kb("18plus"),
        parse_mode="HTML",
    )
    await call.answer()

    if queue_size >= QUICKMATCH_MIN_PLAYERS:
        await _try_start_quickmatch(call.bot, db, mode="18plus")


@router.callback_query(F.data.startswith("quickmatch:cancel:"))
async def cb_quickmatch_cancel(call: CallbackQuery, user: User, state: FSMContext) -> None:
    """Выходим из очереди поиска."""
    mode = call.data.split(":")[2]
    await redis_client.matchmaking_leave(user.tg_id, mode)

    from app.bot.keyboards.inline import main_menu_kb
    from app.bot.handlers.start import _verified_status
    await call.message.edit_text(
        f"🏠 <b>Главное меню</b>\n\n"
        f"{_verified_status(user)}\n"
        f"⭐ Stars: <b>{user.stars_balance}</b>",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
    await call.answer("Поиск отменён.")


async def _try_start_quickmatch(bot, db: AsyncSession, mode: str = "regular") -> None:
    """
    Если в очереди достаточно игроков — создаём комнату и добавляем всех.
    Первый в очереди становится хостом.
    """
    tg_ids = await redis_client.matchmaking_get_all(mode)
    if len(tg_ids) < QUICKMATCH_MIN_PLAYERS:
        return

    # Берём первых MAX игроков
    tg_ids = tg_ids[:QUICKMATCH_MAX_PLAYERS]

    from sqlalchemy import select
    from app.database.models import User as UserModel
    from app.services.lobby_service import create_lobby as _create_lobby

    # Загружаем юзеров из БД
    result = await db.execute(
        select(UserModel).where(UserModel.tg_id.in_(tg_ids))
    )
    users = {u.tg_id: u for u in result.scalars().all()}

    if len(users) < QUICKMATCH_MIN_PLAYERS:
        return

    # Очищаем очередь СРАЗУ чтобы не было двойного старта
    await redis_client.matchmaking_clear(mode)

    # Первый юзер = хост
    host = list(users.values())[0]
    try:
        lobby = await _create_lobby(db, host, is_18_plus=False)
    except ValueError:
        # Хост уже в лобби — берём следующего
        for u in list(users.values())[1:]:
            try:
                lobby = await _create_lobby(db, u, is_18_plus=False)
                host = u
                break
            except ValueError:
                continue
        else:
            return

    # Добавляем остальных
    from app.services.lobby_service import join_lobby as _join_lobby
    joined_users = [host]
    for tg_id, u in users.items():
        if u.id == host.id:
            continue
        _, err = await _join_lobby(db, u, lobby.join_hash)
        if not err or err == "already_joined":
            joined_users.append(u)

    # Обновляем список участников
    members = await get_lobby_members(db, lobby.id)
    member_list = "\n".join(
        f"• {m.user.first_name}{'  👑' if str(m.user_id) == str(lobby.host_id) else ''}"
        for m in members
    )

    # Уведомляем всех найденных игроков
    for u in joined_users:
        is_host_flag = str(u.id) == str(lobby.host_id)
        try:
            msg = await bot.send_message(
                chat_id=u.tg_id,
                text=(
                    f"🎉 <b>Игра найдена!</b>\n\n"
                    f"🔑 Код: <code>{lobby.join_hash}</code>\n"
                    f"👥 Игроки ({len(members)}):\n{member_list}\n\n"
                    f"Нажми <b>✅ Я готов!</b> когда будешь готов начать.\n"
                    f"Игра стартует когда все нажмут Готов."
                ),
                reply_markup=ready_kb(str(lobby.id)),
                parse_mode="HTML",
            )
            await redis_client.set_last_message(u.tg_id, msg.message_id)
        except Exception:
            pass
