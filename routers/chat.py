"""
routers/chat.py — Endpoint de chat conversacional do Nicodemus ADM.

Fluxo:
  POST /chat/message
    1. Valida JWT → CurrentUser
    2. Carrega/retoma sessão via SessionService
    3. Carrega contexto Redis
    4. Monta NicoState
    5. Invoca nico_agent (ReAct)
    6. Persiste user_message + assistant_reply no banco
    7. Atualiza Redis com append_turn
    8. Auto-gera título na primeira mensagem (LLM com prompt curto)
    9. Retorna { session_id, reply, file_id, file_url, faq_plan }

O JWT do usuário logado no dashboard é obrigatório.
Sessões com status "completed" são rejeitadas com 400.
"""
from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from agent.nico_agent import nico_graph
from agent.state import NicoState
from core import memory
from core.auth import CurrentUser, get_current_user
from core.database import get_session
from core.settings import settings
from schemas.session_types import ChatRequest, ChatResponse
from services.session_service import SessionService

logger = structlog.get_logger(__name__)

router = APIRouter()

_title_llm = ChatOpenAI(
    model="gpt-4o-mini",
    api_key=settings.openai_api_key,
    temperature=0,
)


async def _generate_title(message: str) -> str:
    """Gera título curto (≤ 60 chars) a partir da primeira mensagem do gestor."""
    try:
        resp = await _title_llm.ainvoke([
            SystemMessage(
                content=(
                    "Gere um título curto (máximo 60 caracteres) para uma conversa "
                    "de gestão escolar iniciada com a mensagem abaixo. "
                    "Responda apenas o título, sem aspas ou pontuação extra."
                )
            ),
            HumanMessage(content=message),
        ])
        return resp.content.strip()[:500]
    except Exception as exc:
        logger.warning("chat.title_error", error=str(exc))
        return message[:60]


def _extract_file_id(messages: list[dict]) -> str | None:
    """Extrai o file_id do primeiro resultado de tool que contenha esse campo."""
    for msg in messages:
        if msg.get("role") == "tool":
            try:
                data = json.loads(msg.get("content", "{}"))
                if isinstance(data, dict) and data.get("file_id"):
                    return str(data["file_id"])
            except (json.JSONDecodeError, AttributeError):
                pass
    return None


@router.post("/", response_model=ChatResponse)
@router.post("/message", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """
    Ponto de entrada do chat conversacional com memória de sessão.

    Requer session_id de uma sessão ativa — crie via POST /sessions/.
    Sessões encerradas (status=completed) são rejeitadas com 400.
    """
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="Mensagem não pode ser vazia.")

    # Carrega ou retoma sessão (reativa PAUSED e reconstrói Redis se necessário)
    try:
        session = await SessionService.get_or_resume(db, body.session_id, user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if session.status == "completed":
        raise HTTPException(
            status_code=400,
            detail="Sessão encerrada. Crie uma nova sessão para continuar.",
        )

    is_first_message = session.message_count == 0

    # Carrega contexto Redis e adiciona mensagem do usuário
    context = await memory.get_context(body.session_id)
    user_msg = {"role": "user", "content": body.message}
    messages_for_agent = context + [user_msg]

    logger.info(
        "chat.invoke",
        session_id=body.session_id,
        user_id=user.user_id,
        msg_count=len(messages_for_agent),
    )

    # Monta estado inicial e invoca o agente ReAct
    initial_state: NicoState = {
        "user_id":      user.user_id,
        "school_id":    user.school_id,
        "sa_token":     user.sa_token,
        "role":         user.role,
        "user_name":    user.name,
        "session_id":   body.session_id,
        "messages":     messages_for_agent,
        "user_message": body.message,
        "tool_calls":   [],
        "response":     "",
        "error":        None,
    }

    final_state: NicoState = await nico_graph.ainvoke(initial_state, config={"recursion_limit": 10})

    reply = final_state.get("response") or ""
    if not reply:
        reply = "Não consegui processar sua mensagem. Tente novamente."

    # Extrai file_id de resultados de tools do turno atual
    all_messages = final_state.get("messages", [])
    file_id = _extract_file_id(all_messages)
    file_url = f"/report/download/{file_id}" if file_id else None

    # Persiste as mensagens do turno no banco
    await SessionService.add_message(db, session, "user", body.message)
    await SessionService.add_message(
        db,
        session,
        "assistant",
        reply,
        metadata={"file_id": file_id} if file_id else None,
    )

    # Atualiza contexto Redis com o par user/assistant
    assistant_msg = {"role": "assistant", "content": reply}
    await memory.append_turn(body.session_id, user_msg, assistant_msg)

    # Incrementa relatórios se houve geração de arquivo
    if file_id:
        await SessionService.increment_report_count(db, session)

    # Gera título automático na primeira mensagem da sessão
    if is_first_message:
        title = await _generate_title(body.message)
        await SessionService.set_title(db, session, title)

    faq_plan = final_state.get("faq_plan")

    logger.info(
        "chat.ok",
        session_id=body.session_id,
        user_id=user.user_id,
        has_file=file_id is not None,
    )

    return ChatResponse(
        session_id=body.session_id,
        reply=reply,
        file_id=file_id,
        file_url=file_url,
        faq_plan=faq_plan,
    )


# ── FAQ Manager endpoints ─────────────────────────────────────────────────────

from redis.asyncio import Redis as _FaqRedis  # noqa: E402  (local import — evita circular)
from agent.tools.faq_tools import execute_faq_plan as _execute_faq_plan  # noqa: E402
from schemas.faq_schemas import FaqExecuteRequest, FaqExecuteResponse  # noqa: E402
from schemas.faq_schemas import FaqPlan as _FaqPlan  # noqa: E402


@router.post("/faq/execute", response_model=FaqExecuteResponse)
async def execute_faq_plan_endpoint(
    body: FaqExecuteRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """
    Executa as acções aprovadas de um plano FAQ.
    Carrega o plano do Redis pela plan_id e executa apenas as acções aprovadas.
    Cada acção é independente — falha numa não para as outras.
    Requer sessão activa (status=active).
    """
    try:
        session = await SessionService.get_or_resume(db, body.session_id, user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if session.status == "completed":
        raise HTTPException(
            status_code=400,
            detail="Sessão encerrada. Crie uma nova sessão para continuar.",
        )

    result_str = await _execute_faq_plan.ainvoke({
        "plan_id": body.plan_id,
        "approved_actions_json": json.dumps(body.actions),
        "sa_token": user.sa_token,
        "school_id": user.school_id,
    })

    result_data = json.loads(result_str)
    if "error" in result_data:
        error_msg = result_data["error"]
        if "permissão" in error_msg:
            raise HTTPException(status_code=403, detail=error_msg)
        if "não encontrado" in error_msg or "expirado" in error_msg:
            raise HTTPException(status_code=404, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    logger.info(
        "faq.execute.ok",
        plan_id=body.plan_id,
        session_id=body.session_id,
        user_id=user.user_id,
    )
    return FaqExecuteResponse(**result_data)


@router.get("/faq/plan/{plan_id}", response_model=_FaqPlan)
async def get_faq_plan(
    plan_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Recupera um plano FAQ salvo no Redis.
    TTL: 30 minutos após geração. Útil se o gestor fechar o painel antes de executar.
    """
    r = _FaqRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        raw = await r.get(f"nicodemus:faq_plan:{plan_id}")
    finally:
        await r.aclose()

    if not raw:
        raise HTTPException(
            status_code=404,
            detail="Plano expirado ou não encontrado. Gere um novo plano.",
        )

    stored = json.loads(raw)
    if stored.get("school_id") != user.school_id:
        raise HTTPException(
            status_code=403,
            detail="Sem permissão para aceder a este plano.",
        )

    return _FaqPlan(**stored["plan"])
