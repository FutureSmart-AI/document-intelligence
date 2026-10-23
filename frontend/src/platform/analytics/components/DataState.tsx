import type { ReactNode } from "react";

export default function DataState({
  isLoading,
  isError,
  isEmpty,
  children,
  emptyText = "No data for this period.",
  loadingText = "Loading…",
}: {
  isLoading: boolean;
  isError: boolean;
  isEmpty?: boolean;
  error?: unknown;
  children: ReactNode;
  emptyText?: string;
  loadingText?: string;
}) {
  if (isLoading)
    return (
      <div className="flex h-[320px] items-center justify-center text-sm text-slate-400">
        {loadingText}
      </div>
    );
  if (isError)
    return (
      <div className="flex h-[320px] items-center justify-center text-sm text-red-500">
        Failed to load. Try again.
      </div>
    );
  if (isEmpty)
    return (
      <div className="flex h-[320px] items-center justify-center text-sm text-slate-400">
        {emptyText}
      </div>
    );
  return <>{children}</>;
}
