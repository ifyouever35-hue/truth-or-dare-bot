"""Initial migration — create all tables

Revision ID: 0001_initial
Revises: 
Create Date: 2025-01-01 00:00:00.000000

Как применить:
    alembic upgrade head

Как откатить:
    alembic downgrade base

Как создать следующую миграцию после изменения models.py:
    alembic revision --autogenerate -m "add column X to users"
    alembic upgrade head
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── ENUM types ───────────────────────────────────────────────────────────
    # Создаём ENUM типы отдельно — PostgreSQL требует явного создания
    lobby_status = postgresql.ENUM(
        'waiting', 'active', 'closed',
        name='lobbystatus', create_type=False
    )
    lobby_status.create(op.get_bind(), checkfirst=True)

    task_type = postgresql.ENUM(
        'truth', 'dare',
        name='tasktype', create_type=False
    )
    task_type.create(op.get_bind(), checkfirst=True)

    media_required = postgresql.ENUM(
        'none', 'photo', 'video_note',
        name='mediarequired', create_type=False
    )
    media_required.create(op.get_bind(), checkfirst=True)

    ban_type = postgresql.ENUM(
        'warn', 'temp', 'permanent',
        name='bantype', create_type=False
    )
    ban_type.create(op.get_bind(), checkfirst=True)

    # ─── users ────────────────────────────────────────────────────────────────
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tg_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(64), nullable=True),
        sa.Column('first_name', sa.String(128), nullable=False, server_default=''),
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('verified_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stars_balance', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('games_played', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('games_won', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('dares_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('truths_answered', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_banned', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('ban_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tg_id'),
    )
    op.create_index('ix_users_tg_id', 'users', ['tg_id'])
    op.create_index('ix_users_is_verified', 'users', ['is_verified'])

    # ─── lobbies ──────────────────────────────────────────────────────────────
    op.create_table(
        'lobbies',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('host_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('is_18_plus', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('join_hash', sa.String(32), nullable=False),
        sa.Column('status', sa.Enum('waiting', 'active', 'closed', name='lobbystatus'), nullable=False),
        sa.Column('status_message_id', sa.BigInteger(), nullable=True),
        sa.Column('chat_id', sa.BigInteger(), nullable=True),
        sa.Column('current_round', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('current_player_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('current_task_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('task_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['host_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('join_hash'),
    )
    op.create_index('ix_lobbies_status', 'lobbies', ['status'])
    op.create_index('ix_lobbies_join_hash', 'lobbies', ['join_hash'])

    # ─── lobby_members ────────────────────────────────────────────────────────
    op.create_table(
        'lobby_members',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lobby_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('lives', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['lobby_id'], ['lobbies.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('lobby_id', 'user_id', name='uq_lobby_member'),
    )
    op.create_index('ix_lobby_members_lobby_id', 'lobby_members', ['lobby_id'])

    # ─── tasks_pool ───────────────────────────────────────────────────────────
    op.create_table(
        'tasks_pool',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('type', sa.Enum('truth', 'dare', name='tasktype'), nullable=False),
        sa.Column('is_18_plus', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('media_required', sa.Enum('none', 'photo', 'video_note', name='mediarequired'),
                  nullable=False, server_default='none'),
        sa.Column('times_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('times_skipped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tasks_type_18plus', 'tasks_pool', ['type', 'is_18_plus'])
    op.create_index('ix_tasks_active', 'tasks_pool', ['is_active'])

    # ─── media_archive ────────────────────────────────────────────────────────
    op.create_table(
        'media_archive',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lobby_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('file_path', sa.String(512), nullable=False),
        sa.Column('original_file_id', sa.String(256), nullable=True),
        sa.Column('file_type', sa.String(16), nullable=True),
        sa.Column('file_size_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_reported', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('report_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('report_reason', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['lobby_id'], ['lobbies.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_media_user_id', 'media_archive', ['user_id'])
    op.create_index('ix_media_is_reported', 'media_archive', ['is_reported'])
    op.create_index('ix_media_created_at', 'media_archive', ['created_at'])

    # ─── payments ─────────────────────────────────────────────────────────────
    op.create_table(
        'payments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', sa.String(32), nullable=True),
        sa.Column('product', sa.String(64), nullable=True),
        sa.Column('amount', sa.Numeric(10, 2), nullable=True),
        sa.Column('currency', sa.String(8), nullable=True),
        sa.Column('status', sa.String(16), nullable=True, server_default='pending'),
        sa.Column('external_id', sa.String(256), nullable=True),
        sa.Column('telegram_payment_charge_id', sa.String(256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payments_user_id', 'payments', ['user_id'])
    op.create_index('ix_payments_status', 'payments', ['status'])

    # ─── bans ─────────────────────────────────────────────────────────────────
    op.create_table(
        'bans',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('admin_note', sa.Text(), nullable=True),
        sa.Column('ban_type', sa.Enum('warn', 'temp', 'permanent', name='bantype'), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('media_archive_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    # Удаляем таблицы в обратном порядке (с учётом FK)
    op.drop_table('bans')
    op.drop_index('ix_payments_status', 'payments')
    op.drop_index('ix_payments_user_id', 'payments')
    op.drop_table('payments')
    op.drop_index('ix_media_created_at', 'media_archive')
    op.drop_index('ix_media_is_reported', 'media_archive')
    op.drop_index('ix_media_user_id', 'media_archive')
    op.drop_table('media_archive')
    op.drop_index('ix_tasks_active', 'tasks_pool')
    op.drop_index('ix_tasks_type_18plus', 'tasks_pool')
    op.drop_table('tasks_pool')
    op.drop_index('ix_lobby_members_lobby_id', 'lobby_members')
    op.drop_table('lobby_members')
    op.drop_index('ix_lobbies_join_hash', 'lobbies')
    op.drop_index('ix_lobbies_status', 'lobbies')
    op.drop_table('lobbies')
    op.drop_index('ix_users_is_verified', 'users')
    op.drop_index('ix_users_tg_id', 'users')
    op.drop_table('users')

    # Удаляем ENUM типы
    sa.Enum(name='bantype').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='mediarequired').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='tasktype').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='lobbystatus').drop(op.get_bind(), checkfirst=True)
