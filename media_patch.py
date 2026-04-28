"""
media_patch.py — Медиа-менеджер в админ-дашборде.

Что делает:
  1. На странице /admin/users рядом с юзером — колонка "📎 N" с числом медиа,
     кликабельная ссылка ведёт на /admin/users/{tg_id}/media.
  2. Новая страница /admin/users/{tg_id}/media — список всех медиа этого юзера
     с превью (для фото) и ссылками на скачивание/удаление.
  3. /admin/media/{media_id}/view — стримит файл (для превью в HTML и скачивания).
  4. /admin/media/{media_id}/delete (POST) — удаляет файл с диска + помечает в БД.

Применяется ПОСЛЕ mega_patch.py.
Создаёт бэкапы *.bak4.

Запуск:
    python3 media_patch.py
"""
import os
import sys
import shutil

ROOT = os.path.abspath(os.path.dirname(__file__))


def patch_file(path: str, replacements: list, label: str) -> bool:
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        print(f"  [ERROR] {path}: файл не найден")
        return False

    bak = full + ".bak4"
    if not os.path.exists(bak):
        shutil.copy(full, bak)

    src = open(full).read()
    ok_count = 0
    for i, (old, new) in enumerate(replacements, 1):
        if old not in src:
            print(f"  [SKIP] {label} #{i}: блок не найден")
            continue
        if src.count(old) > 1:
            print(f"  [WARN] {label} #{i}: блок встречается {src.count(old)} раз — заменяю первый")
        src = src.replace(old, new, 1)
        ok_count += 1

    open(full, 'w').write(src)
    print(f"  [OK]   {label}: применено {ok_count}/{len(replacements)}")
    return True


def write_file(path: str, content: str) -> None:
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if os.path.exists(full):
        bak = full + ".bak4"
        if not os.path.exists(bak):
            shutil.copy(full, bak)
    open(full, 'w').write(content)
    print(f"  [OK]   создан: {path}")


print("=" * 60)
print("MEDIA PATCH — менеджер медиа в дашборде")
print("=" * 60)
print()


# ──────────────────────────────────────────────────────────────────
# 1. dashboard.py — расширить admin_users + добавить новые роуты
# ──────────────────────────────────────────────────────────────────
print("[1] dashboard.py — admin_users + новые роуты для медиа")

dashboard_replacements = [
    # 1.1 — расширяем admin_users: возвращаем юзеров вместе с media_count
    (
        '''@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
    search: str = "",
):
    query = select(User).order_by(User.created_at.desc()).limit(100)
    if search:
        query = select(User).where(
            User.username.ilike(f"%{search}%") | User.first_name.ilike(f"%{search}%")
        ).limit(100)

    result = await db.execute(query)
    users = result.scalars().all()

    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": users,
        "search": search,
    })''',

        '''@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
    search: str = "",
):
    query = select(User).order_by(User.created_at.desc()).limit(100)
    if search:
        query = select(User).where(
            User.username.ilike(f"%{search}%") | User.first_name.ilike(f"%{search}%")
        ).limit(100)

    result = await db.execute(query)
    users = result.scalars().all()

    # Подсчёт медиа для каждого юзера одним запросом (избегаем N+1)
    from app.database.models import MediaArchive
    user_ids = [u.id for u in users]
    media_counts = {}
    if user_ids:
        cnt_result = await db.execute(
            select(MediaArchive.user_id, func.count(MediaArchive.id))
            .where(
                MediaArchive.user_id.in_(user_ids),
                MediaArchive.is_deleted == False,  # noqa: E712
            )
            .group_by(MediaArchive.user_id)
        )
        media_counts = {uid: cnt for uid, cnt in cnt_result.all()}

    enriched = [
        {"user": u, "media_count": media_counts.get(u.id, 0)}
        for u in users
    ]

    return templates.TemplateResponse("users.html", {
        "request": request,
        "users_with_media": enriched,
        "users": users,  # совместимость со старыми шаблонами
        "search": search,
    })


# ─── Медиа юзера ────────────────────────────────────────────────────────────

@router.get("/users/{tg_id}/media", response_class=HTMLResponse)
async def admin_user_media(
    tg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    from app.database.models import MediaArchive

    user_result = await db.execute(select(User).where(User.tg_id == tg_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    media_result = await db.execute(
        select(MediaArchive)
        .where(
            MediaArchive.user_id == user.id,
            MediaArchive.is_deleted == False,  # noqa: E712
        )
        .order_by(MediaArchive.created_at.desc())
        .limit(200)
    )
    media_list = media_result.scalars().all()

    return templates.TemplateResponse("media_user.html", {
        "request": request,
        "owner": user,
        "media_list": media_list,
    })


@router.get("/media/{media_id}/view")
async def admin_media_view(
    media_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    """Стримит сам файл — используется и для превью (img src), и для скачивания."""
    from app.database.models import MediaArchive
    from fastapi.responses import FileResponse
    from pathlib import Path

    result = await db.execute(select(MediaArchive).where(MediaArchive.id == media_id))
    media = result.scalar_one_or_none()
    if not media or media.is_deleted:
        raise HTTPException(status_code=404, detail="Media not found")

    path = Path(media.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    media_type = "image/jpeg" if media.file_type == "photo" else "video/mp4"
    filename = path.name
    return FileResponse(path, media_type=media_type, filename=filename)


@router.post("/media/{media_id}/delete")
async def admin_media_delete(
    media_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    """Физически удаляет файл с диска + помечает в БД."""
    from app.database.models import MediaArchive
    from app.utils.media import delete_media_file
    from datetime import datetime

    result = await db.execute(select(MediaArchive).where(MediaArchive.id == media_id))
    media = result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    tg_id_for_redirect = None
    user_result = await db.execute(select(User).where(User.id == media.user_id))
    owner = user_result.scalar_one_or_none()
    if owner:
        tg_id_for_redirect = owner.tg_id

    # Физическое удаление
    if not media.is_deleted:
        await delete_media_file(media.file_path)
        media.is_deleted = True
        media.deleted_at = datetime.utcnow()
        await db.flush()

    redirect_url = f"/admin/users/{tg_id_for_redirect}/media" if tg_id_for_redirect else "/admin/users"
    return RedirectResponse(url=redirect_url, status_code=303)''',
    ),
]

patch_file("app/admin/routes/dashboard.py", dashboard_replacements, "dashboard.py")


# ──────────────────────────────────────────────────────────────────
# 2. users.html — добавить колонку "Медиа"
# ──────────────────────────────────────────────────────────────────
print()
print("[2] users.html — колонка медиа")

users_html_replacements = [
    # Заголовок таблицы — добавить колонку перед "Действие"
    (
        '''        <th>Регистрация</th>
        <th>Действие</th>''',

        '''        <th>Регистрация</th>
        <th>Медиа</th>
        <th>Действие</th>''',
    ),
    # Тело таблицы — заменить итерацию
    # БЫЛО: {% for user in users %}
    # СТАЛО: пробегаем по users_with_media если он есть, иначе по users
    # И добавляем ячейку <td> с числом и ссылкой ПЕРЕД ячейкой действий
    (
        '''      <td style="color:var(--muted);font-size:12px;">{{ user.created_at.strftime('%d.%m.%Y') }}</td>
      <td>
        {% if not user.is_banned %}
        <form method="post" action="/admin/ban"''',

        '''      <td style="color:var(--muted);font-size:12px;">{{ user.created_at.strftime('%d.%m.%Y') }}</td>
      <td>
        {% set mc = (users_with_media | selectattr("user.id", "equalto", user.id) | map(attribute="media_count") | list | first) | default(0) %}
        {% if mc and mc > 0 %}
          <a href="/admin/users/{{ user.tg_id }}/media"
             style="color:var(--accent);text-decoration:none;font-size:13px;">
            📎 {{ mc }}
          </a>
        {% else %}
          <span style="color:var(--muted);font-size:12px;">—</span>
        {% endif %}
      </td>
      <td>
        {% if not user.is_banned %}
        <form method="post" action="/admin/ban"''',
    ),
]

patch_file("app/admin/templates/users.html", users_html_replacements, "users.html")


# ──────────────────────────────────────────────────────────────────
# 3. Новый шаблон media_user.html — страница медиа юзера
# ──────────────────────────────────────────────────────────────────
print()
print("[3] media_user.html — новый шаблон")

media_user_html = '''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Медиа юзера — {{ owner.first_name }}</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root { --bg: #0f0f13; --card: #1a1a24; --border: #2e2e42; --accent: #7c5cfc;
          --text: #e8e8f0; --muted: #7a7a9a; --red: #ff4b6e; --green: #3ecf8e; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; }
  .sidebar { position: fixed; top: 0; left: 0; width: 220px; height: 100vh;
    background: var(--card); border-right: 1px solid var(--border); padding: 24px 16px; }
  .sidebar .logo { font-size: 20px; font-weight: 700; color: var(--accent); margin-bottom: 32px; }
  .sidebar nav a { display: flex; align-items: center; gap: 10px; padding: 10px 12px;
    border-radius: 8px; color: var(--muted); text-decoration: none; font-size: 14px; margin-bottom: 4px; }
  .sidebar nav a:hover, .sidebar nav a.active { background: #2a2a3d; color: var(--text); }
  .main { margin-left: 220px; padding: 32px; }
  h1 { font-size: 22px; font-weight: 700; margin-bottom: 8px; }
  .subtitle { color: var(--muted); font-size: 14px; margin-bottom: 24px; }
  .back { display: inline-block; color: var(--accent); text-decoration: none;
    font-size: 13px; margin-bottom: 16px; }
  .back:hover { text-decoration: underline; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 16px; }
  .card { background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden; }
  .card .preview { width: 100%; aspect-ratio: 1; background: #000;
    display: flex; align-items: center; justify-content: center; }
  .card .preview img { max-width: 100%; max-height: 100%; object-fit: cover;
    width: 100%; height: 100%; }
  .card .preview .video-placeholder { color: var(--muted); font-size: 32px; }
  .card .meta { padding: 10px 12px; font-size: 12px; color: var(--muted); }
  .card .meta .reported { color: var(--red); font-weight: 600; }
  .card .actions { display: flex; gap: 6px; padding: 8px 12px 12px; }
  .card .actions a, .card .actions button {
    flex: 1; padding: 6px 10px; border-radius: 6px; border: none;
    font-size: 12px; cursor: pointer; text-align: center; text-decoration: none;
  }
  .btn-download { background: var(--accent); color: #fff; }
  .btn-delete { background: var(--red); color: #fff; }
  .empty { color: var(--muted); padding: 40px; text-align: center; }
</style>
</head>
<body>
<div class="sidebar">
  <div class="logo">🎲 ToD Admin</div>
  <nav>
    <a href="/admin/">📊 Дашборд</a>
    <a href="/admin/reports">🚩 Жалобы</a>
    <a href="/admin/tasks">📋 Задания</a>
    <a href="/admin/users" class="active">👥 Пользователи</a>
  </nav>
</div>

<div class="main">
  <a href="/admin/users" class="back">← Все пользователи</a>
  <h1>📎 Медиа юзера: {{ owner.first_name }}</h1>
  <div class="subtitle">
    @{{ owner.username or "—" }} · tg_id: <code>{{ owner.tg_id }}</code> · всего файлов: {{ media_list|length }}
  </div>

  {% if media_list|length == 0 %}
    <div class="empty">У этого юзера пока нет медиа.</div>
  {% else %}
  <div class="grid">
    {% for m in media_list %}
    <div class="card">
      <div class="preview">
        {% if m.file_type == 'photo' %}
          <img src="/admin/media/{{ m.id }}/view" alt="{{ m.id }}" loading="lazy">
        {% else %}
          <div class="video-placeholder">🎬</div>
        {% endif %}
      </div>
      <div class="meta">
        <div>{{ m.created_at.strftime('%d.%m.%Y %H:%M') }}</div>
        <div>{{ m.file_type }} · {{ (m.file_size_bytes / 1024) | round(1) }} КБ</div>
        {% if m.is_reported %}
          <div class="reported">🚩 Жалоб: {{ m.report_count }}</div>
        {% endif %}
      </div>
      <div class="actions">
        <a class="btn-download" href="/admin/media/{{ m.id }}/view" download>⬇ Скачать</a>
        <form method="post" action="/admin/media/{{ m.id }}/delete" style="flex:1;margin:0;"
              onsubmit="return confirm('Удалить файл навсегда?');">
          <button class="btn-delete" type="submit" style="width:100%;">🗑 Удалить</button>
        </form>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
</div>
</body>
</html>
'''

write_file("app/admin/templates/media_user.html", media_user_html)


# ──────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("MEDIA PATCH ЗАВЕРШЁН")
print("=" * 60)
print()
print("Дальше:")
print("  python3 -c \"import ast; ast.parse(open('app/admin/routes/dashboard.py').read()); print('dashboard.py OK')\"")
print()
print("  docker compose up -d --build app")
print()
print("Откат: cp file.bak4 file && docker compose up -d --build app")
