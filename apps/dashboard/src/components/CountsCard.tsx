import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/api";

export function CountsCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["stats"],
    queryFn: api.stats,
  });

  const items = [
    { label: "Concepts", value: data?.concepts },
    { label: "Sources", value: data?.sources },
    { label: "Relationships", value: data?.relationships },
    { label: "Pending", value: data?.pending },
    { label: "Approved", value: data?.approved },
    { label: "Rejected", value: data?.rejected },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Knowledge Store</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-3 gap-4">
          {items.map((item) => (
            <div key={item.label}>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                {item.label}
              </p>
              {isLoading ? (
                <Skeleton className="h-8 w-16 mt-1" />
              ) : (
                <p className="text-2xl font-semibold">{item.value ?? 0}</p>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}