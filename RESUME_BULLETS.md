# AgentRadar — Resume Capture

## Architecture & design decisions
- Designed dual-layer agentic KM system: SpecTrack (current state) +
  Horizon (weak-signal forecasting) with self-calibration loop
- Chose proposer-critic gate enforced at MCP tool boundary (not in agent code)
  to prevent agent misbehavior from corrupting the knowledge graph
- ROMA (Atomizer→Planner→Executor→Aggregator) implemented as recursive
  LangGraph supervisor with depth cap and context distillation at boundaries

## Infra & tooling
- uv workspace monorepo: 2 packages + 3 services, single lockfile,
  editable cross-package imports via [tool.uv.sources]
- Docker Compose data plane: Neo4j 5.20 + pgvector + MinIO with
  healthcheck-gated init-runner sidecar for race-free schema application

## (fill in as you go)

Architected uv-managed Python monorepo for an autonomous agentic knowledge management system, with workspace-resolved internal packages, single-lockfile dependency management, and shared tooling config (ruff, mypy strict, pytest-asyncio) across 5 workspace members spanning shared libraries and runnable services.


Built type-safe configuration and structured logging foundation for autonomous agent system: pydantic-settings for fail-fast env validation with SecretStr for credential hygiene, contextvar-bound structlog for automatic trace_id propagation across async agent invocations — enabling end-to-end forensic tracing without manual parameter plumbing.

Implemented async data access layer for autonomous agent system across heterogeneous stores: Neo4j 5 with APOC for dynamic-typed relationship commits, asyncpg-backed Postgres with pgvector for cosine-similarity novelty detection, idempotent proposer-critic queue with deterministic content hashing, and aioboto3/MinIO-compatible S3 layer — all behind lazy singleton clients that defer connection cost until first use.