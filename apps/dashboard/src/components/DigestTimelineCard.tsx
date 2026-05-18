import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/api";
import type { Digest, DigestForecast } from "@/types";


const BAND_BADGE_VARIANTS: Record<Digest["confidence_band"], "default" | "success" | "muted"> = {
  high: "success",
  medium: "default",
  weak: "muted",
};

function getForecastText(f: DigestForecast): string {
  return f.prediction ?? f.claim ?? "";
}

function DigestRow({ digest }: { digest: Digest }) {
  const [expanded, setExpanded] = useState(false);
  const formattedDate = new Date(digest.generated_at).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div className="border-b border-border last:border-0 py-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between gap-3 text-left hover:bg-muted/30 -mx-2 px-2 py-1 rounded transition-colors"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2 min-w-0">
          {expanded ? (
            <ChevronDown className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
          )}
          <span className="font-medium text-sm truncate">
            {digest.label || "Untitled digest"}
          </span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-xs text-muted-foreground">{formattedDate}</span>
          <Badge variant={BAND_BADGE_VARIANTS[digest.confidence_band]} className="text-xs">
            {digest.confidence_band} · {(digest.average_confidence * 100).toFixed(0)}%
          </Badge>
          <span className="text-xs text-muted-foreground">
            {digest.forecasts.length} fcst
          </span>
        </div>
      </button>

      {expanded && (
        <div className="mt-3 pl-6 space-y-3">
          <div className="border-l-2 border-primary/30 pl-3 py-0.5">
            <h4 className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
              What the system sees this week
            </h4>
            <p className="text-sm leading-relaxed text-foreground/90">
              {digest.themes}
            </p>
          </div>
          {digest.standout && (
            <div className="bg-muted/40 border-l-2 border-primary pl-3 py-1.5 rounded-r">
              <h4 className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
                Standout
              </h4>
              <p className="text-sm italic">{digest.standout}</p>
            </div>
          )}
          {digest.forecasts.length > 0 && (
            <div>
              <h4 className="text-xs uppercase tracking-wide text-muted-foreground mb-2">
                Forecasts ({digest.forecasts.length})
              </h4>
              <ul className="space-y-1.5 text-sm">
                {digest.forecasts.map((f) => (
                  <li
                    key={`${digest.digest_id}-${f.concept_name}`}
                    className="flex items-baseline gap-2"
                  >
                    <span className="font-medium min-w-[80px]">{f.concept_name}</span>
                    <span className="text-muted-foreground text-xs">
                      {(f.confidence * 100).toFixed(0)}% · {f.horizon_months}mo
                    </span>
                    <span className="text-muted-foreground truncate flex-1">
                      {getForecastText(f)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function DigestTimelineCard({ limit }: { limit: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["digests-recent", limit],
    queryFn: () => api.digestsRecent(limit),
  });

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Recent Digests</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const digests = data?.digests ?? [];

  // The latest digest is shown in hero treatment in the parent component;
  // this widget shows the remaining historical entries.
  const historical = digests.slice(1);

  if (digests.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Recent Digests</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No digests yet. Run the digest workflow:{" "}
            <code>uv run python scripts/forecaster_digest.py</code>
          </p>
        </CardContent>
      </Card>
    );
  }

  if (historical.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Recent Digests</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground italic">
            No older digests yet — the digest above is your most recent.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Digests</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {historical.map((digest) => (
          <DigestRow key={digest.digest_id} digest={digest} />
        ))}
      </CardContent>
    </Card>
  );
}
