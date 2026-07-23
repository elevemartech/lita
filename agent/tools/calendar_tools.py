"""
agent/tools/calendar_tools.py — Ferramentas LangChain para o calendário escolar.

4 ferramentas expostas ao NicoAgent:
  list_calendar_events   → lista/filtra eventos (feriado, prova, formatura, cultural)
  create_calendar_event  → cria um evento novo
  update_calendar_event  → edita um evento existente
  delete_calendar_event  → exclui um evento

Isolamento por escola é automático (SchoolIsolationMixin na eleve-api via
sa_token/ServiceAccount) — não é preciso filtrar por school_id nas leituras.
create_calendar_event é a exceção: o serializer exige "school" no payload.

O sync com o Google Calendar (quando a escola tem conta conectada) acontece
no backend, em melhor esforço, após cada create/update/delete — estas
ferramentas não sabem nem precisam saber disso.

create/update/delete NUNCA devem ser chamadas sem confirmação explícita do
gestor no chat (mesma regra do módulo FAQ) — reforçado no system prompt do
NicoAgent, não aqui.
"""
from __future__ import annotations

import json

import httpx
import structlog
from langchain_core.tools import tool

from core.api_client import DjangoAPIClient

logger = structlog.get_logger(__name__)

_EVENTS_PATH = "/api/v1/events/"


@tool
async def list_calendar_events(
    event_type: str = "",
    start_date: str = "",
    end_date: str = "",
    search: str = "",
    sa_token: str = "",
) -> str:
    """
    Lista eventos do calendário escolar (feriado, prova, formatura, evento cultural).
    Filtros opcionais: event_type ("holiday"|"exam"|"graduation"|"cultural"),
    start_date/end_date (YYYY-MM-DD, intervalo que o evento precisa tocar),
    search (busca em título/descrição). Sem filtros, retorna todos os eventos.
    Use para responder perguntas sobre datas, feriados, provas ou eventos da escola.
    """
    params = {
        k: v
        for k, v in {
            "event_type": event_type,
            "start_date": start_date,
            "end_date": end_date,
            "search": search,
        }.items()
        if v
    }
    try:
        async with DjangoAPIClient(token=sa_token) as client:
            data = await client.get(_EVENTS_PATH, params=params)
        events = data if isinstance(data, list) else data.get("results", [])
    except Exception as exc:
        logger.error("list_calendar_events.request_error", error=str(exc))
        return json.dumps({"error": str(exc)})

    logger.info("list_calendar_events.ok", total=len(events))
    return json.dumps({"events": events, "total": len(events)})


@tool
async def create_calendar_event(
    title: str,
    event_type: str,
    start_date: str,
    end_date: str = "",
    description: str = "",
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Cria um novo evento no calendário escolar.
    event_type deve ser um de: "holiday", "exam", "graduation", "cultural".
    start_date/end_date em YYYY-MM-DD — end_date opcional (default = start_date).
    Se a escola tiver Google Calendar conectado, o evento é espelhado lá
    automaticamente pelo backend.
    NUNCA chame sem confirmação explícita do gestor no chat.
    """
    payload = {
        "school": school_id,
        "title": title,
        "event_type": event_type,
        "start_date": start_date,
        "end_date": end_date or start_date,
        "description": description,
    }
    try:
        async with DjangoAPIClient(token=sa_token) as client:
            event = await client.post(_EVENTS_PATH, json=payload)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("create_calendar_event.request_error", error=str(exc))
        return json.dumps({"error": str(exc)})

    logger.info("create_calendar_event.ok", event_id=event.get("id"))
    return json.dumps({"event": event})


@tool
async def update_calendar_event(
    event_id: int,
    title: str = "",
    event_type: str = "",
    start_date: str = "",
    end_date: str = "",
    description: str = "",
    sa_token: str = "",
) -> str:
    """
    Edita um evento existente do calendário escolar. Envie apenas os campos
    que devem mudar — os demais permanecem como estavam.
    Se a escola tiver Google Calendar conectado, a alteração é espelhada lá
    automaticamente pelo backend.
    NUNCA chame sem confirmação explícita do gestor no chat.
    """
    diff = {
        k: v
        for k, v in {
            "title": title,
            "event_type": event_type,
            "start_date": start_date,
            "end_date": end_date,
            "description": description,
        }.items()
        if v
    }
    if not diff:
        return json.dumps({"error": "Nenhum campo para atualizar foi informado."})

    try:
        async with DjangoAPIClient(token=sa_token) as client:
            event = await client.patch(f"{_EVENTS_PATH}{event_id}/", json=diff)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("update_calendar_event.request_error", event_id=event_id, error=str(exc))
        return json.dumps({"error": str(exc)})

    logger.info("update_calendar_event.ok", event_id=event_id)
    return json.dumps({"event": event})


@tool
async def delete_calendar_event(event_id: int, sa_token: str = "") -> str:
    """
    Exclui um evento do calendário escolar. Se o evento estiver sincronizado
    com o Google Calendar, também é removido de lá automaticamente pelo backend.
    Ação irreversível — NUNCA chame sem confirmação explícita do gestor no chat.
    """
    try:
        async with DjangoAPIClient(token=sa_token) as client:
            await client.delete(f"{_EVENTS_PATH}{event_id}/")
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("delete_calendar_event.request_error", event_id=event_id, error=str(exc))
        return json.dumps({"error": str(exc)})

    logger.info("delete_calendar_event.ok", event_id=event_id)
    return json.dumps({"status": "done", "event_id": event_id})
