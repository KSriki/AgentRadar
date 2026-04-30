import type {
  Health,
  Stats,
  PendingTriple,
  RecentActivity,
  TopConcept,
} from "./types";

// All paths are relative — same-origin via the nginx proxy in prod,
// or the Vite dev proxy in dev. No CORS, no env switching.

async function fetchJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: { Accept: "application/json" } });
  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText} for ${path}`);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  health: () => fetchJson<Health>("/api/health"),
  stats: () => fetchJson<Stats>("/api/stats"),
  pending: (limit = 10) =>
    fetchJson<PendingTriple[]>(`/api/pending?limit=${limit}`),
  recentActivity: (limit = 10) =>
    fetchJson<RecentActivity[]>(`/api/recent-activity?limit=${limit}`),
  topConcepts: (limit = 10, windowDays = 90) =>
    fetchJson<TopConcept[]>(
      `/api/top-concepts?limit=${limit}&window_days=${windowDays}`
    ),
};