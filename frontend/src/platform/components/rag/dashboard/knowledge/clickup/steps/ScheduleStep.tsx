import { Input, Select } from "@/platform/components/ui";
import { cn } from "@/platform/utils";

import { CUSTOM_INTERVAL_UNITS, SCHEDULE_OPTIONS } from "../constants";
import type { ClickUpWizardState } from "../hooks/useClickUpWizardState";

interface ScheduleStepProps {
  wizard: ClickUpWizardState;
}

/** Step 3 — choose how often to crawl ClickUp. */
export const ScheduleStep = ({ wizard }: ScheduleStepProps) => {
  // Represent the custom interval as {value, unitMinutes} derived from customMinutes.
  const unit =
    CUSTOM_INTERVAL_UNITS.slice()
      .reverse()
      .find((u) => wizard.customMinutes % u.minutes === 0) ??
    CUSTOM_INTERVAL_UNITS[0];
  const unitValue = Math.max(
    1,
    Math.round(wizard.customMinutes / unit.minutes),
  );

  return (
    <div>
      <p className="mb-4 text-sm text-slate-600">
        How often should we crawl ClickUp and sync new, updated, or deleted
        documents?
      </p>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {SCHEDULE_OPTIONS.map((opt) => {
          const active = wizard.schedulePreset === opt.preset;
          return (
            <button
              key={opt.preset}
              type="button"
              onClick={() => wizard.setSchedulePreset(opt.preset)}
              className={cn(
                "flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors",
                active
                  ? "border-indigo-500 bg-indigo-50/50"
                  : "border-slate-200 hover:border-slate-300",
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                  active ? "border-indigo-600" : "border-slate-300",
                )}
              >
                {active && (
                  <span className="size-2 rounded-full bg-indigo-600" />
                )}
              </span>
              <span>
                <span className="block text-sm font-medium text-slate-900">
                  {opt.label}
                </span>
                <span className="mt-0.5 block text-xs text-slate-500">
                  {opt.description}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      {wizard.schedulePreset === "custom" && (
        <div className="mt-3 flex items-center gap-2">
          <Input
            type="number"
            min={1}
            fullWidth={false}
            className="w-24"
            size="sm"
            value={String(unitValue)}
            onChange={(v) =>
              wizard.setCustomMinutes(
                Math.max(1, Number(v) || 1) * unit.minutes,
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
            value={unit.label}
            onChange={(label) => {
              const next = CUSTOM_INTERVAL_UNITS.find((u) => u.label === label);
              if (next) wizard.setCustomMinutes(unitValue * next.minutes);
            }}
          />
        </div>
      )}
    </div>
  );
};
