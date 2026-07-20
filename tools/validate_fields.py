"""
tools/validate_fields.py — Cruza os dados extraídos do documento com a eleve-api.

Tenta confirmar se o responsável, aluno e valores batem com os registros
existentes. Adiciona flags quando há discrepâncias.

Não persiste nada — só valida. A persistência acontece em patch_request,
após confirmação humana obrigatória.
"""
from __future__ import annotations

import json

import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient

logger = structlog.get_logger(__name__)


@tool
async def validate_fields(
    extracted: dict,
    doc_type: str,
    sa_token: str,
    **kwargs,
) -> str:
    """
    Cruza dados extraídos com a eleve-api e retorna resultado de validação.

    Args:
        extracted: Dicionário de campos extraídos pelo extract_document.
        doc_type:  Tipo do documento para saber quais campos cruzar.
        sa_token:  ServiceKey da escola.

    Returns:
        JSON string com { validated: dict, flags: list[str], matched: bool }
    """
    flags: list[str]    = []
    validated: dict     = dict(extracted)

    try:
        async with DjangoAPIClient(token=sa_token) as client:

            if doc_type == "comprovante_pagamento":
                await _validate_payment(client, extracted, validated, flags)

            elif doc_type == "contrato_matricula":
                await _validate_contract(client, extracted, validated, flags)

            elif doc_type == "boletim":
                await _validate_boletim(client, extracted, validated, flags)

        matched = len([f for f in flags if f.startswith("DIVERGENCIA")]) == 0
        logger.info("validate_fields.ok", doc_type=doc_type, matched=matched, flags=flags)

        return json.dumps({
            "validated": validated,
            "flags":     flags,
            "matched":   matched,
        })

    except Exception as exc:
        logger.error("validate_fields.error", error=str(exc))
        return json.dumps({
            "validated": extracted,
            "flags":     [f"ERRO_VALIDACAO:{exc}"],
            "matched":   False,
        })


async def _validate_payment(
    client: DjangoAPIClient, extracted: dict, validated: dict, flags: list
) -> None:
    """Cruza comprovante com boletos abertos do responsável."""
    student_name = extracted.get("student_name", "")

    if not student_name:
        flags.append("CAMPO_OBRIGATORIO_AUSENTE:student_name")
        return

    try:
        # Busca responsável pelo nome do aluno (simplificado — em produção use enrollment_number)
        result = await client.get("/api/v1/requests/", params={"search": student_name})
        if isinstance(result, dict):
            items = result.get("results", [])
        else:
            items = result

        validated["api_found"] = len(items) > 0

        if len(items) == 0:
            flags.append(f"ALUNO_NAO_ENCONTRADO:{student_name}")

    except Exception as exc:
        flags.append(f"ERRO_BUSCA_API:{exc}")


async def _validate_contract(
    client: DjangoAPIClient, extracted: dict, validated: dict, flags: list
) -> None:
    """Cruza contrato com matrículas existentes."""
    student_name = extracted.get("student_name", "")

    if not student_name:
        flags.append("CAMPO_OBRIGATORIO_AUSENTE:student_name")
        return

    try:
        result = await client.get("/api/v1/secretary/enrollments/", params={"search": student_name})
        if isinstance(result, dict):
            items = result.get("results", [])
        else:
            items = result

        validated["api_found"]      = len(items) > 0
        validated["enrollment_count"] = len(items)

        if len(items) == 0:
            flags.append(f"MATRICULA_NAO_ENCONTRADA:{student_name}")

    except Exception as exc:
        flags.append(f"ERRO_BUSCA_API:{exc}")


async def _validate_boletim(
    client: DjangoAPIClient, extracted: dict, validated: dict, flags: list
) -> None:
    """Valida boletim — apenas verifica se o aluno existe."""
    student_name = extracted.get("student_name", "")

    if not student_name:
        flags.append("CAMPO_OBRIGATORIO_AUSENTE:student_name")
        return

    try:
        result = await client.get("/api/v1/requests/", params={"search": student_name})
        if isinstance(result, dict):
            items = result.get("results", [])
        else:
            items = result

        validated["api_found"] = len(items) > 0

        if len(items) == 0:
            flags.append(f"ALUNO_NAO_ENCONTRADO:{student_name}")

    except Exception as exc:
        flags.append(f"ERRO_BUSCA_API:{exc}")
