import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { ExternalLink } from "lucide-react";

type Link = { label: string; href: string };

const links: Link[] = [
  { label: "FastAPI Swagger docs", href: "/docs" },
  { label: "MCP Inspector (npm)", href: "https://github.com/modelcontextprotocol/inspector" },
  { label: "Neo4j browser", href: "http://localhost:7474" },
  { label: "MinIO console", href: "http://localhost:9001" },
];

export function QuickLinksCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Quick Links</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm">
          {links.map((link) => (
            <li key={link.href}>
              <a href={link.href} target="_blank" rel="noreferrer" className="flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-muted">
                <span>{link.label}</span>
                <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
              </a>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
