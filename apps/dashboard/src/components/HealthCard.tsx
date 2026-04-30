import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/api";

const labels: Record<string, string> = {
  neo4j: "Neo4j",
  postgres: "Postgres",
  s3: "S3 / MinIO",
  slm: "SLM (Ollama)",
};

export function HealthCard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 5_000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>System Health</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">Unreachable</p>
        ) : (
          <ul className="space-y-2">
            {Object.entries(data!).map(([key, ok]) => (
              <li key={key} className="flex items-center justify-between text-sm">
                <span>{labels[key] ?? key}</span>
                <Badge variant={ok ? "success" : "destructive"}>
                  {ok ? "OK" : "DOWN"}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}