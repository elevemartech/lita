"""
agents/doc_reader.py — Agente de leitura e extração de documentos.

StateGraph:
  extract_node → validate_node → done

  extract_node: chama extract_document (GPT-4o Vision)
  validate_node: chama validate_fields (cruza com eleve-api)
  done: retorna estado final para o router

A persistência (patch_request) NÃO acontece aqui.
Ocorre separadamente via POST /doc/confirm após revisão humana.
"""
from __future__ import annotations

import json
import uuid

import structlog
from langgraph.graph import END, StateGraph

from agents.state import NicoState
from tools.extract_document import extract_document
from tools.validate_fields import validate_fields

logger = structlog.get_logger(__name__)


async def extract_node(state: NicoState) -> NicoState:
    """Extrai campos do documento via GPT-4o Vision."""
    logger.info("doc_reader.extract", doc_type=state.get("doc_type"))

    result_str = await extract_document.ainvoke({
        "file_b64":  state["raw_file_b64"],
        "file_mime": state["file_mime"],
        "doc_type":  state["doc_type"],
        "sa_token":  state["sa_token"],
    })

    result = json.loads(result_str)

    if "error" in result:
        return {**state, "error": result["error"], "extracted": {}, "confidence": 0.0, "flags": []}

    return {
        **state,
        "extracted":  result.get("extracted", {}),
        "confidence": result.get("confidence", 0.0),
        "flags":      result.get("flags", []),
        "error":      None,
    }


async def validate_node(state: NicoState) -> NicoState:
    """Cruza dados extraídos com a eleve-api."""
    if state.get("error"):
        return state

    logger.info("doc_reader.validate", doc_type=state.get("doc_type"))

    result_str = await validate_fields.ainvoke({
        "extracted": state.get("extracted", {}),
        "doc_type":  state["doc_type"],
        "sa_token":  state["sa_token"],
    })

    result = json.loads(result_str)

    # Mescla flags de extração + flags de validação
    all_flags = list(set(state.get("flags", []) + result.get("flags", [])))

    # Gera extraction_id temporário para o /doc/confirm
    extraction_id = str(uuid.uuid4())

    return {
        **state,
        "validated":     result.get("validated", state.get("extracted", {})),
        "flags":         all_flags,
        "extraction_id": extraction_id,
    }


def build_doc_reader_graph() -> StateGraph:
    graph = StateGraph(NicoState)
    graph.add_node("extract",  extract_node)
    graph.add_node("validate", validate_node)

    graph.set_entry_point("extract")
    graph.add_edge("extract",  "validate")
    graph.add_edge("validate", END)

    return graph.compile()


doc_reader_graph = build_doc_reader_graph()
