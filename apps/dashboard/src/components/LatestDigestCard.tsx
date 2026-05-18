import { useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ChevronDown, ChevronRight, Sparkles } from "lucide-react";
import type { Digest, DigestForecast } from "@/types";
import { cn } from "@/lib/utils";

const BAND_BADGE_VARIANTS: Record<Digest["confidence_band"], "default" | "success" | "muted"> = {
  high: "success",
  medium: "default",
  weak: "muted",
};

const BAND_BORDER: Record<Digest["confidence_band"], string> = {
  high: "border-l-emerald-500",
  medium: "border-l-amber-500",
  weak: "border-l-zinc-400",
};

function getForecastText(f: DigestForecast): string {
  // Older digests stored prediction; newer ones may use claim
  return f.prediction ?? f.claim ?? "";
}

function ForecastRow({ f }: { f: DigestForecast }) {
  return (
    <div className="border-l-2 border-border pl-3 py-2 text-sm">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <span className="font-medium">{f.concept_name}</span>
        <span className="text-xs text-muted-foreground">
          {(f.confidence * 100).toFixed(0)}% · {f.horizon_months}mo
        </span>
      </div>
      <p className="text-sm text-muted-foreground leading-relaxed">
        {getForecastText(f)}
      </p>
    </div>
  );
}

export function LatestDigestCard({ digest }: { digest: Digest }) {
  const [showForecasts, setShowForecasts] = useState(false);
  const formattedDate = new Date(digest.generated_at).toLocaleString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  return (
    <Card className={cn("border-l-4", BAND_BORDER[digest.confidence_band])}>
      <CardHeader>
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              Latest Digest
            </CardTitle>
            <p className="text-xs text-muted-foreground mt-1">
              {digest.label || "Untitled digest"} · {formattedDate}
            </p>
          </div>
          <Badge variant={BAND_BADGE_VARIANTS[digest.confidence_band]}>
            {digest.confidence_band} · {(digest.average_confidence * 100).toFixed(0)}%
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Themes — the system's editorial synthesis */}
        <div className="border-l-2 border-primary/30 pl-4 py-1">
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground mb-2">
                What the system sees this week
            </h3>
            <p className="text-base leading-relaxed text-foreground/90">
                {digest.themes}
            </p>
        </div>

        {/* Standout — the system's "if you read one thing, read this" */}
        {digest.standout && (
          <div className="bg-muted/50 border-l-2 border-primary pl-3 py-2 rounded-r">
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
              Standout
            </h3>
            <p className="text-sm italic">{digest.standout}</p>
          </div>
        )}

        {/* Expandable list of internal forecasts */}
        {digest.forecasts.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setShowForecasts((v) => !v)}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              aria-expanded={showForecasts}
            >
              {showForecasts ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              {digest.forecasts.length} forecast{digest.forecasts.length === 1 ? "" : "s"} in this digest
            </button>
            {showForecasts && (
              <div className="mt-3 space-y-2">
                {digest.forecasts.map((f) => (
                  <ForecastRow key={`${digest.digest_id}-${f.concept_name}`} f={f} />
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
