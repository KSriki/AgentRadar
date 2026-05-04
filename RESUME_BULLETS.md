# AgentRadar — Resume Capture

> Working notes for resume bullets. Not committed publicly (see .gitignore).
> Polish + cherry-pick for the master resume after each milestone.

---

## Headline (the one-paragraph version for the top of resume / LinkedIn)

> Architected and built **AgentRadar**, an autonomous multi-agent knowledge
> management system that maintains a Neo4j-backed knowledge graph of agentic
> AI protocols, frameworks, and architectural patterns. Implemented a
> heterogeneous-model agent mesh (small local models for narrow tasks, large
> cloud models reserved for high-stakes reasoning) coordinated through MCP
> tool federation with a storage-boundary proposer-critic gate. Stack:
> LangGraph, fastmcp, Claude on AWS Bedrock + Llama 3.2 via Ollama, Neo4j,
> Postgres + pgvector, FastAPI, Docker, uv workspace monorepo.

---

## Architecture & Design

- Designed dual-layer agentic KM system: **SpecTrack** (continuous ingestion
  of known specs into a typed knowledge graph) + **Horizon** (weak-signal
  detection across arXiv/GitHub/lab blogs) with a Calibrator agent that
  Brier-scores past forecasts to update future confidence weights —
  formal-forecasting calibration is unheard-of in agentic AI projects
- Chose **proposer-critic gate at the storage boundary** rather than in
  agent code: every triple proposed by an agent goes to a Postgres pending
  queue with deterministic content hashing; a separate Critic validates
  faithfulness against source before any commit reaches Neo4j. Even a
  misbehaving agent cannot corrupt the graph because the validation gate
  is enforced at the data layer
- Mapped **ROMA recursion** (Atomizer → Planner → Executor → Aggregator)
  onto LangGraph with hard depth cap and forced parent-context distillation
  at recursion boundaries — the specific anti-context-bloat pattern that
  makes deep agent hierarchies viable
- Adopted **heterogeneous SLM/LLM model assignment** per agent role
  (NVIDIA "SLMs for agentic AI" thesis): Scout/Extractor/Novelty/Calibrator
  on local 3B-parameter models; Critic/Forecaster on Claude Opus/Sonnet via
  Bedrock. Order-of-magnitude cost reduction vs. uniform-LLM agent designs
  with no quality loss on narrow roles

## Infrastructure & Tooling

- **uv workspace monorepo**: 2 shared packages + 3 runnable services, single
  lockfile, editable cross-package imports via `[tool.uv.sources]`. One
  `uv sync` resolves everything; `uv run --package` invokes individual services
- **Docker Compose data plane** with healthcheck-gated startup ordering:
  Neo4j 5 with APOC + Postgres 16 with pgvector + MinIO (S3-compatible) +
  one-shot init sidecars (using official `cypher-shell` and `mc` images)
  for race-free schema application on cold start
- **Multi-stage Dockerfile** for the API service using uv inside the
  container for sub-second incremental builds; non-root runtime; bind-mount
  + uvicorn `--reload` for live-reload dev workflow without rebuilds
- **Two-tier test architecture**: pytest-asyncio unit tests for pure logic,
  marker-gated integration tests against ephemeral Neo4j/Postgres/MinIO
  fixtures with automatic state cleanup. Default `pytest` runs only fast
  units; `pytest -m integration` opts into the full suite

## Data Layer

- Implemented **async data access layer** with lazy-singleton pattern:
  Neo4j 5 (APOC for dynamic typed relationships), asyncpg-backed Postgres
  with pgvector HNSW index for cosine-similarity novelty detection,
  aioboto3 S3 layer that switches between MinIO and AWS S3 by endpoint URL
  alone — same code, both environments
- **Idempotent proposer-critic queue** with deterministic content hashing:
  agents can re-propose the same finding without creating duplicate pending
  rows or undoing prior Critic decisions. Critical property for autonomous
  systems where the same Scout might run hourly against the same source
- Chose **HNSW over ivfflat** for pgvector indexing — correct on small
  datasets without parameter tuning; ivfflat silently returns wrong results
  on tiny tables due to its list/probe model

## Application & Tool Layer

- **Consolidated MCP server into FastAPI app** via fastmcp's ASGI
  integration: hand-written MCP tools mounted at `/mcp` share the same
  async store clients with REST endpoints — single deployable, single
  port, single container
- Built **twelve typed MCP tools** exposing the knowledge store to any
  MCP-compatible agent (Claude Desktop, MCP Inspector, LangGraph via
  langchain-mcp-adapters). Tool surface enforces the proposer-critic
  gate: `propose_triple` writes only to pending queue;
  `approve_triple` is the only path that commits to Neo4j, with
  race-winner semantics and explicit reconciliation flagging when
  cross-store writes partially fail
- **Pluggable SLM client abstraction** in the store package: same agent
  code switches between Ollama-served Llama 3.2 (3B params, runs on
  laptop with Metal GPU acceleration) and Claude Haiku via Bedrock by
  config alone. Demonstrates the heterogeneous-model thesis runnable
  end-to-end with no cloud account required for development

## Observability & Quality

- **Structured logging** via structlog with contextvar-based `trace_id`
  propagation: bind once at request entry, every log emitted by any
  agent/tool/DB call inside the request automatically carries the trace_id
  — full forensic traceability without manual parameter plumbing
- **Type-safe configuration** via pydantic-settings: nested settings
  classes per concern (Neo4jSettings, PostgresSettings, SLMSettings, etc.)
  with `SecretStr` for credentials so passwords never leak via repr/log.
  Fail-fast at process startup if required env vars are missing or
  malformed — never start running with broken config

## First Agent (Scout)

- Built **arXiv Scout** as the first specialist in AgentRadar's ingestion
  loop: pulls cs.AI/cs.LG RSS, dedupes via DB UNIQUE constraints, stores
  raw artifacts to S3 via MCP, makes a single SLM call for concept
  extraction from prose, proposes typed triples through the MCP-served
  proposer-critic gate
- Followed the **deterministic-orchestration / model-only-where-needed**
  pattern: 5-step pipeline, only step 4 (concept extraction from
  unstructured abstracts) uses an LLM. A 50-paper Scout run uses 50 cheap
  SLM calls, vs. ~150 LLM calls a naive ReAct loop would make for the
  same work — order-of-magnitude cost reduction at the same quality

## Second Agent (Critic) — Autonomous Loop Closure

- Built **Critic agent** that closes AgentRadar's autonomous loop:
  consumes from the pending-triples queue via MCP, validates each
  proposal through a three-stage pipeline (structural → ontology →
  faithfulness), and commits decisions back through the proposer-critic
  gate with no human in the loop. After Critic exists, Scout-proposes
  → Critic-decides → Neo4j-grows runs end-to-end without supervision
- Three-stage validation pipeline ordered cheap-to-expensive: structural
  regex check (free), ontology membership check against a known-predicate
  set (free), then SLM-driven faithfulness check (RAGAS-style: does the
  source document actually support the claim?). 1000 triples → ~800 SLM
  calls, not 1000 — same SLM-only-where-needed pattern as the Scout
- **Adopted Wikipedia's verifiability stance over correctness:** the
  Critic certifies that the source supports the claim, not that the
  claim is true in the world. Bounds downstream confidence by source
  reputation, makes the Critic's job tractable for a 3B local model,
  and keeps the trust boundary honest
- Structured-output prompting for verdict / reasoning / confidence —
  no free-form parsing. Defensive JSON-fence stripping for smaller-model
  habits; rejection-on-parse-failure to fail safe
- Race-aware MCP integration: Critic decisions go through approve_triple
  / reject_triple which are themselves race-protected at the database
  layer (only one Critic instance can decide a given triple, even if
  multiple are running)

## Autonomous Supervisor

- Built **long-running supervisor** that turns AgentRadar from script-driven
  to fully autonomous: 200-line asyncio scheduler in its own container,
  manages MCP client lifecycle, fires Scout every 2h and Critic every 15m,
  graceful SIGTERM shutdown, exponential backoff on MCP connect retry
- **Refactored Scout and Critic** from `scripts/` into specialist classes
  implementing an `Agent` Protocol — same logic, but invoked in-process by
  the supervisor instead of as standalone scripts. Original CLI scripts
  retained as ~30-line one-off wrappers for manual demos and debugging
- **Env-driven schedule** via pydantic-settings so deployments override
  cadence without code changes; `SCHEDULE_FIRE_ON_STARTUP=true` is the
  demo-mode escape hatch that fires every job at boot rather than waiting
  out the interval
- **Round-robin scheduling across arXiv categories** so the Scout doesn't
  drown one feed; **trace_id binding per agent invocation** so structured
  logs let you replay one run end-to-end across api, supervisor, and SLM
  output
- **Monotonic clock for interval tracking** (immune to host clock jumps);
  **shutdown event coupled with `asyncio.wait_for`** so Ctrl-C responds
  instantly instead of waiting out the tick interval — the kind of detail
  that separates a script that runs from a service that runs reliably

## Operational Dashboard

- Built **read-only operational dashboard** for AgentRadar: React + Vite +
  TanStack Query + Tailwind/shadcn-ui, single page with 5 widgets covering
  system health (data plane + SLM), knowledge-store counts, recent Critic
  decisions feed, top mentioned concepts with inline-SVG sparkline trends,
  and quick links to admin tooling. Auto-refreshes every 10s
- Added **REST API surface alongside MCP tools** in the existing FastAPI
  app via APIRouter — shares the same async store clients and trace_id
  propagation as the MCP tools, no code duplication
- Architected **single-port nginx reverse proxy** as the platform's public
  face: serves Vite dev server for `/`, proxies `/api/*` to FastAPI REST,
  proxies `/mcp` to fastmcp Streamable HTTP with `proxy_buffering off`
  (required for SSE), proxies `/docs` for the OpenAPI explorer.
  Eliminates CORS by making everything same-origin
- Dockerized the dashboard for first-class compose integration with HMR
  preserved through the proxy: bind-mounted source with chokidar polling
  to handle macOS Docker Desktop's filesystem-event quirks; WebSocket
  upgrade headers in nginx for Vite HMR; anonymous-volume pattern to keep
  Linux node_modules separate from host Mac binaries. **`docker compose up`
  brings up the entire system — data plane, agent API, MCP server, and
  dashboard — in one command**


## Autonomous Supervisor

- Built **long-running supervisor** that turns AgentRadar from script-driven
  to fully autonomous: 200-line asyncio scheduler in its own container,
  manages MCP client lifecycle, fires Scout every 2h and Critic every 15m,
  graceful SIGTERM shutdown, exponential backoff on MCP connect retry
- **Refactored Scout and Critic** from `scripts/` into specialist classes
  implementing an `Agent` Protocol — same logic, but invoked in-process
  by the supervisor instead of as standalone scripts. Original CLI
  scripts retained as ~30-line one-off wrappers for manual demos
- **Env-driven schedule** via pydantic-settings so deployments override
  cadence without code changes; `SCHEDULE_FIRE_ON_STARTUP=true` is the
  demo-mode escape hatch that fires every job at boot rather than
  waiting out the interval
- **Round-robin scheduling across arXiv categories** so the Scout doesn't
  drown one feed; **trace_id binding per agent invocation** so structured
  logs let you replay one run end-to-end across api, supervisor, and
  SLM output
- **Monotonic clock for interval tracking** (immune to host clock jumps)
  and **shutdown event coupled with `asyncio.wait_for`** so Ctrl-C
  responds instantly instead of waiting out the tick interval — the
  kind of detail that separates a script that runs from a service that
  runs reliably

## Third Agent (Tavily Scout) — Open-Web Horizon Scanning

- Built **Tavily-powered Scout** that complements the arXiv Scout's
  academic feed with open-web research: searches Tavily's AI-curated
  index for agent-protocol announcements, framework releases, and
  weak-signal blog posts. Critical for the project's headline thesis —
  catching MCP-equivalent things "before they have a Wikipedia page"
  requires the open web, not just academia
- **Same Agent Protocol, completely different shape**: arXiv Scout
  pulls structured RSS, Tavily Scout asks natural-language research
  questions. Both implement `Agent.run(mcp)`, both schedule into the
  same supervisor with no orchestration changes — the architectural
  prove-out for the agent abstraction
- **Confidence-weighted by Tavily's relevance score**: a result with
  Tavily score 0.9 gets propose_triple confidence 0.67; score 0.5
  gets 0.55. Lets the Critic reason about source quality even before
  faithfulness validation
- **Source-ID scheme uses sha256(url)[:32]** to give every web result
  a stable identifier without storing full URLs as primary keys; lets
  the Critic later fetch cleaned content from S3 for faithfulness
  checks the same way it does for arXiv abstracts
- **Round-robin queries with 6h cadence** — slower than arXiv (2h)
  because Tavily costs credits and the open web doesn't churn as fast
  as arXiv submissions. Six default queries cover the project's topic
  surface without over-spending

## Critic Generalization Across Source Types

- Refactored Critic's source-fetching to **dispatch by source-ID prefix**
  rather than `if source_id.startswith('arxiv:')` — each source type now
  knows how to render its S3 artifact as plain text for the SLM to
  faithfulness-check (`title + abstract` for arxiv, `title + url + content`
  for tavily). Adding the next source type (GitHub, RFC, etc.) is one
  new branch in a clear pattern
- **First multi-source autonomous run validated end-to-end**: arXiv
  preprints + Tavily web results both flowing through the same
  proposer-critic gate, both being faithfulness-checked by a 3B
  local SLM, both yielding typed Neo4j relationships when approved.
  Three real protocol acronyms (MCP, A2A, AP2) tracked across both
  source feeds within the first hour of running

---

## To-do

- ~~Critic agent (faithfulness validation, autonomous loop closure)~~ ✓
- ~~Operational dashboard with reverse proxy~~ ✓
- ~~Supervisor with env-driven schedule, autonomous Scout↔Critic loop~~ ✓
- ~~Second Scout (Tavily) + multi-source Critic dispatch~~ ✓
- Forecaster + first weekly digest output ← NEXT
- Calibrator with Brier-score back-grading
- ROMA supervisor in LangGraph (recursive multi-agent tasks)
- Additional Scouts: GitHub orgs, lab blogs
- Backtest: pre-MCP-launch (Nov 2024) data → does the system flag MCP?
- Terraform module for AWS ECS Fargate deployment