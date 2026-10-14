# EC Virtual Assistant — proof of concept

A virtual assistant with the European Commission look and feel (eUI), whose agent loop runs on
**Haystack 3** and whose long-term memory of each user is kept in a self-hosted
**TencentDB Agent Memory** server. Both LLMs are served by the **AI@EC Platform**.
Everything deploys to **Railway**.

> Demonstration prototype, not an official Commission service. Use public data only.

Architecture overview (design rationale, decisions, risks): see the shared architecture document.

```
browser ──HTTPS──▶ web (nginx + eUI) ──private──▶ agent (Hayhooks + Haystack 3 Agent) ──▶ AI@EC /responses (gpt-5.1)
                                                    │  before_run / after_run hooks, recall tools
                                                    ▼
                                        memory-core (TencentDB Agent Memory, SQLite on a volume)
                                                    │  L1–L3 distillation
                                                    ▼
                                  agent /llm-shim ──▶ AI@EC /chat/completions (Mistral) or /responses (gpt-5.1, when tools are used)
memory-hub (Tencent admin panel) ──private──▶ memory-core
```

## Repository layout

| Path | Railway service | What it is |
| --- | --- | --- |
| `web/` | `web` | Angular 21 + eUI 21 app, served by nginx; nginx forwards `/api/*` and `/v1/chat/completions` to the agent |
| `agent/` | `agent` | Python 3.12, `haystack-ai` 3.3, `hayhooks` 2.1: the Agent, memory hooks and tools, REST routes for the UI, the LLM shim used by memory-core |
| `memory-core/` | `memory-core` | Thin image on top of `agentmemory/memory-core` with our gateway configuration |
| `memory-hub/` | `memory-hub` | Thin image on top of `agentmemory/memory-hub` (Tencent's admin panel) |
| `agent/data/press/` | | Knowledge base: the five seed press releases and speeches (Markdown with JSON front matter; PDFs from the Press Corner also work) |

## How the pieces fit

* **Haystack 3 Agent** (`agent/va/assistant.py`): `OpenAIResponsesChatGenerator` on the AI@EC Platform
  (`/responses`, because `/chat/completions` silently drops `tools`), three tools, and hooks:
  * `before_run` → `load_user_context`: reads the user's L3 profile and relevant L1 memories from memory-core and
    adds them as one labelled system message.
  * `after_run` → `persist_turn`: writes the user/assistant pair to L0 in the background.
  * `before_llm` → `TokenBudgetHook` and `CompactionHook`; `after_tool` → `ToolResultOffloadHook` (safety net).
* **Tools** (`agent/va/tools.py`): `search_press_releases` (BM25 over the knowledge base), `recall_memory`,
  `forget_memory` (only ids shown in the current run can be deleted).
* **Memory** (`agent/va/memory.py`): Tencent's official Python SDK (`tencentdb_agent_memory.v3`, installed from the
  v2.0.2 Git tag because it is not on PyPI). Bootstrap is automatic: admin user, team `EC Virtual Assistant Demo`,
  agent `ec-virtual-assistant`. Each browser gets a pseudonymous `user_id`; each chat window a `session_id`.
* **LLM shim** (`agent/va/llm_shim.py`): memory-core's L2/L3 steps use tool calling over `/chat/completions`. The shim
  sends tool-free requests to Mistral and tool requests to gpt-5.1 via `/responses`, translating both ways.
* **Serving**: Hayhooks exposes the Agent as an OpenAI-compatible `/v1/chat/completions` with streaming
  (`model: "ec_assistant"`). Hayhooks' deploy/upload routes are disabled.

## Deploy on Railway

You need: a Railway account, the [Railway CLI](https://docs.railway.com/guides/cli) (optional), this repository on
GitHub, and an AI@EC Platform API token.

### 1. Create the project and four services

Create one Railway project in the **EU West (Amsterdam)** region. Add four services from this GitHub repository and
set each one's **Root Directory**; the service names matter, they become private host names:

| Service name | Root Directory | Volume (mount path) | Public domain |
| --- | --- | --- | --- |
| `memory-core` | `/memory-core` | yes: `/data/tdai-memory` (1 GB is plenty) | no |
| `agent` | `/agent` | no | no |
| `web` | `/web` | no | **yes** |
| `memory-hub` | `/memory-hub` | yes: `/data/knowledge` | only during demos, target port `8125` |

Each folder has a `railway.json` (Dockerfile build, health check, one replica).

### 2. Generate secrets

```bash
openssl rand -hex 32                                   # TDAI_GATEWAY_API_KEY   (memory-core)
openssl rand -hex 32                                   # LLM_SHIM_TOKEN         (agent)
openssl rand -hex 32                                   # KNOWLEDGE_SERVICE_KEY  (memory-hub)
echo "sk-mem-$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32)"   # MEMORY_ADMIN_USER_KEY (agent)
```

`MEMORY_ADMIN_USER_KEY` is also your login for the memory-hub panel. Keep it: it is bound to the memory-core
volume on first start.

### 3. Set the variables

`${{service.VAR}}` is Railway's reference syntax; paste it literally.

**memory-core**

| Variable | Value |
| --- | --- |
| `TDAI_GATEWAY_API_KEY` | secret from step 2 |
| `TDAI_GATEWAY_HOST` | `::` |
| `TDAI_LLM_BASE_URL` | `http://${{agent.RAILWAY_PRIVATE_DOMAIN}}:8000/llm-shim/v1` |
| `TDAI_LLM_API_KEY` | `${{agent.LLM_SHIM_TOKEN}}` |
| `TDAI_LLM_MODEL` | `aiec-via-shim` |
| `MEMORY_CORE_IMAGE` | `agentmemory/memory-core:<tag>` — pin a tag or digest after the first deploy (default `latest`) |

**agent**

| Variable | Value |
| --- | --- |
| `AIEC_API_KEY` | your AI@EC Platform token |
| `MEMORY_CORE_URL` | `http://${{memory-core.RAILWAY_PRIVATE_DOMAIN}}:8420` |
| `MEMORY_CORE_TOKEN` | `${{memory-core.TDAI_GATEWAY_API_KEY}}` |
| `MEMORY_ADMIN_USER_KEY` | `sk-mem-…` from step 2 |
| `LLM_SHIM_TOKEN` | secret from step 2 |
| `PORT` | `8000` |
| optional | `ASSISTANT_MODEL` (`gpt-5.1`), `ASSISTANT_REASONING_EFFORT` (`low`), `TOKEN_BUDGET` (`60000`), `SHIM_PLAIN_MODEL` (`mistral-small-3.2-24b-instruct-2506`), `SHIM_TOOLS_MODEL` (`gpt-5.1`), `AIEC_BASE_URL` |

**web**

| Variable | Value |
| --- | --- |
| `AGENT_URL` | `http://${{agent.RAILWAY_PRIVATE_DOMAIN}}:8000` |

**memory-hub**

| Variable | Value |
| --- | --- |
| `PANEL_PORT` / `KNOWLEDGE_PORT` | `8125` / `8424` |
| `REMOTE_INSTANCE_ID` / `REMOTE_INSTANCE_NAME` | `default` / `default` |
| `REMOTE_INSTANCE_URL` | `http://${{memory-core.RAILWAY_PRIVATE_DOMAIN}}:8420` |
| `REMOTE_INSTANCE_KEY` | `${{memory-core.TDAI_GATEWAY_API_KEY}}` |
| `KNOWLEDGE_PUBLIC_BASE_URL` | `http://${{RAILWAY_PRIVATE_DOMAIN}}:8424/v3` |
| `KNOWLEDGE_SERVICE_KEY` | secret from step 2 |
| `KNOWLEDGE_AUTH_TOKEN` | `${{KNOWLEDGE_SERVICE_KEY}}` |
| `LLM_MODE` / `LLM_PROTOCOL` | `custom` / `openai` |
| `LLM_BASE_URL` | `http://${{agent.RAILWAY_PRIVATE_DOMAIN}}:8000/llm-shim/v1` |
| `LLM_API_KEY` | `${{agent.LLM_SHIM_TOKEN}}` |
| `LLM_MODEL` | `aiec-via-shim` |
| `KNOWLEDGE_LLM_BINDING_SYNC` | `0` |
| `MEMORY_HUB_IMAGE` | `agentmemory/memory-hub:<tag>` (default `latest`) |

### 4. Deploy and check

Deploy in this order: `memory-core`, `agent`, `web`, then `memory-hub`. Then:

```bash
railway ssh --service agent        # or the service's shell in the Railway dashboard
python -m va.smoke                 # AI@EC models, tool calling, memory-core bootstrap, FTS5, write/read round trip
```

The smoke test also tells you whether Mistral honours `tools` on `/chat/completions`; if it does, the shim is
optional. `GET /api/health` on the web domain shows the same status the UI shows in its badges.

## Demo script (about 5 minutes)

1. Open the web domain. Badges should read *Long-term memory on*, *5 documents*, *gpt-5.1 · AI@EC*.
2. Click *From now on, answer me in Greek and keep answers to three sentences.*
3. Ask *What emergency support did farmers in Czechia, Slovenia and Germany receive?* — the answer cites IP/25/1307;
   the side panel lists the source; *Saved to memory* appears under the answer.
4. Wait a minute or two, click the refresh icon in *What I remember about you*: the preference appears as an L1
   memory (memory-core distils it asynchronously).
5. Click *New conversation* and ask about the car recycling cartel: the answer comes in Greek, and *Memory used in the
   last answer* shows why.
6. Optional: open memory-hub with `MEMORY_ADMIN_USER_KEY` to show the same memory as a governed asset (owner, team,
   agent). Delete a memory from the side panel, or use *Forget everything*.

## Local development

```bash
# agent (Python 3.12+)
cd agent
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # 21 tests, no network, no keys (MockChatGenerator + mocked memory-core)

# offline end-to-end demo: real agent and UI, scripted LLM, in-memory fake memory-core
(cd ../web && yarn install && yarn build)
python -m dev.run_offline                # http://localhost:8000

# web against a running agent on :8000
cd ../web && yarn start                  # http://localhost:4200, proxies /api and /v1 to :8000
```

## Known limitations

* No authentication: the pseudonymous id in the browser scopes memory. Anyone who learns an id can read that
  user's memories. Public data only.
* memory-core keeps SQLite on one volume: one replica only; back up the volume if the demo data matters.
* Memory distillation is asynchronous; L1 appears after about a minute, the L3 profile after about ten turns.
* Recall is BM25 keyword search (no embedding endpoint configured). Paraphrased questions may miss memories.
* The AI@EC Platform silently drops unsupported parameters; behaviour was designed from its documentation and must
  be confirmed with `python -m va.smoke` on first deploy.
* Tencent Docker images are referenced by tag: pin a digest once you have a working deployment.
