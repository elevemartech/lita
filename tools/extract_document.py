"""
tools/extract_document.py — Extrai dados estruturados de documentos via GPT-4o Vision.

Recebe o arquivo em base64, o tipo de documento e retorna JSON com os campos
extraídos e um score de confiança por campo.

A confidence é calculada como média dos scores individuais.
Campos ausentes ou ilegíveis recebem score 0.0 e entram nas flags.
"""
from __future__ import annotations

import json

import structlog
from langchain_core.tools import tool
from openai import AsyncOpenAI

from core.settings import settings
from schemas.doc_types import get_doc_type

logger = structlog.get_logger(__name__)

_client = AsyncOpenAI(api_key=settings.openai_api_key)

_SYSTEM_PROMPT = """\
Você é um extrator especializado em documentos escolares brasileiros.
Analise a imagem e extraia os campos solicitados.

Responda APENAS com JSON válido, sem markdown, sem explicações.
Formato de resposta:
{
  "fields": {
    "nome_do_campo": {
      "value": <valor extraído ou null>,
      "confidence": <float 0.0 a 1.0>
    }
  }
}

Regras:
- confidence 1.0 = campo claramente legível
- confidence 0.5 = campo parcialmente legível ou inferido
- confidence 0.0 = campo ausente ou ilegível (value = null)
- Datas sempre em formato YYYY-MM-DD
- Valores monetários como número float (ex: 1250.00)
- Nunca invente informações — prefira null a inventar
"""


@tool
async def extract_document(
    file_b64: str,
    file_mime: str,
    doc_type: str,
    sa_token: str = "",
    **kwargs,
) -> str:
    """
    Extrai campos de um documento (PDF/imagem) via GPT-4o Vision.

    Args:
        file_b64:  Arquivo codificado em base64.
        file_mime: MIME type: "image/jpeg" | "image/png" | "application/pdf".
        doc_type:  Tipo do documento: "comprovante_pagamento" | "contrato_matricula" | "boletim".

    Returns:
        JSON string com { extracted: dict, confidence: float, flags: list[str] }
    """
    try:
        doc_def = get_doc_type(doc_type)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    fields_list = ", ".join(doc_def["fields"])
    user_prompt = (
        f"{doc_def['prompt_hint']}\n\n"
        f"Campos a extrair: {fields_list}"
    )

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1000,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{file_mime};base64,{file_b64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ],
        )

        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)
        fields_raw: dict = parsed.get("fields", {})

        extracted: dict[str, object] = {}
        flags: list[str] = []
        confidence_scores: list[float] = []

        for field in doc_def["fields"]:
            field_data = fields_raw.get(field, {})
            value      = field_data.get("value")
            conf       = float(field_data.get("confidence", 0.0))

            extracted[field] = value
            confidence_scores.append(conf)

            is_required = field in doc_def.get("required", [])
            if value is None or conf < 0.5:
                if is_required:
                    flags.append(f"CAMPO_OBRIGATORIO_AUSENTE:{field}")
                elif conf < 0.5:
                    flags.append(f"BAIXA_CONFIANCA:{field}")

        avg_confidence = (
            sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0
        )

        logger.info(
            "extract_document.ok",
            doc_type=doc_type,
            confidence=round(avg_confidence, 2),
            flags=flags,
        )

        return json.dumps({
            "extracted":  extracted,
            "confidence": round(avg_confidence, 2),
            "flags":      flags,
        })

    except Exception as exc:
        logger.error("extract_document.error", doc_type=doc_type, error=str(exc))
        return json.dumps({"error": str(exc), "extracted": {}, "confidence": 0.0, "flags": []})
