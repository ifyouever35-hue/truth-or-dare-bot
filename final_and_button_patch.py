"""
final_and_button_patch.py — Финальная карточка + фикс кнопки «Выполнил»

Что делает:
  1. Финальная карточка — персональная:
     - Победитель: 🏆 ПОБЕДА! + счёт + «В главное меню»
     - Проигравший: 💔 Игра окончена + кто победил + счёт + «В главное меню»
     - Убирает кнопку «Играть снова»

  2. Фикс кнопки «✅ Выполнил» для Действия без медиа:
     - Сначала показывается задание БЕЗ кнопки (только Сдаться / Откупиться)
     - Отдельным сообщением снизу: «Когда выполнишь — нажми кнопку ниже» + кнопка «✅ Я выполнил»
     - Это разделяет «вижу задание» и «подтверждаю выполнение»

  3. Для Действия с фото/видео:
     - Кнопки остаются как есть (там «📎 Отправить подтверждение»)
     - Без изменений

Применяется ПОСЛЕ delete_card_patch.py.
Создаёт бэкапы *.bak5

Запуск:
    python3 final_and_button_patch.py
"""
import os
import sys
import shutil

ROOT = os.path.abspath(os.path.dirname(__file__))


def patch_file(path, replacements, label):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        print(f"  [ERROR] {path}: не найден")
        return False
    bak = full + ".bak5"
    if not os.path.exists(bak):
        shutil.copy(full, bak)
    src = open(full).read()
    ok = 0
    for i, (old, new) in enumerate(replacements, 1):
        if old not in src:
            print(f"  [SKIP] {label} #{i}: блок не найден")
            continue
        src = src.replace(old, new, 1)
        ok += 1
    open(full, 'w').write(src)
    print(f"  [OK]   {label}: применено {ok}/{len(replacements)}")
    return True


print("=" * 60)
print("FINAL + BUTTON PATCH")
print("=" * 60)
print()


# ──────────────────────────────────────────────────────────────────
# 1. inline.py — убрать «Играть снова», добавить новую клавиатуру
# ──────────────────────────────────────────────────────────────────
print("[1] inline.py — game_over_kb + task_show_kb")

patch_file("app/bot/keyboards/inline.py", [
    # 1a. game_over_kb — только «В главное меню»
    (
        '''def game_over_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Играть снова", callback_data="menu:create_lobby"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"),
    )
    return builder.as_markup()''',

        '''def game_over_kb() -> InlineKeyboardMarkup:
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
    return builder.as_markup()''',
    ),
], "inline.py — game_over_kb + task_show_kb + task_confirm_kb")


# ──────────────────────────────────────────────────────────────────
# 2. game.py — импорт новых клавиатур
# ──────────────────────────────────────────────────────────────────
print()
print("[2] game.py — импорт новых клавиатур")

patch_file("app/bot/handlers/game.py", [
    (
        '''    task_active_kb,''',
        '''    task_active_kb,
    task_show_kb,
    task_confirm_kb,''',
    ),
], "game.py — импорт task_show_kb, task_confirm_kb")


# ──────────────────────────────────────────────────────────────────
# 3. game.py — cb_pick_task_type: разделить показ задания и кнопки
# ──────────────────────────────────────────────────────────────────
print()
print("[3] game.py — cb_pick_task_type: задание без кнопки Выполнил")

patch_file("app/bot/handlers/game.py", [
    (
        '''    else:
        # Действие — выполняй
        await call.message.edit_text(
            f"⚡ <b>Действие</b>\\n\\n"
            f"<b>{task.text}</b>"
            f"{media_hint}\\n\\n"
            f"⏱ Времени: {settings.task_timer_seconds} сек.\\n\\n"
            f"После выполнения участники проголосуют — засчитать или нет.",
            reply_markup=task_active_kb(lobby_id, task.media_required.value),
            parse_mode="HTML",
        )
        await state.set_state(GamePlay.uploading_dare)
        await state.update_data(
            lobby_id=lobby_id,
            task_id=str(task.id),
            media_required=task.media_required.value,
        )''',

        '''    else:
        # Действие — выполняй
        if task.media_required.value != "none":
            # Требуется фото/видео — показываем с кнопкой «Отправить подтверждение»
            await call.message.edit_text(
                f"⚡ <b>Действие</b>\\n\\n"
                f"<b>{task.text}</b>"
                f"{media_hint}\\n\\n"
                f"⏱ Времени: {settings.task_timer_seconds} сек.\\n\\n"
                f"После выполнения участники проголосуют — засчитать или нет.",
                reply_markup=task_active_kb(lobby_id, task.media_required.value),
                parse_mode="HTML",
            )
        else:
            # Действие без медиа — сначала задание БЕЗ кнопки Выполнил
            await call.message.edit_text(
                f"⚡ <b>Действие</b>\\n\\n"
                f"<b>{task.text}</b>\\n\\n"
                f"⏱ Времени: {settings.task_timer_seconds} сек.\\n\\n"
                f"Выполни задание, затем нажми кнопку ниже.",
                reply_markup=task_show_kb(lobby_id),
                parse_mode="HTML",
            )
            # Отдельное сообщение снизу с кнопкой подтверждения
            try:
                await call.bot.send_message(
                    chat_id=call.from_user.id,
                    text="👇 Когда выполнишь — нажми:",
                    reply_markup=task_confirm_kb(lobby_id),
                )
            except Exception:
                pass
        await state.set_state(GamePlay.uploading_dare)
        await state.update_data(
            lobby_id=lobby_id,
            task_id=str(task.id),
            media_required=task.media_required.value,
        )''',
    ),
], "game.py — cb_pick_task_type: задание без кнопки Выполнил")


# ──────────────────────────────────────────────────────────────────
# 4. game.py — _announce_game_over: персональная карточка
# ──────────────────────────────────────────────────────────────────
print()
print("[4] game.py — _announce_game_over персональная")

patch_file("app/bot/handlers/game.py", [
    (
        '''async def _announce_game_over(
    bot: Bot, lobby: Lobby, members: list[LobbyMember], winner: LobbyMember = None,
) -> None:
    winner_name = winner.user.first_name if winner and winner.user else None
    sorted_members = sorted(members, key=lambda m: m.score, reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    score_lines = [
        f"{medals[i] if i < 3 else f'{i+1}.'} {m.user.first_name} — {m.score} очков"
        for i, m in enumerate(sorted_members)
    ]
    winner_text = f"🏆 Победитель: <b>{winner_name}</b>!\\n\\n" if winner_name else ""
    text = (
        f"🎮 <b>Игра завершена!</b>\\n\\n"
        f"{winner_text}"
        f"📊 <b>Итоговый счёт:</b>\\n" + "\\n".join(score_lines)
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
            pass''',

        '''async def _announce_game_over(
    bot: Bot, lobby: Lobby, members: list[LobbyMember], winner: LobbyMember = None,
) -> None:
    winner_id = winner.user_id if winner else None
    winner_name = winner.user.first_name if winner and winner.user else None

    sorted_members = sorted(members, key=lambda m: m.score, reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    score_lines = [
        f"{medals[i] if i < 3 else f'{i+1}.'} {m.user.first_name} — {m.score} очков"
        for i, m in enumerate(sorted_members)
    ]
    score_block = "📊 <b>Итоговый счёт:</b>\\n" + "\\n".join(score_lines)

    from app.services.lobby_service import close_lobby
    from app.database.session import get_db_context
    from app.database.models import User as UserModel
    from sqlalchemy import select

    async with get_db_context() as db:
        lobby_obj = await get_lobby_by_id(db, str(lobby.id))
        if lobby_obj:
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

    # Персональная карточка для каждого участника
    for member in members:
        is_winner = (winner_id is not None and member.user_id == winner_id)

        if is_winner:
            header = "🏆 <b>ПОБЕДА!</b>"
            sub = "Поздравляем — ты выиграл эту игру!"
        elif winner_name:
            header = "💔 <b>Игра окончена</b>"
            sub = f"Победил <b>{winner_name}</b>. В следующий раз повезёт!"
        else:
            header = "🎮 <b>Игра завершена</b>"
            sub = "Ничья — сыграли одинаково хорошо."

        text = (
            f"{header}\\n\\n"
            f"{sub}\\n\\n"
            f"━━━━━━━━━━\\n"
            f"{score_block}"
        )

        # Удаляем последнюю игровую карточку и отправляем финальную
        try:
            await delete_and_send(
                bot,
                member.user.tg_id,
                str(lobby.id),
                text=text,
                reply_markup=game_over_kb(),
            )
        except Exception:
            # delete_and_send может не быть (если патч не применялся)
            try:
                await bot.send_message(
                    chat_id=member.user.tg_id,
                    text=text,
                    reply_markup=game_over_kb(),
                    parse_mode="HTML",
                )
            except Exception:
                pass''',
    ),
], "game.py — _announce_game_over персональная")


# ──────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("ПАТЧ ПРИМЕНЁН")
print("=" * 60)
print()
print("Синтаксис:")
print("  python3 -c \"import ast; ast.parse(open('app/bot/handlers/game.py').read()); print('game.py OK')\"")
print("  python3 -c \"import ast; ast.parse(open('app/bot/keyboards/inline.py').read()); print('inline.py OK')\"")
print()
print("Пересборка:")
print("  docker compose up -d --build app")
print()
print("Откат:")
print("  cp app/bot/handlers/game.py.bak5 app/bot/handlers/game.py")
print("  cp app/bot/keyboards/inline.py.bak5 app/bot/keyboards/inline.py")
print("  docker compose up -d --build app")
