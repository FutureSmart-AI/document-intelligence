import { useState, type ReactNode } from "react";

import dayjs from "dayjs";
import { Loader2, RefreshCw } from "lucide-react";

import { Button, Dialog } from "@/platform/components/ui";
import { cn } from "@/platform/utils";

import { DisconnectDialog } from "./DisconnectDialog";
import { EditConfigDialog } from "./EditConfigDialog";
import {
  useClickUpConfig,
  useClickUpRuns,
  useTriggerSync,
} from "./hooks/useClickUpApi";
import { ResyncDocSection } from "./ResyncDocSection";
import type { SyncRun } from "./types";
import { relativeTime, scheduleLabel } from "./utils";

interface ConnectedSourcesDialogProps {
  open: boolean;
  onClose: () => void;
}

const RUN_STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  running: "bg-blue-50 text-blue-700 ring-blue-200",
  queued: "bg-blue-50 text-blue-700 ring-blue-200",
  failed: "bg-rose-50 text-rose-700 ring-rose-200",
};

const RunStatusBadge = ({ status }: { status: string }) => (
  <span
    className={cn(
      "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
      RUN_STATUS_STYLES[status] ?? "bg-slate-50 text-slate-700 ring-slate-200",
    )}
  >
    {(status === "running" || status === "queued") && (
      <Loader2 className="size-3 animate-spin" />
    )}
    {status}
  </span>
);

const Stat = ({
  label,
  value,
  sub,
  valueClass,
  subClass,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  valueClass?: string;
  subClass?: string;
}) => (
  <div className="rounded-lg bg-slate-50 px-3 py-2.5">
    <div className="text-xs text-slate-500">{label}</div>
    <div
      className={cn(
        "mt-0.5 text-base font-semibold text-slate-900",
        valueClass,
      )}
    >
      {value}
    </div>
    {sub && (
      <div className={cn("mt-0.5 text-xs text-slate-400", subClass)}>{sub}</div>
    )}
  </div>
);

const ConfigRow = ({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: ReactNode;
  valueClass?: string;
}) => (
  <div className="flex items-center justify-between border-b border-slate-50 py-1.5 text-sm last:border-b-0">
    <span className="text-slate-500">{label}</span>
    <span className={cn("font-medium text-slate-900", valueClass)}>
      {value}
    </span>
  </div>
);

/** "Connected Sources" management panel for the ClickUp connector. */
export const ConnectedSourcesDialog = ({
  open,
  onClose,
}: ConnectedSourcesDialogProps) => {
  const { data: config, isLoading } = useClickUpConfig(open);
  const { data: runs } = useClickUpRuns(20, open);
  const triggerSync = useTriggerSync();
  const [editOpen, setEditOpen] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);

  const latestRun: SyncRun | undefined = runs?.[0];
  const totalErrors = runs?.reduce((sum, r) => sum + (r.errors || 0), 0) ?? 0;
  // Backend has no persisted doc-count field, so derive from the runs list:
  // total docs indexed = sum across runs, "today" = runs that started today.
  const totalIndexed = runs?.reduce((sum, r) => sum + (r.indexed || 0), 0) ?? 0;
  const indexedToday =
    runs?.reduce(
      (sum, r) =>
        sum +
        (r.started_at && dayjs(r.started_at).isSame(dayjs(), "day")
          ? r.indexed || 0
          : 0),
      0,
    ) ?? 0;

  const body = () => {
    if (isLoading) {
      return (
        <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
          <Loader2 className="size-4 animate-spin" /> Loading…
        </div>
      );
    }
    if (!config) {
      return (
        <div className="py-10 text-center">
          <p className="text-sm font-medium text-slate-900">
            No ClickUp source connected
          </p>
          <p className="mx-auto mt-1 max-w-sm text-xs text-slate-500">
            Use “Add Knowledge → ClickUp Docs” to connect a workspace and start
            syncing documents.
          </p>
        </div>
      );
    }

    const sel = config.selection;
    return (
      <div className="space-y-5">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-lg bg-indigo-600 text-lg">
            📋
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-slate-900">
              ClickUp Docs
            </div>
            <div className="truncate text-xs text-slate-500">
              Workspace {sel.workspace_id ?? "—"} ·{" "}
              {(sel.space_ids ?? []).length} space(s)
            </div>
          </div>
          <span
            className={cn(
              "rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset",
              config.enabled
                ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                : "bg-slate-50 text-slate-600 ring-slate-200",
            )}
          >
            {config.enabled ? "● Connected" : "Paused"}
          </span>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat
            label="Docs indexed"
            value={totalIndexed}
            sub={indexedToday > 0 ? `↑${indexedToday} today` : undefined}
            valueClass="text-sm"
            subClass={indexedToday > 0 ? "text-emerald-600" : undefined}
          />
          <Stat
            label="Last sync"
            value={relativeTime(config.last_sync_at)}
            sub={
              config.last_sync_at
                ? dayjs(config.last_sync_at).format("h:mm A")
                : undefined
            }
            valueClass="text-sm"
          />
          <Stat
            label="Schedule"
            value={scheduleLabel(config)}
            sub={config.next_sync_at ? "Next run scheduled" : "Manual / paused"}
            valueClass="text-sm text-emerald-700"
          />
          <Stat
            label="Errors"
            value={totalErrors}
            sub={latestRun ? `Last run: ${latestRun.status}` : undefined}
            valueClass={totalErrors > 0 ? "text-rose-600" : undefined}
          />
        </div>

        {/* Configuration */}
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Configuration
            </span>
            <button
              type="button"
              onClick={() => setEditOpen(true)}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-700"
            >
              Edit →
            </button>
          </div>
          <ConfigRow label="Workspace ID" value={sel.workspace_id ?? "—"} />
          <ConfigRow
            label="Space ID"
            value={(sel.space_ids ?? []).join(", ") || "—"}
          />
          <ConfigRow label="Sync schedule" value={scheduleLabel(config)} />
          <ConfigRow
            label="Exclude keywords"
            value={(sel.exclude_name_keywords ?? []).join(", ") || "None"}
          />
          <ConfigRow
            label="Deletion propagation"
            value={sel.deletion_enabled ? "Enabled" : "Disabled"}
            valueClass={sel.deletion_enabled ? "text-emerald-700" : undefined}
          />
        </div>

        {/* Recent runs */}
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Recent sync runs
            </span>
            <Button
              variant="ghost"
              size="xs"
              startIcon={
                <RefreshCw
                  className={cn(
                    "size-3.5",
                    triggerSync.isPending && "animate-spin",
                  )}
                />
              }
              onClick={() => triggerSync.mutate({ forceReindex: false })}
              disabled={triggerSync.isPending}
            >
              Sync now
            </Button>
          </div>
          <div className="overflow-hidden rounded-lg border border-slate-200">
            {!runs || runs.length === 0 ? (
              <div className="px-3 py-4 text-center text-xs text-slate-400">
                No sync runs yet.
              </div>
            ) : (
              runs.slice(0, 6).map((run) => (
                <div
                  key={run.run_id}
                  className="flex items-center justify-between gap-3 border-b border-slate-50 px-3 py-2 text-xs last:border-b-0"
                >
                  <div className="min-w-0">
                    <div className="font-medium text-slate-700 capitalize">
                      {run.trigger} run
                    </div>
                    <div className="text-slate-400">
                      {run.started_at
                        ? dayjs(run.started_at).format("MMM D, h:mm A")
                        : "—"}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-slate-500">
                      ↑{run.indexed} ·{run.skipped} skipped
                      {run.deleted > 0 ? ` ·${run.deleted} del` : ""}
                    </span>
                    <RunStatusBadge status={run.status} />
                  </div>
                </div>
              ))
            )}
          </div>
          {latestRun?.status === "failed" && latestRun.error_detail && (
            <p className="mt-1.5 text-xs text-rose-600">
              {latestRun.error_detail}
            </p>
          )}
        </div>

        {/* Resync a single doc */}
        <ResyncDocSection selection={sel} />

        {/* Danger zone */}
        <div className="rounded-lg bg-rose-50/50 px-3 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-rose-600">
            Danger zone
          </div>
          <div className="mt-2 flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-slate-900">
                Disconnect ClickUp
              </div>
              <div className="mt-0.5 text-xs text-slate-500">
                Removes the sync config. Choose whether to keep or delete
                indexed docs.
              </div>
            </div>
            <Button
              variant="danger"
              size="sm"
              onClick={() => setDisconnectOpen(true)}
            >
              Disconnect
            </Button>
          </div>
        </div>
      </div>
    );
  };

  return (
    <>
      <Dialog
        open={open && !editOpen && !disconnectOpen}
        onClose={onClose}
        widthClass="max-w-xl"
        title="Connected Sources"
      >
        {body()}
      </Dialog>

      {config && (
        <EditConfigDialog
          open={editOpen}
          onClose={() => setEditOpen(false)}
          config={config}
        />
      )}

      <DisconnectDialog
        open={disconnectOpen}
        onClose={() => setDisconnectOpen(false)}
        onDisconnected={onClose}
      />
    </>
  );
};
