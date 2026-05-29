# CLAUDE.md — Nicodemus ADM

Contexto permanente para o Claude Code. **Leia inteiro antes de qualquer tarefa.**

> Este arquivo é a fonte de verdade arquitetural do projeto.
> Toda decisão de design que não está aqui é uma decisão não tomada — e vai virar bug.

---

## 1. O que é este projeto

Microserviço Python (**FastAPI + LangGraph**) que opera como copiloto de gestão escolar
no painel administrativo da plataforma **Eleve**.

**Não é o eleve-agent.** Não processa WhatsApp. Não atende pais.
Serve exclusivamente gestores autenticados (diretores, secretaria, admin, manager).

Faz três coisas:

```
LER       → Recebe documento (PDF/imagem), extrai dados via GPT-4o Vision,
             valida contra a eleve-api e registra após revisão humana.

RELATAR   → Recebe pedido em linguagem natural, consulta a eleve-api,
             gera arquivo .xlsx ou .docx e entrega para download.

CONVERSAR → Mantém sessões conversacionais persistentes com memória Redis
             e histórico PostgreSQL. Agente ReAct em agent/nico_agent.py.
```

---

## 2. Repositório irmão

API Django em `../eleve-api`. Leia os arquivos diretamente quando precisar
entender endpoints, serializers ou models.

Caminhos úteis:
```
../eleve-api/apps/requests/models.py
../eleve-api/apps/requests/choices.py
../eleve-api/apps/contacts/views/guardian_viewset.py
../eleve-api/apps/contacts/serializers/invoice_serializers.py
../eleve-api/apps/secretary/models.py
../eleve-api/apps/schools/models.py
../eleve-api/config/urls.py
```

---

## 3. Stack

```
FastAPI + uvicorn     → endpoints REST (não webhook)
LangGraph            → StateGraph por fluxo (doc_reader, report_agent, nico_agent)
LangChain + OpenAI   → LLM (gpt-4o-mini), vision (gpt-4o)
openpyxl             → geração de .xlsx
python-docx          → geração de .docx
SQLAlchemy async     → ORM para sessões e mensagens (PostgreSQL)
asyncpg              → driver PostgreSQL assíncrono
Alembic              → migrações de banco de dados
Redis                → contexto conversacional (últimas 20 mensagens, TTL 24h)
httpx                → cliente HTTP assíncrono para a eleve-api
Poetry               → gerenciamento de dependências
```

---

## 4. Autenticação — CRÍTICO

O eleve-agent usa `ServiceKey` de WhatsApp.
**O Nicodemus ADM usa JWT do usuário logado no dashboard.**

```
Dashboard → Authorization: Bearer <jwt_do_usuario>
  → core/auth.py valida JWT
  → extrai { user_id, school_id, role, sa_token, name }
  → role deve ser: "director" | "secretary" | "admin" | "manager"
  → sa_token é usado para chamar a eleve-api (isolamento multi-tenant)
  → name é usado para personalização das respostas do agente
```

O `sa_token` SEMPRE vem do JWT. Nunca é gerado aqui.

**Nunca aceite requisição sem JWT válido.**
**Nunca aceite role fora de ["director", "secretary", "admin", "manager"].**

---

## 5. Endpoints

```
POST /doc/extract
  → Recebe: multipart (arquivo) + doc_type
  → Retorna: campos extraídos + confidence + flags de validação
  → Agente: doc_reader_agent
  → NÃO persiste nada — só extrai e valida. A secretaria confirma antes.

POST /doc/confirm
  → Recebe: extraction_id + campos corrigidos
  → Persiste na eleve-api via patch_request
  → Gera protocolo SEC- ou FIN-

POST /report/generate
  → Recebe: prompt em linguagem natural + format ("xlsx" | "docx")
  → Retorna: file_url (TTL 15min) + summary + preview[]
  → Agente: report_agent

GET /report/download/{file_id}
  → Serve o arquivo gerado (redirect ou stream)

POST /sessions/
  → Cria nova sessão conversacional
  → Retorna: session_id, title, status

GET /sessions/
  → Lista sessões do gestor autenticado (ordenadas por last_activity_at)

GET /sessions/summary/
  → Retorna indicadores vivos da escola para a SummaryBar (ELE-203)
  → Campos: solicitacoes_abertas, matriculas_pendentes, inadimplencia_aberta
  → REST puro — não passa pelo LangGraph

GET /sessions/{id}/
  → Detalhe da sessão + últimas 50 mensagens

POST /sessions/{id}/close/
  → Encerra sessão e gera resumo via LLM

DELETE /sessions/{id}/
  → Soft delete (is_deleted=True)

POST /chat/
  → Recebe: { session_id, message }
  → Processa mensagem via NicoAgent (ReAct)
  → Retorna: { session_id, reply, file_id?, file_url?, faq_plan? }

POST /chat/faq/execute
  → Recebe: { session_id, plan_id, actions: [{id, approved, after?}] }
  → Executa acções aprovadas de um plano FAQ na eleve-api
  → Requer sessão activa; verifica isolamento por school_id

GET /chat/faq/plan/{plan_id}
  → Recupera plano FAQ guardado no Redis (TTL 30 min)
  → 404 se expirado; 403 se school_id não coincide
```

---

## 6. Tipos de documento suportados

```python
# schemas/doc_types.py

DOC_TYPES = {
    "comprovante_pagamento": {
        "fields": ["payer_name", "amount", "payment_date", "bank", "student_name"],
        "eleve_request_type": "comprovante_pagamento",   # FIN-
    },
    "contrato_matricula": {
        "fields": ["guardian_name", "student_name", "grade", "start_date", "monthly_fee"],
        "eleve_request_type": "rematricula",              # SEC-
    },
    "boletim": {
        "fields": ["student_name", "grade", "year", "subjects"],
        "eleve_request_type": "outros",                   # SEC- (endpoint futuro)
    },
}
```

---

## 7. Relatórios disponíveis (report_agent e NicoAgent)

O agente interpreta linguagem natural e mapeia para entidades da eleve-api:

| Entidade        | Endpoint eleve-api                              |
|---|---|
| inadimplência   | GET /contacts/guardians/{id}/invoices/?status=overdue |
| matrículas      | GET /secretary/enrollments/                     |
| frequência      | GET /requests/?tipo=declaracao&subtype=frequencia |
| solicitações    | GET /requests/                                  |
| leads           | inferido via GET /requests/ (tipo MAT-)         |

Formatos de saída: `xlsx` (openpyxl) ou `docx` (python-docx).

---

## 8. Convenção de tool

Segue o mesmo padrão do eleve-agent:

```python
@tool
async def nome_da_tool(param: str, sa_token: str, **kwargs) -> str:
    """Descrição em uma linha — o LLM usa isso para decidir quando chamar."""
    async with DjangoAPIClient(token=sa_token) as client:
        try:
            result = await client.get("/api/v1/endpoint/")
            return "Resultado formatado como texto ou JSON string"
        except Exception as e:
            logger.error("tool_name.error", error=str(e))
            return ""
```

---

## 9. AgentState

### agents/state.py — NicoState (doc_reader e report_agent)

```python
class NicoState(TypedDict, total=False):
    # comum
    user_id:    str
    school_id:  str
    sa_token:   str
    role:       str

    # doc_reader_agent
    doc_type:        str
    raw_file_b64:    str
    extracted:       dict
    validated:       dict
    confidence:      float
    flags:           list[str]
    extraction_id:   str

    # report_agent
    user_prompt:     str
    report_plan:     dict
    report_data:     list[dict]
    file_format:     str        # "xlsx" | "docx"
    file_path:       str
    file_id:         str
    summary:         str

    # saída
    response:        str
    error:           str | None
```

### agent/state.py — NicoState (NicoAgent conversacional)

**DIFERENTE do anterior.** Este é o estado do agente ReAct com memória de sessão.

```python
class NicoState(TypedDict, total=False):
    user_id:      str
    school_id:    str
    sa_token:     str
    role:         str
    user_name:    str      # nome do gestor (personalização)
    session_id:   str
    messages:     list[dict]   # formato OpenAI, serializável para Redis
    user_message: str
    tool_calls:   list[dict]
    response:     str
    error:        str | None
    faq_plan:          dict | None  # FaqPlan serializado — escrito pelo tool_node após build_faq_plan
    tool_error_counts: dict         # contador de falhas consecutivas por tool — reset a cada turno
```

**ATENÇÃO:** Nunca importe os dois `NicoState` no mesmo arquivo. Use o caminho
completo do import para distinguir: `from agent.state import NicoState` vs
`from agents.state import NicoState`.

---

## 10. Arquitetura conversacional

### Sessões (PostgreSQL)

```
models/session.py → ManagerSession
  id, user_id, school_id, role, user_name
  title (default "Nova conversa"), status (active/paused/completed)
  summary, is_deleted, message_count, report_count
  created_at, last_activity_at, ended_at

models/message.py → ManagerMessage
  id, session_id (FK), role (user/assistant/tool)
  content, tool_calls (JSON), metadata_ → coluna "metadata" (JSON)
  created_at
```

Migrations em `alembic/versions/`. Aplicar com:
```bash
poetry run alembic upgrade head
```

### Memória Redis (core/memory.py)

```
Key:   nicodemus:context:{session_id}
Valor: JSON list, máximo 20 mensagens, TTL 24h

get_context(session_id) → list[dict]
set_context(session_id, messages)
append_turn(session_id, user_msg, assistant_msg)
rebuild_from_db(session_id, messages)
clear_context(session_id)
```

### NicoAgent ReAct (agent/nico_agent.py)

```
StateGraph: llm_node → should_use_tools → tool_node → llm_node (loop) | END

llm_node:   converte messages list[dict] → LangChain messages → gpt-4o-mini
tool_node:  injeta sa_token + school_id em cada tool call
TOOLS_REGISTRY (agent/tools/__init__.py):
  - generate_financial_report   (inadimplência)
  - generate_enrollments_report (matrículas)
  - generate_requests_report    (solicitações)
  - list_faqs                   (leitura de FAQs com cache Redis)
  - analyze_faqs                (análise determinística via FaqAnalyzer)
  - build_faq_plan              (gera plano via gpt-4o-mini)
  - execute_faq_plan            (executa acções aprovadas na eleve-api)
```

### SessionService (services/session_service.py)

Métodos estáticos que recebem `db: AsyncSession`. Usam `flush()`, nunca `commit()`.

```
create_session(db, user_id, school_id, role, user_name) → ManagerSession
get_or_resume(db, session_id, user_id) → ManagerSession
add_message(db, session, role, content, tool_calls, metadata) → ManagerMessage
set_title(db, session, title)         # só atualiza se title == "Nova conversa"
close_session(db, session, summary)   # status=completed, limpa Redis
increment_report_count(db, session)
```

---

## 11. Regras de negócio

1. **Revisão humana obrigatória** antes de qualquer PATCH/POST na eleve-api
2. **Confidence < 0.80** → marcar flag e forçar revisão campo a campo
3. **Arquivo gerado expira em 15 minutos** — nunca armazenar permanentemente
4. **Log de toda ação** com `user_id`, `school_id` e `action` para auditoria
5. **Erros de extração** → retornar campos parciais + lista de flags, nunca exception 500
6. **Relatório vazio** → retornar `{ summary: "Nenhum registro encontrado", data: [] }`
   — nunca gerar arquivo em branco
7. **Sessão encerrada** → rejeitar POST /chat/ com 400, exigir nova sessão
8. **Contexto Redis vazio** (sessão retomada) → reconstruir a partir do banco automaticamente

---

## 12. Variáveis de ambiente

| Variável           | Descrição |
|---|---|
| `ELEVE_API_URL`    | URL da Eleve API |
| `OPENAI_API_KEY`   | Chave OpenAI |
| `JWT_SECRET`       | Secret para validar JWT do dashboard |
| `JWT_ALGORITHM`    | Algoritmo JWT (default: HS256) |
| `REDIS_URL`        | URL do Redis |
| `FILE_STORAGE_TTL` | TTL dos arquivos gerados em segundos (default: 900) |
| `FILE_STORAGE_DIR` | Diretório temporário (default: /tmp) |
| `DATABASE_URL`     | URL PostgreSQL async (ex: postgresql+asyncpg://...) |

---

## 13. Gotchas críticos do SQLAlchemy async

1. **`expire_on_commit=False`** em `async_sessionmaker` — evita `MissingGreenlet` ao
   acessar atributos após commit fora do contexto da sessão.

2. **`lazy="select"` em todos os relacionamentos** — nunca usar `lazy="joined"` em async.
   Sempre fazer queries explícitas ao precisar de dados relacionados.

3. **`metadata_` ≠ `metadata`** — a coluna `metadata` em `ManagerMessage` usa o atributo
   Python `metadata_` porque `metadata` é atributo reservado do `DeclarativeBase`.

4. **`flush()` nos services, `commit()` no get_session** — services chamam `flush()` para
   obter IDs gerados sem encerrar a transação. O `get_session` dependency faz o commit.

5. **`alembic/env.py` deve ser async** — o arquivo gerado por `alembic init` é síncrono.
   O env.py deste projeto usa `asyncio.run()` + `create_async_engine`.

---

## 14. Módulo FAQ Manager

### Schemas
- `schemas/faq_schemas.py` — tipos Pydantic do módulo FAQ Manager:
  FaqItem, FaqDiff, FaqAction, FaqPlan, FaqIssue, FaqAnalysisResult,
  FaqExecuteRequest, FaqExecuteActionResult, FaqExecuteResponse

### Ficheiros
- `agent/tools/analyzers/faq_analyzer.py` — FaqAnalyzer — análise determinística pura, sem LLM
- `agent/tools/faq_tools.py` — 4 ferramentas: list_faqs, analyze_faqs, build_faq_plan, execute_faq_plan
- `agent/tools/__init__.py` — TOOLS_REGISTRY actualizado com as 4 ferramentas FAQ
- `agent/state.py` — NicoState com faq_intent, faq_plan, pending_actions
- `agent/nico_agent.py` — system_prompt actualizado + detecção de intenção FAQ no llm_node
- `routers/chat.py` — POST /chat/faq/execute + GET /chat/faq/plan/{id}

### Fluxo completo
1. Gestor envia mensagem com intenção FAQ → llm_node detecta via keywords
2. LLM chama analyze_faqs → FaqAnalyzer corre análise determinística (zero LLM)
3. LLM chama build_faq_plan → gpt-4o-mini redige o plano → guardado no Redis (TTL 30min)
4. Nicodemus apresenta plano com before/after por acção (máx. 20 acções)
5. Gestor aprova/rejeita/edita acções no frontend
6. Frontend chama POST /chat/faq/execute com acções aprovadas
7. execute_faq_plan itera sobre acções → PATCH/POST na eleve-api
8. Resultado devolvido ao gestor em PT-BR

### Redis keys do módulo FAQ
```
nicodemus:faqs:{school_id}        → cache de list_faqs (TTL 5 min)
nicodemus:faq_plan:{plan_id}      → plano gerado (TTL 30 min)
  payload: {"school_id": "...", "plan": {...FaqPlan...}}
```

### Regras de negócio
- execute_faq_plan NUNCA corre sem plan_id válido no Redis
- Acções do tipo `deactivate` NUNCA deletam — só mudam `status=inactive`
- Cada execução logada com user_id, school_id, plan_id, action_id, status
- Plano associado ao school_id — outro tenant não pode executá-lo (403)
- FaqAnalyzer é Python puro — NUNCA usar LLM para análise determinística
- Cache de list_faqs: TTL 5 min por school_id (invalidar manualmente após execute)

---

## 15. Histórico de features

- **ELE-203 ✅** — GET /sessions/summary/ criado em `routers/sessions.py`;
  `SummaryResponse` adicionado a `schemas/session_types.py`;
  rota posicionada antes de `/{session_id}/` para evitar conflito UUID.

- **ELE-211 ✅** — NameError corrigido em `analyze_faqs` (`agent/tools/faq_tools.py`):
  `intent=intent` removido do `logger.info` (variável inexistente nesse escopo).

- **ELE-212 ✅** — `faq_plan` escrito no `NicoState` após `build_faq_plan`:
  reset para `None` no `llm_node`; detecção e escrita no `tool_node` (`agent/nico_agent.py`).

- **ELE-213 ✅** — `faq_plan: Optional[dict]` adicionado a `ChatResponse`
  (`schemas/session_types.py`) e propagado no endpoint POST /chat/ (`routers/chat.py`).

- **ELE-214 ✅** — `_FAQ_PRIMARY_TRIGGERS` definido como constante de módulo em
  `agent/nico_agent.py` (13 gatilhos). Frases naturais do gestor como "verifique respostas
  vazias", "corrija duplicadas" ou "melhore a central de ajuda" agora disparam o fluxo
  FAQ sem exigir "faq" ou "pergunta" explícitos.

- **ELE-215 ✅** — Circuit breaker no `tool_node` (`agent/nico_agent.py`) para evitar
  `GraphRecursionError` quando uma tool devolve `{"error": "..."}` sem lançar exception.
  `tool_error_counts: dict` adicionado ao `NicoState` (`agent/state.py`); contador resetado
  a cada turno no `llm_node`; após 2 falhas consecutivas na mesma tool, o `tool_node` termina
  o grafo imediatamente com `error: "circuit_breaker:{tool}"` e resposta amigável ao gestor.

- **ELE-216 ✅** — `list_faqs` e `execute_faq_plan` corrigidos para usar `DjangoAPIClient`
  (`Authorization: ServiceKey`) em vez de `httpx.AsyncClient` manual (`Authorization: Bearer`).
  `from typing import Any` removido (deixou de ser usado). `agent/tools/faq_tools.py`.

- **ELE-217 ✅** — Loop infinito no NicoAgent corrigido (`agent/nico_agent.py`).
  Causa raiz: a mensagem `assistant` guardada no histórico não incluía `tool_calls`,
  tornando os `ToolMessage` seguintes órfãos — o LangChain removia-os e o LLM voltava
  a chamar a mesma ferramenta indefinidamente.
  Dois pontos corrigidos no `llm_node`:
  (1) ao fazer append da mensagem assistant, inclui agora `tool_calls` em formato OpenAI
  (`{id, type, function: {name, arguments}}`);
  (2) ao reconstruir LangChain messages, o bloco `elif r == "assistant"` usa agora
  `ToolCall(id, name, args)` e lê `tc["function"]["name"]` / `json.loads(tc["function"]["arguments"])`.
  Import `ToolCall` adicionado ao topo do módulo.

- **ELE-218 ✅** — Erro de validação Pydantic em `build_faq_plan` corrigido (`agent/tools/faq_tools.py`).
  Causa raiz: o LLM gerava `"before": "General"` (string) em vez de `"before": {"category": "General"}`
  (objecto), porque o exemplo no prompt mostrava `"before": null` para todos os tipos de acção.
  Correcção: bloco de `actions` no prompt substituído por dois exemplos explícitos — um `edit`
  com `before`/`after` como objectos JSON completos, e um `create` com `before: null`.
  Quatro regras adicionadas ao fim da lista: `before` e `after` são SEMPRE objectos JSON;
  `deactivate` exige `after: {"status": "inactive"}`; `edit` de categoria exige
  `before` com pelo menos `{"category": "..."}`; `create` exige `before: null`.

- **ELE-219 ✅** — `faq_plan` apagado pelo `llm_node` após `tool_node` escrever o plano
  (`agent/nico_agent.py`). Causa raiz: o `llm_node` incluía `"faq_plan": None`
  incondicionalmente no return, apagando o plano antes de chegar ao `ChatResponse`.
  Correcção: `faq_plan` só é resetado a `None` quando o LLM não chamou `build_faq_plan`
  neste turno **e** não existe `faq_plan` no estado corrente — caso contrário a chave é
  omitida do return, preservando o valor escrito pelo `tool_node`.

---

## 13. Deploy em Produção (Swarm) — Lições aprendidas

### DATABASE_URL
- Driver obrigatório: `postgresql+asyncpg://`
- asyncpg **não** funciona com Transaction Pooler do Supabase (porta 6543)
- Usar **Session Pooler** (mesmo host do pooler, porta **5432**) — IPv4 + asyncpg compatível
- Conexão direta Supabase (`db.<ref>.supabase.co:5432`) exige IPv6 — VPS usa IPv4, não funciona

### Rede overlay
- `internal: true` bloqueia acesso à internet — remover quando o serviço precisa sair para APIs externas (Supabase, OpenAI)
- `nico_redis` continua isolado sem portas expostas — segurança mantida sem `internal: true`

### Imports
- `tools/__init__.py` deve estar vazio — nunca importar de `agent.tools` aqui
- Importações circulares entre `tools/` e `agent/tools/` causam `ModuleNotFoundError` no startup

### Password com caracteres especiais na URL
- `,` → `%2C`, `!` → `%21`, `/` → `%2F`, `%` → `%25`
