-- AgentRadar Postgres schema. Auto-applied by docker-entrypoint-initdb.d
-- on first container startup (when the data volume is empty).

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Concept embeddings: semantic similarity for novelty detection
-- ---------------------------------------------------------------------------
CREATE TABLE concept_embeddings (
    concept_name   TEXT PRIMARY KEY,
    embedding      vector(1024),  -- must match settings.embedding.dim
    description    TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW (Hierarchical Navigable Small Worlds) — works correctly on small
-- datasets, no tuning needed. ivfflat is the alternative but requires careful
-- list/probe configuration and produces wrong results on tiny tables.
CREATE INDEX concept_embeddings_hnsw_idx
    ON concept_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- Pending triples: proposer-critic queue gate
-- ---------------------------------------------------------------------------
CREATE TABLE pending_triples (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposer_agent   TEXT NOT NULL,
    subject          TEXT NOT NULL,
    predicate        TEXT NOT NULL,
    object           TEXT NOT NULL,
    source_id        TEXT NOT NULL,
    confidence       FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    proposal_hash    TEXT UNIQUE NOT NULL,  -- idempotency key
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'rejected')),
    rejection_reason TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at       TIMESTAMPTZ
);

CREATE INDEX pending_triples_status_created_idx
    ON pending_triples (status, created_at);

-- ---------------------------------------------------------------------------
-- Mention events: time-series for the Forecaster's velocity calculation
-- ---------------------------------------------------------------------------
CREATE TABLE mention_events (
    concept_name  TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (concept_name, source_id)
);

CREATE INDEX mention_events_concept_observed_idx
    ON mention_events (concept_name, observed_at DESC);

-- ---------------------------------------------------------------------------
-- Forecasts: track predictions for self-calibration
-- ---------------------------------------------------------------------------

-- Forecasts produced by the Forecaster agent.
-- Each row is one prediction about one concept, with provenance and
-- (eventually) Calibrator-graded outcomes.
CREATE TABLE IF NOT EXISTS forecasts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_name      TEXT NOT NULL,
    claim             TEXT NOT NULL,
    confidence        DOUBLE PRECISION NOT NULL
                          CHECK (confidence >= 0.0 AND confidence <= 1.0),
    confidence_band   TEXT NOT NULL DEFAULT 'weak'
                          CHECK (confidence_band IN ('weak', 'medium', 'high')),
    horizon_months    INTEGER NOT NULL
                          CHECK (horizon_months >= 1 AND horizon_months <= 24),
    reasoning         TEXT NOT NULL DEFAULT '',
    cited_source_ids  TEXT[] NOT NULL DEFAULT '{}',
    evidence_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    predicted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Calibrator (future): outcome graded after the horizon elapses
    outcome           TEXT CHECK (outcome IN ('hit', 'miss', 'partial')),
    graded_at         TIMESTAMPTZ,
    graded_notes      TEXT
);

CREATE INDEX IF NOT EXISTS forecasts_predicted_at_idx
    ON forecasts(predicted_at DESC);
CREATE INDEX IF NOT EXISTS forecasts_outcome_idx
    ON forecasts(outcome) WHERE outcome IS NOT NULL;
CREATE INDEX IF NOT EXISTS forecasts_concept_name_idx
    ON forecasts(concept_name);



-- Weekly digests produced by the ROMA digest workflow.
-- Each digest snapshots its N forecasts so it's reproducible later
-- regardless of subsequent forecasts table churn.
CREATE TABLE IF NOT EXISTS digests (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label              TEXT NOT NULL DEFAULT '',
    themes             TEXT NOT NULL,
    standout           TEXT NOT NULL DEFAULT '',
    forecasts_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
    average_confidence DOUBLE PRECISION NOT NULL
                           CHECK (average_confidence >= 0.0 AND average_confidence <= 1.0),
    confidence_band    TEXT NOT NULL
                           CHECK (confidence_band IN ('weak', 'medium', 'high')),
    generated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS digests_generated_at_idx
    ON digests(generated_at DESC);
