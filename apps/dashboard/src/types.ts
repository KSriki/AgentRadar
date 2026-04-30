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