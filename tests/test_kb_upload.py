"""
tests/test_kb_upload.py — Testes do fluxo de upload → base de conhecimento.

Cobre:
  - routers/upload.py::_classify_document_bucket (classificação do balde)
  - routers/chat.py::confirm_kb_upload_endpoint (staging → persistência)

Mesmo estilo de mocks já usado em tests/test_faq_tools.py para as ferramentas
de FAQ: Redis mockado via AsyncMock, sem infraestrutura real.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.auth import CurrentUser
from routers.upload import _classify_document_bucket
from schemas.kb_schemas import KbConfirmRequest

# ── _classify_document_bucket ────────────────────────────────────────────────


def _fake_llm(payload: dict | None = None, error: Exception | None = None):
    """
    Substitui o módulo inteiro _classify_llm por um objecto simples.

    ChatOpenAI é um model pydantic — não dá pra usar patch() num atributo
    individual dele (__setattr__/__delattr__ do pydantic rejeita campos não
    declarados). Por isso trocamos a referência inteira no módulo.
    """
    if error is not None:
        return SimpleNamespace(ainvoke=AsyncMock(side_effect=error))
    resp = MagicMock()
    resp.content = json.dumps(payload)
    return SimpleNamespace(ainvoke=AsyncMock(return_value=resp))


@pytest.mark.asyncio
async def test_classify_knowledge_base():
    """Regimento escolar deve ser classificado como knowledge_base."""
    fake = _fake_llm({"bucket": "knowledge_base", "category": "regimento", "reason": "Regras da escola"})
    with patch("routers.upload._classify_llm", fake):
        result = await _classify_document_bucket("Regimento interno da escola...", "regimento.pdf")

    assert result["bucket"] == "knowledge_base"
    assert result["category"] == "regimento"


@pytest.mark.asyncio
async def test_classify_parent_document():
    """Lista de material deve ser classificada como parent_document."""
    fake = _fake_llm({"bucket": "parent_document", "category": "material", "reason": "Lista de material 2025"})
    with patch("routers.upload._classify_llm", fake):
        result = await _classify_document_bucket("Lista de material 3º ano...", "lista_material.pdf")

    assert result["bucket"] == "parent_document"
    assert result["category"] == "material"


@pytest.mark.asyncio
async def test_classify_transactional():
    """Comprovante de pagamento deve ser classificado como transactional."""
    fake = _fake_llm({"bucket": "transactional", "category": "", "reason": "Comprovante bancário"})
    with patch("routers.upload._classify_llm", fake):
        result = await _classify_document_bucket("Comprovante de pagamento...", "comprovante.pdf")

    assert result["bucket"] == "transactional"


@pytest.mark.asyncio
async def test_classify_invalid_bucket_falls_back_to_none():
    """Bucket fora do conjunto esperado deve cair em 'none' em vez de propagar lixo."""
    fake = _fake_llm({"bucket": "qualquer_coisa", "category": "x", "reason": "y"})
    with patch("routers.upload._classify_llm", fake):
        result = await _classify_document_bucket("texto qualquer", "arquivo.txt")

    assert result["bucket"] == "none"


@pytest.mark.asyncio
async def test_classify_llm_error_falls_back_to_none():
    """Erro na chamada do LLM não deve propagar exceção — cai em 'none'."""
    fake = _fake_llm(error=RuntimeError("boom"))
    with patch("routers.upload._classify_llm", fake):
        result = await _classify_document_bucket("texto qualquer", "arquivo.txt")

    assert result["bucket"] == "none"


# ── POST /chat/kb/confirm ─────────────────────────────────────────────────────


def _fake_user(school_id: str = "sch1") -> CurrentUser:
    return CurrentUser(
        user_id="user1",
        school_id=school_id,
        role="manager",
        sa_token="tok",
        name="Gestora Teste",
    )


@pytest.mark.asyncio
async def test_confirm_kb_upload_not_found():
    """upload_id inexistente/expirado no Redis deve retornar 404 sem chamar a eleve-api."""
    from fastapi import HTTPException

    from routers.chat import confirm_kb_upload_endpoint

    mock_session = MagicMock(status="active")
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.aclose = AsyncMock()

    body = KbConfirmRequest(
        session_id="11111111-1111-1111-1111-111111111111",
        upload_id="upload_nonexistent",
        bucket="knowledge_base",
        category="regimento",
    )

    with patch("routers.chat.SessionService.get_or_resume", AsyncMock(return_value=mock_session)), \
         patch("routers.chat._FaqRedis.from_url", return_value=mock_redis):
        with pytest.raises(HTTPException) as exc_info:
            await confirm_kb_upload_endpoint(body, user=_fake_user(), db=AsyncMock())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_confirm_kb_upload_wrong_school():
    """Upload de outra escola deve retornar 403 sem persistir nada."""
    from fastapi import HTTPException

    from routers.chat import confirm_kb_upload_endpoint

    mock_session = MagicMock(status="active")
    staged = json.dumps({
        "school_id": "escola_A",
        "bucket": "knowledge_base",
        "filename": "regimento.pdf",
        "mime_type": "application/pdf",
        "file_b64": "",
        "suggested": {},
    })
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=staged)
    mock_redis.delete = AsyncMock()
    mock_redis.aclose = AsyncMock()

    body = KbConfirmRequest(
        session_id="11111111-1111-1111-1111-111111111111",
        upload_id="upload_abc",
        bucket="knowledge_base",
        category="regimento",
    )

    with patch("routers.chat.SessionService.get_or_resume", AsyncMock(return_value=mock_session)), \
         patch("routers.chat._FaqRedis.from_url", return_value=mock_redis):
        with pytest.raises(HTTPException) as exc_info:
            await confirm_kb_upload_endpoint(
                body, user=_fake_user(school_id="escola_B"), db=AsyncMock()
            )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_confirm_kb_upload_bucket_mismatch():
    """bucket informado no confirm precisa bater com o do staging original."""
    from fastapi import HTTPException

    from routers.chat import confirm_kb_upload_endpoint

    mock_session = MagicMock(status="active")
    staged = json.dumps({
        "school_id": "sch1",
        "bucket": "parent_document",
        "filename": "lista.pdf",
        "mime_type": "application/pdf",
        "file_b64": "",
        "suggested": {},
    })
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=staged)
    mock_redis.delete = AsyncMock()
    mock_redis.aclose = AsyncMock()

    body = KbConfirmRequest(
        session_id="11111111-1111-1111-1111-111111111111",
        upload_id="upload_abc",
        bucket="knowledge_base",  # divergente do staging (parent_document)
        category="regimento",
    )

    with patch("routers.chat.SessionService.get_or_resume", AsyncMock(return_value=mock_session)), \
         patch("routers.chat._FaqRedis.from_url", return_value=mock_redis):
        with pytest.raises(HTTPException) as exc_info:
            await confirm_kb_upload_endpoint(body, user=_fake_user(), db=AsyncMock())

    assert exc_info.value.status_code == 400
