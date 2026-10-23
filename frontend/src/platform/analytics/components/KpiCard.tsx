import type { ReactNode } from "react";

import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import { cn } from "@/platform/utils";

interface KpiCardProps {
  label: string;
  value: string;
  delta?: number | null;
  deltaText?: string;
  deltaPrefix?: string;
  deltaSuffix?: string;
  direction?: "up" | "down" | "flat";
  goodWhen?: "up" | "down";
  /** "text" = inline coloured arrow (default); "pill" = filled badge. */
  deltaVariant?: "text" | "pill";
  hint?: string;
  icon?: ReactNode;
  iconBg?: string;
  valueColor?: string;
  /** "md" (default) keeps the shared 2xl/semibold; "lg" is the Overview 3xl/bold. */
  valueSize?: "md" | "lg";
  isLoading?: boolean;
  /** Sub-metric block rendered below the delta (e.g. token pills). */
  children?: ReactNode;
  /** Footer action, e.g. a "View … →" cross-tab link. */
  footer?: ReactNode;
}

export default function KpiCard({
  label,
  value,
  delta,
  deltaText,
  deltaPrefix,
  deltaSuffix,
  direction = "flat",
  goodWhen = "up",
  deltaVariant = "text",
  hint,
  icon,
  iconBg = "bg-indigo-50",
  valueColor,
  valueSize = "md",
  isLoading,
  children,
  footer,
}: KpiCardProps) {
  const showDelta =
    delta !== null && delta !== undefined && direction !== "flat";
  const good = direction === goodWhen;
  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4 relative">
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
        <div className="mt-2 h-7 w-20 rounded bg-slate-100 animate-pulse" />
      ) : (
        <div
          className={cn(
            "mt-1",
            valueSize === "lg"
              ? "text-3xl font-bold"
              : "text-2xl font-semibold",
            valueColor ?? "text-slate-900",
          )}
        >
          {value}
        </div>
      )}
      {showDelta &&
        (deltaVariant === "pill" ? (
          <div
            data-testid="kpi-delta"
            data-good={good}
            className="mt-2 flex items-center gap-2 text-xs"
          >
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-semibold",
                good
                  ? "bg-emerald-50 text-emerald-700"
                  : "bg-amber-50 text-amber-700",
              )}
            >
              {direction === "up" ? (
                <ArrowUp className="size-3" />
              ) : direction === "down" ? (
                <ArrowDown className="size-3" />
              ) : (
                <Minus className="size-3" />
              )}
              {deltaPrefix}
              {Math.abs(delta as number).toLocaleString()}
              {deltaSuffix}
            </span>
            {deltaText && <span className="text-slate-400">{deltaText}</span>}
          </div>
        ) : (
          <div
            data-testid="kpi-delta"
            data-good={good}
            className={cn(
              "mt-2 inline-flex items-center gap-1 text-xs font-medium",
              good ? "text-emerald-600" : "text-red-600",
            )}
          >
            {direction === "up" ? (
              <ArrowUp className="size-3" />
            ) : direction === "down" ? (
              <ArrowDown className="size-3" />
            ) : (
              <Minus className="size-3" />
            )}
            <span>
              {deltaPrefix}
              {Math.abs(delta as number).toLocaleString()}
              {deltaSuffix}
            </span>
            {deltaText && (
              <span className="text-slate-400 font-normal">{deltaText}</span>
            )}
          </div>
        ))}
      {hint && <div className="mt-1 text-[11px] text-slate-400">{hint}</div>}
      {children && !isLoading && <div className="mt-2">{children}</div>}
      {footer && !isLoading && <div className="mt-3">{footer}</div>}
    </div>
  );
}
