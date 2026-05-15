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

## Config-Driven Scout Queries

- Externalized Tavily Scout's queries from env-baked strings to a YAML
  config file at `config/scouts/tavily_queries.yaml`, bind-mounted
  read-only into the supervisor container. Edit-and-restart workflow
  with no rebuilds; failing-fast YAML validation surfaces
  misconfiguration immediately
- **Separation of concerns between env vars and config files** in the
  supervisor: env vars travel with the container (cadence, MCP target,
  feature flags); YAML files travel with the project and reload on
  restart (curated content like queries, prompts, ontology). Different
  lifecycles, different mechanisms — visible in the code by living in
  separate modules (`schedule.py` vs `config_loader.py`)
- Designed as a stepping stone to graph-aware query generation: the
  queries source is now a function call (`load_tavily_queries()`)
  rather than a hardcoded list, so swapping in graph-derived queries
  was a one-line change in one place

## Graph-Aware Query Generation

- Built **query planner that derives Tavily Scout queries from current
  graph state**, making the system genuinely agentic in its discovery
  rather than executing a fixed query list. Three derivation strategies:
  - **Corroboration queries** for singleton-mentioned concepts ("did
    anyone else write about X?") — finds second sources for one-shot
    observations
  - **Velocity-spike queries** for concepts whose mention rate just
    jumped — searches for the announcement or launch that triggered
    the spike
  - **Adjacency queries** for high-output authorities — "what else has
    <Lab> announced recently?" because labs that ship one notable
    thing usually ship others
- **Pull-based architecture**: queries are recomputed at supervisor
  startup and on each YAML reload, not pushed by a separate scheduled
  agent. The graph is millisecond-cheap to query at this scale; pull
  avoids premature complexity of a separate planner agent
- **Static + derived combined**, with static-from-YAML taking precedence
  in dedup. Cold-start scenarios (empty graph) gracefully fall back to
  the curated static set; failures in any single derivation strategy
  are caught and logged without breaking the Scout
- Pure-templating implementation (deterministic, free, fast) with a
  clean swap-in point for SLM-based query rewriting if templates ever
  underperform — same Lever-1-then-Lever-2 sequencing principle that
  let us avoid building the wrong abstraction first
- **The architectural moment**: this is when AgentRadar moved from
  "agent system that executes prescribed searches" to "agent system
  whose curiosity is informed by what it already knows" — the
  difference between user-driven RAG and genuinely autonomous research

## Discovery Methodology (interview talking point)

- Concept discovery flows from raw source text through SLM extraction
  to Postgres mentions to Neo4j relationships — concept names are
  **discovered by the agents from prose**, not predefined by the
  developer. The Top Concepts dashboard widget shows what the system
  decided was protocol/framework/pattern-shaped, not what was hardcoded
- Tavily query pool balances **specific named-thing lookups** (curated
  in YAML) with **open-ended discovery queries** ("new AI agent
  protocols announced") that surface names the system has never
  encountered before. Roughly 70% of new concepts come from
  open-ended queries; 30% are corroborating evidence for previously-named ones
- Demonstrable in a 2-minute interview: *"The system noticed Anthropic
  introduced multiple concepts I track. Without me writing the query,
  the next Scout run searches 'new agent framework or tool from
  Anthropic.' If they announced something new this week, it surfaces
  here before I knew to look for it."* That's the thing the project
  promises to do — and it actually does it


## Fourth Agent (TrendScout) — Multi-Source Trend Aggregation

- Built **TrendScout** that watches heterogeneous trend sources and
  funnels them through the same SLM extraction + proposer-critic
  pipeline as the other Scouts. Three concrete sources today, all of
  which pulled real signal on first run:
  - **GitHub trending repos** in agentic-AI topics via the GitHub Search
    REST API (chosen for stability after Atom feeds and HTML scraping
    both proved deprecated/unreliable)
  - **Hacker News front-page** filtered for agent-related keywords via
    Algolia's free HN API
  - **Lab RSS feeds** from OpenAI, Google AI, DeepMind, LangChain, and
    HuggingFace — first-party announcements before they reach aggregators
- **Two-tier abstraction**: `TrendScout` agent class implements the
  `Agent` protocol at the supervisor boundary. Underneath, a
  `TrendSource` Protocol lets each source handle its own fetching while
  emitting uniform `TrendItem` instances. Adding Reddit, Discord, or
  another source is one new file in `trend_sources/` — the abstraction
  is now proven across four very different shapes (RSS, REST API,
  search API, JSON+scraping fallback)
- **Per-source error isolation validated in production**: on first run,
  four of nine sources had transient HTTP issues (deprecated Atom
  feeds, redirects, 404s, HTML structure changes). The supervisor
  logged each failure with diagnostic context, unaffected sources kept
  producing data, and fixes were targeted single-file edits — never
  architectural. Each source now uses the most stable interface
  available, chosen empirically rather than by convention
- **Critic generalized cleanly across the new sources**: three new
  prefix branches in the source-dispatch table (`trend-github:`,
  `trend-hn:`, `trend-lab_rss:`) for faithfulness validation. The
  pattern that worked for Tavily continues to scale; eventually this
  dispatch deserves its own module, but six branches still fit
  inline cleanly
- Each source is **independently configured via YAML** under
  `config/scouts/trend_*.yaml` — topics, keywords, feed URLs, time
  windows. Same edit-and-restart workflow as Lever 1


## ROMA Orchestrator on LangGraph

- Built **ROMA recursive supervisor** as a LangGraph `StateGraph` — the
  Atomizer → Planner → Executor → Aggregator pattern from the paper,
  expressed as a compiled state machine with typed state flowing through
  nodes and conditional edges. Designed as a general orchestration
  primitive at the supervisor root, with the Forecaster as its first
  consumer
- **Typed state schema (`ForecastState`)** with `Annotated[..., operator.add]`
  reducer fields so multi-path execution (Session 2's composite tasks)
  merges results cleanly without state-schema changes
- **Heterogeneous node strategy by design**: Atomizer + routing functions
  are deterministic Python (no LLM cost on every dispatch); Executor is
  the only LLM-bound node, where the actual reasoning happens; Aggregator
  is deterministic (composes results, classifies confidence band). This
  is the SLM/LLM-tier-per-role principle from the README's NVIDIA-SLM
  reference, applied at the orchestrator level
- **Hard recursion cap (`MAX_DEPTH = 3`)** with forced atomicity at the
  cap — prevents runaway recursion while allowing genuine multi-level
  decomposition (Session 2 territory)
- Conscious architectural distinction: **knowledge graph (Neo4j) is
  what we know; orchestration graph (LangGraph StateGraph) is how we
  think about what to do next.** Same word, different categories — the
  README's "graph" prose now refers to both with care
- Composite-workflow scaffolding present but deliberately unfilled: in
  Session 1 the Planner returns empty subtasks and routes directly to
  the Aggregator, so the atomic path is fully exercised without the
  unfinished recursion logic interfering. Session 2 plugs in real
  decomposition without touching the graph topology

## Fifth Agent (Forecaster) — Headline Output

- Built **Forecaster agent** consuming the (graph-aware, multi-source)
  knowledge graph and producing the system's headline output: a
  structured forecast about a tracked concept with confidence band,
  horizon, reasoning, and cited evidence. **AgentRadar now does the
  thing the README promises**: data flows in via four Scouts, the
  Critic validates, the graph grows, and the Forecaster reads from it
  to make actual claims about the future
- **Single-concept atomic workflow (Session 1)**: forecasts one
  highest-velocity-not-recently-forecasted concept per invocation.
  Candidate selection is one SQL query against `mention_events` and
  `forecasts` joined; deliberately narrow so the orchestration shape
  is validated end-to-end before adding the multi-concept digest
  (Session 2)
- **Structured-output pipeline via Pydantic**: the Forecaster's
  Executor calls the SLM with a JSON-schema prompt, parses the response
  through a `CandidateForecast` Pydantic model with field-level
  validation (confidence in [0,1], horizon in [1,24] months). Markdown-
  fenced JSON from smaller models is handled defensively
- **Graceful degradation when the SLM stumbles**: if the model emits
  unparseable JSON, the Aggregator's weak-fallback produces an
  "Insufficient signal to forecast" row with `confidence_band='weak'`
  and `confidence=0.0` rather than crashing. The pipeline always
  completes; the dashboard always has data; the failure mode is
  honest about itself
- **MCP persistence with provenance**: new `propose_forecast` and
  `list_recent_forecasts` tools wire the Forecaster's output into the
  existing storage layer. The forecasts table carries `confidence_band`,
  `reasoning`, `cited_source_ids`, `evidence_snapshot`, and
  `predicted_at` — enough provenance for the Calibrator (future) to
  grade fairly months later
- **Pluggable model tier**: Session 1 uses Ollama's `llama3.2:3b`
  locally for everything; the Forecaster's interface to the SLM is
  identical to what Bedrock-served Claude would expose, so swapping
  in `BEDROCK_FORECASTER_MODEL_ID` (Session 2) is a config change,
  not a code change. Today the Forecaster ships forecasts on a
  hobbyist's laptop with zero API costs

---


## To-do

- ~~Critic agent (faithfulness validation, autonomous loop closure)~~ ✓
- ~~Operational dashboard with reverse proxy~~ ✓
- ~~Supervisor with env-driven schedule, autonomous Scout↔Critic loop~~ ✓
- ~~Second Scout (Tavily) + multi-source Critic dispatch~~ ✓
- ~~Config-driven Scout queries (Lever 1)~~ ✓
- ~~Graph-aware query generation (Lever 2)~~ ✓
- ~~TrendScout (Lever 3): GitHub, HN, lab RSS~~ ✓
- ~~Comprehensive testing: ~200 unit tests + ~40 integration tests~~ ✓
- ~~ROMA orchestrator on LangGraph (Forecaster's first workflow)~~ ✓
- ~~Forecaster Session 1: single-concept atomic forecasts~~ ✓
- Forecaster Session 2: composite digest workflow (top-N forecasts via recursion)
- Bedrock fallback for Forecaster (BEDROCK_FORECASTER_MODEL_ID)
- Dashboard widget surfacing live forecasts ← NEXT
- Calibrator with Brier-score back-grading
- Backtest: pre-MCP-launch (Nov 2024) data → does the system flag MCP?
- Terraform module for AWS ECS Fargate deployment