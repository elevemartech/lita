"""
agent/tools/faq_tools.py — Ferramentas LangChain para gestão de FAQs.

4 ferramentas expostas ao NicoAgent:
  list_faqs        → lista FAQs da escola via eleve-api (com cache Redis)
  analyze_faqs     → análise determinística via FaqAnalyzer
  build_faq_plan   → gera plano de acções via gpt-4o-mini
  execute_faq_plan → executa acções aprovadas na eleve-api

O sa_token e school_id são SEMPRE injectados pelo tool_node a partir do estado.
"""
from __future__ import annotations

import json
import uuid

import httpx
import structlog
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from redis.asyncio import Redis

from agent.tools.analyzers.faq_analyzer import FaqAnalyzer
from core.api_client import DjangoAPIClient
from core.settings import settings
from schemas.faq_schemas import (
    FaqAnalysisResult,
    FaqExecuteActionResult,
    FaqExecuteResponse,
    FaqItem,
    FaqPlan,
)

logger = structlog.get_logger(__name__)

_FAQ_CACHE_PREFIX = "nicodemus:faqs"
_FAQ_CACHE_TTL = 300       # 5 minutos

_PLAN_PREFIX = "nicodemus:faq_plan"
_PLAN_TTL = 1800           # 30 minutos


def _get_redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


@tool
async def list_faqs(sa_token: str = "", school_id: str = "") -> str:
    """
    Lista todas as FAQs da escola via eleve-api.
    Use quando precisar ler, analisar ou referenciar as FAQs existentes.
    Cache de 5 minutos por escola. Retorna JSON com lista e total.
    """
    cache_key = f"{_FAQ_CACHE_PREFIX}:{school_id}"
    r = _get_redis()
    try:
        cached = await r.get(cache_key)
        if cached:
            logger.info("list_faqs.cache_hit", school_id=school_id)
            return cached
    finally:
        await r.aclose()

    all_faqs: list[dict] = []

    try:
        async with DjangoAPIClient(token=sa_token) as client:
            data = await client.get(
                "/api/v1/faqs/",
                params={"school": school_id, "page_size": 200},
            )
        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list):
            all_faqs = results
    except Exception as exc:
        logger.error("list_faqs.request_error", error=str(exc), school_id=school_id)
        return json.dumps({"error": str(exc)})

    result = json.dumps({"faqs": all_faqs, "total": len(all_faqs)})

    r = _get_redis()
    try:
        await r.set(cache_key, result, ex=_FAQ_CACHE_TTL)
    finally:
        await r.aclose()

    logger.info("list_faqs.ok", total=len(all_faqs), school_id=school_id)
    return result


@tool
async def analyze_faqs(
    intent: str = "audit",
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Analisa as FAQs da escola e detecta problemas: duplicatas, respostas vazias,
    categorias erradas e FAQs desactualizadas.
    Use antes de build_faq_plan para obter a análise determinística das FAQs.
    Retorna JSON com FaqAnalysisResult (issues, gaps de cobertura, estatísticas).
    """
    raw = await list_faqs.ainvoke({"sa_token": sa_token, "school_id": school_id})
    raw_data = json.loads(raw)

    if "error" in raw_data:
        logger.error("analyze_faqs.list_error", error=raw_data["error"], school_id=school_id)
        return raw

    faqs = [FaqItem(**item) for item in raw_data.get("faqs", [])]
    result: FaqAnalysisResult = FaqAnalyzer().analyze(faqs)

    logger.info(
        "analyze_faqs.ok",
        total=result.total_faqs,
        issues=len(result.issues),
        school_id=school_id,
    )
    return result.model_dump_json()


@tool
async def build_faq_plan(
    intent: str = "organize",
    analysis_json: str = "",
    session_id: str = "",
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Gera um plano de acções para melhorar as FAQs com base na análise.
    Chame APÓS analyze_faqs. O plano é guardado no Redis por 30 minutos.
    Retorna JSON com FaqPlan (before/after por acção, justificativas em PT-BR).
    NUNCA execute o plano sem confirmação explícita do gestor.
    """
    plan_id = f"plan_{uuid.uuid4().hex[:8]}"

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=settings.openai_api_key,
        temperature=0,
    )

    prompt = (
        "Você é o Nicodemus ADM. Com base nesta análise de FAQs, gere um plano de acções.\n\n"
        f"Análise: {analysis_json}\n"
        f"Intenção do gestor: {intent}\n\n"
        "Responda APENAS com JSON válido seguindo este schema:\n"
        "{\n"
        f'  "plan_id": "{plan_id}",\n'
        f'  "session_id": "{session_id}",\n'
        f'  "intent": "{intent}",\n'
        '  "summary": {"edit": 0, "create": 0, "deactivate": 0},\n'
        '  "analysis_text": "Resumo em PT-BR para o gestor (2-3 frases)",\n'
        '  "actions": [\n'
        '    {\n'
        '      "id": "act_1",\n'
        '      "type": "edit|create|deactivate",\n'
        '      "faq_id": null,\n'
        '      "before": null,\n'
        '      "after": {"campo": "valor_proposto"},\n'
        '      "reason": "Justificativa em PT-BR (1 frase)",\n'
        '      "approved": true,\n'
        '      "status": "pending"\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        "Regras:\n"
        "- Máximo 20 acções por plano\n"
        "- Para issues do tipo 'empty_answer': sugere uma resposta concisa baseada na pergunta\n"
        "- Para 'duplicate': sugere deactivate na FAQ mais antiga\n"
        "- Para 'wrong_category': sugere edit na categoria correcta\n"
        "- Para 'stale': sugere edit na resposta se for possível melhorá-la\n"
        "- Nunca inventes dados factuais (valores, datas, nomes)\n"
        "- Nunca uses markdown no JSON\n"
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        plan_dict = json.loads(response.content.strip())
        plan = FaqPlan(**plan_dict)
    except Exception as exc:
        logger.error("build_faq_plan.llm_error", error=str(exc), school_id=school_id)
        return json.dumps({"error": f"Erro ao gerar plano: {str(exc)}"})

    redis_payload = json.dumps({
        "school_id": school_id,
        "plan": plan.model_dump(mode="json"),
    })
    r = _get_redis()
    try:
        await r.set(f"{_PLAN_PREFIX}:{plan_id}", redis_payload, ex=_PLAN_TTL)
    finally:
        await r.aclose()

    logger.info(
        "build_faq_plan.ok",
        plan_id=plan_id,
        actions=len(plan.actions),
        school_id=school_id,
    )
    return plan.model_dump_json()


@tool
async def execute_faq_plan(
    plan_id: str = "",
    approved_actions_json: str = "",
    sa_token: str = "",
    school_id: str = "",
) -> str:
    """
    Executa as acções aprovadas de um plano FAQ na eleve-api.
    Requer plan_id válido gerado por build_faq_plan (TTL 30 min).
    NUNCA chame esta ferramenta sem confirmação explícita do gestor.
    Cada acção é independente — falha numa não cancela as outras.
    """
    r = _get_redis()
    try:
        redis_raw = await r.get(f"{_PLAN_PREFIX}:{plan_id}")
    finally:
        await r.aclose()

    if not redis_raw:
        return json.dumps({"error": "Plano não encontrado ou expirado. Gere um novo plano."})

    stored = json.loads(redis_raw)
    if stored.get("school_id") != school_id:
        logger.warning(
            "execute_faq_plan.wrong_school",
            plan_id=plan_id,
            school_id=school_id,
        )
        return json.dumps({"error": "Sem permissão para executar este plano."})

    try:
        approved_actions: list[dict] = (
            json.loads(approved_actions_json) if approved_actions_json else []
        )
    except json.JSONDecodeError:
        return json.dumps({"error": "approved_actions_json inválido."})

    approved_map = {a["id"]: a for a in approved_actions}
    plan_data = stored["plan"]
    results: list[FaqExecuteActionResult] = []
    counts: dict[str, int] = {"done": 0, "failed": 0, "skipped": 0}

    async with DjangoAPIClient(token=sa_token) as client:

        for action in plan_data.get("actions", []):
            action_id = action["id"]
            override = approved_map.get(action_id, {})
            approved = override.get("approved", action.get("approved", True))

            if not approved:
                results.append(FaqExecuteActionResult(action_id=action_id, status="skipped"))
                counts["skipped"] += 1
                continue

            after = override.get("after") or action.get("after", {})
            faq_id = action.get("faq_id")
            action_type = action["type"]

            try:
                if action_type == "create":
                    await client.post("/api/v1/faqs/", json=after)
                elif action_type == "edit":
                    await client.patch(f"/api/v1/faqs/{faq_id}/", json=after)
                elif action_type == "deactivate":
                    await client.patch(f"/api/v1/faqs/{faq_id}/", json={"status": "inactive"})
                else:
                    results.append(FaqExecuteActionResult(
                        action_id=action_id,
                        status="failed",
                        error=f"Tipo de acção desconhecido: {action_type}",
                    ))
                    counts["failed"] += 1
                    continue

                results.append(FaqExecuteActionResult(action_id=action_id, status="done"))
                counts["done"] += 1

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                results.append(FaqExecuteActionResult(
                    action_id=action_id,
                    status="failed",
                    error=str(exc),
                ))
                counts["failed"] += 1
                logger.error(
                    "execute_faq_plan.request_error",
                    action_id=action_id,
                    error=str(exc),
                )

            logger.info(
                "execute_faq_plan.action",
                plan_id=plan_id,
                action_id=action_id,
                action_type=action_type,
                school_id=school_id,
                status=results[-1].status,
            )

    done, failed, skipped = counts["done"], counts["failed"], counts["skipped"]
    parts = []
    if done:
        parts.append(f"{done} acção(ões) executada(s) com sucesso")
    if failed:
        parts.append(f"{failed} com erro")
    if skipped:
        parts.append(f"{skipped} ignorada(s)")
    text = (", ".join(parts) + ".") if parts else "Nenhuma acção executada."

    response_obj = FaqExecuteResponse(summary=counts, results=results, text=text)
    logger.info("execute_faq_plan.ok", plan_id=plan_id, school_id=school_id, **counts)
    return response_obj.model_dump_json()
