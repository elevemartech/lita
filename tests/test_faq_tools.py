"""
tests/test_faq_tools.py — Testes unitários do módulo FAQ Manager.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.tools.analyzers.faq_analyzer import FaqAnalyzer
from schemas.faq_schemas import FaqItem

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_faq(**kwargs) -> FaqItem:
    """Cria FaqItem com valores padrão substituíveis."""
    defaults = {
        "id": 1,
        "question": "Qual o horário de funcionamento?",
        "answer": "Funcionamos de segunda a sexta das 7h às 17h.",
        "category": "Vida Escolar",
        "status": "active",
        "updated_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    return FaqItem(**defaults)


@pytest.fixture
def clean_faqs() -> list[FaqItem]:
    """FAQs sem nenhum problema detectável."""
    return [
        make_faq(
            id=1,
            question="Qual o horário de entrada?",
            answer="O portão abre às 7h da manhã para os alunos.",
            category="Vida Escolar",
        ),
        make_faq(
            id=2,
            question="Como faço a matrícula?",
            answer="Entre em contato com a secretaria para iniciar o processo.",
            category="Matrículas",
        ),
        make_faq(
            id=3,
            question="Quando vence o boleto?",
            answer="Os boletos vencem todo dia 10 do mês corrente.",
            category="Financeiro",
        ),
    ]


# ── FaqAnalyzer — empty_answer ────────────────────────────────────────────────


def test_empty_answer_detected():
    """FAQ com answer vazio deve gerar issue empty_answer."""
    faqs = [make_faq(id=1, answer="")]
    result = FaqAnalyzer().analyze(faqs)
    assert any(i.issue_type == "empty_answer" and i.faq_id == 1 for i in result.issues)
    assert result.empty_count == 1


def test_no_issues_on_clean_faqs(clean_faqs):
    """Lista de FAQs correctas não deve gerar issues de empty/stale/quality."""
    result = FaqAnalyzer().analyze(clean_faqs)
    assert result.empty_count == 0
    assert result.stale_count == 0
    assert not any(
        i.issue_type in ("empty_answer", "stale", "quality") for i in result.issues
    )


# ── FaqAnalyzer — duplicates ──────────────────────────────────────────────────


def test_duplicate_detected():
    """Duas FAQs com perguntas muito similares devem ser agrupadas como duplicatas."""
    faqs = [
        make_faq(id=1, question="Qual o horário de entrada da escola hoje?"),
        make_faq(id=2, question="Qual o horário de entrada da escola amanhã?"),
    ]
    result = FaqAnalyzer().analyze(faqs)
    assert len(result.duplicate_groups) >= 1
    all_grouped_ids = [fid for g in result.duplicate_groups for fid in g]
    assert 1 in all_grouped_ids and 2 in all_grouped_ids


def test_not_duplicate_when_different():
    """FAQs sobre temas distintos não devem ser marcadas como duplicatas."""
    faqs = [
        make_faq(id=1, question="Qual o horário de entrada?"),
        make_faq(id=2, question="Como pago a mensalidade escolar?"),
    ]
    result = FaqAnalyzer().analyze(faqs)
    assert len(result.duplicate_groups) == 0


# ── FaqAnalyzer — stale ───────────────────────────────────────────────────────


def test_stale_detected():
    """FAQ com updated_at > 90 dias deve gerar issue stale."""
    old = datetime.now(UTC) - timedelta(days=120)
    faqs = [
        make_faq(
            id=1,
            updated_at=old,
            answer="Resposta longa o suficiente para não ser quality issue",
        )
    ]
    result = FaqAnalyzer().analyze(faqs)
    assert any(i.issue_type == "stale" and i.faq_id == 1 for i in result.issues)
    assert result.stale_count == 1


def test_stale_not_triggered_when_recent():
    """FAQ actualizada ontem não deve ter issue stale."""
    recent = datetime.now(UTC) - timedelta(days=1)
    faqs = [make_faq(id=1, updated_at=recent)]
    result = FaqAnalyzer().analyze(faqs)
    assert not any(i.issue_type == "stale" for i in result.issues)
    assert result.stale_count == 0


def test_stale_not_triggered_when_updated_at_none():
    """FAQ sem updated_at não deve gerar issue stale — dado em falta não penaliza."""
    faqs = [make_faq(id=1, updated_at=None)]
    result = FaqAnalyzer().analyze(faqs)
    assert not any(i.issue_type == "stale" for i in result.issues)


# ── FaqAnalyzer — wrong_category ─────────────────────────────────────────────


def test_wrong_category_detected():
    """FAQ sobre mensalidade e boleto em 'Vida Escolar' deve gerar wrong_category."""
    faqs = [
        make_faq(
            id=1,
            question="Qual o valor da mensalidade e como pago o boleto?",
            category="Vida Escolar",
        )
    ]
    result = FaqAnalyzer().analyze(faqs)
    assert any(i.issue_type == "wrong_category" and i.faq_id == 1 for i in result.issues)


# ── FaqAnalyzer — coverage_gaps ──────────────────────────────────────────────


def test_coverage_gap_detected():
    """Categorias sem nenhuma FAQ activa devem aparecer em coverage_gaps."""
    faqs = [
        make_faq(id=1, category="Vida Escolar"),
        make_faq(id=2, category="Financeiro"),
    ]
    result = FaqAnalyzer().analyze(faqs)
    assert "Matrículas" in result.coverage_gaps
    assert "Transporte" in result.coverage_gaps
    assert "Vida Escolar" not in result.coverage_gaps
    assert "Financeiro" not in result.coverage_gaps


def test_no_coverage_gap_when_all_categories_covered():
    """Categorias cobertas não devem aparecer em coverage_gaps."""
    categories = FaqAnalyzer.VALID_CATEGORIES
    faqs = [
        make_faq(id=i + 1, category=cat)
        for i, cat in enumerate(categories)
    ]
    result = FaqAnalyzer().analyze(faqs)
    assert result.coverage_gaps == []


# ── Ferramentas com mocks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_faqs_success():
    """list_faqs deve retornar JSON com lista e total quando a API responde 200."""
    from agent.tools.faq_tools import list_faqs

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "id": 1,
                "question": "Pergunta 1",
                "answer": "Resposta 1",
                "category": "Financeiro",
                "status": "active",
            }
        ],
        "next": None,
    }

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("agent.tools.faq_tools._get_redis", return_value=mock_redis), \
         patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        result = await list_faqs.ainvoke({"sa_token": "tok", "school_id": "sch1"})

    data = json.loads(result)
    assert "faqs" in data
    assert data["total"] == 1
    assert data["faqs"][0]["id"] == 1


@pytest.mark.asyncio
async def test_list_faqs_api_error():
    """Erro HTTP da API deve retornar JSON com error sem lançar excepção."""
    from agent.tools.faq_tools import list_faqs

    # Resposta httpx real (não MagicMock) — raise_for_status() precisa
    # levantar de verdade para exercitar o except em list_faqs.
    mock_request = httpx.Request("GET", "http://test/api/v1/faqs/")
    mock_resp = httpx.Response(500, request=mock_request, text="Internal Server Error")

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.aclose = AsyncMock()

    with patch("agent.tools.faq_tools._get_redis", return_value=mock_redis), \
         patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        result = await list_faqs.ainvoke({"sa_token": "tok", "school_id": "sch1"})

    data = json.loads(result)
    assert "error" in data
    assert "500" in data["error"]


@pytest.mark.asyncio
async def test_list_faqs_cache_hit():
    """Cache hit no Redis deve retornar imediatamente sem chamar a API."""
    from agent.tools.faq_tools import list_faqs

    cached_payload = json.dumps({"faqs": [], "total": 0})

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=cached_payload)
    mock_redis.aclose = AsyncMock()

    with patch("agent.tools.faq_tools._get_redis", return_value=mock_redis), \
         patch("httpx.AsyncClient") as mock_cls:
        result = await list_faqs.ainvoke({"sa_token": "tok", "school_id": "sch1"})
        mock_cls.assert_not_called()

    assert result == cached_payload


@pytest.mark.asyncio
async def test_execute_faq_plan_not_found():
    """plan_id inexistente no Redis deve retornar erro claro sem lançar excepção."""
    from agent.tools.faq_tools import execute_faq_plan

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.aclose = AsyncMock()

    with patch("agent.tools.faq_tools._get_redis", return_value=mock_redis):
        result = await execute_faq_plan.ainvoke({
            "plan_id": "plan_nonexistent",
            "approved_actions_json": "[]",
            "sa_token": "tok",
            "school_id": "sch1",
        })

    data = json.loads(result)
    assert "error" in data
    assert "não encontrado" in data["error"].lower() or "expirado" in data["error"].lower()


@pytest.mark.asyncio
async def test_execute_faq_plan_wrong_school():
    """Plano de outro tenant deve retornar 403 sem executar acções."""
    from agent.tools.faq_tools import execute_faq_plan

    stored = json.dumps({"school_id": "escola_A", "plan": {"actions": []}})

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=stored)
    mock_redis.aclose = AsyncMock()

    with patch("agent.tools.faq_tools._get_redis", return_value=mock_redis):
        result = await execute_faq_plan.ainvoke({
            "plan_id": "plan_abc",
            "approved_actions_json": "[]",
            "sa_token": "tok",
            "school_id": "escola_B",  # diferente do armazenado
        })

    data = json.loads(result)
    assert "error" in data
    assert "permissão" in data["error"].lower()
