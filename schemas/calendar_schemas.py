"""
schemas/calendar_schemas.py — Tipos do calendário escolar (apps/events na eleve-api).

CalendarEvent aqui é o calendário interno da escola (feriado, prova, formatura,
evento cultural) — não confundir com a conexão OAuth do Google Calendar
(usada só para visitas, gerida pelo eleve-agent). Quando a escola tem o Google
conectado, o backend espelha esses eventos lá em melhor esforço; o campo
google_event_id reflete se essa sincronização já aconteceu.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

CalendarEventType = Literal["holiday", "exam", "graduation", "cultural"]


class CalendarEventItem(BaseModel):
    id: int
    title: str
    description: str = ""
    event_type: CalendarEventType
    start_date: str
    end_date: str | None = None
    google_event_id: str = ""
