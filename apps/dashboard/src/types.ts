export type Health = {
  neo4j: boolean;
  postgres: boolean;
  s3: boolean;
  slm: boolean;
};

export type Stats = {
  concepts: number;
  sources: number;
  relationships: number;
  pending: number;
  approved: number;
  rejected: number;
};

export type PendingTriple = {
  id: string;
  proposer_agent: string;
  subject: string;
  predicate: string;
  object: string;
  source_id: string;
  confidence: number;
  status: "pending" | "approved" | "rejected";
  created_at: string;
};

export type RecentActivity = {
  id: string;
  proposer_agent: string;
  subject: string;
  predicate: string;
  object: string;
  source_id: string;
  status: "approved" | "rejected";
  decided_at: string;
};

export type TopConcept = {
  concept: string;
  mentions: number;
  velocity: number;
  buckets: { week: string; mentions: number }[];
};




export type Forecast = {
  id: string;
  concept_name: string;
  claim: string;
  confidence: number;
  confidence_band: "weak" | "medium" | "high";
  horizon_months: number;
  reasoning: string;
  cited_source_ids: string[];
  predicted_at: string;
  outcome: "hit" | "miss" | "partial" | null;
  graded_at: string | null;
};

export type SourceBreakdownEntry = {
  source_type: string;
  mentions: number;
  percentage: number;
};

export type SourceBreakdown = {
  total_mentions: number;
  by_source_type: SourceBreakdownEntry[];
};

export type DigestForecast = {
  concept_name: string;
  prediction?: string;          // older digests may have used `claim` instead
  claim?: string;
  confidence: number;
  horizon_months: number;
  reasoning?: string;
  cited_concept_ids?: string[];
  cited_source_ids?: string[];
};

export type Digest = {
  digest_id: string;
  label: string;
  themes: string;
  standout: string;
  forecasts: DigestForecast[];
  average_confidence: number;
  confidence_band: "weak" | "medium" | "high";
  generated_at: string;
};

export type DigestsResponse = {
  digests: Digest[];
  count: number;
};
