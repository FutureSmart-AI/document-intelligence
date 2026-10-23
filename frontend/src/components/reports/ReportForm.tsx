"use client";

import { useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import {
  Btn,
  Card,
  Chip,
  FieldHelp,
  FieldLabel,
  IconBtn,
  Input,
  LinkBtn,
  PageHead,
  Pill,
  Select,
  Textarea,
} from "@/components/ui";
import type { QueryGroup } from "@/data/reports";
import {
  SAMPLE_CSV,
  SAMPLE_JSON,
} from "@/platform/components/batch-reports/constants";
import {
  DOW_OPTIONS,
  fieldsToSchedule,
  getTimezones,
  scheduleToFields,
  type ScheduleFields,
  type ScheduleMode,
} from "@/platform/components/batch-reports/schedule";
import { useToast } from "@/platform/components/ToastContext";
import type { ScheduleInput } from "@/platform/types/batch-reports";

import { parseQuestionGroups } from "./parseQuestionGroups";

/** What the form edits — the report as this screen holds it before sending. */
export interface ReportDraft {
  name: string;
  instructions: string;
  analysis: string;
  groups: QueryGroup[];
  schedule?: ScheduleInput | null;
}

/**
 * The report definition: its questions, how they are grouped, and when it runs.
 *
 * Two things about the schedule are the service's, not this form's. It stores
 * only `{ frequency, cron, timezone }` — there is no field for a time of day —
 * so "daily at 09:00" is encoded as a cron expression by the shared schedule
 * helpers, the same ones the agent dashboard's report form uses. And a group
 * carries a type and its questions and nothing else: the artifact draws a group
 * name, but the API has no field for one, so this form does not offer a box
 * whose contents would be dropped on save.
 */
export default function ReportForm({
  draft,
  onDraftChange,
  editing,
  maxQuestions,
  saving,
  onCancel,
  onSave,
}: {
  draft: ReportDraft;
  onDraftChange: (next: ReportDraft) => void;
  editing: boolean;
  /** This collection's cap, when the caller may read it. */
  maxQuestions?: number;
  saving: boolean;
  onCancel: () => void;
  onSave: (runNow: boolean) => void;
}) {
  const { toast } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [showFormat, setShowFormat] = useState(false);
  const [schedule, setSchedule] = useState<ScheduleFields>(() =>
    scheduleToFields(draft.schedule),
  );

  const timezones = useMemo(() => getTimezones(), []);
  const asked = draft.groups.reduce(
    (total, group) =>
      total + group.queries.filter((query) => query.trim()).length,
    0,
  );
  const overCap = maxQuestions != null && asked > maxQuestions;

  const patch = (next: Partial<ReportDraft>) =>
    onDraftChange({ ...draft, ...next });

  const patchSchedule = (next: Partial<ScheduleFields>) => {
    const merged = { ...schedule, ...next };
    setSchedule(merged);
    patch({ schedule: fieldsToSchedule(merged) });
  };

  const setGroup = (index: number, next: QueryGroup) =>
    patch({
      groups: draft.groups.map((group, i) => (i === index ? next : group)),
    });

  const importFile = async (file: File) => {
    try {
      const groups = parseQuestionGroups(await file.text(), file.name);
      // Replacing rather than merging, and said out loud in the format panel:
      // a file that appends would make two uploads of the same file different
      // from one.
      patch({ groups });
      toast.success(
        `Loaded ${groups.reduce((total, group) => total + group.queries.length, 0)} questions in ${groups.length} group${groups.length === 1 ? "" : "s"}.`,
      );
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "That file could not be read.",
      );
    }
  };

  return (
    <>
      <LinkBtn className="mb-3.5" onClick={onCancel}>
        <Icon name="caretLeft" className="size-4" /> Batch reports
      </LinkBtn>
      <PageHead
        title={editing ? "Edit report" : "New report"}
        sub="Group the questions you want answered together, then run them now or on a schedule."
      />

      <Card className="mb-3.5">
        <div className="p-4">
          <div className="mb-4">
            <FieldLabel required htmlFor="report-name">
              Report name
            </FieldLabel>
            <Input
              id="report-name"
              placeholder="For example: Weekly candidate pool review"
              value={draft.name}
              onChange={(event) => patch({ name: event.target.value })}
            />
          </div>
          <div>
            <FieldLabel>Questions</FieldLabel>
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone={overCap ? "danger" : "count"} className="tnum">
                {maxQuestions == null
                  ? `${asked} question${asked === 1 ? "" : "s"}`
                  : `${asked} / ${maxQuestions} used`}
              </Pill>
              <Btn size="sm" onClick={() => setShowFormat((open) => !open)}>
                {showFormat ? "Hide" : "Show"} upload format
              </Btn>
              <Btn size="sm" onClick={() => fileInput.current?.click()}>
                Upload JSON or CSV
              </Btn>
              <input
                ref={fileInput}
                type="file"
                accept=".json,.csv,application/json,text/csv"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  // Cleared so picking the same file twice fires again.
                  event.target.value = "";
                  if (file) importFile(file);
                }}
              />
            </div>
            <FieldHelp>
              {maxQuestions == null
                ? "The service caps how many questions one report may hold; it reports 422 over the cap."
                : `This collection allows ${maxQuestions} questions per report.`}
            </FieldHelp>
          </div>

          {showFormat && (
            <div className="mt-3.5 rounded-di-md border border-line bg-sunken p-3.5">
              <Pill tone="warn">Uploading replaces every group below</Pill>
              <p className="mt-2.5 text-[12px] font-bold text-ink">
                CSV columns
              </p>
              <pre className="mt-1 overflow-x-auto rounded-di-sm border border-line bg-surface p-2.5 font-mono text-[11.5px] text-ink-2">
                {SAMPLE_CSV}
              </pre>
              <p className="mt-2.5 text-[12px] font-bold text-ink">JSON</p>
              <pre className="mt-1 overflow-x-auto rounded-di-sm border border-line bg-surface p-2.5 font-mono text-[11.5px] text-ink-2">
                {SAMPLE_JSON}
              </pre>
              <FieldHelp>
                A CSV group name is read as a label only — the service stores a
                group&apos;s type and its questions, so names are not saved.
              </FieldHelp>
            </div>
          )}
        </div>
      </Card>

      {draft.groups.map((group, groupIndex) => {
        const sequential = group.type === "sequential";
        return (
          <Card key={groupIndex} className="mb-3.5">
            <div className="flex flex-wrap items-center gap-2.5 border-b border-line px-4 py-3">
              <span className="text-[13.5px] font-bold text-ink">
                Group {groupIndex + 1}
              </span>
              <div
                className="inline-flex overflow-hidden rounded-di-md border border-line"
                role="tablist"
                aria-label={`Group ${groupIndex + 1} type`}
              >
                {(["independent", "sequential"] as const).map((type) => (
                  <button
                    key={type}
                    type="button"
                    role="tab"
                    aria-selected={group.type === type}
                    onClick={() => setGroup(groupIndex, { ...group, type })}
                    className={
                      group.type === type
                        ? "cursor-pointer bg-accent-soft px-2.5 py-1.5 text-[12px] font-bold text-accent-softtext"
                        : "cursor-pointer bg-surface px-2.5 py-1.5 text-[12px] font-semibold text-ink-2 hover:bg-sunken"
                    }
                  >
                    {type === "independent" ? "Independent" : "Sequential"}
                  </button>
                ))}
              </div>
              <Chip tone="muted" className="tnum">
                {group.queries.length} question
                {group.queries.length === 1 ? "" : "s"}
              </Chip>
              <Btn
                size="sm"
                className="ml-auto"
                disabled={draft.groups.length === 1}
                onClick={() =>
                  patch({
                    groups: draft.groups.filter((_, i) => i !== groupIndex),
                  })
                }
              >
                Remove group
              </Btn>
            </div>
            <div className="p-4">
              <p className="mb-2.5 text-[12px] text-ink-2">
                {sequential
                  ? "Questions run in order, sharing one conversation — each can build on the answer before it."
                  : "Questions run independently of one another."}
              </p>
              <div className="grid gap-2">
                {group.queries.map((query, queryIndex) => (
                  <div key={queryIndex} className="flex items-center gap-2">
                    <span className="tnum w-6 shrink-0 text-center text-[11.5px] text-ink-3">
                      {queryIndex + 1}
                    </span>
                    <Input
                      aria-label={`${sequential ? "Step" : "Question"} ${queryIndex + 1} of group ${groupIndex + 1}`}
                      placeholder="Ask a question about this collection"
                      value={query}
                      onChange={(event) =>
                        setGroup(groupIndex, {
                          ...group,
                          queries: group.queries.map((entry, i) =>
                            i === queryIndex ? event.target.value : entry,
                          ),
                        })
                      }
                    />
                    <IconBtn
                      label="Remove question"
                      disabled={group.queries.length === 1}
                      onClick={() =>
                        setGroup(groupIndex, {
                          ...group,
                          queries: group.queries.filter(
                            (_, i) => i !== queryIndex,
                          ),
                        })
                      }
                    >
                      <Icon name="x" className="size-4" />
                    </IconBtn>
                  </div>
                ))}
              </div>
              <Btn
                size="sm"
                className="mt-2.5"
                startIcon={<Icon name="plus" className="size-4" />}
                onClick={() =>
                  setGroup(groupIndex, {
                    ...group,
                    queries: [...group.queries, ""],
                  })
                }
              >
                Add question
              </Btn>
            </div>
          </Card>
        );
      })}

      <div className="mb-3.5 flex flex-wrap gap-2">
        {(["independent", "sequential"] as const).map((type) => (
          <Btn
            key={type}
            size="sm"
            startIcon={<Icon name="plus" className="size-4" />}
            onClick={() =>
              patch({ groups: [...draft.groups, { type, queries: [""] }] })
            }
          >
            {type === "independent" ? "Independent" : "Sequential"} group
          </Btn>
        ))}
      </div>

      <Card className="mb-3.5">
        <div className="grid gap-4 p-4">
          <div>
            <FieldLabel htmlFor="report-instructions">
              Report instructions
            </FieldLabel>
            <Textarea
              id="report-instructions"
              rows={3}
              placeholder="How every question in this report should be answered"
              value={draft.instructions}
              onChange={(event) => patch({ instructions: event.target.value })}
            />
            <FieldHelp>
              Applied to every question in the report, and shown on each run.
            </FieldHelp>
          </div>
          <div>
            <FieldLabel htmlFor="report-analysis">Analysis prompt</FieldLabel>
            <Textarea
              id="report-analysis"
              rows={3}
              placeholder="What the overall analysis at the top of the results should cover"
              value={draft.analysis}
              onChange={(event) => patch({ analysis: event.target.value })}
            />
            <FieldHelp>
              Used once per run, over all of the answers together.
            </FieldHelp>
          </div>

          <div className="grid gap-3.5 sm:grid-cols-2">
            <div>
              <FieldLabel htmlFor="report-schedule">Schedule</FieldLabel>
              <Select
                id="report-schedule"
                value={schedule.mode}
                onChange={(event) =>
                  patchSchedule({ mode: event.target.value as ScheduleMode })
                }
              >
                <option value="">Only when I run it</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
                <option value="cron">Custom expression</option>
              </Select>
            </div>

            {schedule.mode && schedule.mode !== "cron" && (
              <div>
                <FieldLabel htmlFor="report-time">Time</FieldLabel>
                <Input
                  id="report-time"
                  type="time"
                  value={schedule.time}
                  onChange={(event) =>
                    patchSchedule({ time: event.target.value })
                  }
                />
              </div>
            )}

            {schedule.mode === "weekly" && (
              <div>
                <FieldLabel htmlFor="report-dow">Day of the week</FieldLabel>
                <Select
                  id="report-dow"
                  value={schedule.dow}
                  onChange={(event) =>
                    patchSchedule({ dow: event.target.value })
                  }
                >
                  {DOW_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </div>
            )}

            {schedule.mode === "monthly" && (
              <div>
                <FieldLabel htmlFor="report-dom">Day of the month</FieldLabel>
                <Select
                  id="report-dom"
                  value={schedule.dom}
                  onChange={(event) =>
                    patchSchedule({ dom: event.target.value })
                  }
                >
                  {Array.from({ length: 28 }, (_, i) => String(i + 1)).map(
                    (day) => (
                      <option key={day} value={day}>
                        {day}
                      </option>
                    ),
                  )}
                </Select>
                <FieldHelp>
                  Stops at 28 so a monthly report fires in February too.
                </FieldHelp>
              </div>
            )}

            {schedule.mode === "cron" && (
              <div>
                <FieldLabel required htmlFor="report-cron">
                  Schedule expression
                </FieldLabel>
                <Input
                  id="report-cron"
                  className="font-mono"
                  placeholder="0 9 * * 1-5"
                  value={schedule.cron}
                  onChange={(event) =>
                    patchSchedule({ cron: event.target.value })
                  }
                />
                <FieldHelp>
                  Five fields: minute, hour, day of month, month, day of week.
                </FieldHelp>
              </div>
            )}

            {schedule.mode && (
              <div>
                <FieldLabel htmlFor="report-tz">Timezone</FieldLabel>
                <Select
                  id="report-tz"
                  value={schedule.timezone}
                  onChange={(event) =>
                    patchSchedule({ timezone: event.target.value })
                  }
                >
                  {timezones.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone}
                    </option>
                  ))}
                </Select>
                <FieldHelp>The schedule fires in this timezone.</FieldHelp>
              </div>
            )}
          </div>
        </div>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11.5px] text-ink-3">
          {editing
            ? "Saving does not start a run — run it from the report's own page."
            : "“Save report” does not start a run; use “Save & run” for that."}
        </span>
        <Btn className="ml-auto" onClick={onCancel}>
          Cancel
        </Btn>
        <Btn disabled={saving} onClick={() => onSave(false)}>
          Save report
        </Btn>
        {!editing && (
          <Btn
            variant="primary"
            startIcon={<Icon name="play" className="size-4" />}
            disabled={saving}
            onClick={() => onSave(true)}
          >
            Save &amp; run
          </Btn>
        )}
      </div>
    </>
  );
}
