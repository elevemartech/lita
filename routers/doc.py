"""
routers/doc.py — Endpoints de leitura e confirmação de documentos.

POST /doc/extract  → recebe arquivo, extrai dados, valida, retorna para revisão
POST /doc/confirm  → recebe dados confirmados pelo usuário, persiste na eleve-api

Fluxo obrigatório:
  /extract → revisão humana no dashboard → /confirm

Nunca persista sem confirmação humana.
"""
from __future__ import annotations

import base64
import json
import uuid

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from agents.doc_reader import doc_reader_graph
from agents.state import NicoState
from core.auth import CurrentUser, get_current_user
from schemas.doc_types import SUPPORTED_DOC_TYPES
from tools.patch_request import patch_request

logger = structlog.get_logger(__name__)

router = APIRouter()

# Armazenamento temporário das extrações pendentes de confirmação
# Em produção: use Redis com TTL
_pending_extractions: dict[str, dict] = {}

ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp",
    "application/pdf",
}
MAX_FILE_SIZE_MB = 10


class ConfirmRequest(BaseModel):
    extraction_id: str
    confirmed_fields: dict


# ── POST /doc/extract ──────────────────────────────────────────────────────────

@router.post("/extract")
async def extract_document_endpoint(
    file: UploadFile = File(...),
    doc_type: str    = Form(...),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Recebe um documento (PDF ou imagem), extrai dados via GPT-4o Vision
    e valida contra a eleve-api.

    Retorna os dados para revisão humana — não persiste nada.

    Args:
        file:     Arquivo (PDF, JPG, PNG, WEBP). Máx 10 MB.
        doc_type: Tipo do documento. Valores: comprovante_pagamento |
                  contrato_matricula | boletim
    """
    # Validação do tipo de documento
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"doc_type inválido. Use: {', '.join(SUPPORTED_DOC_TYPES)}",
        )

    # Validação do MIME
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo de arquivo não suportado: {content_type}. Use PDF, JPG, PNG ou WEBP.",
        )

    # Leitura e validação de tamanho
    file_bytes = await file.read()
    size_mb    = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Arquivo muito grande ({size_mb:.1f} MB). Máximo: {MAX_FILE_SIZE_MB} MB.",
        )

    file_b64 = base64.b64encode(file_bytes).decode("utf-8")

    logger.info(
        "doc.extract.start",
        user_id=user.user_id,
        school_id=user.school_id,
        doc_type=doc_type,
        size_mb=round(size_mb, 2),
    )

    # Executa o grafo
    initial_state: NicoState = {
        "user_id":    user.user_id,
        "school_id":  user.school_id,
        "sa_token":   user.sa_token,
        "role":       user.role,
        "doc_type":   doc_type,
        "raw_file_b64": file_b64,
        "file_mime":  content_type,
    }

    final_state: NicoState = await doc_reader_graph.ainvoke(initial_state)

    if final_state.get("error"):
        logger.error("doc.extract.error", error=final_state["error"], user_id=user.user_id)
        raise HTTPException(status_code=500, detail=final_state["error"])

    extraction_id = final_state.get("extraction_id", str(uuid.uuid4()))

    # Salva extração pendente para o /confirm
    _pending_extractions[extraction_id] = {
        "doc_type":  doc_type,
        "school_id": user.school_id,
        "sa_token":  user.sa_token,
        "user_id":   user.user_id,
        "extracted": final_state.get("extracted", {}),
        "validated": final_state.get("validated", {}),
    }

    logger.info(
        "doc.extract.ok",
        extraction_id=extraction_id,
        confidence=final_state.get("confidence"),
        flags=final_state.get("flags"),
    )

    return {
        "extraction_id": extraction_id,
        "doc_type":      doc_type,
        "extracted":     final_state.get("extracted", {}),
        "validated":     final_state.get("validated", {}),
        "confidence":    final_state.get("confidence", 0.0),
        "flags":         final_state.get("flags", []),
        "requires_review": final_state.get("confidence", 0.0) < 0.80
                           or bool(final_state.get("flags")),
    }


# ── POST /doc/confirm ──────────────────────────────────────────────────────────

@router.post("/confirm")
async def confirm_document_endpoint(
    body: ConfirmRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Confirma os dados revisados pelo usuário e persiste na eleve-api.

    Gera protocolo SEC- ou FIN- via signal da eleve-api.

    Args:
        extraction_id:    ID retornado pelo /extract.
        confirmed_fields: Campos corrigidos/aprovados pelo usuário.
    """
    pending = _pending_extractions.pop(body.extraction_id, None)

    if not pending:
        raise HTTPException(
            status_code=404,
            detail="extraction_id não encontrado ou já confirmado.",
        )

    # Garante que o confirm veio da mesma escola da extração
    if pending["school_id"] != user.school_id:
        raise HTTPException(status_code=403, detail="Escola inválida para esta extração.")

    logger.info(
        "doc.confirm.start",
        extraction_id=body.extraction_id,
        doc_type=pending["doc_type"],
        user_id=user.user_id,
    )

    result_str = await patch_request.ainvoke({
        "doc_type":        pending["doc_type"],
        "confirmed_fields": body.confirmed_fields,
        "sa_token":        user.sa_token,
        "student_name":    body.confirmed_fields.get("student_name", ""),
    })

    result = json.loads(result_str)

    if "error" in result:
        logger.error("doc.confirm.error", error=result["error"])
        raise HTTPException(status_code=500, detail=result["error"])

    logger.info("doc.confirm.ok", protocol=result.get("protocol"), user_id=user.user_id)

    return {
        "protocol":   result.get("protocol"),
        "request_id": result.get("request_id"),
        "message":    f"Documento registrado com protocolo {result.get('protocol')}.",
    }
