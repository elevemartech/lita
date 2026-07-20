"""
tests/test_report_agent.py — Testes do fluxo de geração de relatórios.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.report_agent import report_graph
from agents.state import NicoState

FAKE_SA_TOKEN = "sa_live_test_abc123"

FAKE_INVOICE_DATA = [
    {"student_name": "Ana Lima",   "grade": "7A", "amount": 1200.0, "due_date": "2025-04-10"},
    {"student_name": "Bruno Melo", "grade": "8B", "amount":  950.0, "due_date": "2025-04-05"},
]


@pytest.fixture
def base_state() -> NicoState:
    return {
        "user_id":   "user-001",
        "school_id": "school-001",
        "sa_token":  FAKE_SA_TOKEN,
        "role":      "director",
        "user_prompt": "Relatório de inadimplência de abril em Excel",
        "file_format": "xlsx",
    }


def _mock_llm_plan(entity="inadimplencia", fmt="xlsx") -> MagicMock:
    plan = {
        "entity":  entity,
        "filters": {"month": "2025-04"},
        "columns": ["student_name", "grade", "amount", "due_date"],
        "format":  fmt,
        "title":   "Inadimplência - Abril 2025",
    }
    mock_response      = MagicMock()
    mock_response.content = json.dumps(plan)
    return mock_response


# ── Testes ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gera_xlsx_com_dados(base_state):
    """Deve gerar file_id quando há dados disponíveis."""
    with patch("agents.report_agent._llm") as mock_llm, \
         patch("tools.query_api.DjangoAPIClient") as mock_api, \
         patch("tools.generate_xlsx.save_file", return_value="file-001"):

        mock_llm.ainvoke = AsyncMock(return_value=_mock_llm_plan("inadimplencia", "xlsx"))

        mock_api_instance = AsyncMock()
        mock_api_instance.__aenter__ = AsyncMock(return_value=mock_api_instance)
        mock_api_instance.__aexit__  = AsyncMock(return_value=None)
        mock_api_instance.get        = AsyncMock(return_value={"results": FAKE_INVOICE_DATA})
        mock_api.return_value        = mock_api_instance

        result: NicoState = await report_graph.ainvoke(base_state)

    assert result.get("error") is None
    assert result.get("file_id") == "file-001"
    assert len(result.get("report_data", [])) == 2


@pytest.mark.asyncio
async def test_gera_docx_quando_solicitado(base_state):
    """Deve chamar generate_docx quando formato é docx."""
    state = {**base_state, "file_format": "docx", "user_prompt": "Relatório de matrículas em Word"}

    with patch("agents.report_agent._llm") as mock_llm, \
         patch("tools.query_api.DjangoAPIClient") as mock_api, \
         patch("tools.generate_docx.save_file", return_value="file-docx-001"):

        mock_llm.ainvoke = AsyncMock(return_value=_mock_llm_plan("matriculas", "docx"))

        mock_api_instance = AsyncMock()
        mock_api_instance.__aenter__ = AsyncMock(return_value=mock_api_instance)
        mock_api_instance.__aexit__  = AsyncMock(return_value=None)
        mock_api_instance.get        = AsyncMock(return_value={"results": FAKE_INVOICE_DATA})
        mock_api.return_value        = mock_api_instance

        result: NicoState = await report_graph.ainvoke(state)

    assert result.get("file_id") == "file-docx-001"


@pytest.mark.asyncio
async def test_relatorio_vazio_sem_erro(base_state):
    """Relatório sem dados deve retornar summary adequado, não erro."""
    with patch("agents.report_agent._llm") as mock_llm, \
         patch("tools.query_api.DjangoAPIClient") as mock_api, \
         patch("tools.generate_xlsx.save_file", return_value="file-empty"):

        mock_llm.ainvoke = AsyncMock(return_value=_mock_llm_plan())

        mock_api_instance = AsyncMock()
        mock_api_instance.__aenter__ = AsyncMock(return_value=mock_api_instance)
        mock_api_instance.__aexit__  = AsyncMock(return_value=None)
        mock_api_instance.get        = AsyncMock(return_value={"results": []})
        mock_api.return_value        = mock_api_instance

        result: NicoState = await report_graph.ainvoke(base_state)

    assert result.get("error") is None
    assert "Nenhum registro" in result.get("summary", "")


@pytest.mark.asyncio
async def test_summary_contem_total(base_state):
    """Summary deve indicar o total de registros encontrados."""
    with patch("agents.report_agent._llm") as mock_llm, \
         patch("tools.query_api.DjangoAPIClient") as mock_api, \
         patch("tools.generate_xlsx.save_file", return_value="file-002"):

        mock_llm.ainvoke = AsyncMock(return_value=_mock_llm_plan())

        mock_api_instance = AsyncMock()
        mock_api_instance.__aenter__ = AsyncMock(return_value=mock_api_instance)
        mock_api_instance.__aexit__  = AsyncMock(return_value=None)
        mock_api_instance.get        = AsyncMock(return_value={"results": FAKE_INVOICE_DATA})
        mock_api.return_value        = mock_api_instance

        result: NicoState = await report_graph.ainvoke(base_state)

    assert "2" in result.get("summary", "")
