"""
agent/tools/update_student_record.py — Atualização de dados cadastrais de alunos.
"""
from __future__ import annotations

import json

import httpx
import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient

logger = structlog.get_logger(__name__)

_ALLOWED_FIELDS = {
    "guardian_phone",
    "guardian_email",
    "guardian_name",
    "address",
    "address_complement",
    "address_number",
    "zip_code",
    "city",
    "state",
    "emergency_contact",
    "emergency_phone",
    "health_notes",
    "transport_notes",
}


@tool
async def update_student_record(
    student_id: str,
    student_name: str,
    fields: dict,
    dry_run: bool = True,
    sa_token: str = "",
    school_id: str = "",
    **kwargs,
) -> str:
    """
    Atualiza dados cadastrais de um aluno no sistema.

    IMPORTANTE: Sempre chame primeiro com dry_run=True para mostrar o diff
    ao gestor. Só execute com dry_run=False após confirmação explícita.

    Campos que podem ser atualizados:
    guardian_phone, guardian_email, guardian_name, address, address_complement,
    address_number, zip_code, city, state, emergency_contact, emergency_phone,
    health_notes, transport_notes.

    Parâmetros:
        student_id:   UUID do enrollment (use find_student para obter).
        student_name: Nome do aluno (para confirmação legível).
        fields:       Dict com os campos a atualizar e os novos valores.
        dry_run:      True = preview apenas. False = executa o PATCH.
    """
    invalid = [f for f in fields if f not in _ALLOWED_FIELDS]
    if invalid:
        return json.dumps({
            "error": f"Campos não permitidos: {invalid}",
            "allowed_fields": sorted(_ALLOWED_FIELDS),
        })

    valid_fields = {k: v for k, v in fields.items() if k in _ALLOWED_FIELDS}

    if not valid_fields:
        return json.dumps({"error": "Nenhum campo válido para atualizar."})

    if dry_run:
        try:
            async with DjangoAPIClient(token=sa_token) as client:
                current = await client.get(f"/api/v1/secretary/enrollments/{student_id}/")
        except httpx.HTTPStatusError as exc:
            logger.error(
                "update_student_record.fetch_error",
                status_code=exc.response.status_code,
                body=exc.response.text,
                student_id=student_id,
            )
            return json.dumps({
                "error": f"Erro ao buscar cadastro atual: HTTP {exc.response.status_code}"
            })
        except Exception as exc:
            logger.error("update_student_record.fetch_error", error=str(exc), student_id=student_id)
            return json.dumps({"error": str(exc)})

        diff = {
            field: {"de": current.get(field, "?"), "para": value}
            for field, value in valid_fields.items()
        }
        return json.dumps({
            "preview": True,
            "student_name": student_name,
            "changes": diff,
            "instruction": "Confirme as alterações acima para prosseguir.",
        })

    try:
        async with DjangoAPIClient(token=sa_token) as client:
            await client.patch(
                f"/api/v1/secretary/enrollments/{student_id}/",
                json=valid_fields,
            )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "update_student_record.patch_error",
            status_code=exc.response.status_code,
            body=exc.response.text,
            student_id=student_id,
        )
        return json.dumps({
            "error": f"Erro ao atualizar cadastro: HTTP {exc.response.status_code}",
            "detail": exc.response.text,
        })
    except Exception as exc:
        logger.error("update_student_record.patch_error", error=str(exc), student_id=student_id)
        return json.dumps({"error": str(exc)})

    logger.info("update_student_record.done", student_id=student_id, fields=list(valid_fields))
    return json.dumps({
        "success": True,
        "student_name": student_name,
        "updated_fields": list(valid_fields.keys()),
        "message": f"Cadastro de {student_name} atualizado com sucesso.",
    })
