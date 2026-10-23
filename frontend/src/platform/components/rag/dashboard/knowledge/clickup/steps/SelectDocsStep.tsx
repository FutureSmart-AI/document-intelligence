import { Check, FolderClosed, Loader2, Minus } from "lucide-react";

import { Switch } from "@/platform/components/ui";
import { cn } from "@/platform/utils";

import { ChipsField } from "../components/ChipsField";
import { useClickUpFolders } from "../hooks/useClickUpApi";
import type { ClickUpWizardState } from "../hooks/useClickUpWizardState";

interface SelectDocsStepProps {
  wizard: ClickUpWizardState;
}

const SectionDivider = ({ label }: { label: string }) => (
  <div className="my-4 flex items-center gap-2.5">
    <div className="h-px flex-1 bg-slate-100" />
    <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
      {label}
    </span>
    <div className="h-px flex-1 bg-slate-100" />
  </div>
);

const Checkbox = ({
  state,
  onClick,
}: {
  state: "on" | "off" | "half";
  onClick: () => void;
}) => (
  <button
    type="button"
    onClick={onClick}
    className={cn(
      "flex size-4 shrink-0 items-center justify-center rounded border",
      state === "off"
        ? "border-slate-300 bg-white"
        : "border-indigo-600 bg-indigo-600 text-white",
    )}
    aria-checked={state === "on"}
    role="checkbox"
  >
    {state === "on" && <Check className="size-3" />}
    {state === "half" && <Minus className="size-3" />}
  </button>
);

/** Folder list for a single space — fetched lazily once the space id is known. */
const SpaceFolderList = ({
  wizard,
  spaceId,
}: SelectDocsStepProps & { spaceId: string }) => {
  const { data: folders, isLoading, isError } = useClickUpFolders(spaceId);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-3 py-3 text-xs text-slate-500">
        <Loader2 className="size-3.5 animate-spin" /> Loading folders…
      </div>
    );
  }
  if (isError) {
    return (
      <div className="px-3 py-3 text-xs text-rose-600">
        Failed to load folders for space {spaceId}.
      </div>
    );
  }
  if (!folders || folders.length === 0) {
    return (
      <div className="px-3 py-3 text-xs text-slate-400">
        No folders found in space {spaceId}.
      </div>
    );
  }

  return (
    <>
      {folders.map((folder) => {
        const checked =
          wizard.selectAllFolders ||
          wizard.selectedFolderIds.includes(folder.id);
        return (
          <div
            key={folder.id}
            className="flex items-center gap-2 border-b border-slate-50 px-3 py-2 last:border-b-0 hover:bg-slate-50"
          >
            <Checkbox
              state={checked ? "on" : "off"}
              onClick={() => wizard.toggleFolder(folder.id)}
            />
            <FolderClosed className="size-3.5 text-indigo-500" />
            <span className="text-sm text-slate-700">{folder.name}</span>
          </div>
        );
      })}
    </>
  );
};

/** Step 2 — pick folders, add specific docs, exclude keywords, set indexing toggles. */
export const SelectDocsStep = ({ wizard }: SelectDocsStepProps) => {
  return (
    <div className="space-y-1">
      <label className="mb-2 block text-sm font-medium text-slate-700">
        Select folders to index
      </label>

      <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-200">
        {/* Select-all header */}
        <div className="flex items-center gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
          <Checkbox
            state={wizard.selectAllFolders ? "on" : "half"}
            onClick={() => {
              wizard.setSelectAllFolders(!wizard.selectAllFolders);
            }}
          />
          <FolderClosed className="size-3.5 text-indigo-500" />
          <span className="text-sm font-semibold text-slate-700">
            All folders {wizard.selectAllFolders ? "" : "(custom selection)"}
          </span>
        </div>
        {wizard.spaceIds.map((spaceId) => (
          <SpaceFolderList key={spaceId} wizard={wizard} spaceId={spaceId} />
        ))}
      </div>

      <p className="pt-2 text-xs text-slate-500">
        {wizard.selectAllFolders
          ? "All folders in the selected space(s) will be indexed."
          : `${wizard.selectedFolderIds.length} folder(s) selected.`}
      </p>

      {/* Add specific docs */}
      <SectionDivider label="Also add specific docs (optional)" />
      <p className="mb-2 text-xs text-slate-500">
        Paste a doc URL or Doc ID. These are indexed in addition to the folders
        above; sub-pages are included automatically.
      </p>
      <ChipsField
        chips={wizard.docIds}
        onAdd={wizard.addDocId}
        onRemove={wizard.removeDocId}
        placeholder="Paste doc URL or Doc ID (e.g. abc12de-3456)"
      />

      {/* Exclude keywords */}
      <SectionDivider label="Exclude by name keyword" />
      <p className="mb-2 text-xs text-slate-500">
        Any folder, doc, or page whose name contains these keywords is skipped
        (case-insensitive).
      </p>
      <ChipsField
        chips={wizard.excludeKeywords}
        onAdd={wizard.addKeyword}
        onRemove={wizard.removeKeyword}
        placeholder="e.g. ARCHIVE, OLD, DEPRECATED"
        addLabel="Add keyword"
        variant="amber"
      />

      {/* Indexing options */}
      <SectionDivider label="Indexing options" />
      <div className="divide-y divide-slate-50">
        <div className="flex items-start justify-between gap-3 py-2.5">
          <div>
            <div className="text-sm font-medium text-slate-900">
              Skip empty pages
            </div>
            <div className="mt-0.5 text-xs text-slate-500">
              Empty pages are always skipped — their content is not chunked or
              embedded.
            </div>
          </div>
          <Switch checked disabled />
        </div>
        <div className="flex items-start justify-between gap-3 py-2.5">
          <div>
            <div className="text-sm font-medium text-slate-900">
              Enable deletion propagation
            </div>
            <div className="mt-0.5 text-xs text-slate-500">
              When a doc or page is deleted in ClickUp, remove it from the index
              on the next sync.
            </div>
          </div>
          <Switch
            checked={wizard.deletionEnabled}
            onCheckedChange={wizard.setDeletionEnabled}
          />
        </div>
        <div className="flex items-start justify-between gap-3 py-2.5">
          <div>
            <div className="text-sm font-medium text-slate-900">
              Force reindex on next sync
            </div>
            <div className="mt-0.5 text-xs text-slate-500">
              Override the freshness check — re-index every selected page on the
              next run only.
            </div>
          </div>
          <Switch
            checked={wizard.forceReindex}
            onCheckedChange={wizard.setForceReindex}
          />
        </div>
      </div>
    </div>
  );
};
