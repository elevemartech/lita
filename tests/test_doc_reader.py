"""
tests/test_doc_reader.py — Testes do fluxo de leitura de documentos.

Usa respx para mockar as chamadas HTTP à eleve-api e
monkeypatching para mockar o GPT-4o Vision.
"""
from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from agents.doc_reader import doc_reader_graph
from agents.state import NicoState

FAKE_SA_TOKEN = "sa_live_test_abc123"
FAKE_FILE_B64 = base64.b64encode(b"fake_image_bytes").decode()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def base_state() -> NicoState:
    return {
        "user_id":      "user-001",
        "school_id":    "school-001",
        "sa_token":     FAKE_SA_TOKEN,
        "role":         "secretary",
        "doc_type":     "comprovante_pagamento",
        "raw_file_b64": FAKE_FILE_B64,
        "file_mime":    "image/jpeg",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_vision_response(fields: dict) -> str:
    """Monta resposta simulada do GPT-4o Vision."""
    return json.dumps({
        "fields": {
            k: {"value": v, "confidence": 0.95}
            for k, v in fields.items()
        }
    })


# ── Testes ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_comprovante_ok(base_state):
    """Extração bem-sucedida de comprovante de pagamento."""
    vision_fields = {
        "payer_name":   "Maria Silva",
        "amount":       1250.00,
        "payment_date": "2025-04-01",
        "bank":         "Nubank",
        "student_name": "João Silva",
        "description":  "Mensalidade abril",
    }

    mock_completion = AsyncMock()
    mock_completion.choices[0].message.content = _mock_vision_response(vision_fields)

    with patch("tools.extract_document._client") as mock_client, \
         patch("tools.validate_fields.DjangoAPIClient") as mock_api:

        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_api_instance = AsyncMock()
        mock_api_instance.__aenter__ = AsyncMock(return_value=mock_api_instance)
        mock_api_instance.__aexit__  = AsyncMock(return_value=None)
        mock_api_instance.get        = AsyncMock(return_value={"results": [{"id": "req-001"}]})
        mock_api.return_value        = mock_api_instance

        result: NicoState = await doc_reader_graph.ainvoke(base_state)

    assert result.get("error") is None
    assert result["extracted"]["payer_name"]   == "Maria Silva"
    assert result["extracted"]["amount"]       == 1250.00
    assert result["extracted"]["payment_date"] == "2025-04-01"
    assert result["confidence"] >= 0.80
    assert result.get("extraction_id") is not None


@pytest.mark.asyncio
async def test_extract_baixa_confianca_gera_flags(base_state):
    """Campo com confiança < 0.5 deve gerar flag."""
    mock_completion = AsyncMock()
    mock_completion.choices[0].message.content = json.dumps({
        "fields": {
            "payer_name":   {"value": None,     "confidence": 0.0},
            "amount":       {"value": 500.0,    "confidence": 0.9},
            "payment_date": {"value": "2025-04-01", "confidence": 0.9},
            "bank":         {"value": None,     "confidence": 0.0},
            "student_name": {"value": "Lucas",  "confidence": 0.8},
            "description":  {"value": None,     "confidence": 0.0},
        }
    })

    with patch("tools.extract_document._client") as mock_client, \
         patch("tools.validate_fields.DjangoAPIClient") as mock_api:

        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_api_instance = AsyncMock()
        mock_api_instance.__aenter__ = AsyncMock(return_value=mock_api_instance)
        mock_api_instance.__aexit__  = AsyncMock(return_value=None)
        mock_api_instance.get        = AsyncMock(return_value={"results": []})
        mock_api.return_value        = mock_api_instance

        result: NicoState = await doc_reader_graph.ainvoke(base_state)

    assert any("payer_name" in f for f in result.get("flags", []))


@pytest.mark.asyncio
async def test_doc_type_invalido(base_state):
    """doc_type inválido deve retornar erro."""
    state = {**base_state, "doc_type": "tipo_inexistente"}

    with patch("tools.extract_document._client") as mock_client:
        mock_completion = AsyncMock()
        mock_completion.choices[0].message.content = "{}"
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        result: NicoState = await doc_reader_graph.ainvoke(state)

    assert result.get("error") is not None or result.get("extracted") == {}


@pytest.mark.asyncio
async def test_extraction_id_gerado(base_state):
    """Toda extração bem-sucedida deve gerar um extraction_id."""
    vision_fields = {
        "payer_name":   "Ana Costa",
        "amount":       800.0,
        "payment_date": "2025-03-15",
        "bank":         "Inter",
        "student_name": "Pedro Costa",
        "description":  "Mensalidade",
    }

    mock_completion = AsyncMock()
    mock_completion.choices[0].message.content = _mock_vision_response(vision_fields)

    with patch("tools.extract_document._client") as mock_client, \
         patch("tools.validate_fields.DjangoAPIClient") as mock_api:

        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_api_instance = AsyncMock()
        mock_api_instance.__aenter__ = AsyncMock(return_value=mock_api_instance)
        mock_api_instance.__aexit__  = AsyncMock(return_value=None)
        mock_api_instance.get        = AsyncMock(return_value={"results": [{"id": "req-002"}]})
        mock_api.return_value        = mock_api_instance

        result: NicoState = await doc_reader_graph.ainvoke(base_state)

    assert result.get("extraction_id")
    assert len(result["extraction_id"]) == 36  # UUID v4
