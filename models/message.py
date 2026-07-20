"""
models/message.py — Mensagem individual de uma sessão conversacional.

role: "user" | "assistant" | "tool"

Nota: o atributo Python é metadata_ (mapeado para coluna "metadata") porque
"metadata" é um atributo reservado da classe Base do SQLAlchemy.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class ManagerMessage(Base):
    __tablename__ = "manager_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("manager_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # role: user | assistant | tool
    role:       Mapped[str]            = mapped_column(String(50), nullable=False)
    content:    Mapped[str]            = mapped_column(Text,       nullable=False)
    tool_calls: Mapped[dict | None] = mapped_column(JSON,       nullable=True)
    metadata_:  Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True    # alias evita conflito com Base.metadata
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    session: Mapped[ManagerSession] = relationship(  # noqa: F821
        "ManagerSession", back_populates="messages"
    )
