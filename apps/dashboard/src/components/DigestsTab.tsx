import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/api";
import { LatestDigestCard } from "./LatestDigestCard";
import { DigestTimelineCard } from "./DigestTimelineCard";

const COUNT_OPTIONS = [5, 10, 25, 50] as const;
type CountOption = (typeof COUNT_OPTIONS)[number];

export function DigestsTab() {
  const [limit, setLimit] = useState<CountOption>(10);

  // Latest digest comes from the same query; we slice off index 0 for the hero
  const { data, isLoading } = useQuery({
    queryKey: ["digests-recent", limit],
    queryFn: () => api.digestsRecent(limit),
  });

  const latest = data?.digests[0];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-base font-medium">Forecast Digests</h2>
        <div className="flex items-center gap-2 text-sm">
          <label htmlFor="digest-count" className="text-muted-foreground">
            Show
          </label>
          <select
            id="digest-count"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value) as CountOption)}
            className="bg-card border border-border rounded px-2 py-1 text-sm"
          >
            {COUNT_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>

      {isLoading ? (
        <Card>
          <CardHeader>
            <CardTitle>Latest Digest</CardTitle>
          </CardHeader>
          <CardContent>
            <Skeleton className="h-48 w-full" />
          </CardContent>
        </Card>
      ) : latest ? (
        <LatestDigestCard digest={latest} />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Latest Digest</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              No digests yet. Generate one with{" "}
              <code>uv run python scripts/forecaster_digest.py</code>
            </p>
          </CardContent>
        </Card>
      )}

      <DigestTimelineCard limit={limit} />
    </div>
  );
}
