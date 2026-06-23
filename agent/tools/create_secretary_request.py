"""
agent/tools/create_secretary_request.py — Criação de solicitações de secretaria.
"""
from __future__ import annotations

import json

import httpx
import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient

logger = structlog.get_logger(__name__)

_REQUEST_TYPES = {
    "boleto_2via",
    "negociacao",
    "comprovante_pagamento",
    "atendimento_financeiro",
    "cancelamento",
    "rematricula",
    "matricula",
    "outros",
}

_DOCUMENT_TYPES = {
    "declaracao",
    "declaracao_matricula",
    "declaracao_frequencia",
    "declaracao_conclusao",
    "historico",
    "transferencia",
    "boletim",
}

_ALL_TYPES = _REQUEST_TYPES | _DOCUMENT_TYPES


@tool
async def create_secretary_request(
    request_type: str,
    student_name: str,
    description: str,
    siga_user_id: str = "",
    student_id: str = "",
    details: dict = {},
    dry_run: bool = True,
    sa_token: str = "",
    school_id: str = "",
    **kwargs,
) -> str:
    """
    Cria uma solicitação de secretaria para um aluno.

    IMPORTANTE: Sempre chame primeiro com dry_run=True para mostrar o preview
    ao gestor. Só execute com dry_run=False após confirmação explícita.

    Tipos de solicitação disponíveis:
    - Documentos: declaracao, declaracao_matricula, declaracao_frequencia,
      declaracao_conclusao, historico, transferencia, boletim
    - Secretaria: boleto_2via, negociacao, comprovante_pagamento,
      atendimento_financeiro, cancelamento, rematricula, matricula, outros

    Parâmetros:
        request_type:  Tipo da solicitação (ver lista acima).
        student_name:  Nome completo do aluno.
        description:   Descrição da solicitação em linguagem natural.
        siga_user_id:  ID do aluno no SIGA (use find_student para obter).
        student_id:    UUID do enrollment (use find_student para obter).
        details:       Campos adicionais específicos do tipo.
        dry_run:       True = preview apenas. False = executa a criação.
    """
    if request_type not in _ALL_TYPES:
        return json.dumps({
            "error": f"Tipo '{request_type}' inválido.",
            "valid_document_types": sorted(_DOCUMENT_TYPES),
            "valid_request_types": sorted(_REQUEST_TYPES),
        })

    destination = "secretary/documents" if request_type in _DOCUMENT_TYPES else "requests"

    if dry_run:
        return json.dumps({
            "preview": True,
            "action": "criar solicitação",
            "request_type": request_type,
            "student_name": student_name,
            "description": description,
            "destination": destination,
            "instruction": (
                "Preview da solicitação acima. "
                "Chame novamente com dry_run=False para confirmar a criação."
            ),
        })

    try:
        async with DjangoAPIClient(token=sa_token) as client:
            if request_type in _DOCUMENT_TYPES:
                payload = {
                    "document_type": request_type,
                    "student_name": student_name,
                    "siga_user_id": siga_user_id,
                    "student_id": student_id,
                    "description": description,
                    **details,
                }
                result = await client.post("/api/v1/secretary/documents/", json=payload)
                protocol = result.get("protocol", result.get("id", "?"))
                return json.dumps({
                    "success": True,
                    "protocol": protocol,
                    "type": "document",
                    "message": f"Solicitação criada com sucesso. Protocolo: {protocol}",
                })
            else:
                payload = {
                    "request_type": request_type,
                    "description": description,
                    "siga_user_id": siga_user_id,
                    "details": {"student_name": student_name, **details},
                }
                result = await client.post("/api/v1/requests/", json=payload)
                protocol = result.get("protocol", "?")
                return json.dumps({
                    "success": True,
                    "protocol": protocol,
                    "type": "request",
                    "message": f"Solicitação criada. Protocolo: {protocol}",
                })

    except httpx.HTTPStatusError as exc:
        logger.error(
            "create_secretary_request.http_error",
            status_code=exc.response.status_code,
            body=exc.response.text,
            request_type=request_type,
        )
        return json.dumps({
            "error": f"Erro ao criar solicitação: HTTP {exc.response.status_code}",
            "detail": exc.response.text,
        })
    except Exception as exc:
        logger.error("create_secretary_request.error", error=str(exc), request_type=request_type)
        return json.dumps({"error": str(exc)})
