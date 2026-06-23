"""
agent/tools/__init__.py — Ferramentas do NicoAgent (agente conversacional ReAct).

Estas wrappers chamam as tools existentes em tools/ via .ainvoke() sem modificá-las.
O sa_token e school_id são injetados pelo tool_node a partir do estado da sessão.

TOOLS_REGISTRY é a lista de ferramentas expostas ao LLM via bind_tools().
"""
from __future__ import annotations

import json

from langchain_core.tools import tool

# Importa as tools existentes — NÃO modifica seus módulos
from tools.generate_docx import generate_docx as _generate_docx
from tools.generate_xlsx import generate_xlsx as _generate_xlsx
from tools.query_api import query_api as _query_api
from agent.tools.faq_tools import analyze_faqs, build_faq_plan, execute_faq_plan, list_faqs
from agent.tools.get_school_summary import get_school_summary
from agent.tools.find_student import find_student
from agent.tools.create_secretary_request import create_secretary_request
from agent.tools.update_student_record import update_student_record


@tool
async def generate_financial_report(
    format: str = "xlsx",
    month: str = "",
    grade: str = "",
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Gera relatório de inadimplência financeira (alunos com mensalidades em atraso).
    Use quando o gestor perguntar sobre inadimplentes, atrasos, débitos, mensalidades vencidas.
    Parâmetros opcionais: format (xlsx|docx), month (YYYY-MM), grade (série/ano escolar).
    """
    filters: dict = {}
    if month:
        filters["month"] = month
    if grade:
        filters["grade"] = grade

    data_str = await _query_api.ainvoke({
        "entity": "inadimplencia",
        "filters": filters,
        "sa_token": sa_token,
    })
    data_result = json.loads(data_str)
    data = data_result.get("data", [])

    columns = ["student_name", "grade", "guardian_name", "amount", "due_date", "days_overdue"]
    file_input = {
        "data": data,
        "columns": columns,
        "title": "Relatório de Inadimplência",
        "school_id": school_id,
        "school_name": "Escola",
        "sa_token": sa_token,
    }

    if format.lower() == "docx":
        return await _generate_docx.ainvoke(file_input)
    return await _generate_xlsx.ainvoke(file_input)


@tool
async def generate_enrollments_report(
    format: str = "xlsx",
    year: str = "",
    grade: str = "",
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Gera relatório de matrículas (novas matrículas, alunos matriculados, rematrículas).
    Use quando o gestor perguntar sobre matrículas, novos alunos, enrollment, vagas preenchidas.
    Parâmetros opcionais: format (xlsx|docx), year (YYYY), grade (série escolar).
    """
    filters: dict = {}
    if year:
        filters["year"] = year
    if grade:
        filters["grade"] = grade

    data_str = await _query_api.ainvoke({
        "entity": "matriculas",
        "filters": filters,
        "sa_token": sa_token,
    })
    data_result = json.loads(data_str)
    data = data_result.get("data", [])

    columns = ["student_name", "grade", "guardian_name", "enrollment_date", "status", "monthly_fee"]
    file_input = {
        "data": data,
        "columns": columns,
        "title": "Relatório de Matrículas",
        "school_id": school_id,
        "school_name": "Escola",
        "sa_token": sa_token,
    }

    if format.lower() == "docx":
        return await _generate_docx.ainvoke(file_input)
    return await _generate_xlsx.ainvoke(file_input)


@tool
async def generate_requests_report(
    format: str = "xlsx",
    status: str = "",
    month: str = "",
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Gera relatório de solicitações abertas na secretaria (protocolos, documentos, pedidos).
    Use quando o gestor perguntar sobre solicitações, protocolos, secretaria, chamados abertos.
    Parâmetros opcionais: format (xlsx|docx), status (pendente/concluido), month (YYYY-MM).
    """
    filters: dict = {}
    if status:
        filters["status"] = status
    if month:
        filters["month"] = month

    data_str = await _query_api.ainvoke({
        "entity": "solicitacoes",
        "filters": filters,
        "sa_token": sa_token,
    })
    data_result = json.loads(data_str)
    data = data_result.get("data", [])

    columns = ["protocol", "request_type", "student_name", "guardian_name", "created_at", "status"]
    file_input = {
        "data": data,
        "columns": columns,
        "title": "Relatório de Solicitações",
        "school_id": school_id,
        "school_name": "Escola",
        "sa_token": sa_token,
    }

    if format.lower() == "docx":
        return await _generate_docx.ainvoke(file_input)
    return await _generate_xlsx.ainvoke(file_input)


TOOLS_REGISTRY = [
    generate_financial_report,
    generate_enrollments_report,
    generate_requests_report,
    list_faqs,
    analyze_faqs,
    build_faq_plan,
    execute_faq_plan,
    get_school_summary,
    find_student,
    create_secretary_request,
    update_student_record,
]
