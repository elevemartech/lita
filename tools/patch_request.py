"""
tools/patch_request.py — Persiste dados confirmados na eleve-api após revisão humana.

Só é chamado via POST /doc/confirm — NUNCA diretamente pelo LLM sem aprovação.
Cria um Request na eleve-api, que gera protocolo e BoardCard via signal.
"""
from __future__ import annotations

import json

import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient
from schemas.doc_types import get_doc_type

logger = structlog.get_logger(__name__)


@tool
async def patch_request(
    doc_type: str,
    confirmed_fields: dict,
    sa_token: str,
    student_name: str = "",
    **kwargs,
) -> str:
    """
    Cria Request na eleve-api com os dados confirmados pela secretaria.

    Args:
        doc_type:         Tipo do documento — define o request_type.
        confirmed_fields: Campos revisados e aprovados pelo usuário.
        sa_token:         ServiceKey da escola.
        student_name:     Nome do aluno para descrição do protocolo.

    Returns:
        JSON string com { protocol: str, request_id: str } ou { error: str }
    """
    try:
        doc_def      = get_doc_type(doc_type)
        request_type = doc_def["eleve_request_type"]

        description = _build_description(doc_type, confirmed_fields, student_name)

        payload = {
            "request_type": request_type,
            "description":  description,
            "details":      confirmed_fields,
        }

        async with DjangoAPIClient(token=sa_token) as client:
            result = await client.post("/api/v1/requests/", json=payload)

        protocol   = result.get("protocol", "—")
        request_id = result.get("id", "—")

        logger.info("patch_request.ok", protocol=protocol, request_type=request_type)
        return json.dumps({"protocol": protocol, "request_id": request_id})

    except Exception as exc:
        logger.error("patch_request.error", doc_type=doc_type, error=str(exc))
        return json.dumps({"error": str(exc)})


def _build_description(doc_type: str, fields: dict, student_name: str) -> str:
    name = student_name or fields.get("student_name", "?")
    labels = {
        "comprovante_pagamento": (
            f"Comprovante de pagamento registrado pelo Nicodemus ADM. Aluno: {name}."
        ),
        "contrato_matricula": (
            f"Contrato de matrícula processado pelo Nicodemus ADM. Aluno: {name}."
        ),
        "boletim": f"Boletim escolar registrado pelo Nicodemus ADM. Aluno: {name}.",
    }
    return labels.get(doc_type, f"Documento processado pelo Nicodemus ADM ({doc_type}).")
