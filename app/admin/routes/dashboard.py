"""
app/admin/routes/dashboard.py — Веб-дашборд администратора.

Доступ: Basic Auth (логин/пароль из .env).
Маршруты:
  GET  /admin/           — дашборд (DAU, MAU, доходы)
  GET  /admin/reports    — медиа с жалобами
  POST /admin/ban        — забанить пользователя
  GET  /admin/tasks      — список заданий
  POST /admin/tasks      — добавить задание
  POST /admin/tasks/{id} — изменить активность задания
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import (
    Ban,
    BanType,
    Lobby,
    LobbyStatus,
    MediaArchive,
    Payment,
    TaskType,
    TasksPool,
    User,
)
from app.database.session import get_db
from app.services.user_service import ban_user_db
from app.utils.redis_client import redis_client

from pathlib import Path as _Path

router = APIRouter(prefix="/admin", tags=["admin"])
_TEMPLATES_DIR = str(_Path(__file__).parent.parent / "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)
security = HTTPBasic()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ─── Auth ─────────────────────────────────────────────────────────────────────

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct_user = credentials.username == settings.admin_username
    correct_pass = credentials.password == settings.admin_password
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ─── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # DAU — активные сегодня
    dau_result = await db.execute(
        select(func.count(User.id)).where(User.last_active_at >= today)
    )
    dau = dau_result.scalar() or 0

    # MAU — активные за текущий месяц
    mau_result = await db.execute(
        select(func.count(User.id)).where(User.last_active_at >= month_start)
    )
    mau = mau_result.scalar() or 0

    # Всего пользователей
    total_users_result = await db.execute(select(func.count(User.id)))
    total_users = total_users_result.scalar() or 0

    # Активные комнаты
    active_lobbies_result = await db.execute(
        select(func.count(Lobby.id)).where(Lobby.status == LobbyStatus.ACTIVE)
    )
    active_lobbies = active_lobbies_result.scalar() or 0

    # Доход за месяц (UAH + XTR отдельно)
    revenue_result = await db.execute(
        select(Payment.currency, func.sum(Payment.amount))
        .where(Payment.status == "success", Payment.created_at >= month_start)
        .group_by(Payment.currency)
    )
    revenue = {row[0]: float(row[1]) for row in revenue_result.all()}

    # Конверсия в Verified
    verified_result = await db.execute(
        select(func.count(User.id)).where(User.is_verified == True)  # noqa: E712
    )
    verified_count = verified_result.scalar() or 0
    conversion = round(verified_count / total_users * 100, 1) if total_users > 0 else 0

    # Жалобы ожидающие проверки
    reports_result = await db.execute(
        select(func.count(MediaArchive.id)).where(
            MediaArchive.is_reported == True,  # noqa: E712
            MediaArchive.is_deleted == False,  # noqa: E712
        )
    )
    pending_reports = reports_result.scalar() or 0

    # Комнат создано за сегодня
    lobbies_today_result = await db.execute(
        select(func.count(Lobby.id)).where(Lobby.created_at >= today)
    )
    lobbies_today = lobbies_today_result.scalar() or 0

    # Всего игр сыграно (закрытых лобби)
    total_games_result = await db.execute(
        select(func.count(Lobby.id)).where(Lobby.status == LobbyStatus.CLOSED)
    )
    total_games = total_games_result.scalar() or 0

    context = {
        "request": request,
        "dau": dau,
        "mau": mau,
        "total_users": total_users,
        "active_lobbies": active_lobbies,
        "revenue": revenue,
        "conversion": conversion,
        "verified_count": verified_count,
        "pending_reports": pending_reports,
        "lobbies_today": lobbies_today,
        "total_games": total_games,
        "now": now.strftime("%d.%m.%Y %H:%M"),
    }
    return templates.TemplateResponse("dashboard.html", context)


# ─── Reports ──────────────────────────────────────────────────────────────────

@router.get("/reports", response_class=HTMLResponse)
async def admin_reports(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
    page: int = 1,
):
    per_page = 20
    offset = (page - 1) * per_page

    result = await db.execute(
        select(MediaArchive)
        .where(
            MediaArchive.is_reported == True,  # noqa: E712
            MediaArchive.is_deleted == False,  # noqa: E712
        )
        .order_by(MediaArchive.report_count.desc())
        .limit(per_page)
        .offset(offset)
    )
    reports = result.scalars().all()

    # Загружаем всех юзеров одним запросом (вместо N+1)
    user_ids = [m.user_id for m in reports]
    users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_map = {u.id: u for u in users_result.scalars().all()}

    enriched = [{"media": m, "user": users_map.get(m.user_id)} for m in reports]

    return templates.TemplateResponse("reports.html", {
        "request": request,
        "reports": enriched,
        "page": page,
    })


# ─── Ban ─────────────────────────────────────────────────────────────────────

@router.post("/ban")
async def admin_ban_user(
    tg_id: int = Form(...),
    reason: str = Form(...),
    ban_type: str = Form("permanent"),
    media_archive_id: str = Form(None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    user_result = await db.execute(select(User).where(User.tg_id == tg_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await ban_user_db(db, user, reason)

    # Добавляем в Redis blacklist
    ttl = None if ban_type == "permanent" else 60 * 60 * 24 * 30  # 30 дней
    await redis_client.ban_user(tg_id, ttl_seconds=ttl)

    # Записываем в таблицу банов
    ban = Ban(
        user_id=user.id,
        admin_note=reason,
        ban_type=BanType(ban_type),
        media_archive_id=media_archive_id,
    )
    db.add(ban)

    return RedirectResponse(url="/admin/reports", status_code=303)


# ─── Tasks management ─────────────────────────────────────────────────────────

@router.get("/tasks", response_class=HTMLResponse)
async def admin_tasks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
    task_type: str = "all",
    is_18_plus: str = "all",
):
    query = select(TasksPool).order_by(TasksPool.created_at.desc())
    if task_type != "all":
        query = query.where(TasksPool.type == TaskType(task_type))
    if is_18_plus == "true":
        query = query.where(TasksPool.is_18_plus == True)  # noqa: E712
    elif is_18_plus == "false":
        query = query.where(TasksPool.is_18_plus == False)  # noqa: E712

    result = await db.execute(query.limit(200))
    tasks = result.scalars().all()

    return templates.TemplateResponse("tasks.html", {
        "request": request,
        "tasks": tasks,
        "filter_type": task_type,
        "filter_18": is_18_plus,
    })


@router.post("/tasks")
async def admin_add_task(
    text: str = Form(...),
    task_type: str = Form(...),
    is_18_plus: bool = Form(False),
    media_required: str = Form("none"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    from app.database.models import MediaRequired
    task = TasksPool(
        type=TaskType(task_type),
        is_18_plus=is_18_plus,
        text=text,
        media_required=MediaRequired(media_required),
    )
    db.add(task)
    return RedirectResponse(url="/admin/tasks", status_code=303)


@router.post("/tasks/{task_id}/toggle")
async def admin_toggle_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin),
):
    result = await db.execute(select(TasksPool).where(TasksPool.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404)
    task.is_active = not task.is_active
    return RedirectResponse(url="/admin/tasks", status_code=303)


# ─── Users search ─────────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
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
    })
