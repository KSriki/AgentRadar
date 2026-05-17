import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/api";
import type { Forecast } from "@/types";
import { cn } from "@/lib/utils";

const BAND_COLORS: Record<Forecast["confidence_band"], string> = {
  high: "border-l-emerald-500",
  medium: "border-l-amber-500",
  weak: "border-l-zinc-400",
};

const BAND_BADGE_VARIANTS: Record<Forecast["confidence_band"], "default" | "success" | "muted"> = {
  high: "success",
  medium: "default",
  weak: "muted",
};

function ForecastEntry({ f }: { f: Forecast }) {
  const formattedDate = new Date(f.predicted_at).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div
      className={cn(
        "border-l-4 pl-4 py-3 mb-3 last:mb-0",
        BAND_COLORS[f.confidence_band]
      )}
    >
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold text-sm">{f.concept_name}</h3>
          <Badge variant={BAND_BADGE_VARIANTS[f.confidence_band]} className="text-xs">
            {f.confidence_band} · {(f.confidence * 100).toFixed(0)}%
          </Badge>
        </div>
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          {f.horizon_months}mo · {formattedDate}
        </span>
      </div>
      <p className="text-sm mb-2 leading-relaxed">{f.claim}</p>
      {f.reasoning && (
        <p className="text-xs text-muted-foreground mb-2 italic">
          {f.reasoning}
        </p>
      )}
      {f.cited_source_ids.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {f.cited_source_ids.slice(0, 5).map((sid) => (
            <Badge key={sid} variant="muted" className="text-[10px] font-mono">
              {sid}
            </Badge>
          ))}
          {f.cited_source_ids.length > 5 && (
            <span className="text-[10px] text-muted-foreground">
              +{f.cited_source_ids.length - 5} more
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function LatestForecastsCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["forecasts-recent"],
    queryFn: () => api.forecastsRecent(10),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Latest Forecasts</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-20 w-full" />
            ))}
          </div>
        ) : data && data.length > 0 ? (
          <div>
            {data.map((f) => (
              <ForecastEntry key={f.id} f={f} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No forecasts yet. Run the Forecaster:{" "}
            <code>uv run python scripts/forecaster_once.py --concept MCP</code>
          </p>
        )}
      </CardContent>
    </Card>
  );
}