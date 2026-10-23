import { cn } from "@/platform/utils";

import type { AnalyticsTabKey } from "../types";

/**
 * Generic over the key set so a product can render a tab the agent dashboard
 * does not have — NL2SQL adds "failures", which only its service reports —
 * without widening `AnalyticsTabKey` and breaking this page's exhaustive
 * `tabComponents` record.
 */
export default function TabNav<K extends string = AnalyticsTabKey>({
  tabs,
  activeTab,
  onChange,
}: {
  tabs: { key: K; label: string }[];
  activeTab: K;
  onChange: (k: K) => void;
}) {
  return (
    <div
      role="tablist"
      className="flex flex-wrap gap-1 border-b border-slate-200"
    >
      {tabs.map((t) => {
        const active = t.key === activeTab;
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.key)}
            className={cn(
              "cursor-pointer px-3 py-2 text-sm font-medium -mb-px border-b-2 transition-colors",
              active
                ? "border-indigo-600 text-indigo-600"
                : "border-transparent text-slate-500 hover:text-slate-700",
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
