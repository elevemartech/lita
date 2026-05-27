"""
routers/sessions.py — CRUD de sessões conversacionais.

GET    /sessions/                    → lista sessões do gestor autenticado
POST   /sessions/                    → cria nova sessão
GET    /sessions/summary/            → indicadores da escola para a SummaryBar
GET    /sessions/{id}/               → detalhe + últimas 50 mensagens
POST   /sessions/{id}/briefing/      → gera e persiste briefing diário (idempotente)
POST   /sessions/{id}/close/         → encerra sessão e gera resumo via LLM
DELETE /sessions/{id}/               → soft delete (is_deleted=True)

Todos os endpoints validam que a sessão pertence ao user_id do JWT.
"""
from __future__ import annotations

import json
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.tools.get_school_summary import get_school_summary
from core import memory
from core.auth import CurrentUser, get_current_user
from core.database import get_session
from core.settings import settings
from models.message import ManagerMessage
from models.session import ManagerSession
from schemas.session_types import (
    BriefingResponse,
    MessageResponse,
    SessionDetailResponse,
    SessionResponse,
    SummaryResponse,
)
from services.session_service import SessionService

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/", response_model=list[SessionResponse])
async def list_sessions(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession  = Depends(get_session),
):
    """Lista sessões ativas e pausadas do gestor, ordenadas pela mais recente."""
    stmt = (
        select(ManagerSession)
        .where(
            ManagerSession.user_id    == user.user_id,
            ManagerSession.is_deleted == False,  # noqa: E712
        )
        .order_by(ManagerSession.last_activity_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=SessionResponse, status_code=201)
async def create_session(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession  = Depends(get_session),
):
    """Cria nova sessão conversacional para o gestor autenticado."""
    session = await SessionService.create_session(
        db,
        user_id=user.user_id,
        school_id=user.school_id,
        role=user.role,
        user_name=user.name,
    )
    logger.info("sessions.create", user_id=user.user_id, session_id=str(session.id))
    return session


@router.get("/summary/", response_model=SummaryResponse)
async def get_summary(
    user: CurrentUser = Depends(get_current_user),
):
    """
    Retorna indicadores vivos da escola para a SummaryBar do painel.
    Reutiliza a tool get_school_summary sem passar pelo LangGraph.
    Falha em qualquer query individual retorna 0 para esse campo.
    """
    import re

    try:
        summary_json = await get_school_summary.ainvoke({
            "sa_token":  user.sa_token,
            "school_id": user.school_id,
            "user_name": user.name,
        })
        raw = json.loads(summary_json).get("summary_data", "")
    except Exception:
        raw = ""

    def extract(key: str) -> int:
        m = re.search(rf"{key}:\s*(\d+)", raw)
        return int(m.group(1)) if m else 0

    return SummaryResponse(
        solicitacoes_abertas=extract("solicitacoes_abertas"),
        matriculas_pendentes=extract("matriculas_pendentes"),
        inadimplencia_aberta=extract("inadimplencia_aberta"),
    )


@router.get("/{session_id}/", response_model=SessionDetailResponse)
async def get_session_detail(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession  = Depends(get_session),
):
    """Retorna detalhes da sessão com as últimas 50 mensagens (ordem cronológica)."""
    stmt = select(ManagerSession).where(
        ManagerSession.id         == uuid.UUID(session_id),
        ManagerSession.user_id    == user.user_id,
        ManagerSession.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    # Busca últimas 50 mensagens e ordena cronologicamente para exibição
    msg_stmt = (
        select(ManagerMessage)
        .where(ManagerMessage.session_id == session.id)
        .order_by(ManagerMessage.created_at.desc())
        .limit(50)
    )
    msg_result  = await db.execute(msg_stmt)
    messages    = list(reversed(msg_result.scalars().all()))

    session_resp = SessionResponse.model_validate(session)
    return SessionDetailResponse(
        **session_resp.model_dump(),
        messages=[MessageResponse.model_validate(m) for m in messages],
        summary=session.summary,
    )


@router.post("/{session_id}/briefing/", response_model=BriefingResponse)
async def generate_briefing(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession  = Depends(get_session),
):
    """
    Gera e persiste o briefing diário como primeira mensagem da sessão.

    Idempotente: se a sessão já tiver mensagens, retorna a primeira mensagem
    assistant existente sem gerar um novo briefing.
    """
    stmt = select(ManagerSession).where(
        ManagerSession.id         == uuid.UUID(session_id),
        ManagerSession.user_id    == user.user_id,
        ManagerSession.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    # Idempotência — retorna briefing já existente sem gerar outro
    if session.message_count > 0:
        msg_stmt = (
            select(ManagerMessage)
            .where(
                ManagerMessage.session_id == session.id,
                ManagerMessage.role       == "assistant",
            )
            .order_by(ManagerMessage.created_at.asc())
            .limit(1)
        )
        msg_result       = await db.execute(msg_stmt)
        first_assistant  = msg_result.scalar_one_or_none()
        if first_assistant:
            return BriefingResponse(
                session_id=session_id,
                briefing=first_assistant.content,
            )

    # Busca dados da escola em paralelo via tool
    summary_json = await get_school_summary.ainvoke({
        "sa_token":  user.sa_token,
        "school_id": user.school_id,
        "user_name": user.name,
    })

    # Formata em linguagem natural via gpt-4o-mini
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=settings.openai_api_key,
        temperature=0,
        max_tokens=200,
    )
    response = await llm.ainvoke([
        SystemMessage(
            content=(
                f"Você é o Nicodemus, copiloto de gestão escolar.\n"
                f"Formate os dados abaixo em uma saudação de briefing diário curta e objetiva "
                f"para o gestor {user.name}. Use linguagem natural e amigável.\n"
                f"Mencione apenas itens com count > 0. Máximo 3 linhas.\n"
                f"Termine sugerindo uma ação concreta ou perguntando como pode ajudar."
            )
        ),
        HumanMessage(content=summary_json),
    ])
    briefing_text = response.content.strip()

    # Persiste como primeira mensagem da sessão
    await SessionService.add_message(
        db,
        session,
        "assistant",
        briefing_text,
        None,
        {"type": "briefing"},
    )
    await db.commit()

    # Atualiza contexto Redis — user_msg vazio pois não há mensagem do gestor
    await memory.append_turn(
        session_id,
        {"role": "user", "content": ""},
        {"role": "assistant", "content": briefing_text},
    )

    logger.info("sessions.briefing", session_id=session_id, user_id=user.user_id)
    return BriefingResponse(session_id=session_id, briefing=briefing_text)


@router.post("/{session_id}/close/", response_model=SessionResponse)
async def close_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession  = Depends(get_session),
):
    """Encerra a sessão e gera resumo via LLM a partir das últimas mensagens."""
    stmt = select(ManagerSession).where(
        ManagerSession.id         == uuid.UUID(session_id),
        ManagerSession.user_id    == user.user_id,
        ManagerSession.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    if session.status == "completed":
        raise HTTPException(status_code=400, detail="Sessão já encerrada.")

    summary = await _generate_summary(db, session)
    await SessionService.close_session(db, session, summary)
    logger.info("sessions.closed", session_id=session_id, user_id=user.user_id)
    return session


@router.delete("/{session_id}/", status_code=204)
async def delete_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession  = Depends(get_session),
):
    """Soft delete — marca is_deleted=True. Os dados permanecem no banco."""
    stmt = select(ManagerSession).where(
        ManagerSession.id         == uuid.UUID(session_id),
        ManagerSession.user_id    == user.user_id,
        ManagerSession.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    session.is_deleted = True
    logger.info("sessions.deleted", session_id=session_id, user_id=user.user_id)


async def _generate_summary(db: AsyncSession, session: ManagerSession) -> str:
    """Gera resumo de 2-3 frases da sessão via gpt-4o-mini."""
    stmt = (
        select(ManagerMessage)
        .where(ManagerMessage.session_id == session.id)
        .order_by(ManagerMessage.created_at.desc())
        .limit(20)
    )
    result = await db.execute(stmt)
    msgs   = list(reversed(result.scalars().all()))

    if not msgs:
        return "Sessão sem mensagens."

    transcript = "\n".join(
        f"{m.role}: {m.content[:200]}" for m in msgs
    )

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=settings.openai_api_key,
        temperature=0,
    )
    response = await llm.ainvoke([
        SystemMessage(
            content="Resuma em 2-3 frases o que foi discutido nesta sessão de gestão escolar. "
                    "Seja objetivo e mencione os principais tópicos abordados."
        ),
        HumanMessage(content=transcript),
    ])
    return response.content.strip()
