"""
tools/search_knowledge_base.py — Busca semântica na base de conhecimento da escola.

Consome POST /api/v1/knowledge-base/search/ da eleve-api via DjangoAPIClient.
O sa_token e school_id são injetados pelo tool_node a partir do estado da sessão
— nunca passados explicitamente pelo LLM.

Retorna os chunks mais relevantes formatados como contexto para o LLM responder
com base em documentos reais da escola (regimentos, circulares, políticas, etc.).
"""
from __future__ import annotations

import json

import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient

logger = structlog.get_logger(__name__)

_MAX_CHUNKS   = 5     # teto para não estourar o contexto do LLM
_SCORE_CUTOFF = 0.45  # cosine distance — abaixo disso o resultado é irrelevante


@tool
async def search_knowledge_base(
    query: str,
    top_k: int = 5,
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Busca semântica na base de conhecimento interno da escola.

    Use quando o gestor perguntar sobre documentos, políticas, procedimentos,
    regimentos, circulares, normas ou qualquer informação interna da escola
    que possa estar nos arquivos cadastrados.

    Exemplos de uso:
    - "qual é a política de uniforme?"
    - "como funciona o processo de matrícula?"
    - "quais documentos são necessários para transferência?"
    - "qual o regimento interno sobre faltas?"

    Parâmetros:
        query:  Pergunta ou trecho em linguagem natural para buscar.
        top_k:  Quantidade de resultados (1–5). Default: 5.
    """
    if not query or not query.strip():
        return json.dumps({"error": "query não pode estar vazia."})

    top_k = max(1, min(int(top_k), _MAX_CHUNKS))

    try:
        async with DjangoAPIClient(token=sa_token) as client:
            result = await client.post(
                "/api/v1/knowledge-base/search/",
                json={"query": query.strip(), "top_k": top_k},
            )
    except Exception as exc:
        logger.error("search_knowledge_base.api_error", error=str(exc), school_id=school_id)
        return json.dumps({"error": f"Falha ao consultar a base de conhecimento: {exc}"})

    raw_results: list[dict] = result.get("results", [])
    total: int = result.get("total", 0)

    if total == 0:
        logger.info("search_knowledge_base.no_results", query=query[:60], school_id=school_id)
        return json.dumps({
            "found": False,
            "message": (
                "Nenhum documento relevante encontrado na base de conhecimento "
                "da escola para essa consulta."
            ),
            "query": query,
        })

    # Filtra chunks com score ruim (cosine distance alta = baixa similaridade)
    relevant = [r for r in raw_results if r.get("score", 1.0) <= _SCORE_CUTOFF]

    if not relevant:
        logger.info(
            "search_knowledge_base.low_relevance",
            query=query[:60],
            total=total,
            school_id=school_id,
        )
        return json.dumps({
            "found": False,
            "message": (
                "Encontrei documentos na base de conhecimento, mas nenhum com relevância "
                "suficiente para responder a essa pergunta com segurança."
            ),
            "query": query,
        })

    # Formata os chunks como contexto legível
    context_blocks = []
    for i, chunk in enumerate(relevant, start=1):
        block = (
            f"[{i}] Arquivo: {chunk.get('file_name', '?')} "
            f"(categoria: {chunk.get('category', '?')}, "
            f"relevância: {round(1 - chunk.get('score', 0), 2):.0%})\n"
            f"{chunk.get('content', '')}"
        )
        context_blocks.append(block)

    context_text = "\n\n---\n\n".join(context_blocks)

    logger.info(
        "search_knowledge_base.ok",
        query=query[:60],
        returned=len(relevant),
        school_id=school_id,
    )

    return json.dumps({
        "found": True,
        "total_found": len(relevant),
        "context": context_text,
        "instruction": (
            "Use o contexto acima para responder ao gestor. "
            "Cite o nome do arquivo quando relevante. "
            "Se o contexto não for suficiente para responder com precisão, diga isso claramente."
        ),
    })
