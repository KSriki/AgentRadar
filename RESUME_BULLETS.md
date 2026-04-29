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

---

## To-do

- Critic agent (faithfulness validation, autonomous loop closure)
- ROMA supervisor wired into LangGraph with checkpointer
- Forecaster + first weekly digest output
- Calibrator with Brier-score back-grading
- Backtest: pre-MCP-launch (Nov 2024) data → does the system flag MCP?
- Next.js dashboard
- Terraform module for AWS ECS Fargate deployment