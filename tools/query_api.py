"""
tools/query_api.py — Busca dados da eleve-api para montagem de relatórios.

Recebe o plano gerado pelo report_agent e executa as chamadas necessárias.
Retorna lista normalizada de registros prontos para generate_xlsx/generate_docx.
"""
from __future__ import annotations

import json

import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient

logger = structlog.get_logger(__name__)


@tool
async def query_api(
    entity: str,
    filters: dict,
    sa_token: str,
    **kwargs,
) -> str:
    """
    Busca dados da eleve-api conforme entidade e filtros do plano do relatório.

    Args:
        entity:  Entidade do relatório: "inadimplencia" | "matriculas" |
            "solicitacoes" | "frequencia".
        filters: Dicionário de filtros (month, grade, status, year, etc.).
        sa_token: ServiceKey da escola.

    Returns:
        JSON string com { data: list[dict], total: int, entity: str }
    """
    try:
        async with DjangoAPIClient(token=sa_token) as client:
            data = await _fetch(client, entity, filters)

        logger.info("query_api.ok", entity=entity, total=len(data), filters=filters)
        return json.dumps({"data": data, "total": len(data), "entity": entity})

    except Exception as exc:
        logger.error("query_api.error", entity=entity, error=str(exc))
        return json.dumps({"data": [], "total": 0, "entity": entity, "error": str(exc)})


async def _fetch(client: DjangoAPIClient, entity: str, filters: dict) -> list[dict]:
    params = {k: v for k, v in filters.items() if v}

    if entity == "matriculas":
        result = await client.get("/api/v1/secretary/enrollments/", params=params)
        return _normalize(result)

    if entity == "solicitacoes":
        result = await client.get("/api/v1/requests/", params=params)
        return _normalize(result)

    if entity == "frequencia":
        params.update({"tipo": "declaracao"})
        result = await client.get("/api/v1/requests/", params=params)
        return _normalize(result)

    if entity == "inadimplencia":
        # Inadimplência requer busca por responsável — estratégia simplificada:
        # busca requests do tipo financeiro em atraso
        params.update({"request_type": "boleto_2via"})
        result = await client.get("/api/v1/requests/", params=params)
        return _normalize(result)

    return []


def _normalize(result: dict | list) -> list[dict]:
    """Garante sempre uma lista, independente do formato de retorno da API."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("results", [])
    return []
