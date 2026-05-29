"""
agent/state.py — Estado do agente conversacional ReAct (NicoAgent).

ATENÇÃO: Este módulo é DIFERENTE de agents/state.py (doc_reader + report_agent).
         Não importe os dois no mesmo arquivo — use o caminho completo para distinguir.

Este TypedDict é exclusivo para o fluxo conversacional com memória de sessão.
O campo `messages` é uma lista de dicts no formato OpenAI (serializável para JSON/Redis).
"""
from __future__ import annotations

from typing import TypedDict


class NicoState(TypedDict, total=False):
    # Identidade do gestor (injetado pelo router, nunca pelo LLM)
    user_id:      str
    school_id:    str
    sa_token:     str
    role:         str
    user_name:    str

    # Sessão
    session_id:   str

    # Conversa em formato OpenAI messages list
    # [{"role": "user"|"assistant"|"tool", "content": "..."}]
    messages:     list[dict]

    # Mensagem atual (antes de ser adicionada ao messages)
    user_message: str

    # Ciclo ReAct — tool calls do turno atual
    tool_calls:   list[dict]

    # Saída final do turno
    response:     str
    error:        str | None

    # ── faq_manager ──────────────────────────────────────────────────
    faq_intent:      str            # "analyze"|"organize"|"audit"|"create"|"edit"|"bulk_clean"
    faq_plan:        dict | None    # FaqPlan serializado (evita dependência circular)
    pending_actions: list[dict]     # acções aprovadas aguardando execução

    # ── circuit breaker ──────────────────────────────────────────────
    tool_error_counts: dict         # contador de falhas consecutivas por tool — reset a cada turno
