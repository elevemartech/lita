"""
agents/state.py — Estado compartilhado dos agentes do Nicodemus ADM.

NicoState é o TypedDict passado pelo StateGraph em ambos os fluxos.
Campos do doc_reader e do report_agent coexistem no mesmo tipo —
cada agente usa apenas os campos relevantes ao seu fluxo.
"""
from __future__ import annotations

from typing import TypedDict


class NicoState(TypedDict, total=False):
    # ── Identidade (injetado pelo router, nunca pelo LLM) ────────────
    user_id:    str
    school_id:  str
    sa_token:   str
    role:       str

    # ── doc_reader_agent ─────────────────────────────────────────────
    doc_type:        str           # "comprovante_pagamento" | "contrato_matricula" | "boletim"
    raw_file_b64:    str           # arquivo em base64 para o GPT-4o Vision
    file_mime:       str           # "image/jpeg" | "image/png" | "application/pdf"
    extracted:       dict          # campos extraídos pelo Vision
    validated:       dict          # campos cruzados com a eleve-api
    confidence:      float         # 0.0 → 1.0 — confiança média da extração
    flags:           list[str]     # campos com problema ou baixa confiança
    extraction_id:   str           # ID temporário para /doc/confirm

    # ── report_agent ─────────────────────────────────────────────────
    user_prompt:     str           # pedido em linguagem natural
    report_plan:     dict          # { entity, filters, columns, format }
    report_data:     list[dict]    # registros buscados da API
    file_format:     str           # "xlsx" | "docx"
    file_path:       str           # caminho em disco (temporário)
    file_id:         str           # ID para download
    summary:         str           # resumo textual do relatório

    # ── saída geral ──────────────────────────────────────────────────
    response:        str
    error:           str | None
