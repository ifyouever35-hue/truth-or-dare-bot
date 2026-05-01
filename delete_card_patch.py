"""
delete_card_patch.py — «Удалять старую карточку при появлении новой»

Что делает:
  Добавляет хелпер delete_and_send() который:
    1. Берёт message_id из Redis по ключу card:{lobby_id}:{tg_id}
    2. Пытается удалить старое сообщение
    3. Отправляет новое
    4. Сохраняет новый message_id в Redis

  Заменяет send_safe() и bot.send_message() в 7 ключевых местах
  (start of turn, task shown, truth/dare answer, voting, result)
  на delete_and_send() — для обоих игроков (активный + наблюдатели).

Применяется на ЧИСТЫЙ (откатанный) game.py.
Создаёт бэкап game.py.bak

Запуск:
    python3 delete_card_patch.py

После:
    python3 -c "import ast; ast.parse(open('app/bot/handlers/game.py').read()); print('OK')"
    docker compose up -d --build app
"""
import os
import sys
import shutil

ROOT = os.path.abspath(os.path.dirname(__file__))
TARGET = os.path.join(ROOT, "app/bot/handlers/game.py")

if not os.path.exists(TARGET):
    print(f"ERROR: {TARGET} не найден")
    sys.exit(1)

shutil.copy(TARGET, TARGET + ".bak")
print(f"Бэкап: {TARGET}.bak")

src = open(TARGET).read()


def patch(old, new, label):
    global src
    if old not in src:
        print(f"  [SKIP] {label}: блок не найден")
        return
    if src.count(old) > 1:
        print(f"  [WARN] {label}: встречается {src.count(old)} раз, заменяю первый")
    src = src.replace(old, new, 1)
    print(f"  [OK]   {label}")


# ── 1. Добавить хелпер delete_and_send после redis_client импорта ──────────

patch(
    '''async def send_turn_notification(bot: Bot, lobby: Lobby, members: list[LobbyMember]) -> None:''',

    '''async def delete_and_send(
    bot,
    tg_id: int,
    lobby_id: str,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> None:
    """Удаляет старую карточку игрока и отправляет новую.

    Хранит message_id в Redis: card:{lobby_id}:{tg_id}.
    Если удалить не удалось (уже удалено, слишком старое) — просто отправляет.
    """
    key = f"card:{lobby_id}:{tg_id}"
    old_id_str = await redis_client.get(key)
    if old_id_str:
        try:
            await bot.delete_message(chat_id=tg_id, message_id=int(old_id_str))
        except Exception:
            pass  # не страшно — удалено раньше или слишком старое

    try:
        msg = await bot.send_message(
            chat_id=tg_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        await redis_client.set(key, str(msg.message_id), ttl=7200)
    except Exception:
        pass


async def send_turn_notification(bot: Bot, lobby: Lobby, members: list[LobbyMember]) -> None:''',
    "1. Хелпер delete_and_send",
)


# ── 2. send_turn_notification: активный + наблюдатели ─────────────────────

patch(
    '''    # Активному игроку
    await send_safe(bot,
        chat_id=current_member.user.tg_id,
        text=(
            f"🎯 <b>Ваш ход!</b>  (Раунд {lobby.current_round})\\n\\n"
            f"<b>Счёт:</b>\\n{scoreboard}\\n\\n"
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
                f"⏳ Ход следующего игрока  (Раунд {lobby.current_round})\\n\\n"
                f"<b>Счёт:</b>\\n{scoreboard}\\n\\n"
                f"Ожидайте выбора..."
            ),
            parse_mode="HTML",
        )''',

    '''    # Активному игроку — удаляем старую карточку, отправляем новую
    await delete_and_send(
        bot,
        current_member.user.tg_id,
        str(lobby.id),
        text=(
            f"🎯 <b>Ваш ход!</b>  (Раунд {lobby.current_round})\\n\\n"
            f"<b>Счёт:</b>\\n{scoreboard}\\n\\n"
            f"Выберите тип задания:"
        ),
        reply_markup=task_choice_kb(str(lobby.id)),
    )

    # Наблюдателям — с задержкой (Telegram flood limit: 30 msg/sec)
    spectators = [m for m in members if m.user_id != current_member.user_id]
    for i, member in enumerate(spectators):
        if i > 0:
            await asyncio.sleep(0.035)
        await delete_and_send(
            bot,
            member.user.tg_id,
            str(lobby.id),
            text=(
                f"⏳ Ход следующего игрока  (Раунд {lobby.current_round})\\n\\n"
                f"<b>Счёт:</b>\\n{scoreboard}\\n\\n"
                f"Ожидайте выбора задания..."
            ),
        )''',
    "2. send_turn_notification — delete_and_send для всех",
)


# ── 3. cb_pick_task_type: уведомление наблюдателей о выбранном задании ────

patch(
    '''    # Уведомляем всех — что выбрал и какое задание
    spectator_text = (
        f"👁 Игрок выбрал {type_label}\\n\\n"
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
            pass''',

    '''    # Уведомляем всех — что выбрал и какое задание
    spectator_text = (
        f"👁 Игрок выбрал {type_label}\\n\\n"
        f"<b>{task.text}</b>"
        f"{media_hint}"
    )
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        await delete_and_send(
            call.bot, m.user.tg_id, str(lobby.id),
            text=spectator_text,
        )''',
    "3. cb_pick_task_type — наблюдатели видят задание",
)


# ── 4. msg_truth_answer: пересылка ответа наблюдателям ────────────────────

patch(
    '''    # Пересылаем ответ всем остальным
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
        "✅ Ответ отправлен всем игрокам!\\n\\nНажми кнопку когда закончишь.",
        reply_markup=truth_answer_kb(lobby_id),
        parse_mode="HTML",
    )''',

    '''    # Пересылаем ответ всем остальным (анонимно, без "Переслано от")
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        try:
            # Сначала удаляем старую карточку и шлём заголовок
            await delete_and_send(
                message.bot, m.user.tg_id, str(lobby.id),
                text="💬 <b>Игрок ответил на вопрос</b>\\n\\nСмотри ниже 👇",
            )
            # Потом пересылаем сам ответ отдельным сообщением (медиа нельзя в edit)
            await message.copy_to(chat_id=m.user.tg_id)
        except Exception:
            pass

    # Показываем кнопку "Я ответил" активному игроку
    await message.answer(
        "✅ Ответ отправлен всем игрокам!\\n\\nНажми кнопку когда закончишь.",
        reply_markup=truth_answer_kb(lobby_id),
        parse_mode="HTML",
    )''',
    "4. msg_truth_answer — ответ пересылается наблюдателям",
)


# ── 5. cb_truth_done: голосование у наблюдателей ──────────────────────────

patch(
    '''    for m in voters:
        try:
            await call.bot.send_message(
                chat_id=m.user.tg_id,
                text=(
                    "🗳 <b>Голосование!</b>\\n\\n"
                    "Игрок ответил(а) на вопрос.\\n"
                    "Засчитать ответ?"
                ),
                reply_markup=vote_kb(lobby_id),
                parse_mode="HTML",
            )
        except Exception:
            pass


# ─── ДЕЙСТВИЕ: подтверждение выполнения ──────────────────────────────────────''',

    '''    for m in voters:
        await delete_and_send(
            call.bot, m.user.tg_id, lobby_id,
            text=(
                "🗳 <b>Голосование!</b>\\n\\n"
                "Игрок ответил(а) на вопрос.\\n"
                "Засчитать ответ?"
            ),
            reply_markup=vote_kb(lobby_id),
        )


# ─── ДЕЙСТВИЕ: подтверждение выполнения ──────────────────────────────────────''',
    "5. cb_truth_done — голосование наблюдателям",
)


# ── 6. cb_task_done: голосование за выполнение действия ───────────────────

patch(
    '''    for m in voters:
        try:
            await call.bot.send_message(
                chat_id=m.user.tg_id,
                text=(
                    f"🗳 <b>Голосование!</b>\\n\\n"
                    "Игрок говорит что выполнил(а) задание.\\n\\n"
                    f"Вы согласны?"
                ),
                reply_markup=vote_kb(lobby_id),
                parse_mode="HTML",
            )
        except Exception:
            pass


# ─── Загрузка медиа для действия ─────────────────────────────────────────────''',

    '''    for m in voters:
        await delete_and_send(
            call.bot, m.user.tg_id, lobby_id,
            text=(
                "🗳 <b>Голосование!</b>\\n\\n"
                "Игрок говорит что выполнил(а) задание.\\n"
                "Вы согласны?"
            ),
            reply_markup=vote_kb(lobby_id),
        )


# ─── Загрузка медиа для действия ─────────────────────────────────────────────''',
    "6. cb_task_done — голосование за действие",
)


# ── 7. msg_custom_task_entered: произвольное задание наблюдателям ──────────

patch(
    '''    # Наблюдателям
    for i, m in enumerate([mb for mb in members if str(mb.user_id) != str(user.id)]):
        if i > 0:
            await _asyncio.sleep(0.035)
        await send_safe(message.bot, chat_id=m.user.tg_id,
                        text=spectator_text, parse_mode="HTML")''',

    '''    # Наблюдателям — удаляем старую карточку, шлём новую
    for i, m in enumerate([mb for mb in members if str(mb.user_id) != str(user.id)]):
        if i > 0:
            await _asyncio.sleep(0.035)
        await delete_and_send(
            message.bot, m.user.tg_id, str(lobby.id),
            text=spectator_text,
        )''',
    "7. msg_custom_task_entered — произвольное задание",
)


# ── Запись файла и итог ────────────────────────────────────────────────────

open(TARGET, 'w').write(src)

print()
print("=" * 60)
print("ПАТЧ ПРИМЕНЁН")
print("=" * 60)
print()
print("Проверь синтаксис:")
print("  python3 -c \"import ast; ast.parse(open('app/bot/handlers/game.py').read()); print('OK')\"")
print()
print("Пересобери образ:")
print("  docker compose up -d --build app")
print()
print("Откат:")
print("  cp app/bot/handlers/game.py.bak app/bot/handlers/game.py")
print("  docker compose up -d --build app")
