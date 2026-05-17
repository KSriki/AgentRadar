import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/api";
import type { SourceBreakdownEntry } from "@/types";

// Brand colors per source type — distinct enough to read at a glance.
// Using Tailwind's color palette so it adapts to dark/light themes.
const SOURCE_COLORS: Record<string, { bar: string; dot: string }> = {
  arxiv:        { bar: "bg-blue-500",    dot: "bg-blue-500" },
  blog:         { bar: "bg-emerald-500", dot: "bg-emerald-500" },
  github:       { bar: "bg-purple-500",  dot: "bg-purple-500" },
  conference:   { bar: "bg-amber-500",   dot: "bg-amber-500" },
  spec:         { bar: "bg-rose-500",    dot: "bg-rose-500" },
  rfc:          { bar: "bg-cyan-500",    dot: "bg-cyan-500" },
  other:        { bar: "bg-zinc-400",    dot: "bg-zinc-400" },
};

function colorFor(sourceType: string) {
  return SOURCE_COLORS[sourceType] ?? SOURCE_COLORS.other;
}

function StackedBar({ entries }: { entries: SourceBreakdownEntry[] }) {
  return (
    <div className="flex w-full h-8 rounded overflow-hidden border border-border">
      {entries.map((e) => {
        const colors = colorFor(e.source_type);
        return (
          <div
            key={e.source_type}
            className={colors.bar}
            style={{ width: `${e.percentage * 100}%` }}
            title={`${e.source_type}: ${e.mentions} mentions (${(
              e.percentage * 100
            ).toFixed(1)}%)`}
          />
        );
      })}
    </div>
  );
}

function Legend({ entries }: { entries: SourceBreakdownEntry[] }) {
  return (
    <div className="grid grid-cols-2 gap-2 mt-4">
      {entries.map((e) => {
        const colors = colorFor(e.source_type);
        return (
          <div key={e.source_type} className="flex items-center gap-2 text-xs">
            <span className={`inline-block w-3 h-3 rounded-sm ${colors.dot}`} />
            <span className="font-medium">{e.source_type}</span>
            <span className="text-muted-foreground ml-auto">
              {e.mentions} · {(e.percentage * 100).toFixed(1)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function SourceBreakdownCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["source-breakdown"],
    queryFn: () => api.sourceBreakdown(),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Signal by Source</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : data && data.by_source_type.length > 0 ? (
          <div>
            <p className="text-xs text-muted-foreground mb-3">
              {data.total_mentions.toLocaleString()} mentions across{" "}
              {data.by_source_type.length} source{" "}
              {data.by_source_type.length === 1 ? "type" : "types"}
            </p>
            <StackedBar entries={data.by_source_type} />
            <Legend entries={data.by_source_type} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No mentions recorded yet.
          </p>
        )}
      </CardContent>
    </Card>
  );
}