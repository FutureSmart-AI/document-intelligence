"use client";

import type { ReactNode } from "react";

import { ArrowDown, ArrowUp } from "lucide-react";

import { cn } from "@/platform/utils";

interface PerfKpiCardProps {
  label: string;
  value: string;
  /** Signed % change vs previous period (e.g. +8.3 / -4.1). Null hides the pill. */
  delta?: number | null;
  goodWhen?: "up" | "down";
  icon?: ReactNode;
  iconBg?: string;
  valueColor?: string;
  isLoading?: boolean;
}

export default function PerfKpiCard({
  label,
  value,
  delta,
  goodWhen = "down",
  icon,
  iconBg = "bg-indigo-50",
  valueColor = "text-slate-900",
  isLoading,
}: PerfKpiCardProps) {
  const hasDelta = delta != null && delta !== 0;
  const direction: "up" | "down" = (delta ?? 0) > 0 ? "up" : "down";
  const good = direction === goodWhen;
  const deltaWord = direction === "up" ? "slower" : "faster";

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

      {/* Delta pill — blank slot kept when no data so cards stay aligned */}
      <div className="mt-2 h-6">
        {hasDelta && (
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold",
              good
                ? "bg-emerald-50 text-emerald-700"
                : "bg-amber-50 text-amber-700",
            )}
          >
            {direction === "up" ? (
              <ArrowUp className="size-3" />
            ) : (
              <ArrowDown className="size-3" />
            )}
            {Math.abs(delta as number)}% {deltaWord} vs prev
          </span>
        )}
      </div>
    </div>
  );
}
