"""
app/bot/handlers/game.py — Игровой процесс.

Флоу:
  ПРАВДА:
    pick:truth → answering_truth (игрок отвечает текстом/голосом/кружком)
    → все видят ответ → truth_done → следующий ход

  ДЕЙСТВИЕ:
    pick:dare → uploading_dare (игрок загружает фото/видео)
    → все видят и голосуют ✅/❌
    → большинство ✅ → следующий ход
    → большинство ❌ → redo_or_buyout → переделать или откупиться

  ГОТОВНОСТЬ:
    game:ready:{lobby_id} → все нажали → старт
"""
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import (
    buyout_confirm_kb,
    game_over_kb,
    ready_kb,
    redo_or_buyout_kb,
    report_media_kb,
    task_active_kb,
    task_choice_kb,
    truth_answer_kb,
    vote_kb,
)
from app.bot.states import GamePlay
from app.config import settings
from app.database.models import Lobby, LobbyMember, TaskType, User
from app.services.game_service import (
    buyout_task,
    complete_task,
    get_next_task,
    start_task_timer,
    surrender_task,
)
from app.services.lobby_service import get_lobby_by_id, get_lobby_members
from app.utils.redis_client import redis_client

router = Router()


# ─── Уведомление о ходе ──────────────────────────────────────────────────────

async def send_turn_notification(bot: Bot, lobby: Lobby, members: list[LobbyMember]) -> None:
    """Отправляем уведомление о начале нового хода всем игрокам."""
    if not members:
        return

    import asyncio
    from app.utils.broadcast import send_safe

    current_idx = lobby.current_player_index % len(members)
    current_member = members[current_idx]

    await redis_client.set(
        f"current_player:{str(lobby.id)}",
        str(current_member.user.tg_id),
        ttl=3600,
    )

    score_lines = []
    for m in members:
        arrow = " ◀️" if m.user_id == current_member.user_id else "  "
        score_lines.append(f"{arrow} {m.user.first_name}  ❤️{m.lives}  ⭐{m.score}")
    scoreboard = "\n".join(score_lines)

    # Активному игроку
    await send_safe(bot,
        chat_id=current_member.user.tg_id,
        text=(
            f"🎯 <b>Ваш ход!</b>  (Раунд {lobby.current_round})\n\n"
            f"<b>Счёт:</b>\n{scoreboard}\n\n"
            f"Выберите тип задания:"
        ),
        reply_markup=task_choice_kb(str(lobby.id)),
        parse_mode="HTML",
    )

    # Наблюдателям — с задержкой (Telegram flood limit: 30 msg/sec)
    spectators = [m for m in members if m.user_id != current_member.user_id]
    for i, member in enumerate(spectators):
        if i > 0:
            await asyncio.sleep(0.035)
        await send_safe(bot,
            chat_id=member.user.tg_id,
            text=(
                f"⏳ Ход следующего игрока  (Раунд {lobby.current_round})\n\n"
                f"<b>Счёт:</b>\n{scoreboard}\n\n"
                f"Ожидайте выбора..."
            ),
            parse_mode="HTML",
        )


# ─── Система готовности ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:ready:"))
async def cb_player_ready(call: CallbackQuery, user: User, db: AsyncSession) -> None:
    lobby_id = call.data.split(":")[2]
    if not await redis_client.acquire_action(user.tg_id, "ready", ttl=5):
        await call.answer("⏳ Обрабатывается...", show_alert=False)
        return
    lobby = await get_lobby_by_id(db, lobby_id)
    if not lobby:
        await call.answer("❌ Комната не найдена.", show_alert=True)
        return

    members = await get_lobby_members(db, lobby.id)
    total = len(members)

    ready_count = await redis_client.ready_add(lobby_id, user.tg_id)

    # Обновляем карточку нажавшего
    await call.message.edit_text(
        f"✅ <b>Вы готовы!</b>\n\n"
        f"Ждём остальных: {ready_count}/{total} готовы",
        parse_mode="HTML",
    )
    await call.answer("✅ Готов!")

    # Уведомляем остальных
    for member in members:
        if str(member.user_id) == str(user.id):
            continue
        try:
            await call.bot.send_message(
                chat_id=member.user.tg_id,
                text=f"✅ Ещё один игрок готов! ({ready_count}/{total})",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Все готовы — стартуем!
    if ready_count >= total:
        # Защита от двойного старта
        from app.database.models import LobbyStatus
        if lobby.status == LobbyStatus.ACTIVE:
            return

        await redis_client.ready_clear(lobby_id)

        # Стартуем игру напрямую (без проверки хоста — quickmatch)
        lobby.status = LobbyStatus.ACTIVE
        lobby.current_round = 1
        lobby.current_player_index = 0
        await db.flush()

        # Сохраняем текущего игрока в Redis для быстрой проверки
        current_player = members[0]
        await redis_client.set(
            f"current_player:{lobby_id}",
            str(current_player.user.tg_id),
            ttl=3600,
        )

        await send_turn_notification(call.bot, lobby, members)


# ─── Своё задание ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:custom:"))
async def cb_custom_task(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Игрок хочет задать своё задание."""
    lobby_id = call.data.split(":")[2]

    # Проверяем что это ход этого игрока
    lobby = await get_lobby_by_id(db, lobby_id)
    if not lobby:
        await call.answer("❌ Комната не найдена.", show_alert=True)
        return

    members = await get_lobby_members(db, lobby.id)
    current_idx = lobby.current_player_index % len(members) if members else 0
    cached_tg = await redis_client.get(f"current_player:{lobby_id}")
    is_current = (
        (members and str(members[current_idx].user_id) == str(user.id)) or
        (cached_tg and int(cached_tg) == user.tg_id)
    )
    if not is_current:
        await call.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗣 Задать правду", callback_data=f"game:customtype:truth:{lobby_id}"),
        InlineKeyboardButton(text="⚡ Задать действие", callback_data=f"game:customtype:dare:{lobby_id}"),
    )
    builder.row(InlineKeyboardButton(text="« Назад", callback_data=f"game:pick:back:{lobby_id}"))

    await call.message.edit_text(
        "✏️ <b>Своё задание</b>\n\n"
        "Выбери тип — потом напиши текст задания.\n"
        "Остальные игроки его увидят и проголосуют.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("game:customtype:"))
async def cb_custom_task_type(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    parts = call.data.split(":")
    task_type = parts[2]   # truth / dare
    lobby_id  = parts[3]

    await state.set_state(GamePlay.entering_custom)
    await state.update_data(lobby_id=lobby_id, custom_type=task_type)

    type_label = "правду (вопрос)" if task_type == "truth" else "действие (задание)"
    await call.message.edit_text(
        f"✏️ Напиши <b>{type_label}</b> для других игроков.\n\n"
        f"Просто отправь текст следующим сообщением.\n"
        f"<i>Не злоупотребляй — жалобы могут привести к бану.</i>",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(GamePlay.entering_custom)
async def msg_custom_task_entered(
    message: Message,
    user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Игрок ввёл текст своего задания."""
    text = message.text.strip() if message.text else ""

    if not text:
        await message.answer("❌ Отправь текстовое сообщение с заданием.")
        return

    if len(text) < 5:
        await message.answer("❌ Слишком коротко. Напиши нормальное задание.")
        return

    if len(text) > 300:
        await message.answer("❌ Слишком длинно (макс. 300 символов). Сократи.")
        return

    fsm = await state.get_data()
    lobby_id = fsm.get("lobby_id")
    task_type_str = fsm.get("custom_type", "dare")

    # Удаляем сообщение пользователя с заданием (анонимность)
    try:
        await message.delete()
    except Exception:
        pass

    lobby = await get_lobby_by_id(db, lobby_id)
    if not lobby:
        await state.clear()
        return

    members = await get_lobby_members(db, lobby.id)
    task_type = TaskType.TRUTH if task_type_str == "truth" else TaskType.DARE

    # Записываем в лобби как текущую задачу (без ID из БД — кастомная)
    lobby.current_task_id = None
    from datetime import datetime, timedelta
    lobby.task_expires_at = datetime.utcnow() + timedelta(seconds=settings.task_timer_seconds)
    await db.flush()

    type_label = "🗣 Правда" if task_type == TaskType.TRUTH else "⚡ Действие"
    media_hint = ""

    # Оповещаем всех игроков
    from app.utils.broadcast import send_safe
    import asyncio as _asyncio

    spectator_text = (
        f"✏️ Игрок придумал <b>своё задание</b>\n\n"
        f"{type_label}\n\n"
        f"<b>{text}</b>"
    )

    # Активному игроку — с кнопками выполнения
    if task_type == TaskType.TRUTH:
        from app.bot.keyboards.inline import truth_answer_kb
        await send_safe(
            message.bot,
            chat_id=user.tg_id,
            text=(
                f"{type_label} — <b>Ваше задание</b>\n\n"
                f"<b>{text}</b>\n\n"
                f"Ответь и нажми кнопку."
            ),
            reply_markup=truth_answer_kb(lobby_id),
            parse_mode="HTML",
        )
        await state.set_state(GamePlay.answering_truth)
        await state.update_data(lobby_id=lobby_id, task_id=None)
    else:
        await send_safe(
            message.bot,
            chat_id=user.tg_id,
            text=(
                f"{type_label} — <b>Ваше задание</b>\n\n"
                f"<b>{text}</b>\n\n"
                f"⏱ Времени: {settings.task_timer_seconds} сек."
            ),
            reply_markup=task_active_kb(lobby_id, "none"),
            parse_mode="HTML",
        )
        await state.set_state(GamePlay.uploading_dare)
        await state.update_data(lobby_id=lobby_id, task_id=None, media_required="none")

    # Наблюдателям
    for i, m in enumerate([mb for mb in members if str(mb.user_id) != str(user.id)]):
        if i > 0:
            await _asyncio.sleep(0.035)
        await send_safe(message.bot, chat_id=m.user.tg_id,
                        text=spectator_text, parse_mode="HTML")


# ─── Назад к выбору (из экрана своего задания) ────────────────────────────────

@router.callback_query(F.data.startswith("game:pick:back:"))
async def cb_pick_back(call: CallbackQuery, user: User, db: AsyncSession) -> None:
    lobby_id = call.data.split(":")[3]
    await call.message.edit_text(
        f"🎯 <b>Ваш ход!</b>\n\nВыберите тип задания:",
        reply_markup=task_choice_kb(lobby_id),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Выбор типа задания ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:pick:"))
async def cb_pick_task_type(
    call: CallbackQuery, user: User, db: AsyncSession, state: FSMContext,
) -> None:
    parts = call.data.split(":")
    task_type_str = parts[2]
    lobby_id = parts[3]

    lobby = await get_lobby_by_id(db, lobby_id)
    if not lobby:
        await call.answer("❌ Комната не найдена.", show_alert=True)
        return

    members = await get_lobby_members(db, lobby.id)
    if not members:
        await call.answer("❌ Нет игроков.", show_alert=True)
        return

    # Проверяем по двум критериям: Redis tg_id (быстро) + БД индекс (надёжно)
    current_idx = lobby.current_player_index % len(members)
    current_member = members[current_idx]
    is_current_by_db = str(current_member.user_id) == str(user.id)

    # Дополнительная проверка через Redis (на случай расхождения)
    cached_tg = await redis_client.get(f"current_player:{lobby_id}")
    is_current_by_redis = cached_tg and int(cached_tg) == user.tg_id

    if not is_current_by_db and not is_current_by_redis:
        await call.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return

    task_type = TaskType.TRUTH if task_type_str == "truth" else TaskType.DARE
    task = await get_next_task(db, lobby.is_18_plus, task_type)
    if not task:
        await call.answer("😅 В пуле нет заданий. Попробуйте другой тип.", show_alert=True)
        return

    lobby.current_task_id = task.id
    from datetime import datetime, timedelta
    lobby.task_expires_at = datetime.utcnow() + timedelta(seconds=settings.task_timer_seconds)
    await db.flush()

    type_label = "🗣 Правда" if task_type == TaskType.TRUTH else "⚡ Действие"
    media_hint = ""
    if task.media_required.value != "none":
        media_hint = "\n\n📎 <b>Требуется фото или видео!</b>"

    # Уведомляем всех — что выбрал и какое задание
    spectator_text = (
        f"👁 Игрок выбрал {type_label}\n\n"
        f"<b>{task.text}</b>"
        f"{media_hint}"
    )
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        try:
            await call.bot.send_message(
                chat_id=m.user.tg_id,
                text=spectator_text,
                parse_mode="HTML",
            )
        except Exception:
            pass

    if task_type == TaskType.TRUTH:
        # Правда — ждём текстовый/голосовой ответ
        await call.message.edit_text(
            f"🗣 <b>Правда</b>\n\n"
            f"<b>{task.text}</b>\n\n"
            f"Ответь текстом, голосовым сообщением или кружком.\n"
            f"Все участники увидят твой ответ.",
            reply_markup=truth_answer_kb(lobby_id),
            parse_mode="HTML",
        )
        await state.set_state(GamePlay.answering_truth)
        await state.update_data(lobby_id=lobby_id, task_id=str(task.id))
    else:
        # Действие — выполняй
        await call.message.edit_text(
            f"⚡ <b>Действие</b>\n\n"
            f"<b>{task.text}</b>"
            f"{media_hint}\n\n"
            f"⏱ Времени: {settings.task_timer_seconds} сек.\n\n"
            f"После выполнения участники проголосуют — засчитать или нет.",
            reply_markup=task_active_kb(lobby_id, task.media_required.value),
            parse_mode="HTML",
        )
        await state.set_state(GamePlay.uploading_dare)
        await state.update_data(
            lobby_id=lobby_id,
            task_id=str(task.id),
            media_required=task.media_required.value,
        )
        await start_task_timer(lobby_id=lobby_id, message_id=call.message.message_id,
                               chat_id=call.from_user.id, bot=call.bot)

    await call.answer()


# ─── ПРАВДА: получаем ответ ───────────────────────────────────────────────────

@router.message(GamePlay.answering_truth)
async def msg_truth_answer(message: Message, user: User, db: AsyncSession, state: FSMContext) -> None:
    """Игрок отправил ответ на правду (текст/голос/кружок/фото)."""
    fsm = await state.get_data()
    lobby_id = fsm.get("lobby_id")
    if not lobby_id:
        await message.answer("❌ Контекст утерян.")
        await state.clear()
        return

    lobby = await get_lobby_by_id(db, lobby_id)
    members = await get_lobby_members(db, lobby.id)

    # Пересылаем ответ всем остальным
    player_name = user.first_name
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        try:
            await message.bot.send_message(
                chat_id=m.user.tg_id,
                text="💬 <b>Игрок</b> отвечает на вопрос:",
                parse_mode="HTML",
            )
            # copy_to копирует без "Переслано от" — анонимно!
            await message.copy_to(chat_id=m.user.tg_id)
        except Exception:
            pass

    # Показываем кнопку "Я ответил"
    await message.answer(
        "✅ Ответ отправлен всем игрокам!\n\nНажми кнопку когда закончишь.",
        reply_markup=truth_answer_kb(lobby_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("game:truth_done:"))
async def cb_truth_done(call: CallbackQuery, user: User, db: AsyncSession, state: FSMContext) -> None:
    """Игрок завершил ответ — запускаем голосование."""
    lobby_id = call.data.split(":")[2]
    # Защита от двойного нажатия
    if not await redis_client.acquire_action(user.tg_id, "truth_done", ttl=3):
        await call.answer("⏳ Обрабатывается...", show_alert=False)
        return
    lobby, member = await _get_lobby_and_member(db, lobby_id, user)
    if not lobby or not member:
        await call.answer("❌ Ошибка.", show_alert=True)
        return

    members = await get_lobby_members(db, lobby.id)
    voters = [m for m in members if str(m.user_id) != str(user.id)]

    if not voters:
        # Один игрок — засчитываем сразу
        result = await complete_task(db, lobby, member, with_media=False)
        await state.clear()
        await call.message.edit_text(f"✅ Правда засчитана! +{result['points_earned']} очков.")
        await call.answer()
        if result.get("game_over"):
            await _announce_game_over(call.bot, lobby, members)
        else:
            await send_turn_notification(call.bot, lobby, members)
        return

    # Запускаем голосование
    await redis_client.vote_clear(lobby_id)
    await state.set_state(GamePlay.waiting_vote)

    await call.message.edit_text(
        "⏳ <b>Ждём голосов...</b>\n\nУчастники решают — засчитать ответ или нет.",
        parse_mode="HTML",
    )
    await call.answer()

    for m in voters:
        try:
            await call.bot.send_message(
                chat_id=m.user.tg_id,
                text=(
                    "🗳 <b>Голосование!</b>\n\n"
                    "Игрок ответил(а) на вопрос.\n"
                    "Засчитать ответ?"
                ),
                reply_markup=vote_kb(lobby_id),
                parse_mode="HTML",
            )
        except Exception:
            pass


# ─── ДЕЙСТВИЕ: подтверждение выполнения ──────────────────────────────────────

@router.callback_query(F.data.startswith("game:done:"))
async def cb_task_done(call: CallbackQuery, user: User, db: AsyncSession, state: FSMContext) -> None:
    """Игрок нажал 'Выполнил' (без медиа) — запускаем голосование."""
    lobby_id = call.data.split(":")[2]
    # Защита от двойного нажатия
    if not await redis_client.acquire_action(user.tg_id, "task_done", ttl=3):
        await call.answer("⏳ Обрабатывается...", show_alert=False)
        return
    lobby, member = await _get_lobby_and_member(db, lobby_id, user)
    if not lobby or not member:
        await call.answer("❌ Ошибка.", show_alert=True)
        return

    members = await get_lobby_members(db, lobby.id)
    voters = [m for m in members if str(m.user_id) != str(user.id)]

    if not voters:
        # Одиночная игра — засчитываем сразу
        result = await complete_task(db, lobby, member, with_media=False)
        await state.clear()
        await call.message.edit_text(f"✅ Выполнено! +{result['points_earned']} очков.")
        await call.answer()
        if result.get("game_over"):
            await _announce_game_over(call.bot, lobby, members)
        else:
            await send_turn_notification(call.bot, lobby, members)
        return

    await redis_client.vote_clear(lobby_id)
    await state.set_state(GamePlay.waiting_vote)

    await call.message.edit_text(
        f"⏳ <b>Ждём голосов игроков...</b>\n\n"
        f"Участники решают — засчитать выполнение или нет.",
        parse_mode="HTML",
    )
    await call.answer()

    for m in voters:
        try:
            await call.bot.send_message(
                chat_id=m.user.tg_id,
                text=(
                    f"🗳 <b>Голосование!</b>\n\n"
                    "Игрок говорит что выполнил(а) задание.\n\n"
                    f"Вы согласны?"
                ),
                reply_markup=vote_kb(lobby_id),
                parse_mode="HTML",
            )
        except Exception:
            pass


# ─── Загрузка медиа для действия ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:upload_media:"))
async def cb_upload_media_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GamePlay.uploading_dare)
    await call.message.answer(
        "📸 Отправьте фото или 🎥 кружочек как подтверждение.\n\n"
        "⚠️ Пересланные не принимаются — только новая съёмка.",
    )
    await call.answer()


@router.message(GamePlay.uploading_dare, F.photo | F.video_note)
async def msg_dare_media_received(
    message: Message, user: User, db: AsyncSession, state: FSMContext,
) -> None:
    if message.forward_date or message.forward_from:
        await message.answer("❌ Пересланные сообщения не принимаются.")
        return

    fsm = await state.get_data()
    lobby_id = fsm.get("lobby_id")
    task_id  = fsm.get("task_id")
    if not lobby_id:
        await message.answer("❌ Контекст утерян.")
        await state.clear()
        return

    processing = await message.answer("⏳ Обрабатываем...")

    # Скачиваем и сохраняем файл
    if message.photo:
        file = message.photo[-1]
        file_type = "photo"
    else:
        file = message.video_note
        file_type = "video_note"

    bot_file = await message.bot.get_file(file.file_id)
    file_url = f"https://api.telegram.org/file/bot{settings.bot_token}/{bot_file.file_path}"

    from app.utils.media import save_and_compress_photo, save_and_compress_video
    from app.services.media_service import save_media_record
    import uuid

    if file_type == "photo":
        file_info = await save_and_compress_photo(file_url, str(user.id), file.file_id)
    else:
        file_info = await save_and_compress_video(file_url, str(user.id), file.file_id)

    if not file_info:
        await processing.edit_text("❌ Ошибка обработки. Попробуйте ещё раз.")
        return

    lobby = await get_lobby_by_id(db, lobby_id)
    media_record = await save_media_record(
        db, user, lobby,
        task_id=uuid.UUID(task_id) if task_id else None,
        file_info=file_info,
    )

    await processing.delete()
    members = await get_lobby_members(db, lobby.id)
    voters = [m for m in members if str(m.user_id) != str(user.id)]

    # Сохраняем media_id в Redis
    await redis_client.set_current_media(lobby_id, str(media_record.id))
    await redis_client.vote_clear(lobby_id)
    await state.set_state(GamePlay.waiting_vote)

    await message.answer(
        "📤 <b>Отправлено!</b>\n\nУчастники смотрят и голосуют...",
        parse_mode="HTML",
    )

    # Рассылаем медиа с кнопками голосования
    caption = "📤 <b>Игрок</b> выполнил(а) задание!"
    for m in voters:
        try:
            if file_type == "photo":
                await message.bot.send_photo(
                    chat_id=m.user.tg_id,
                    photo=message.photo[-1].file_id,
                    caption=caption,
                    reply_markup=vote_kb(lobby_id),
                    parse_mode="HTML",
                    protect_content=True,
                )
            else:
                await message.bot.send_message(
                    chat_id=m.user.tg_id,
                    text=caption,
                    parse_mode="HTML",
                )
                await message.bot.send_video_note(
                    chat_id=m.user.tg_id,
                    video_note=message.video_note.file_id,
                    reply_markup=vote_kb(lobby_id),
                    protect_content=True,
                )
        except Exception:
            pass


# ─── Голосование ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:vote:"))
async def cb_vote(call: CallbackQuery, user: User, db: AsyncSession) -> None:
    parts = call.data.split(":")
    vote_type = parts[2]   # yes / no
    lobby_id  = parts[3]

    # Уже голосовал?
    if await redis_client.vote_has_voted(lobby_id, user.tg_id):
        await call.answer("Вы уже проголосовали.", show_alert=True)
        return

    if vote_type == "yes":
        await redis_client.vote_yes(lobby_id, user.tg_id)
    else:
        await redis_client.vote_no(lobby_id, user.tg_id)

    yes, no = await redis_client.vote_counts(lobby_id)

    # Убираем кнопки у проголосовавшего
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer(f"Ваш голос: {'✅' if vote_type == 'yes' else '❌'}")

    # Проверяем — все ли проголосовали
    lobby = await get_lobby_by_id(db, lobby_id)
    if not lobby:
        return
    members = await get_lobby_members(db, lobby.id)
    voters_count = len(members) - 1  # без исполнителя
    total_votes = yes + no

    if total_votes < voters_count:
        # Ещё не все — сообщаем прогресс активному игроку
        current_idx = lobby.current_player_index % len(members)
        current_member = members[current_idx]
        try:
            await call.bot.send_message(
                chat_id=current_member.user.tg_id,
                text=f"🗳 Голосов: {total_votes}/{voters_count}  (✅ {yes}  ❌ {no})",
            )
        except Exception:
            pass
        return

    # Все проголосовали — подводим итог
    await redis_client.vote_clear(lobby_id)
    current_idx = lobby.current_player_index % len(members)
    performer = members[current_idx]

    if yes > no:
        # Большинство ЗА — засчитываем
        result = await complete_task(db, lobby, performer, with_media=True)
        verdict = f"🎉 <b>Задание выполнено!</b>  +{result['points_earned']} очков"
        performer_msg = f"🎉 Участники засчитали выполнение! +{result['points_earned']} очков"

        for m in members:
            try:
                if str(m.user_id) == str(performer.user_id):
                    await call.bot.send_message(chat_id=m.user.tg_id, text=performer_msg, parse_mode="HTML")
                else:
                    await call.bot.send_message(
                        chat_id=m.user.tg_id,
                        text=f"{verdict}\n(За: {yes}  Против: {no})",
                        parse_mode="HTML",
                    )
            except Exception:
                pass

        if result.get("game_over"):
            await _announce_game_over(call.bot, lobby, members)
        else:
            await send_turn_notification(call.bot, lobby, members)
    else:
        # Большинство ПРОТИВ — переделать или откупиться
        verdict = f"❌ <b>Не засчитано!</b>  (За: {yes}  Против: {no})"
        for m in members:
            try:
                if str(m.user_id) == str(performer.user_id):
                    await call.bot.send_message(
                        chat_id=m.user.tg_id,
                        text=f"❌ <b>Участники не засчитали!</b>\n\nПеределай или откупись.",
                        reply_markup=redo_or_buyout_kb(lobby_id),
                        parse_mode="HTML",
                    )
                else:
                    await call.bot.send_message(
                        chat_id=m.user.tg_id,
                        text=f"{verdict}\n\nИгрок должен переделать или откупиться.",
                        parse_mode="HTML",
                    )
            except Exception:
                pass


@router.callback_query(F.data.startswith("game:redo:"))
async def cb_redo(call: CallbackQuery, user: User, db: AsyncSession, state: FSMContext) -> None:
    """Игрок решил переделать задание."""
    lobby_id = call.data.split(":")[2]
    # Защита от двойного нажатия
    if not await redis_client.acquire_action(user.tg_id, "redo", ttl=3):
        await call.answer("⏳ Обрабатывается...", show_alert=False)
        return
    lobby, member = await _get_lobby_and_member(db, lobby_id, user)
    if not lobby or not member:
        await call.answer("❌ Ошибка.", show_alert=True)
        return

    fsm_data = await state.get_data()
    media_req = fsm_data.get("media_required", "none")

    await state.set_state(GamePlay.uploading_dare)
    await call.message.edit_text(
        "🔄 <b>Переделываем!</b>\n\n"
        "Выполни задание снова и отправь подтверждение.",
        reply_markup=task_active_kb(lobby_id, media_req),
        parse_mode="HTML",
    )

    members = await get_lobby_members(db, lobby.id)
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        try:
            await call.bot.send_message(
                chat_id=m.user.tg_id,
                text="🔄 Игрок переделывает задание...",
                parse_mode="HTML",
            )
        except Exception:
            pass
    await call.answer()


# ─── Сдаться ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:surrender:"))
async def cb_surrender(call: CallbackQuery, user: User, db: AsyncSession, state: FSMContext) -> None:
    lobby_id = call.data.split(":")[2]
    # Защита от двойного нажатия
    if not await redis_client.acquire_action(user.tg_id, "surrender", ttl=3):
        await call.answer("⏳ Обрабатывается...", show_alert=False)
        return
    lobby, member = await _get_lobby_and_member(db, lobby_id, user)
    if not lobby or not member:
        await call.answer("❌ Ошибка.", show_alert=True)
        return

    result = await surrender_task(db, lobby, member)
    await state.clear()

    if result["eliminated"]:
        await call.message.edit_text("💀 Вы выбыли! Жизни закончились.")
    else:
        await call.message.edit_text(f"❌ Сдались. -1 жизнь. Осталось: {member.lives} ❤️")
    await call.answer()

    members = await get_lobby_members(db, lobby.id)
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        try:
            msg = (f"💀 Один из игроков выбыл!" if result["eliminated"]
                   else f"❌ Один из игроков сдался(ась). Осталось {member.lives} ❤️")
            await call.bot.send_message(chat_id=m.user.tg_id, text=msg, parse_mode="HTML")
        except Exception:
            pass

    if result.get("game_over"):
        await _announce_game_over(call.bot, lobby, members, result.get("winner"))
    else:
        await send_turn_notification(call.bot, lobby, members)


# ─── Откупиться ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:buyout:"))
async def cb_buyout_prompt(call: CallbackQuery, user: User) -> None:
    if "confirm" in call.data or "cancel" in call.data:
        return
    lobby_id = call.data.split(":")[2]
    await call.message.edit_text(
        f"💰 <b>Откупиться</b>\n\n"
        f"Стоимость: {settings.buyout_cost_stars} ⭐\n"
        f"Ваш баланс: {user.stars_balance} ⭐\n\n"
        f"Задание будет пропущено без потери жизни.",
        reply_markup=buyout_confirm_kb(lobby_id, settings.buyout_cost_stars),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("game:buyout_confirm:"))
async def cb_buyout_confirm(call: CallbackQuery, user: User, db: AsyncSession, state: FSMContext) -> None:
    lobby_id = call.data.split(":")[2]
    # Защита от двойного нажатия
    if not await redis_client.acquire_action(user.tg_id, "buyout", ttl=3):
        await call.answer("⏳ Обрабатывается...", show_alert=False)
        return
    lobby, member = await _get_lobby_and_member(db, lobby_id, user)
    if not lobby or not member:
        await call.answer("❌ Ошибка.", show_alert=True)
        return

    success, error, turn = await buyout_task(db, lobby, member)
    if not success:
        await call.answer(error, show_alert=True)
        return

    await state.clear()
    new_balance = member.user.stars_balance
    await call.message.edit_text(f"💸 Откупились за {settings.buyout_cost_stars} ⭐\nОстаток: {new_balance} ⭐")
    await call.answer("💸 Откуп засчитан!")

    members = await get_lobby_members(db, lobby.id)
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        try:
            await call.bot.send_message(
                chat_id=m.user.tg_id,
                text=f"💸 Игрок откупился(ась) от задания ({settings.buyout_cost_stars} ⭐)",
                parse_mode="HTML",
            )
        except Exception:
            pass

    if turn.get("game_over"):
        await _announce_game_over(call.bot, lobby, members, turn.get("winner"))
    else:
        await send_turn_notification(call.bot, lobby, members)


@router.callback_query(F.data.startswith("game:buyout_cancel:"))
async def cb_buyout_cancel(call: CallbackQuery, state: FSMContext) -> None:
    fsm = await state.get_data()
    lobby_id = call.data.split(":")[2]
    media_req = fsm.get("media_required", "none")
    await call.message.edit_text(
        "Откуп отменён. Выполняй задание!",
        reply_markup=task_active_kb(lobby_id, media_req),
    )
    await call.answer()


# ─── Жалоба на медиа ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:report:"))
async def cb_report_media(call: CallbackQuery, db: AsyncSession) -> None:
    parts = call.data.split(":")
    lobby_id = parts[2]
    media_id = await redis_client.get_current_media(lobby_id)
    if not media_id:
        await call.answer("❌ Медиа уже удалено.", show_alert=True)
        return
    from app.services.media_service import report_media
    await report_media(db, media_id)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer("🚩 Жалоба отправлена.", show_alert=True)


# ─── Конец игры ───────────────────────────────────────────────────────────────

async def _announce_game_over(
    bot: Bot, lobby: Lobby, members: list[LobbyMember], winner: LobbyMember = None,
) -> None:
    winner_name = winner.user.first_name if winner and winner.user else None
    sorted_members = sorted(members, key=lambda m: m.score, reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    score_lines = [
        f"{medals[i] if i < 3 else f'{i+1}.'} {m.user.first_name} — {m.score} очков"
        for i, m in enumerate(sorted_members)
    ]
    winner_text = f"🏆 Победитель: <b>{winner_name}</b>!\n\n" if winner_name else ""
    text = (
        f"🎮 <b>Игра завершена!</b>\n\n"
        f"{winner_text}"
        f"📊 <b>Итоговый счёт:</b>\n" + "\n".join(score_lines)
    )

    from app.services.lobby_service import close_lobby
    from app.database.session import get_db_context
    from app.database.models import User as UserModel
    from sqlalchemy import select

    async with get_db_context() as db:
        lobby_obj = await get_lobby_by_id(db, str(lobby.id))
        if lobby_obj:
            # Один запрос на всех участников (избегаем N+1)
            member_ids = [m.user_id for m in members]
            res_users = await db.execute(
                select(UserModel).where(UserModel.id.in_(member_ids))
            )
            users_map = {u.id: u for u in res_users.scalars().all()}
            for u in users_map.values():
                u.games_played += 1
            if winner and winner.user_id in users_map:
                users_map[winner.user_id].games_won += 1
            await close_lobby(db, lobby_obj)

    for member in members:
        try:
            await bot.send_message(
                chat_id=member.user.tg_id,
                text=text,
                reply_markup=game_over_kb(),
                parse_mode="HTML",
            )
        except Exception:
            pass


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _get_lobby_and_member(db: AsyncSession, lobby_id: str, user: User):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    lobby = await get_lobby_by_id(db, lobby_id)
    if not lobby:
        return None, None
    result = await db.execute(
        select(LobbyMember)
        .where(LobbyMember.lobby_id == lobby.id, LobbyMember.user_id == user.id)
        .options(selectinload(LobbyMember.user))
    )
    return lobby, result.scalar_one_or_none()


# ─── Выход из активной игры ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("game:leave:"))
async def cb_leave_game(
    call: CallbackQuery,
    user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Игрок выходит из активной игры. Если осталось >= 2 — игра продолжается."""
    lobby_id = call.data.split(":")[2]
    # Защита от двойного нажатия
    if not await redis_client.acquire_action(user.tg_id, "leave_game", ttl=3):
        await call.answer("⏳ Обрабатывается...", show_alert=False)
        return
    lobby = await get_lobby_by_id(db, lobby_id)
    if not lobby:
        await call.answer("❌ Комната не найдена.", show_alert=True)
        return

    # Помечаем игрока как неактивного
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(LobbyMember)
        .where(LobbyMember.lobby_id == lobby.id, LobbyMember.user_id == user.id)
        .options(selectinload(LobbyMember.user))
    )
    member = result.scalar_one_or_none()
    if member:
        member.is_active = False
        await db.flush()

    await state.clear()

    # Сообщаем покинувшему
    from app.bot.keyboards.inline import main_menu_kb
    await call.message.edit_text(
        "🚪 Вы покинули игру.\n\nВаши очки сохранены, но из результатов вы исключены.",
        reply_markup=main_menu_kb(),
    )
    await call.answer("Вы покинули игру")

    # Проверяем сколько активных осталось
    members = await get_lobby_members(db, lobby.id)
    active = [m for m in members if m.is_active]

    if len(active) < 2:
        # Игра заканчивается
        winner = active[0] if active else None
        await _announce_game_over(call.bot, lobby, members, winner)
        return

    # Уведомляем оставшихся
    for m in active:
        try:
            await call.bot.send_message(
                chat_id=m.user.tg_id,
                text=(
                    f"🚪 Один из игроков покинул игру.\n\n"
                    f"Осталось: <b>{len(active)}</b> игроков.\n"
                    f"Игра продолжается!"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Если это был ход вышедшего — переходим к следующему
    current_idx = lobby.current_player_index % max(len(members), 1)
    if str(members[current_idx].user_id) == str(user.id):
        from app.services.game_service import _advance_turn
        turn = await _advance_turn(db, lobby)
        if turn["game_over"]:
            await _announce_game_over(call.bot, lobby, active, turn.get("winner"))
        else:
            await send_turn_notification(call.bot, lobby, active)
