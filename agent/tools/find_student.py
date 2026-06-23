"""
agent/tools/find_student.py — Busca de alunos por nome na eleve-api.
"""
from __future__ import annotations

import json

import httpx
import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient

logger = structlog.get_logger(__name__)


@tool
async def find_student(
    name: str,
    grade: str = "",
    sa_token: str = "",
    school_id: str = "",
    **kwargs,
) -> str:
    """
    Busca um aluno pelo nome (e opcionalmente série/turma).
    Use SEMPRE antes de criar solicitações ou atualizar cadastros,
    para garantir que o aluno existe e obter seu ID correto.

    Parâmetros:
        name:  Nome completo ou parcial do aluno.
        grade: Série/turma para filtrar (opcional). Ex: "7A", "3° ano".
    """
    params: dict = {"search": name, "page_size": 5}
    if grade:
        params["grade"] = grade

    try:
        async with DjangoAPIClient(token=sa_token) as client:
            result = await client.get("/api/v1/secretary/enrollments/", params=params)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "find_student.http_error",
            status_code=exc.response.status_code,
            body=exc.response.text,
            name=name,
        )
        return json.dumps({"error": f"Erro ao buscar aluno: HTTP {exc.response.status_code}"})
    except Exception as exc:
        logger.error("find_student.error", error=str(exc), name=name)
        return json.dumps({"error": str(exc)})

    students = [
        {
            "id": e.get("id", ""),
            "student_name": e.get("student_name", e.get("nome", "")),
            "grade": e.get("grade", e.get("serie", "")),
            "guardian_name": e.get("guardian_name", ""),
            "guardian_phone": e.get("guardian_phone", ""),
            "enrollment_status": e.get("enrollment_status", e.get("status", "")),
            "siga_user_id": e.get("siga_user_id", ""),
        }
        for e in result.get("results", [])
    ]
    total = result.get("count", len(students))

    logger.info("find_student.done", name=name, total=total)

    if total == 0:
        return json.dumps({"found": False, "message": "Nenhum aluno encontrado com esse nome."})

    payload: dict = {"found": True, "total": total, "students": students}
    if total > 1:
        payload["instruction"] = (
            "Múltiplos alunos encontrados. Peça ao gestor para confirmar qual."
        )
    return json.dumps(payload)
