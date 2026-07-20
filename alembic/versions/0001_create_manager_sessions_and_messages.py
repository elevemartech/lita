"""create_manager_sessions_and_messages

Revision ID: 0001
Revises:
Create Date: 2026-04-13 00:00:00.000000

Cria as tabelas:
  - manager_sessions: sessões conversacionais dos gestores
  - manager_messages: mensagens individuais de cada sessão
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manager_sessions",
        sa.Column("id",               postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id",          sa.String(255), nullable=False),
        sa.Column("school_id",        sa.String(255), nullable=False),
        sa.Column("role",             sa.String(50),  nullable=False),
        sa.Column("user_name",        sa.String(255), nullable=False),
        sa.Column(
            "title", sa.String(500), nullable=False, server_default="Nova conversa"
        ),
        sa.Column("status",           sa.String(50),  nullable=False, server_default="active"),
        sa.Column("summary",          sa.Text(),      nullable=True),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("message_count",    sa.Integer(),   nullable=False, server_default=sa.text("0")),
        sa.Column("report_count",     sa.Integer(),   nullable=False, server_default=sa.text("0")),
        sa.Column("created_at",       sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at",         sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_manager_sessions_user_id",   "manager_sessions", ["user_id"])
    op.create_index("ix_manager_sessions_school_id", "manager_sessions", ["school_id"])

    op.create_table(
        "manager_messages",
        sa.Column("id",         postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("manager_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role",       sa.String(50), nullable=False),
        sa.Column("content",    sa.Text(),     nullable=False),
        sa.Column("tool_calls", postgresql.JSON(), nullable=True),
        sa.Column("metadata",   postgresql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_manager_messages_session_id", "manager_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_manager_messages_session_id", table_name="manager_messages")
    op.drop_table("manager_messages")
    op.drop_index("ix_manager_sessions_school_id", table_name="manager_sessions")
    op.drop_index("ix_manager_sessions_user_id",   table_name="manager_sessions")
    op.drop_table("manager_sessions")
