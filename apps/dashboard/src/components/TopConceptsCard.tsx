import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/api";

function Sparkline({ buckets }: { buckets: { mentions: number }[] }) {
  if (buckets.length < 2) return <span className="text-muted-foreground text-xs">—</span>;
  const max = Math.max(...buckets.map((b) => b.mentions), 1);
  const w = 80;
  const h = 20;
  const points = buckets
    .map((b, i) => {
      const x = (i / (buckets.length - 1)) * w;
      const y = h - (b.mentions / max) * h;
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        points={points}
      />
    </svg>
  );
}

export function TopConceptsCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["top-concepts"],
    queryFn: () => api.topConcepts(10, 90),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Top Mentioned Concepts (90d)</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
          </div>
        ) : data && data.length > 0 ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted-foreground">
                <th className="text-left font-normal">Concept</th>
                <th className="text-right font-normal">Mentions</th>
                <th className="text-right font-normal">Trend</th>
                <th className="text-right font-normal">Slope</th>
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr key={row.concept} className="border-b last:border-0">
                  <td className="py-1.5 font-medium">{row.concept}</td>
                  <td className="py-1.5 text-right">{row.mentions}</td>
                  <td className="py-1.5 text-right text-primary">
                    <Sparkline buckets={row.buckets} />
                  </td>
                  <td className="py-1.5 text-right text-xs">
                    {row.velocity > 0 ? "+" : ""}
                    {row.velocity.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground">
            No mentions yet. Run the Scout: <code>uv run python scripts/scout_arxiv.py</code>
          </p>
        )}
      </CardContent>
    </Card>
  );
}