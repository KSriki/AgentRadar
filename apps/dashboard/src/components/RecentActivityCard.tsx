import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/api";

function relativeTime(iso: string): string {
  const dt = new Date(iso);
  const seconds = Math.floor((Date.now() - dt.getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function RecentActivityCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["recent-activity"],
    queryFn: () => api.recentActivity(10),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Critic Decisions</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : data && data.length > 0 ? (
          <ul className="space-y-2 text-sm">
            {data.map((row) => (
              <li
                key={row.id}
                className="flex items-center justify-between py-1 border-b last:border-0"
              >
                <div className="truncate">
                  <span className="font-medium">{row.subject}</span>
                  <span className="mx-1 text-muted-foreground">
                    -[{row.predicate}]→
                  </span>
                  <span>{row.object}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0 ml-3">
                  <Badge variant={row.status === "approved" ? "success" : "destructive"}>
                    {row.status}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {relativeTime(row.decided_at)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No decisions yet — propose and approve a triple to see activity.</p>
        )}
      </CardContent>
    </Card>
  );
}