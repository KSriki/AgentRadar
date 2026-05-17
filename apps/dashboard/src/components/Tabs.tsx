import { type ReactNode, useState } from "react";
import { cn } from "@/lib/utils";

export type TabDef = {
  id: string;
  label: string;
  content: ReactNode;
};

type TabsProps = {
  tabs: TabDef[];
  initialTab?: string;
};

export function Tabs({ tabs, initialTab }: TabsProps) {
  const [active, setActive] = useState(initialTab ?? tabs[0]?.id);
  const current = tabs.find((t) => t.id === active) ?? tabs[0];

  return (
    <div className="w-full">
      <div
        role="tablist"
        className="flex gap-1 border-b border-border mb-6"
      >
        {tabs.map((tab) => {
          const isActive = tab.id === active;
          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={isActive}
              onClick={() => setActive(tab.id)}
              className={cn(
                "px-4 py-2 text-sm font-medium transition-colors",
                "border-b-2 -mb-px",
                isActive
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel">{current?.content}</div>
    </div>
  );
}
