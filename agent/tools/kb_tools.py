"""
agent/tools/kb_tools.py — Ferramentas de busca semântica para o NicoAgent.

2 ferramentas expostas ao NicoAgent:
  search_knowledge_base → busca RAG na base de conhecimento interna da escola
  search_faqs_semantic  → busca semântica nas FAQs (embedding já existe na eleve-api)

Ambas fazem busca AO VIVO (sem cache) — a resposta reflete sempre o que está
indexado no momento, nunca um snapshot desatualizado.

O sa_token e school_id são SEMPRE injectados pelo tool_node a partir do estado.
"""
from __future__ import annotations

import json

import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient

logger = structlog.get_logger(__name__)


@tool
async def search_knowledge_base(
    query: str,
    top_k: int = 5,
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Busca semântica (RAG) na base de conhecimento interna da escola — regimento,
    calendário, políticas, cardápio, comunicados internos e demais documentos
    indexados. Use antes de responder perguntas factuais sobre regras, prazos
    ou informações da escola, em vez de responder de memória.
    Retorna JSON com os trechos mais relevantes e o nome do arquivo de origem.
    """
    try:
        async with DjangoAPIClient(token=sa_token) as client:
            result = await client.post(
                "/api/v1/knowledge-base/search/",
                json={"query": query, "top_k": top_k},
            )
    except Exception as exc:
        logger.error("search_knowledge_base.error", error=str(exc), school_id=school_id)
        return json.dumps({"error": str(exc)})

    logger.info(
        "search_knowledge_base.ok",
        query=query,
        total=result.get("total", 0),
        school_id=school_id,
    )
    return json.dumps(result)


@tool
async def search_faqs_semantic(
    query: str,
    top_k: int = 5,
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Busca semântica nas FAQs ativas da escola (por similaridade de significado,
    não só por palavra-chave). Use antes de responder perguntas que um
    responsável ou gestor faria no dia a dia (mensalidade, uniforme, horários,
    matrícula...), pra ancorar a resposta em FAQs reais em vez de inventar.
    Retorna JSON com as FAQs mais próximas semanticamente da pergunta.
    """
    try:
        async with DjangoAPIClient(token=sa_token) as client:
            result = await client.get(
                "/api/v1/faqs/semantic-search/",
                params={"q": query, "top_k": top_k},
            )
    except Exception as exc:
        logger.error("search_faqs_semantic.error", error=str(exc), school_id=school_id)
        return json.dumps({"error": str(exc)})

    logger.info(
        "search_faqs_semantic.ok",
        query=query,
        total=len(result.get("results", [])),
        school_id=school_id,
    )
    return json.dumps(result)
