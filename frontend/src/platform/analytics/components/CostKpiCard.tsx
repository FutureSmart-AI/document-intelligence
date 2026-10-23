"use client";

import type { ReactNode } from "react";

import { ArrowDown, ArrowUp } from "lucide-react";

import { cn } from "@/platform/utils";

interface CostKpiCardProps {
  label: string;
  value: string;
  /** Signed % change already formatted for display (e.g. "12%"). Omit hides pill. */
  deltaLabel?: string | null;
  /** Sign of the delta — drives arrow + good/bad colouring. */
  deltaDirection?: "up" | "down";
  /** Which direction is good. Cost up is bad, so default is "down". */
  goodWhen?: "up" | "down";
  deltaText?: string;
  /** Small descriptive line under the value (e.g. "this period"). */
  hint?: string;
  icon?: ReactNode;
  iconBg?: string;
  valueColor?: string;
  isLoading?: boolean;
}

export default function CostKpiCard({
  label,
  value,
  deltaLabel,
  deltaDirection = "up",
  goodWhen = "down",
  deltaText = "vs prev period",
  hint,
  icon,
  iconBg = "bg-indigo-50",
  valueColor = "text-slate-900",
  isLoading,
}: CostKpiCardProps) {
  const good = deltaDirection === goodWhen;

  return (
    <div className="h-full rounded-lg border border-gray-200 bg-white shadow-sm p-4 relative">
      {icon && (
        <div
          className={cn(
            "absolute top-4 right-4 size-8 rounded-lg flex items-center justify-center",
            iconBg,
          )}
        >
          {icon}
        </div>
      )}
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>

      {isLoading ? (
        <div className="mt-2 h-8 w-24 rounded bg-slate-100 animate-pulse" />
      ) : (
        <div className={cn("mt-1 text-3xl font-bold", valueColor)}>{value}</div>
      )}

      {hint && !isLoading && (
        <div className="mt-0.5 text-[11px] text-slate-600">{hint}</div>
      )}

      {/* Delta pill — blank slot kept when no data so cards stay aligned */}
      <div className="mt-2 h-6">
        {deltaLabel && (
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold",
              good
                ? "bg-emerald-50 text-emerald-700"
                : "bg-amber-50 text-amber-700",
            )}
          >
            {deltaDirection === "up" ? (
              <ArrowUp className="size-3" />
            ) : (
              <ArrowDown className="size-3" />
            )}
            {deltaLabel} {deltaText}
          </span>
        )}
      </div>
    </div>
  );
}
