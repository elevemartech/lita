"""agent/nico_agent.py — Agente conversacional ReAct para gestores escolares.

StateGraph: llm_node → should_use_tools → tool_node → llm_node (loop) | END

O LLM decide quando usar ferramentas (ReAct pattern).
O tool_node injeta sa_token e school_id em cada chamada de ferramenta.
As mensagens ficam em list[dict] (formato OpenAI) para serializar direto no Redis.
"""
from __future__ import annotations

import json

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from agent.state import NicoState
from agent.tools import TOOLS_REGISTRY
from core.settings import settings

logger = structlog.get_logger(__name__)

_llm = ChatOpenAI(
    model="gpt-4o-mini",
    api_key=settings.openai_api_key,
    temperature=0,
).bind_tools(TOOLS_REGISTRY)

_MAX_TOOL_ITERATIONS = 5

_FAQ_PRIMARY_TRIGGERS = {
    "faq", "pergunta", "base de conhecimento",
    "central de ajuda", "help center", "respostas",
    "dúvida", "dúvidas", "duplicada", "duplicado",
    "vazia", "sem resposta", "knowledge base",
}


def _build_system_prompt(user_name: str, role: str) -> str:
    return (
        f"Você é o Nicodemus ADM, copiloto de gestão escolar da plataforma Eleve.\n"
        f"Você está atendendo {user_name} ({role}).\n\n"
        "Você pode ajudar com:\n"
        "- Relatórios de inadimplência, matrículas e solicitações\n"
        "- Perguntas sobre gestão escolar em linguagem natural\n"
        "- Interpretação de dados e indicadores da escola\n"
        "- Gestão inteligente de FAQs da escola (analisar, organizar, criar, auditar)\n\n"
        "Para FAQs, use as ferramentas: analyze_faqs → build_faq_plan → (aguardar aprovação) → execute_faq_plan\n"
        "NUNCA execute_faq_plan sem confirmação explícita do gestor.\n\n"
        "Regras:\n"
        f"- Seja objetivo e profissional. Chame o gestor pelo nome ({user_name}).\n"
        "- Quando gerar um relatório, confirme o file_id retornado pela ferramenta.\n"
        "- Nunca invente dados — use apenas o que as ferramentas retornam.\n"
        "- Se não souber responder, diga claramente.\n"
        "- Responda sempre em português brasileiro.\n"
        "- Para FAQs: sempre mostre o plano ANTES de executar. Nunca execute sem aprovação.\n"
        "- Se uma ferramenta retornar erro, informe o gestor claramente e NÃO tente chamar a ferramenta novamente.\n"
    )


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """
    Remove mensagens role='tool' órfãs do histórico.

    A OpenAI exige que toda mensagem tool venha imediatamente após
    uma mensagem assistant que contenha tool_calls. Quando o histórico
    vem do Redis com turnos antigos, essa sequência pode estar quebrada
    causando BadRequestError 400.
    """
    valid_tc_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls", []):
                tc_id = tc.get("id", "")
                if tc_id:
                    valid_tc_ids.add(tc_id)

    sanitized = []
    for m in messages:
        if m.get("role") == "tool":
            if m.get("tool_call_id", "") not in valid_tc_ids:
                logger.warning(
                    "nico_agent.orphan_tool_msg_removed",
                    tool_call_id=m.get("tool_call_id"),
                    tool_name=m.get("tool_name"),
                )
                continue
        sanitized.append(m)

    surviving_tc_ids: set[str] = {
        m.get("tool_call_id", "")
        for m in sanitized
        if m.get("role") == "tool"
    }
    result = []
    for m in sanitized:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            has_response = any(
                tc.get("id", "") in surviving_tc_ids
                for tc in m.get("tool_calls", [])
            )
            if not has_response:
                logger.warning(
                    "nico_agent.assistant_tool_calls_removed",
                    tool_calls=[tc.get("name") for tc in m.get("tool_calls", [])],
                )
                if m.get("content"):
                    result.append({"role": "assistant", "content": m["content"]})
                continue
        result.append(m)

    return result


def _count_tool_iterations(messages: list[dict]) -> int:
    """Conta quantas rodadas de tool_calls ocorreram no turno actual."""
    count = 0
    for m in reversed(messages):
        if m.get("role") == "user":
            break
        if m.get("role") == "tool":
            count += 1
    return count


async def llm_node(state: NicoState) -> NicoState:
    """
    Chama gpt-4o-mini com as mensagens da sessão.
    Converte list[dict] → objetos LangChain para a chamada, e retorna de volta a dict.
    """
    user_name = state.get("user_name", "Gestor")
    role      = state.get("role", "manager")
    messages  = state.get("messages", [])

    # Sanitiza histórico antes de converter — remove tool msgs órfãs
    messages = _sanitize_messages(messages)

    # Converte dicts → LangChain messages
    lc_messages: list = [SystemMessage(content=_build_system_prompt(user_name, role))]
    for m in messages:
        r = m.get("role")
        c = m.get("content", "")
        if r == "user":
            lc_messages.append(HumanMessage(content=c))
        elif r == "assistant":
            raw_tcs = m.get("tool_calls", [])
            if raw_tcs:
                lc_tool_calls = [
                    ToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        args=json.loads(tc["function"]["arguments"]),
                    )
                    for tc in raw_tcs
                ]
                lc_messages.append(AIMessage(content=c, tool_calls=lc_tool_calls))
            else:
                lc_messages.append(AIMessage(content=c))
        elif r == "tool":
            lc_messages.append(
                ToolMessage(content=c, tool_call_id=m.get("tool_call_id", ""))
            )

    # Detecta intenção FAQ a partir da última mensagem do utilizador
    faq_keywords = {
        "analyze": ["analise", "diagnóstico", "diagnose", "analis"],
        "audit": ["duplicada", "duplicado", "vazia", "vazio", "sem resposta", "auditoria", "audit"],
        "organize": ["organiz", "categori", "agrupe", "reorganiz"],
        "create": ["crie", "cria", "nova faq", "adicione", "adiciona"],
        "edit": ["melhore", "melhora", "corrija", "corrige", "actualize", "atualize"],
        "bulk_clean": ["corrija todos", "limpe todas", "bulk", "em massa"],
    }
    last_user_msg = next(
        (m["content"].lower() for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    faq_intent = None
    if any(kw in last_user_msg for kws in faq_keywords.values() for kw in kws):
        if any(trigger in last_user_msg for trigger in _FAQ_PRIMARY_TRIGGERS):
            for intent, keywords in faq_keywords.items():
                if any(kw in last_user_msg for kw in keywords):
                    faq_intent = intent
                    break

    logger.info(
        "nico_agent.llm_call",
        session_id=state.get("session_id"),
        msg_count=len(lc_messages),
    )

    response = await _llm.ainvoke(lc_messages)

    # Extrai tool_calls se houver
    tool_calls = []
    if response.tool_calls:
        tool_calls = [
            {
                "id":        tc["id"],
                "name":      tc["name"],
                "arguments": tc["args"],
            }
            for tc in response.tool_calls
        ]

    # Append a resposta do assistente ao histórico
    assistant_msg: dict = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id":       tc["id"],
                "type":     "function",
                "function": {
                    "name":      tc["name"],
                    "arguments": json.dumps(tc["args"]),
                },
            }
            for tc in response.tool_calls
        ]
    new_messages = list(messages) + [assistant_msg]

    # Só reseta faq_plan se o LLM NÃO chamou build_faq_plan neste turno
    # (se chamou, o tool_node vai escrever o plano no estado a seguir)
    has_build_plan_call = any(
        tc.get("name") == "build_faq_plan" for tc in tool_calls
    )

    return {
        **state,
        "messages":          new_messages,
        "tool_calls":        tool_calls,
        "response":          response.content or "",
        "error":             None,
        "tool_error_counts": {},
        **({
            "faq_plan": None
        } if not has_build_plan_call and not state.get("faq_plan") else {}),
        **({
            "faq_intent": faq_intent
        } if faq_intent else {}),
    }


async def tool_node(state: NicoState) -> NicoState:
    """
    Executa os tool_calls do turno atual.
    Injeta sa_token e school_id em cada chamada e adiciona os resultados ao histórico.
    """
    tool_calls = state.get("tool_calls", [])
    sa_token   = state.get("sa_token", "")
    school_id  = state.get("school_id", "")
    messages   = list(state.get("messages", []))

    tool_map = {t.name: t for t in TOOLS_REGISTRY}

    for tc in tool_calls:
        name    = tc["name"]
        args    = dict(tc.get("arguments", {}))
        tc_id   = tc.get("id", "")
        tool_fn = tool_map.get(name)

        if not tool_fn:
            result_str = json.dumps({"error": f"Ferramenta '{name}' não encontrada."})
        else:
            args["sa_token"]  = sa_token
            args["school_id"] = school_id
            try:
                result_str = await tool_fn.ainvoke(args)
            except Exception as exc:
                logger.error("tool_node.error", tool=name, error=str(exc))
                result_str = json.dumps({"error": str(exc)})

        # Detetar se o resultado é um erro
        tool_error_counts = dict(state.get("tool_error_counts") or {})
        try:
            result_data = json.loads(result_str)
            if isinstance(result_data, dict) and "error" in result_data:
                tool_error_counts[name] = tool_error_counts.get(name, 0) + 1
            else:
                tool_error_counts[name] = 0
        except (json.JSONDecodeError, TypeError):
            tool_error_counts[name] = 0

        state = {**state, "tool_error_counts": tool_error_counts}

        # Circuit breaker — tool falhou 2x consecutivas → parar loop
        if tool_error_counts.get(name, 0) >= 2:
            logger.warning(
                "tool_node.circuit_breaker",
                tool=name,
                session_id=state.get("session_id"),
            )
            return {
                **state,
                "messages": messages,
                "tool_calls": [],
                "tool_error_counts": {},
                "response": "Não consegui acessar as informações necessárias no momento. Tente novamente em alguns instantes.",
                "error": f"circuit_breaker:{name}",
            }

        messages.append({
            "role":         "tool",
            "content":      result_str,
            "tool_call_id": tc_id,
            "tool_name":    name,
        })

        if name == "build_faq_plan":
            try:
                plan_data = json.loads(result_str)
                if "plan_id" in plan_data:
                    state = {**state, "faq_plan": plan_data}
            except (json.JSONDecodeError, KeyError):
                pass

        logger.info("tool_node.executed", tool=name, session_id=state.get("session_id"))

    return {**state, "messages": messages, "tool_calls": []}


def should_use_tools(state: NicoState) -> str:
    """
    Rota condicional: há tool_calls → tool_node; senão → END.
    Impõe limite máximo de iterações de tools por turno para evitar loops infinitos.
    """
    if not state.get("tool_calls"):
        return END

    iterations = _count_tool_iterations(state.get("messages", []))
    if iterations >= _MAX_TOOL_ITERATIONS:
        logger.warning(
            "nico_agent.max_tool_iterations_reached",
            iterations=iterations,
            session_id=state.get("session_id"),
        )
        return END

    return "tool_node"


def build_nico_graph() -> StateGraph:
    graph = StateGraph(NicoState)

    graph.add_node("llm_node",  llm_node)
    graph.add_node("tool_node", tool_node)

    graph.set_entry_point("llm_node")
    graph.add_conditional_edges(
        "llm_node",
        should_use_tools,
        {"tool_node": "tool_node", END: END},
    )
    graph.add_edge("tool_node", "llm_node")

    return graph.compile()


nico_graph = build_nico_graph()
