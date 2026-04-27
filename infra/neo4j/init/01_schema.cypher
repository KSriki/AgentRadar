// AgentRadar Neo4j schema. Applied by the init-runner sidecar after Neo4j
// reports healthy. Idempotent — uses IF NOT EXISTS for re-runs.

// Uniqueness constraints (auto-create indexes for the constrained property)
CREATE CONSTRAINT concept_name_unique IF NOT EXISTS
    FOR (c:Concept) REQUIRE c.name IS UNIQUE;

CREATE CONSTRAINT authority_name_unique IF NOT EXISTS
    FOR (a:Authority) REQUIRE a.name IS UNIQUE;

CREATE CONSTRAINT source_id_unique IF NOT EXISTS
    FOR (s:Source) REQUIRE s.id IS UNIQUE;

CREATE CONSTRAINT forecast_id_unique IF NOT EXISTS
    FOR (f:Forecast) REQUIRE f.id IS UNIQUE;

// Indexes for hot query paths in the Forecaster and Novelty Detector
CREATE INDEX concept_type_idx IF NOT EXISTS
    FOR (c:Concept) ON (c.type);

CREATE INDEX source_observed_at_idx IF NOT EXISTS
    FOR (s:Source) ON (s.observed_at);