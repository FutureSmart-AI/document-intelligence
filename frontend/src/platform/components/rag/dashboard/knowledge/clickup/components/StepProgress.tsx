import { Check } from "lucide-react";

import { cn } from "@/platform/utils";

const STEPS = ["Connect", "Select docs", "Sync schedule", "Review"];

interface StepProgressProps {
  /** Current step, 1-indexed. */
  current: number;
}

/** Horizontal "1 — 2 — 3 — 4" progress indicator for the ClickUp wizard. */
export const StepProgress = ({ current }: StepProgressProps) => {
  return (
    <div className="mb-5 flex items-center">
      {STEPS.map((label, i) => {
        const stepNum = i + 1;
        const done = current > stepNum;
        const active = current === stepNum;
        return (
          <div key={label} className="flex flex-1 items-center last:flex-none">
            <div className="flex items-center gap-2">
              <div
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                  done || active
                    ? "bg-indigo-600 text-white"
                    : "bg-slate-100 text-slate-400",
                )}
              >
                {done ? <Check className="size-3.5" /> : stepNum}
              </div>
              <span
                className={cn(
                  "whitespace-nowrap text-xs",
                  active ? "font-medium text-slate-900" : "text-slate-400",
                )}
              >
                {label}
              </span>
            </div>
            {stepNum < STEPS.length && (
              <div className="mx-3 h-px flex-1 bg-slate-200" />
            )}
          </div>
        );
      })}
    </div>
  );
};
