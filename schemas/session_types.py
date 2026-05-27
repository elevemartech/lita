"""
schemas/session_types.py — Schemas de request/response para sessões e chat.

Todos os schemas de resposta usam model_config = {"from_attributes": True}
para compatibilidade com instâncias ORM do SQLAlchemy.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SessionResponse(BaseModel):
    id:               uuid.UUID
    title:            str
    status:           str
    message_count:    int
    report_count:     int
    created_at:       datetime
    last_activity_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id:         uuid.UUID
    role:       str
    content:    str
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionDetailResponse(SessionResponse):
    messages: list[MessageResponse] = []
    summary:  Optional[str] = None


class ChatRequest(BaseModel):
    session_id: str
    message:    str


class ChatResponse(BaseModel):
    session_id: str
    reply:      str
    file_id:    Optional[str] = None
    file_url:   Optional[str] = None


class BriefingResponse(BaseModel):
    session_id: str
    briefing:   str


class SummaryResponse(BaseModel):
    solicitacoes_abertas: int
    matriculas_pendentes: int
    inadimplencia_aberta: int

    model_config = {"from_attributes": True}
