import { useEffect, useState } from "react";

import {
  Button,
  Dialog,
  Input,
  Select,
  Switch,
} from "@/platform/components/ui";

import { ChipsField } from "./components/ChipsField";
import { CUSTOM_INTERVAL_UNITS, SCHEDULE_OPTIONS } from "./constants";
import { useSaveClickUpConfig } from "./hooks/useClickUpApi";
import type { ConnectorConfig, SchedulePreset } from "./types";
import { configToSchedule, normalizeKeyword, scheduleToConfig } from "./utils";

interface EditConfigDialogProps {
  open: boolean;
  onClose: () => void;
  config: ConnectorConfig;
}

// Schedule presets selectable in the edit overlay. "custom" reveals the
// interval + unit controls below the dropdown.
const EDIT_PRESETS: SchedulePreset[] = [
  "1h",
  "6h",
  "12h",
  "daily",
  "weekly",
  "monthly",
  "custom",
  "manual",
];

/** Edit the saved ClickUp config — space, schedule, keywords, toggles. */
export const EditConfigDialog = ({
  open,
  onClose,
  config,
}: EditConfigDialogProps) => {
  const saveConfig = useSaveClickUpConfig();

  const [spaceIdsInput, setSpaceIdsInput] = useState("");
  const [preset, setPreset] = useState<SchedulePreset>("6h");
  const [customMinutes, setCustomMinutes] = useState(240);
  const [keywords, setKeywords] = useState<string[]>([]);
  const [deletionEnabled, setDeletionEnabled] = useState(true);
  const [forceReindex, setForceReindex] = useState(false);

  // Re-hydrate local state from the saved config whenever the dialog opens.
  useEffect(() => {
    if (!open) return;
    setSpaceIdsInput((config.selection.space_ids ?? []).join(", "));
    const nextPreset = configToSchedule(config);
    setPreset(nextPreset);
    // Seed the custom interval from the saved value when it's a non-preset
    // interval; otherwise keep a sensible default.
    setCustomMinutes(
      nextPreset === "custom" && config.interval_minutes
        ? config.interval_minutes
        : 240,
    );
    setKeywords(config.selection.exclude_name_keywords ?? []);
    setDeletionEnabled(config.selection.deletion_enabled ?? true);
    setForceReindex(false);
  }, [open, config]);

  // Represent the custom interval as {value, unit} derived from customMinutes.
  const customUnit =
    CUSTOM_INTERVAL_UNITS.slice()
      .reverse()
      .find((u) => customMinutes % u.minutes === 0) ?? CUSTOM_INTERVAL_UNITS[0];
  const customUnitValue = Math.max(
    1,
    Math.round(customMinutes / customUnit.minutes),
  );

  const addKeyword = (raw: string) => {
    const kw = normalizeKeyword(raw);
    if (!kw) return false;
    setKeywords((prev) => (prev.includes(kw) ? prev : [...prev, kw]));
    return true;
  };
  const removeKeyword = (kw: string) =>
    setKeywords((prev) => prev.filter((k) => k !== kw));

  const handleSave = async () => {
    await saveConfig.mutateAsync({
      space_ids: spaceIdsInput
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      exclude_name_keywords: keywords,
      deletion_enabled: deletionEnabled,
      force_reindex: forceReindex,
      ...scheduleToConfig(preset, customMinutes),
    });
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      widthClass="max-w-lg"
      title="Edit ClickUp Configuration"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleSave}
            loading={saveConfig.isPending}
            loadingText="Saving…"
          >
            Save changes
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-sm font-medium text-slate-700">
            Workspace ID
          </label>
          <Input
            fullWidth
            value={config.selection.workspace_id ?? ""}
            readOnly
            disabled
          />
          <p className="text-xs text-slate-500">
            Workspace cannot be changed. Disconnect and reconnect to change it.
          </p>
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium text-slate-700">Space ID</label>
          <Input
            fullWidth
            value={spaceIdsInput}
            onChange={setSpaceIdsInput}
            placeholder="Comma-separate for multiple spaces"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium text-slate-700">
            Sync schedule
          </label>
          <Select
            fullWidth
            value={preset}
            onChange={(v) => setPreset(v as SchedulePreset)}
            options={EDIT_PRESETS.map((p) => ({
              label: SCHEDULE_OPTIONS.find((o) => o.preset === p)?.label ?? p,
              value: p,
            }))}
          />
          {preset === "custom" && (
            <div className="flex items-center gap-2 pt-1">
              <Input
                type="number"
                min={1}
                fullWidth={false}
                className="w-24"
                size="sm"
                value={String(customUnitValue)}
                onChange={(v) =>
                  setCustomMinutes(
                    Math.max(1, Number(v) || 1) * customUnit.minutes,
                  )
                }
              />
              <Select
                size="sm"
                fullWidth={false}
                options={CUSTOM_INTERVAL_UNITS.map((u) => ({
                  label: u.label,
                  value: u.label,
                }))}
                value={customUnit.label}
                onChange={(label) => {
                  const next = CUSTOM_INTERVAL_UNITS.find(
                    (u) => u.label === label,
                  );
                  if (next) setCustomMinutes(customUnitValue * next.minutes);
                }}
              />
            </div>
          )}
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium text-slate-700">
            Exclude keywords
          </label>
          <ChipsField
            chips={keywords}
            onAdd={addKeyword}
            onRemove={removeKeyword}
            placeholder="Add keyword…"
            variant="amber"
          />
        </div>

        <div className="divide-y divide-slate-50 border-t border-slate-100 pt-2">
          <div className="flex items-start justify-between gap-3 py-2.5">
            <div>
              <div className="text-sm font-medium text-slate-900">
                Deletion propagation
              </div>
              <div className="mt-0.5 text-xs text-slate-500">
                Remove docs from the index when deleted in ClickUp.
              </div>
            </div>
            <Switch
              checked={deletionEnabled}
              onCheckedChange={setDeletionEnabled}
            />
          </div>
          <div className="flex items-start justify-between gap-3 py-2.5">
            <div>
              <div className="text-sm font-medium text-slate-900">
                Force reindex on next sync
              </div>
              <div className="mt-0.5 text-xs text-slate-500">
                Override the freshness check for the next run only.
              </div>
            </div>
            <Switch checked={forceReindex} onCheckedChange={setForceReindex} />
          </div>
        </div>

        <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
          To rotate the API key, disconnect and reconnect with a new token.
        </p>
      </div>
    </Dialog>
  );
};
