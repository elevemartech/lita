"""
services/session_service.py — CRUD e lógica de ciclo de vida de sessões conversacionais.

Todas as funções recebem db: AsyncSession como primeiro argumento.
NUNCA chamam db.commit() — o commit é responsabilidade do get_session dependency.
Usam db.flush() para obter IDs gerados sem encerrar a transação.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import memory
from models.message import ManagerMessage
from models.session import ManagerSession

logger = structlog.get_logger(__name__)


class SessionService:

    @staticmethod
    async def create_session(
        db: AsyncSession,
        user_id: str,
        school_id: str,
        role: str,
        user_name: str,
    ) -> ManagerSession:
        """Cria nova sessão conversacional com status active."""
        session = ManagerSession(
            id=uuid.uuid4(),
            user_id=user_id,
            school_id=school_id,
            role=role,
            user_name=user_name,
            status="active",
        )
        db.add(session)
        await db.flush()
        logger.info("session.created", session_id=str(session.id), user_id=user_id)
        return session

    @staticmethod
    async def get_or_resume(
        db: AsyncSession,
        session_id: str,
        user_id: str,
    ) -> ManagerSession:
        """
        Carrega a sessão. Se estiver PAUSED, reativa para ACTIVE e reconstrói
        o contexto Redis a partir do banco (caso o Redis tenha expirado).

        Raises:
            ValueError: sessão não encontrada ou não pertence ao usuário.
        """
        stmt = select(ManagerSession).where(
            ManagerSession.id == uuid.UUID(session_id),
            ManagerSession.user_id == user_id,
            ManagerSession.is_deleted == False,  # noqa: E712
        )
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if session is None:
            raise ValueError(f"Sessão {session_id} não encontrada ou não pertence ao usuário.")

        if session.status == "paused":
            session.status = "active"
            session.last_activity_at = datetime.now(UTC)

            # Reconstrói Redis se o contexto tiver expirado
            context = await memory.get_context(session_id)
            if not context:
                msgs = await SessionService._load_messages_as_openai_format(db, session_id)
                await memory.rebuild_from_db(session_id, msgs)

            logger.info("session.resumed", session_id=session_id)

        return session

    @staticmethod
    async def _load_messages_as_openai_format(
        db: AsyncSession,
        session_id: str,
    ) -> list[dict]:
        """Carrega últimas 40 mensagens (20 turns) do banco no formato OpenAI."""
        stmt = (
            select(ManagerMessage)
            .where(ManagerMessage.session_id == uuid.UUID(session_id))
            .order_by(ManagerMessage.created_at.desc())
            .limit(40)
        )
        result = await db.execute(stmt)
        rows = list(reversed(result.scalars().all()))
        return [{"role": m.role, "content": m.content} for m in rows]

    @staticmethod
    async def add_message(
        db: AsyncSession,
        session: ManagerSession,
        role: str,
        content: str,
        tool_calls: dict | None = None,
        metadata: dict | None = None,
    ) -> ManagerMessage:
        """Persiste mensagem e atualiza contadores da sessão."""
        msg = ManagerMessage(
            id=uuid.uuid4(),
            session_id=session.id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            metadata_=metadata,
        )
        db.add(msg)
        session.message_count += 1
        session.last_activity_at = datetime.now(UTC)
        await db.flush()
        return msg

    @staticmethod
    async def set_title(
        db: AsyncSession,
        session: ManagerSession,
        title: str,
    ) -> None:
        """Atualiza o título apenas se ainda for o padrão 'Nova conversa'."""
        if session.title == "Nova conversa":
            session.title = title[:500]
            logger.info("session.title_set", session_id=str(session.id), title=title)

    @staticmethod
    async def close_session(
        db: AsyncSession,
        session: ManagerSession,
        summary: str,
    ) -> None:
        """Encerra a sessão e remove o contexto Redis."""
        session.status = "completed"
        session.summary = summary
        session.ended_at = datetime.now(UTC)
        await memory.clear_context(str(session.id))
        logger.info("session.closed", session_id=str(session.id))

    @staticmethod
    async def increment_report_count(
        db: AsyncSession,
        session: ManagerSession,
    ) -> None:
        """Incrementa o contador de relatórios gerados na sessão."""
        session.report_count += 1
