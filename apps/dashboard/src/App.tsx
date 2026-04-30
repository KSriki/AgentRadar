import { HealthCard } from "@/components/HealthCard";
import { CountsCard } from "@/components/CountsCard";
import { RecentActivityCard } from "@/components/RecentActivityCard";
import { TopConceptsCard } from "@/components/TopConceptsCard";
import { QuickLinksCard } from "@/components/QuickLinksCard";

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-card">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">📡 AgentRadar</h1>
            <p className="text-xs text-muted-foreground">
              Autonomous agentic knowledge management
            </p>
          </div>
          <span className="text-xs text-muted-foreground">
            Auto-refresh every 10s
          </span>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-6 grid grid-cols-1 md:grid-cols-3 gap-4">
        <HealthCard />
        <div className="md:col-span-2"><CountsCard /></div>

        <div className="md:col-span-2 md:row-span-2">
          <TopConceptsCard />
        </div>
        <QuickLinksCard />

        <div className="md:col-span-3">
          <RecentActivityCard />
        </div>
      </main>
    </div>
  );
}