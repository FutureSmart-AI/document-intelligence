import { RunProgress, RunStatus } from "@/platform/types/batch-reports";

export const isLiveRun = (status: RunStatus) =>
  status === "pending" || status === "running";

// Backend workaround: a run can finish (progress 100%, all queries accounted
// for) while its status field is still "pending" — the worker doesn't always
// flip the lifecycle status. Treat that as completed so results can display.
export const isEffectivelyDone = (
  status: RunStatus | undefined,
  progress: RunProgress | undefined,
) => {
  if (status && !isLiveRun(status)) return true; // genuine terminal state
  if (!progress || progress.total_queries === 0) return false;
  return (
    progress.completed_queries + progress.failed_queries >=
    progress.total_queries
  );
};

// The status to display, accounting for the stuck-pending backend behaviour.
export const effectiveRunStatus = (
  status: RunStatus,
  progress: RunProgress | undefined,
): RunStatus => {
  if (status === "pending" && isEffectivelyDone(status, progress)) {
    return progress && progress.failed_queries === progress.total_queries
      ? "failed"
      : "completed";
  }
  return status;
};
