"""
schemas/kb_schemas.py — Schemas do fluxo de upload → base de conhecimento.

Espelha o padrão já usado no módulo FAQ Manager (schemas/faq_schemas.py):
uma sugestão fica em staging (Redis) aguardando revisão humana, e só é
persistida na eleve-api após confirmação explícita via POST /chat/kb/confirm.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DocSuggestion(BaseModel):
    """Sugestão de classificação/metadados de um arquivo enviado no chat."""

    upload_id:           str
    bucket:               Literal["knowledge_base", "parent_document"]
    filename:             str
    suggested_name:       str
    suggested_category:   str
    description:          str = ""
    tags:                 list[str] = []              # apenas knowledge_base
    trigger_phrases:      list[str] = []               # apenas parent_document
    audiences:            list[str] = []                # apenas parent_document
    reason:               str = ""                       # por que a Lita classificou assim


class KbConfirmRequest(BaseModel):
    """Body do POST /chat/kb/confirm."""

    session_id:  str
    upload_id:   str
    bucket:      Literal["knowledge_base", "parent_document"]
    category:    str
    name:        str | None = None
    description: str | None = None
    tags:            list[str] | None = None
    trigger_phrases: list[str] | None = None
    audiences:       list[str] | None = None


class KbConfirmResponse(BaseModel):
    """Resposta do POST /chat/kb/confirm."""

    status:  Literal["done", "error"]
    id:      str | None = None
    bucket:  str
    message: str
