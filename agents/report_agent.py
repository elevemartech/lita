"""
agents/report_agent.py — Agente de geração de relatórios.

StateGraph:
  plan_node → query_node → build_node → done

  plan_node:  interpreta o prompt e monta o plano { entity, filters, columns, format }
  query_node: chama query_api com o plano
  build_node: chama generate_xlsx ou generate_docx conforme format
  done:       retorna file_id + summary
"""
from __future__ import annotations

import json

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from agents.state import NicoState
from core.settings import settings
from schemas.report_types import SUPPORTED_ENTITIES, SUPPORTED_FORMATS
from tools.generate_docx import generate_docx
from tools.generate_xlsx import generate_xlsx
from tools.query_api import query_api

logger = structlog.get_logger(__name__)

_llm = ChatOpenAI(
    model="gpt-4o-mini",
    api_key=settings.openai_api_key,
    temperature=0,
)

_PLAN_SYSTEM = f"""\
Você é o Nicodemus ADM, copiloto de gestão escolar.
Sua tarefa é interpretar um pedido de relatório em linguagem natural
e retornar um plano de execução estruturado.

Entidades disponíveis: {", ".join(SUPPORTED_ENTITIES)}
Formatos disponíveis: {", ".join(SUPPORTED_FORMATS)}

Responda APENAS com JSON válido, sem markdown:
{{
  "entity":  "<entidade>",
  "filters": {{"month": "<YYYY-MM>", "grade": "<série>", "status": "<status>", "year": "<YYYY>"}},
  "columns": ["<col1>", "<col2>"],
  "format":  "<xlsx|docx>",
  "title":   "<título do relatório>"
}}

Regras:
- Inclua apenas filtros mencionados no pedido (não invente)
- Se o formato não for mencionado, use "xlsx"
- Se a entidade não for clara, use "solicitacoes"
- Colunas devem ser relevantes para a entidade escolhida
"""


async def plan_node(state: NicoState) -> NicoState:
    """Interpreta o prompt e monta o plano de relatório."""
    logger.info("report_agent.plan", prompt=state.get("user_prompt", "")[:80])

    messages = [
        SystemMessage(content=_PLAN_SYSTEM),
        HumanMessage(content=state["user_prompt"]),
    ]

    try:
        response    = await _llm.ainvoke(messages)
        plan        = json.loads(response.content.strip())
        file_format = plan.get("format", "xlsx").lower()

        if file_format not in SUPPORTED_FORMATS:
            file_format = "xlsx"

        return {**state, "report_plan": plan, "file_format": file_format, "error": None}

    except Exception as exc:
        logger.error("report_agent.plan_error", error=str(exc))
        return {**state, "error": str(exc)}


async def query_node(state: NicoState) -> NicoState:
    """Busca dados na eleve-api conforme o plano."""
    if state.get("error"):
        return state

    plan    = state.get("report_plan", {})
    entity  = plan.get("entity", "solicitacoes")
    filters = plan.get("filters", {})

    logger.info("report_agent.query", entity=entity, filters=filters)

    result_str = await query_api.ainvoke({
        "entity":   entity,
        "filters":  filters,
        "sa_token": state["sa_token"],
    })

    result = json.loads(result_str)
    data   = result.get("data", [])
    total  = result.get("total", 0)

    summary = f"{total} registro(s) encontrado(s) para '{plan.get('title', entity)}'."
    if total == 0:
        summary = "Nenhum registro encontrado para os filtros aplicados."

    return {**state, "report_data": data, "summary": summary}


async def build_node(state: NicoState) -> NicoState:
    """Gera o arquivo .xlsx ou .docx com os dados."""
    if state.get("error"):
        return state

    plan       = state.get("report_plan", {})
    data       = state.get("report_data", [])
    fmt        = state.get("file_format", "xlsx")
    columns    = plan.get("columns", list(data[0].keys()) if data else [])
    title      = plan.get("title", "Relatório")

    logger.info("report_agent.build", format=fmt, rows=len(data))

    tool_input = {
        "data":        data,
        "columns":     columns,
        "title":       title,
        "school_id":   state["school_id"],
        "school_name": state.get("school_name", "Escola"),
        "sa_token":    state["sa_token"],
    }

    if fmt == "docx":
        result_str = await generate_docx.ainvoke(tool_input)
    else:
        result_str = await generate_xlsx.ainvoke(tool_input)

    result = json.loads(result_str)

    if "error" in result:
        return {**state, "error": result["error"]}

    return {**state, "file_id": result.get("file_id", "")}


def build_report_graph() -> StateGraph:
    graph = StateGraph(NicoState)
    graph.add_node("plan",  plan_node)
    graph.add_node("query", query_node)
    graph.add_node("build", build_node)

    graph.set_entry_point("plan")
    graph.add_edge("plan",  "query")
    graph.add_edge("query", "build")
    graph.add_edge("build", END)

    return graph.compile()


report_graph = build_report_graph()
