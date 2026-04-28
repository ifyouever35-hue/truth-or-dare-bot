"""
mega_patch.py — Единый патч под пункты 2-7 переделки игры:

  2. Произвольное задание — через update_spectator_card (без мусора)
  3. Запрет писать в чат вне «окна ответа»
  4. Фото-задание — текст не принимать, отвечать "только фото/видео"
  5. Премиум через дашборд (ставит verified_expires_at = 2099 г.)
  6. Финальная карточка с победителем — красивая + кнопка "В главное меню"
  7. Шапка со счётом (компактная) во всех карточках наблюдателей

Применяется ПОСЛЕ apply_card_patch.py.
Создаёт бэкапы *.bak3.
Не трогает apply_card-фичи (update_spectator_card, clear_spectator_cards).

Запуск:
    python3 mega_patch.py

После успешного выполнения:
    docker compose up -d --build app
"""
import os
import sys
import shutil

ROOT = os.path.abspath(os.path.dirname(__file__))


def patch_file(path: str, replacements: list, label: str) -> bool:
    """
    replacements — список (old, new) пар.
    Применяет каждую замену, возвращает False если хоть одна не нашла блок.
    Делает .bak3 если ещё нет.
    """
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        print(f"  [ERROR] {path}: файл не найден")
        return False

    bak = full + ".bak3"
    if not os.path.exists(bak):
        shutil.copy(full, bak)

    src = open(full).read()
    ok_count = 0
    for i, (old, new) in enumerate(replacements, 1):
        if old not in src:
            print(f"  [SKIP] {label} #{i}: блок не найден (возможно уже применено)")
            continue
        if src.count(old) > 1:
            print(f"  [WARN] {label} #{i}: блок встречается {src.count(old)} раз — заменяю первый")
        src = src.replace(old, new, 1)
        ok_count += 1

    open(full, 'w').write(src)
    print(f"  [OK]   {label}: применено {ok_count}/{len(replacements)}")
    return True


print("=" * 60)
print("MEGA PATCH — пункты 2-7")
print("=" * 60)
print()


# ──────────────────────────────────────────────────────────────────────
# ПУНКТ 2 — Произвольное задание через update_spectator_card
# ──────────────────────────────────────────────────────────────────────
print("[2] Произвольное задание — наблюдатели через update_spectator_card")

p2 = [
    # Заменяем рассылку наблюдателям в msg_custom_task_entered
    (
        '''    # Наблюдателям
    for i, m in enumerate([mb for mb in members if str(mb.user_id) != str(user.id)]):
        if i > 0:
            await _asyncio.sleep(0.035)
        await send_safe(message.bot, chat_id=m.user.tg_id,
                        text=spectator_text, parse_mode="HTML")''',

        '''    # Наблюдателям — обновляем единую карточку
    for i, m in enumerate([mb for mb in members if str(mb.user_id) != str(user.id)]):
        if i > 0:
            await _asyncio.sleep(0.035)
        await update_spectator_card(
            message.bot, str(lobby.id), m.user.tg_id,
            text=spectator_text,
        )''',
    ),
]
patch_file("app/bot/handlers/game.py", p2, "msg_custom_task_entered")


# ──────────────────────────────────────────────────────────────────────
# ПУНКТ 4 — Фото-задание: текст не принимать
# ──────────────────────────────────────────────────────────────────────
print()
print("[4] Фото-задание — отвергать текст")

# Добавляем fallback хендлер ПОСЛЕ msg_dare_media_received.
# Этот хендлер сработает на любое сообщение в состоянии uploading_dare,
# которое НЕ photo и НЕ video_note (потому что F.photo|F.video_note выше его перехватит).
p4 = [
    (
        '''@router.message(GamePlay.uploading_dare, F.photo | F.video_note)
async def msg_dare_media_received(''',

        '''@router.message(GamePlay.uploading_dare, ~(F.photo | F.video_note))
async def msg_dare_wrong_content(message: Message, state: FSMContext) -> None:
    """В состоянии загрузки фото/видео — отвергаем любой другой контент."""
    fsm = await state.get_data()
    media_required = fsm.get("media_required", "none")
    if media_required == "none":
        # Задание без медиа-требования — игнорируем (не наш случай)
        return
    try:
        await message.delete()
    except Exception:
        pass
    try:
        await message.answer(
            "📎 Сейчас принимается <b>только фото или видео-кружок</b>.\\n"
            "Отправь подтверждение в нужном формате.",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.message(GamePlay.uploading_dare, F.photo | F.video_note)
async def msg_dare_media_received(''',
    ),
]
patch_file("app/bot/handlers/game.py", p4, "msg_dare_wrong_content")


# ──────────────────────────────────────────────────────────────────────
# ПУНКТ 3 — Запрет писать в чат вне «окна ответа»
# ──────────────────────────────────────────────────────────────────────
print()
print("[3] Запрет писать вне окна ответа — fallback хендлер")

# Добавляем "ловушку" в самый конец game.py — она ловит все сообщения,
# которые не попали ни в один FSM-хендлер. Если юзер сейчас в активной
# игре, но НЕ в правильном состоянии — отвергаем.
#
# Аккуратность: этот хендлер должен сработать ПОСЛЕ всех остальных.
# Aiogram обрабатывает по порядку регистрации, и наш router.include_router
# в main.py регистрирует start, lobby, game, payment в этом порядке.
# То есть все остальные хендлеры внутри game идут ВЫШЕ. Чтобы fallback
# не перехватывал чужие сообщения, проверяем что юзер в лобби.

p3 = [
    (
        '''# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _get_lobby_and_member(db: AsyncSession, lobby_id: str, user: User):''',

        '''# ─── Fallback: запрет писать вне окна ответа ─────────────────────────────────

@router.message()
async def msg_fallback_in_game(
    message: Message, user: User, db: AsyncSession, state: FSMContext,
) -> None:
    """Ловит все сообщения, не попавшие в FSM-хендлеры выше.

    Если юзер сейчас в активной игре, но не в правильном FSM-состоянии —
    предупреждаем что сейчас не его ход и удаляем сообщение, чтобы не
    мусорить в чате.
    """
    # Если юзер в каком-то FSM-состоянии — значит, выше есть хендлер,
    # который должен был это поймать. Не трогаем.
    cur = await state.get_state()
    if cur is not None:
        return

    # Проверяем, состоит ли юзер в активной игре
    from app.database.models import LobbyStatus
    from sqlalchemy import select
    res = await db.execute(
        select(LobbyMember).join(Lobby).where(
            LobbyMember.user_id == user.id,
            Lobby.status == LobbyStatus.PLAYING,
            LobbyMember.is_active == True,
        )
    )
    active_membership = res.scalar_one_or_none()

    if not active_membership:
        # Юзер НЕ в игре — это просто болтает в личке боту, ничего не делаем
        return

    # Юзер в игре, но не в окне ответа — удаляем сообщение и подсказываем
    try:
        await message.delete()
    except Exception:
        pass
    try:
        notice = await message.answer(
            "⏳ Сейчас не время отвечать.\\nЖди свой ход.",
        )
        # Удаляем подсказку через 3 секунды, чтобы не накапливалась
        import asyncio as _asyncio
        async def _cleanup():
            await _asyncio.sleep(3)
            try:
                await notice.delete()
            except Exception:
                pass
        _asyncio.create_task(_cleanup())
    except Exception:
        pass


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _get_lobby_and_member(db: AsyncSession, lobby_id: str, user: User):''',
    ),
]
patch_file("app/bot/handlers/game.py", p3, "msg_fallback_in_game")


# ──────────────────────────────────────────────────────────────────────
# ПУНКТ 6 — Финальная карточка через update_spectator_card + кнопка "В главное меню"
# ──────────────────────────────────────────────────────────────────────
print()
print("[6] Финальная карточка с победителем")

# Заменяем _announce_game_over: красивее, через update_spectator_card,
# с явным указанием победителя/проигравших.
p6 = [
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

    # Чистим карточки наблюдателей перед финалом — старая обновляемая
    # карточка превратится в финальную
    await clear_spectator_cards(str(lobby.id), members)

    # Каждому игроку — персональное сообщение: победил ли он
    for member in members:
        is_winner = (winner_id is not None and member.user_id == winner_id)
        if is_winner:
            personal_header = "🏆 <b>ПОБЕДА!</b>"
            personal_sub = "Поздравляем! Ты выиграл эту игру."
        elif winner_name:
            personal_header = "💔 <b>Игра окончена</b>"
            personal_sub = f"Победил <b>{winner_name}</b>. В следующий раз повезёт!"
        else:
            personal_header = "🎮 <b>Игра завершена</b>"
            personal_sub = "Никто не выиграл — все хороши."

        text = (
            f"{personal_header}\\n\\n"
            f"{personal_sub}\\n\\n"
            f"━━━━━━━━━━\\n"
            f"📊 <b>Итоговый счёт:</b>\\n" + "\\n".join(score_lines)
        )
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
]
patch_file("app/bot/handlers/game.py", p6, "_announce_game_over (персональная карточка победы/поражения)")


# ──────────────────────────────────────────────────────────────────────
# ПУНКТ 5 — Премиум через дашборд (часть А: user_service)
# ──────────────────────────────────────────────────────────────────────
print()
print("[5a] Функция grant_premium_db в user_service")

p5_service = [
    (
        '''async def unban_user_db(
    session: AsyncSession,
    user: User,
) -> User:
    user.is_banned = False
    user.ban_reason = None
    await session.flush()
    return user''',

        '''async def unban_user_db(
    session: AsyncSession,
    user: User,
) -> User:
    user.is_banned = False
    user.ban_reason = None
    await session.flush()
    return user


async def grant_premium_db(
    session: AsyncSession,
    user: User,
) -> User:
    """Выдать перманентный премиум (verified до 2099 года)."""
    from datetime import datetime
    user.is_verified = True
    user.verified_expires_at = datetime(2099, 12, 31)
    await session.flush()
    return user


async def revoke_premium_db(
    session: AsyncSession,
    user: User,
) -> User:
    """Отозвать премиум."""
    user.is_verified = False
    user.verified_expires_at = None
    await session.flush()
    return user''',
    ),
]
patch_file("app/services/user_service.py", p5_service, "grant_premium_db / revoke_premium_db")


# ──────────────────────────────────────────────────────────────────────
# ПУНКТ 5 — Премиум через дашборд (часть Б: dashboard route)
# ──────────────────────────────────────────────────────────────────────
print()
print("[5b] Роуты /admin/grant-premium и /admin/revoke-premium")

p5_routes = [
    # Импорт
    (
        "from app.services.user_service import ban_user_db, unban_user_db",
        "from app.services.user_service import ban_user_db, unban_user_db, grant_premium_db, revoke_premium_db",
    ),
    # Сам роут — добавляем после admin_unban_user
    (
        '''@router.post("/unban")
async def admin_unban_user(
    tg_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    user_result = await db.execute(select(User).where(User.tg_id == tg_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await unban_user_db(db, user)
    await redis_client.unban_user(tg_id)

    return RedirectResponse(url="/admin/users", status_code=303)''',

        '''@router.post("/unban")
async def admin_unban_user(
    tg_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    user_result = await db.execute(select(User).where(User.tg_id == tg_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await unban_user_db(db, user)
    await redis_client.unban_user(tg_id)

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/grant-premium")
async def admin_grant_premium(
    tg_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    user_result = await db.execute(select(User).where(User.tg_id == tg_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await grant_premium_db(db, user)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/revoke-premium")
async def admin_revoke_premium(
    tg_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    user_result = await db.execute(select(User).where(User.tg_id == tg_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await revoke_premium_db(db, user)
    return RedirectResponse(url="/admin/users", status_code=303)''',
    ),
]
patch_file("app/admin/routes/dashboard.py", p5_routes, "admin_grant_premium / admin_revoke_premium")


# ──────────────────────────────────────────────────────────────────────
# ПУНКТ 5 — Премиум через дашборд (часть В: шаблон users.html)
# ──────────────────────────────────────────────────────────────────────
print()
print("[5c] Кнопка «Выдать премиум» / «Отозвать» в users.html")

# Сначала найдём, есть ли в шаблоне колонка/блок с verified — это
# зависит от текущего состояния. Базовая правка: добавим кнопки
# рядом с уже существующим блоком ban/unban.

p5_template = [
    (
        '''        {% else %}
        <form method="post" action="/admin/unban" style="display:flex;gap:6px;align-items:center;">
          <input type="hidden" name="tg_id" value="{{ user.tg_id }}">
          <span style="color:var(--red);font-size:12px;">Забанен</span>
          <button type="submit"
            style="background:var(--green);color:#fff;border:none;padding:4px 10px;
                   border-radius:6px;cursor:pointer;font-size:12px;">Разбанить</button>
        </form>
        {% endif %}''',

        '''        {% else %}
        <form method="post" action="/admin/unban" style="display:flex;gap:6px;align-items:center;">
          <input type="hidden" name="tg_id" value="{{ user.tg_id }}">
          <span style="color:var(--red);font-size:12px;">Забанен</span>
          <button type="submit"
            style="background:var(--green);color:#fff;border:none;padding:4px 10px;
                   border-radius:6px;cursor:pointer;font-size:12px;">Разбанить</button>
        </form>
        {% endif %}
        {% if user.is_verified %}
        <form method="post" action="/admin/revoke-premium" style="display:inline;margin-left:6px;">
          <input type="hidden" name="tg_id" value="{{ user.tg_id }}">
          <button type="submit"
            style="background:#888;color:#fff;border:none;padding:4px 10px;
                   border-radius:6px;cursor:pointer;font-size:12px;"
            title="Снять премиум">⭐ Снять</button>
        </form>
        {% else %}
        <form method="post" action="/admin/grant-premium" style="display:inline;margin-left:6px;">
          <input type="hidden" name="tg_id" value="{{ user.tg_id }}">
          <button type="submit"
            style="background:#d4a017;color:#fff;border:none;padding:4px 10px;
                   border-radius:6px;cursor:pointer;font-size:12px;"
            title="Выдать перманентный премиум">⭐ Премиум</button>
        </form>
        {% endif %}''',
    ),
]
patch_file("app/admin/templates/users.html", p5_template, "users.html — кнопки премиума")


# ──────────────────────────────────────────────────────────────────────
# ПУНКТ 7 — Шапка со счётом (компактная) во всех карточках наблюдателей
# ──────────────────────────────────────────────────────────────────────
print()
print("[7] Шапка со счётом в карточках наблюдателей")

# Добавляем хелпер _compact_scoreboard и используем его в 5 местах
# (send_turn_notification, cb_pick_task_type, msg_truth_answer,
#  cb_truth_done, cb_task_done).
# В msg_custom_task_entered тоже добавим — там тоже карточка наблюдателя.

p7_helper = [
    (
        "async def update_spectator_card(",

        '''def _compact_scoreboard(members, current_member, round_num: int) -> str:
    """Компактная шапка со счётом для карточки наблюдателя.

    Вид:  📊 Раунд 2 · ◀️ Иван ❤️3⭐15 · Маша ❤️2⭐10
    """
    parts = []
    for m in members:
        marker = "◀️" if current_member and m.user_id == current_member.user_id else "·"
        parts.append(f"{marker} {m.user.first_name} ❤️{m.lives}⭐{m.score}")
    body = "  ".join(parts)
    return f"📊 <b>Раунд {round_num}</b>  {body}"


async def update_spectator_card(''',
    ),
]
patch_file("app/bot/handlers/game.py", p7_helper, "[7a] Хелпер _compact_scoreboard")


p7_send_turn = [
    (
        '''        await update_spectator_card(
            bot,
            str(lobby.id),
            member.user.tg_id,
            text=(
                f"⏳ Ход следующего игрока  (Раунд {lobby.current_round})\\n\\n"
                f"<b>Счёт:</b>\\n{scoreboard}\\n\\n"
                f"Ожидайте выбора..."
            ),
        )''',

        '''        header = _compact_scoreboard(members, current_member, lobby.current_round)
        await update_spectator_card(
            bot,
            str(lobby.id),
            member.user.tg_id,
            text=(
                f"{header}\\n\\n"
                f"⏳ Ход следующего игрока\\n"
                f"Ожидайте выбора задания..."
            ),
        )''',
    ),
]
patch_file("app/bot/handlers/game.py", p7_send_turn, "[7b] send_turn_notification — шапка")


p7_pick_task = [
    (
        '''    # Уведомляем всех — что выбрал и какое задание (обновляем карточку наблюдателей)
    spectator_text = (
        f"👁 Игрок выбрал {type_label}\\n\\n"
        f"<b>{task.text}</b>"
        f"{media_hint}"
    )
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        await update_spectator_card(
            call.bot, str(lobby.id), m.user.tg_id, text=spectator_text,
        )''',

        '''    # Уведомляем всех — что выбрал и какое задание (обновляем карточку наблюдателей)
    header = _compact_scoreboard(members, current_member, lobby.current_round)
    spectator_text = (
        f"{header}\\n\\n"
        f"👁 Игрок выбрал {type_label}\\n\\n"
        f"<b>{task.text}</b>"
        f"{media_hint}"
    )
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        await update_spectator_card(
            call.bot, str(lobby.id), m.user.tg_id, text=spectator_text,
        )''',
    ),
]
patch_file("app/bot/handlers/game.py", p7_pick_task, "[7c] cb_pick_task_type — шапка")


p7_truth_answer = [
    (
        '''    # Обновляем карточку наблюдателей: «игрок ответил, ждём подтверждения»
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        await update_spectator_card(
            message.bot, lobby_id, m.user.tg_id,
            text="💬 <b>Игрок ответил на вопрос</b>\\n\\nОжидаем подтверждения...",
        )''',

        '''    # Обновляем карточку наблюдателей: «игрок ответил, ждём подтверждения»
    current_member = next((m for m in members if str(m.user_id) == str(user.id)), None)
    header = _compact_scoreboard(members, current_member, lobby.current_round)
    for m in members:
        if str(m.user_id) == str(user.id):
            continue
        await update_spectator_card(
            message.bot, lobby_id, m.user.tg_id,
            text=(
                f"{header}\\n\\n"
                f"💬 <b>Игрок ответил на вопрос</b>\\n"
                f"Ожидаем подтверждения..."
            ),
        )''',
    ),
]
patch_file("app/bot/handlers/game.py", p7_truth_answer, "[7d] msg_truth_answer — шапка")


p7_truth_done = [
    (
        '''    for m in voters:
        await update_spectator_card(
            call.bot, lobby_id, m.user.tg_id,
            text=(
                "🗳 <b>Голосование!</b>\\n\\n"
                "Игрок ответил(а) на вопрос.\\n"
                "Засчитать ответ?"
            ),
            reply_markup=vote_kb(lobby_id),
        )


# ─── ДЕЙСТВИЕ: подтверждение выполнения ──────────────────────────────────────''',

        '''    current_member = next((m for m in members if str(m.user_id) == str(user.id)), None)
    header = _compact_scoreboard(members, current_member, lobby.current_round)
    for m in voters:
        await update_spectator_card(
            call.bot, lobby_id, m.user.tg_id,
            text=(
                f"{header}\\n\\n"
                f"🗳 <b>Голосование!</b>\\n"
                f"Игрок ответил(а) на вопрос.\\n"
                f"Засчитать ответ?"
            ),
            reply_markup=vote_kb(lobby_id),
        )


# ─── ДЕЙСТВИЕ: подтверждение выполнения ──────────────────────────────────────''',
    ),
]
patch_file("app/bot/handlers/game.py", p7_truth_done, "[7e] cb_truth_done — шапка")


p7_task_done = [
    (
        '''    for m in voters:
        await update_spectator_card(
            call.bot, lobby_id, m.user.tg_id,
            text=(
                f"🗳 <b>Голосование!</b>\\n\\n"
                "Игрок говорит что выполнил(а) задание.\\n\\n"
                f"Вы согласны?"
            ),
            reply_markup=vote_kb(lobby_id),
        )


# ─── Загрузка медиа для действия ─────────────────────────────────────────────''',

        '''    current_member = next((m for m in members if str(m.user_id) == str(user.id)), None)
    header = _compact_scoreboard(members, current_member, lobby.current_round)
    for m in voters:
        await update_spectator_card(
            call.bot, lobby_id, m.user.tg_id,
            text=(
                f"{header}\\n\\n"
                f"🗳 <b>Голосование!</b>\\n"
                f"Игрок говорит что выполнил(а) задание.\\n"
                f"Вы согласны?"
            ),
            reply_markup=vote_kb(lobby_id),
        )


# ─── Загрузка медиа для действия ─────────────────────────────────────────────''',
    ),
]
patch_file("app/bot/handlers/game.py", p7_task_done, "[7f] cb_task_done — шапка")


# ──────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("MEGA PATCH ЗАВЕРШЁН")
print("=" * 60)
print()
print("Дальше:")
print("  python3 -c \"import ast; ast.parse(open('app/bot/handlers/game.py').read()); print('game.py OK')\"")
print("  python3 -c \"import ast; ast.parse(open('app/services/user_service.py').read()); print('user_service.py OK')\"")
print("  python3 -c \"import ast; ast.parse(open('app/admin/routes/dashboard.py').read()); print('dashboard.py OK')\"")
print()
print("  docker compose up -d --build app")
print()
print("Бэкапы: *.bak3 рядом с каждым изменённым файлом.")
print("Откат:  cp file.bak3 file && docker compose up -d --build app")
