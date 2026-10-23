import type { ReactNode } from "react";

import { cn } from "@/platform/utils";

export default function ChartCard({
  title,
  subtitle,
  children,
  className,
  action,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex-1 min-w-0 rounded-lg border border-gray-200 bg-white shadow-sm p-4",
        className,
      )}
    >
      <div className="flex items-start justify-between mb-2">
        <div>
          <h4 className="text-sm font-semibold text-slate-700">{title}</h4>
          {subtitle && (
            <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>
          )}
        </div>
        {action && <div>{action}</div>}
      </div>
      {children}
    </div>
  );
}
