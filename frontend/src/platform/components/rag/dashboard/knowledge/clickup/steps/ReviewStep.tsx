import type { ReactNode } from "react";

import { TriangleAlert } from "lucide-react";

import type { ClickUpWizardState } from "../hooks/useClickUpWizardState";
import { scheduleLabel, scheduleToConfig } from "../utils";

interface ReviewStepProps {
  wizard: ClickUpWizardState;
}

const Row = ({ label, value }: { label: string; value: ReactNode }) => (
  <div className="grid grid-cols-[160px_1fr] gap-2 border-b border-slate-100 py-1.5 text-sm last:border-b-0">
    <span className="text-slate-500">{label}</span>
    <span className="font-medium text-slate-900">{value}</span>
  </div>
);

/** Step 4 — review the collected settings before the initial index run. */
export const ReviewStep = ({ wizard }: ReviewStepProps) => {
  const schedule = scheduleToConfig(
    wizard.schedulePreset,
    wizard.customMinutes,
  );

  return (
    <div>
      <p className="mb-4 text-sm text-slate-600">
        Review your settings before starting the initial index run.
      </p>

      <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2">
        <Row label="Workspace" value={wizard.workspaceId} />
        <Row label="Spaces" value={wizard.spaceIds.join(", ") || "—"} />
        <Row
          label="Folders in scope"
          value={
            wizard.selectAllFolders
              ? "All folders"
              : `${wizard.selectedFolderIds.length} selected`
          }
        />
        <Row
          label="Additional docs"
          value={
            wizard.docIds.length
              ? `${wizard.docIds.length} specific doc(s)`
              : "None"
          }
        />
        <Row
          label="Excluded keywords"
          value={
            wizard.excludeKeywords.length
              ? wizard.excludeKeywords.join(", ")
              : "None"
          }
        />
        <Row label="Sync schedule" value={scheduleLabel(schedule)} />
        <Row
          label="Deletion propagation"
          value={
            <span
              className={
                wizard.deletionEnabled ? "text-emerald-700" : "text-slate-900"
              }
            >
              {wizard.deletionEnabled ? "Enabled" : "Disabled"}
            </span>
          }
        />
        <Row
          label="Force reindex"
          value={
            wizard.forceReindex
              ? "Yes — reprocess all pages"
              : "No — skip unchanged"
          }
        />
      </div>

      <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-800">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" />
        <span>
          Initial indexing runs in the background and may take several minutes.
          You can close this dialog and monitor progress from the knowledge
          list.
        </span>
      </div>
    </div>
  );
};
