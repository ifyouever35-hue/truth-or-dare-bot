"""
app/database/models.py — ORM-модели всех таблиц.

Дизайн-решения:
  • Все PK — UUID (избегаем предсказуемых инкрементных ID).
  • Индексы на часто фильтруемые поля (tg_id, lobby_id, status).
  • Soft-delete через deleted_at — физически строки не удаляем.
  • UUID хранится как CHAR(36) в SQLite (для тестов) и как UUID в PostgreSQL.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import TypeDecorator, CHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ─── Cross-DB UUID type ───────────────────────────────────────────────────────

class UUID(TypeDecorator):
    """
    Хранит UUID как CHAR(36) в SQLite (для тестов)
    и как нативный UUID в PostgreSQL (для прода).

    Это позволяет использовать одни и те же модели и в тестах (SQLite),
    и в продакшене (PostgreSQL) без дублирования кода.
    """
    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value) if isinstance(value, uuid.UUID) else value

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if not isinstance(value, uuid.UUID):
            return uuid.UUID(str(value))
        return value


class Base(DeclarativeBase):
    pass


# ─── Enums ────────────────────────────────────────────────────────────────────

class LobbyStatus(str, enum.Enum):
    WAITING = "waiting"   # ждём игроков
    ACTIVE = "active"     # игра идёт
    CLOSED = "closed"     # завершена


class TaskType(str, enum.Enum):
    TRUTH = "truth"
    DARE = "dare"


class MediaRequired(str, enum.Enum):
    NONE = "none"
    PHOTO = "photo"
    VIDEO_NOTE = "video_note"


class BanType(str, enum.Enum):
    WARN = "warn"
    TEMP = "temp"
    PERMANENT = "permanent"


# ─── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4
    )
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128), default="")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Баланс для откупов (в Stars)
    stars_balance: Mapped[int] = mapped_column(Integer, default=0)

    # Статистика
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    games_won: Mapped[int] = mapped_column(Integer, default=0)
    dares_completed: Mapped[int] = mapped_column(Integer, default=0)
    truths_answered: Mapped[int] = mapped_column(Integer, default=0)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_reason: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relations
    hosted_lobbies: Mapped[list["Lobby"]] = relationship(back_populates="host")
    memberships: Mapped[list["LobbyMember"]] = relationship(back_populates="user")
    media_files: Mapped[list["MediaArchive"]] = relationship(back_populates="user")

    __table_args__ = (
        Index("ix_users_tg_id", "tg_id"),
        Index("ix_users_is_verified", "is_verified"),
    )

    @property
    def is_verification_active(self) -> bool:
        if not self.is_verified or not self.verified_expires_at:
            return False
        return self.verified_expires_at > datetime.utcnow()


# ─── Lobbies ──────────────────────────────────────────────────────────────────

class Lobby(Base):
    __tablename__ = "lobbies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4
    )
    host_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("users.id"), nullable=False
    )
    is_18_plus: Mapped[bool] = mapped_column(Boolean, default=False)

    # Уникальный хэш для инвайт-ссылки: t.me/bot?start=join_XXXXXX
    join_hash: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    status: Mapped[LobbyStatus] = mapped_column(
        Enum(LobbyStatus), default=LobbyStatus.WAITING
    )

    # ID сообщения в чате, которое показывает статус лобби
    status_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Счётчик раундов
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    current_player_index: Mapped[int] = mapped_column(Integer, default=0)

    # ID текущего задания
    current_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID())
    task_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relations
    host: Mapped["User"] = relationship(back_populates="hosted_lobbies")
    members: Mapped[list["LobbyMember"]] = relationship(back_populates="lobby")

    __table_args__ = (
        Index("ix_lobbies_status", "status"),
        Index("ix_lobbies_join_hash", "join_hash"),
    )


class LobbyMember(Base):
    __tablename__ = "lobby_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4
    )
    lobby_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("lobbies.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("users.id"), nullable=False
    )

    score: Mapped[int] = mapped_column(Integer, default=0)
    lives: Mapped[int] = mapped_column(Integer, default=3)  # из settings
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relations
    lobby: Mapped["Lobby"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("lobby_id", "user_id", name="uq_lobby_member"),
        Index("ix_lobby_members_lobby_id", "lobby_id"),
    )


# ─── Tasks ────────────────────────────────────────────────────────────────────

class TasksPool(Base):
    __tablename__ = "tasks_pool"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4
    )
    type: Mapped[TaskType] = mapped_column(Enum(TaskType), nullable=False)
    is_18_plus: Mapped[bool] = mapped_column(Boolean, default=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    media_required: Mapped[MediaRequired] = mapped_column(
        Enum(MediaRequired), default=MediaRequired.NONE
    )

    # Статистика использования
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    times_skipped: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_tasks_type_18plus", "type", "is_18_plus"),
        Index("ix_tasks_active", "is_active"),
    )


# ─── Media Archive ────────────────────────────────────────────────────────────

class MediaArchive(Base):
    __tablename__ = "media_archive"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("users.id"), nullable=False
    )
    lobby_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("lobbies.id"), nullable=False
    )
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID())

    # Путь к сжатому файлу на сервере
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_file_id: Mapped[str] = mapped_column(String(256))  # Telegram file_id
    file_type: Mapped[str] = mapped_column(String(16))  # "photo" / "video_note"
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    # Жалобы
    is_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    report_count: Mapped[int] = mapped_column(Integer, default=0)
    report_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Флаг удаления с сервера
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relations
    user: Mapped["User"] = relationship(back_populates="media_files")

    __table_args__ = (
        Index("ix_media_user_id", "user_id"),
        Index("ix_media_is_reported", "is_reported"),
        Index("ix_media_created_at", "created_at"),
    )


# ─── Payments ─────────────────────────────────────────────────────────────────

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("users.id"), nullable=False
    )

    # "stars" | "wayforpay" | "monobank"
    provider: Mapped[str] = mapped_column(String(32))
    # "verified_30d" | "stars_100" | "buyout"
    product: Mapped[str] = mapped_column(String(64))

    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(8))  # "UAH" / "XTR" (Stars)

    # "pending" | "success" | "failed" | "refunded"
    status: Mapped[str] = mapped_column(String(16), default="pending")

    external_id: Mapped[Optional[str]] = mapped_column(String(256))  # ID транзакции провайдера
    telegram_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_payments_user_id", "user_id"),
        Index("ix_payments_status", "status"),
    )


# ─── Bans ─────────────────────────────────────────────────────────────────────

class Ban(Base):
    __tablename__ = "bans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("users.id"), nullable=False
    )
    admin_note: Mapped[Optional[str]] = mapped_column(Text)
    ban_type: Mapped[BanType] = mapped_column(Enum(BanType))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    media_archive_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID())

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
