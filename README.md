# AgentRadar

> **Autonomous agentic knowledge management for the agentic AI ecosystem itself.**
>
> AgentRadar is a multi-agent system that maintains a living knowledge graph of
> AI agent protocols, frameworks, and architectural patterns — combining stable
> spec tracking with weak-signal horizon scanning to produce citation-backed
> quarterly forecasts of what's likely to matter next.

> ⚠️ **Status: under active construction.** The data plane and shared libraries
> are working; the agent layer is being built incrementally. See [Roadmap](#roadmap).

---

## Why this exists

The agentic AI space is reshaping itself faster than any one engineer can track
manually. The shift happens on three fronts simultaneously:

**Protocols.** **MCP** (Model Context Protocol, Anthropic, Nov 2024) and **A2A**
(Agent-to-Agent, Google, Apr 2025) reshaped the field within a year of their
release — both reached production adoption before most teams had finished
reading the original announcement post. Since then **ACP**, **ANP**, **AP2**,
**UCP**, and **UTCP** have entered the conversation, with more arriving every
quarter.

**Orchestration patterns.** Agent-graph designs are evolving from flat
ReAct loops toward recursive meta-architectures. **ROMA** ([Recursive Open
Meta-Agent](https://arxiv.org/abs/2602.01848)) is a representative example:
a four-role Atomizer → Planner → Executor → Aggregator loop where Executors
can re-invoke the supervisor on non-atomic subtasks. The pattern matters
because it specifically addresses context-bloat in deep agent hierarchies —
a problem that flat orchestration silently fails on.

**Model deployment economics.** NVIDIA's
[*Small Language Models are the Future of Agentic AI*](https://arxiv.org/abs/2506.02153)
(Belcak et al., 2025) makes the case that most agentic invocations are
narrow, repetitive, specialized tasks for which SLMs are sufficient —
and dramatically cheaper to run. The paper's stronger claim is that
**heterogeneous** systems (SLMs for narrow tasks + LLMs only where general
reasoning is essential) are the natural endpoint, not pure-LLM swarms.

The question this project asks:

> *Could an autonomous system have flagged MCP as significant in
> December 2024 — based only on public signals — before it had a Wikipedia page?*

If yes, the same system can flag whatever the next MCP-equivalent is *now*,
buying engineers and decision-makers a real lead time advantage.

## What it does

AgentRadar runs two complementary loops, both fully autonomous:

**SpecTrack** (rear-view mirror)
Continuously ingests known specs, frameworks, and standards from authoritative
public sources. Builds a typed knowledge graph of the current state of the
agentic stack: who introduced what, what supersedes what, what's competing
with what, and what depends on what.

**Horizon** (windshield)
Scans low-profile signals — arXiv preprints, fresh GitHub orgs, lab blog posts,
RFC drafts — for emerging concepts before they have widespread adoption. Tracks
mention velocity across independent sources and detects multi-lab convergence
patterns. Produces a quarterly forecast document with explicit confidence bands
and full citation provenance.

A **Calibrator** agent grades past forecasts against actual outcomes, feeding
the calibration data back into future confidence weighting. Over time, the
system's track record on its own predictions becomes a measurable artifact.

## How it actually runs

`docker compose up -d` brings up the entire system:

- **nginx** (port 80) — single public entry point; serves the dashboard,
  proxies API and MCP traffic
- **dashboard** — Vite-served React app with hot-reload through the proxy
- **api** — FastAPI hosting both REST endpoints and the fastmcp tool surface
- **supervisor** — long-running asyncio scheduler that fires Scout every
  2 hours and Critic every 15 minutes (env-overridable). Connects to the
  api's MCP endpoint on a persistent client; new trace_id per agent run
  for full forensic logging
- **neo4j**, **postgres** (with pgvector), **minio** — the data plane
- **Ollama on host** — Llama 3.2 3B for narrow agent tasks (Scout's concept
  extraction, Critic's faithfulness validation)

Open `http://localhost` and within 15 minutes the dashboard fills with real
arXiv data flowing through the proposer-critic loop, no human input required.

## Why this design

A few choices that aren't obvious and deserve naming:

**Proposer-critic gate at the storage boundary, not in agent code.**
Every triple proposed by an agent goes into a Postgres queue with a
deterministic content hash. A separate Critic agent validates faithfulness
(does the source actually contain this claim?) and ontology compliance before
anything is committed to Neo4j. Even a misbehaving agent cannot corrupt the
graph because the validation gate is enforced at the data layer, not by
convention.

**Public sources only, read-only outputs.**
The system never writes to anyone else's services and never represents the
user externally. Worst-case failure mode is a confidently wrong forecast —
embarrassing, not damaging. This is a deliberate scope choice that lets the
agents run truly unattended.

**ROMA orchestration mapped onto LangGraph.**
The supervisor is a recursive Atomizer → Planner → Executor → Aggregator
loop. When an Executor decides a subtask is itself non-atomic, it re-invokes
the supervisor at depth+1 with a *distilled* parent context, never the full
parent state. A hard depth cap prevents runaway recursion. This is the
specific anti-context-bloat pattern that makes deep agent hierarchies viable.

**Heterogeneous models per agent role.**
Following the NVIDIA SLM-for-agents thesis, AgentRadar is designed to use
small language models for narrow, repetitive roles (Scout, Extractor,
Novelty Detector, Calibrator) and reserve large models for the high-stakes
reasoning roles (Critic, Forecaster). Each agent reads its model from
config — the same code runs against Ollama-served SLMs locally and Bedrock-
served Claude in production. The cost-per-forecast difference between
"every agent uses Opus" and "the right model for each job" is roughly an
order of magnitude.

**Self-calibration over self-confidence.**
Every forecast carries an explicit confidence number. Every quarter, the
Calibrator looks back at predictions made 1, 2, and 4 quarters earlier and
grades them as hit/miss/partial. The graded outcomes update the weights that
determine confidence on future forecasts. Brier-score calibration is standard
in formal forecasting and almost unheard-of in agentic AI projects — it's
what differentiates a forecast you can trust from one you can't.

## Architecture

                    ┌─────────────────────────────────────┐
                    │  Supervisor (asyncio scheduler)     │
                    │  Scout → 2h · Critic → 15m          │
                    │  env-driven cadence, graceful SIGTERM│
                    └────────────┬────────────────────────┘
                                 │ MCP (HTTP)
                       ┌─────────▼──────────┐
                       │   FastAPI + MCP    │
                       │  REST + tool layer │
                       └─────────┬──────────┘
                                 │
       ┌──────────────┬──────────┼──────────┬──────────────────┐
       ▼              ▼          ▼          ▼                  ▼
  ┌─────────┐    ┌────────┐ ┌────────┐ ┌────────┐         ┌──────────┐
  │  Scout  │    │ Critic │ │ Future │ │ Future │   ...   │ Future   │
  │ (arXiv) │    │        │ │ Scouts │ │Forecast│         │Calibrator│
  └─────────┘    └────────┘ └────────┘ └────────┘         └──────────┘
       │              │          │          │                  │
       └──────────────┴──────────┴──────────┴──────────────────┘
                                 │
                       ┌─────────▼─────────┐
                       │   MCP Tool Layer  │
                       │ • Neo4j           │
                       │ • pgvector        │
                       │ • S3 (MinIO)      │
                       │ • SLM (Ollama)    │
                       └─────────┬─────────┘
                                 │
                  ┌──────────────▼──────────────┐
                  │    Knowledge Store          │
                  │ Neo4j: typed graph          │
                  │ pgvector: embeddings        │
                  │ S3:    raw artifacts        │
                  └─────────────────────────────┘

   Reverse proxy (nginx :80)
     /         → React dashboard
     /api/*    → REST endpoints
     /mcp      → fastmcp Streamable HTTP
     /docs     → FastAPI Swagger

### The agents

| Agent | Role | Model class | Scheduled |
|---|---|---|---|
| **Scout** (one per source class) | Pull new artifacts from arXiv, GitHub, lab blogs, RFCs, conference proceedings. Dump raw content to S3, emit "saw something" events. | SLM | Hourly–daily |
| **Extractor** | Pull structured triples (subject, predicate, object, source, confidence) out of raw artifacts. Output goes to the proposal queue, never directly to the graph. | SLM | Per-event |
| **Novelty Detector** | For each candidate concept, decide whether it maps to an existing graph node (refinement) or is genuinely new (potential signal). Vector similarity + name matching. | SLM | Per-proposal |
| **Critic** | Validate every proposed triple before commit: faithfulness against source, ontology compliance, source reputation. Reject or approve. | **LLM** | Continuous |
| **Forecaster** | Query the graph for rising-velocity concepts, multi-lab convergence patterns, breaking-version signals. Generate the forecast document. | **LLM** | Weekly + quarterly |
| **Calibrator** | Look back at past forecasts whose horizon has elapsed. Grade them. Update confidence-weighting parameters. | SLM | Quarterly |

### The knowledge graph schema

```
Concept ──INSTANCE_OF→ ConceptType {Protocol, Framework, Pattern, Model, Tool}
Concept ──INTRODUCED_BY→ Authority {Lab, Company, Researcher}
Concept ──FIRST_SEEN_IN→ Source {Paper, Repo, BlogPost, Spec}
Concept ──IMPLEMENTS→ Concept    (e.g., FastMCP IMPLEMENTS MCP)
Concept ──COMPETES_WITH→ Concept (e.g., A2A COMPETES_WITH ANP)
Concept ──SUPERSEDES→ Concept    (e.g., A2A SUPERSEDES IBM ACP after merger)
Concept ──MENTIONED_IN→ Source   {observed_at, sentiment, reach_score}

Forecast ──PREDICTS→ Concept {predicted_at, confidence, horizon_months}
Forecast ──CITES→ Source
Forecast ──GRADED_AS→ Outcome {hit, miss, partial}
```

Temporal properties on edges (`observed_at`, `predicted_at`, `decided_at`) are
what make the Calibrator's look-back honest. You can ask the graph "what did
we know about MCP in March 2026, and what did we predict from there?" and get
a real answer.

## Tech stack

**Language & runtime**
- Python 3.12, async-first
- [uv](https://docs.astral.sh/uv/) workspace monorepo, single lockfile, editable cross-package imports

**Orchestration & agents**
- [LangGraph](https://langchain-ai.github.io/langgraph/) for the ROMA supervisor (graph-based state machine, persistence, checkpointing)
- [FastMCP](https://github.com/modelcontextprotocol/python-sdk) for tool servers
- [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) for binding MCP tools into LangGraph agents
- A2A protocol for inter-agent task lifecycle

**Models** (heterogeneous by design)
- **LLM tier** (Critic, Forecaster): Claude Sonnet 4 / Opus 4 via AWS Bedrock
- **SLM tier** (Scout, Extractor, Novelty, Calibrator): pluggable provider —
  Ollama-served Llama 3.1 / Qwen 2.5 / Phi-4 locally; Claude Haiku or
  vLLM-served small models in production
- Amazon Titan Text Embeddings v2 for concept similarity (pluggable to local)

**Knowledge stores**
- Neo4j 5 with APOC for typed graph with dynamic relationship creation
- Postgres 16 with pgvector (HNSW index) for embedding similarity
- MinIO (S3-compatible) for raw artifact archive

**Quality & observability**
- [RAGAS](https://docs.ragas.io/) and [DeepEval](https://github.com/confident-ai/deepeval) for retrieval and faithfulness evaluation
- structlog with contextvar-based trace_id propagation
- LangSmith for agent traces (planned)
- OpenTelemetry → Grafana for system metrics (planned)

**Validation & quality**
- pydantic for cross-process boundaries
- pydantic-settings for fail-fast config validation
- pytest with marker-gated unit/integration suites
- ruff + mypy strict across the workspace

## Repository layout

```
agentradar/
├── pyproject.toml              # workspace coordinator
├── uv.lock                     # single lockfile
├── docker-compose.yml          # data plane: neo4j + postgres + minio + init
│
├── infra/                      # non-Python infrastructure
│   ├── neo4j/init/             # auto-applied Cypher schema
│   └── postgres/init/          # auto-applied SQL schema
│
├── packages/                   # shared libraries
│   ├── agentradar-core/        # config, types, logging
│   └── agentradar-store/       # async clients: Neo4j, Postgres, S3, embeddings
│
├── services/                   # runnable applications
│   ├── mcp-server/             # exposes the knowledge store as MCP tools
│   ├── supervisor/             # ROMA supervisor + the six agents
│   └── api/                    # FastAPI for the dashboard
│
├── apps/dashboard/             # Next.js dashboard (planned)
└── tests/
    ├── unit/                   # pure logic, no network
    └── integration/            # against the docker-compose data plane
```

## Quick start

```bash
# 1. clone and enter
git clone https://github.com/<you>/agentradar.git
cd agentradar

# 2. configure
cp .env.example .env
# edit .env — most defaults match docker-compose.yml; you'll need real
# AWS credentials (or AWS_PROFILE) for Bedrock-backed agents

# 3. data plane
docker compose up -d
docker compose logs init-neo4j init-minio   # verify schemas + bucket created

# 4. python workspace
uv sync

# 5. run tests
uv run pytest                  # unit tests only — fast, no docker required
uv run pytest -m integration   # full suite against the data plane
```

### Verifying the data plane

```bash
# Neo4j browser
open http://localhost:7474       # login: neo4j / agentradar_dev

# MinIO console
open http://localhost:9001       # login: agentradar / agentradar_dev

# Postgres
docker compose exec postgres psql -U agentradar -d agentradar -c '\dt'
```

## Roadmap

- [x] uv workspace monorepo with shared libraries and runnable services
- [x] Async data clients (Neo4j, Postgres, pgvector, S3) with lazy singletons
- [x] Auto-applied schema via docker-compose init sidecars
- [x] Two-tier test architecture (unit + marker-gated integration)
- [x] Structured logging with contextvar-based trace propagation
- [x] MCP server exposing the knowledge store as tools
- [x] First Scout (arXiv) end-to-end through the proposer-critic loop
- [x] Critic agent with three-stage validation (structural → ontology → faithfulness)
- [x] Operational dashboard with single-port nginx reverse proxy
- [x] Supervisor with env-driven schedule, autonomous Scout↔Critic loop
- [ ] ROMA supervisor wired into LangGraph for complex multi-agent tasks
- [ ] Forecaster generating first weekly digest
- [ ] Calibrator with Brier-score back-grading
- [ ] Additional Scouts: GitHub orgs, lab blogs, RFC drafts
- [ ] Backtest: feed pre-MCP-launch data, see if the system flags MCP
- [ ] Terraform module for AWS ECS Fargate deployment
## Design decisions worth reading about

If you're evaluating this project for technical depth, these are the design
notes that explain the non-obvious choices:

- *Proposer-critic gate enforced at the storage layer, not in agent code* —
  prevents agent misbehavior from corrupting the graph
- *ROMA recursion with parent-context distillation* — bounds context growth at
  arbitrary recursion depth
- *Heterogeneous SLM/LLM model assignment* — orders-of-magnitude cost reduction
  vs. uniform-LLM agent designs, with no quality loss on narrow roles
- *Lazy singleton clients with async context managers* — cheap import, expensive
  resources only on first use, clean shutdown
- *HNSW over ivfflat for pgvector* — correct on small datasets, no tuning
- *Marker-gated integration tests* — `pytest` is safe-by-default; `pytest -m integration` opts in
- *Asyncio supervisor with env-driven schedule* — single-process scheduler
  with monotonic timing, stagger-on-startup, graceful shutdown, persistent
  MCP session. ~200 lines of Python; easily replaced with APScheduler or
  external cron when scale demands it

## References

The shifts AgentRadar is built to track and apply:

- Anthropic, [Model Context Protocol](https://modelcontextprotocol.io/) (Nov 2024)
- Google, [Agent-to-Agent Protocol](https://github.com/a2aproject) (Apr 2025)
- Sentient AI Labs et al., *[ROMA: Recursive Open Meta-Agent Framework for Long-Horizon Multi-Agent Systems](https://arxiv.org/abs/2602.01848)*
- Belcak et al. (NVIDIA), *[Small Language Models are the Future of Agentic AI](https://arxiv.org/abs/2506.02153)*

## Contributing

This is a personal portfolio project, but issues and PRs are welcome — especially:

- New Scout sources (arXiv, GitHub, lab blogs are first; suggest others)
- Improvements to the velocity / convergence detection heuristics
- Better evaluation harnesses for the Forecaster
- Backtest datasets

## License

MIT — see [LICENSE](./LICENSE).

## Acknowledgements

Built on the shoulders of giants: LangGraph, MCP (Anthropic), A2A (Google),
pgvector, Neo4j APOC, and the broader open-source agentic AI ecosystem this
project aims to track.