"use client";

import { useMemo, useState } from "react";

import Icon from "@/components/Icon";
import {
  Btn,
  Card,
  Chip,
  EmptyNote,
  IconBtn,
  Input,
  LinkBtn,
  Modal,
  PageHead,
  Pill,
  SectionLabel,
  Select,
} from "@/components/ui";
import {
  useLastRuns,
  useReportMutations,
  useReports,
  useReportSettings,
  useRun,
  useRuns,
  useRunStatus,
  type QueryGroup,
  type Report,
} from "@/data/reports";
import { toChatSources } from "@/platform/components/batch-reports/batchSources";
import { friendlyReportError } from "@/platform/components/batch-reports/errorMessage";
import {
  effectiveRunStatus,
  isEffectivelyDone,
  isLiveRun,
} from "@/platform/components/batch-reports/runStatus";
import {
  describeSchedule,
  scheduleShortLabel,
} from "@/platform/components/batch-reports/schedule";
import { Markdown } from "@/platform/components/conversations/markdown";
import SourcesList from "@/platform/components/conversations/SourcesList";
import { useToast } from "@/platform/components/ToastContext";
import { useAuth } from "@/platform/hooks/useAuth";
import type {
  ExportFormat,
  QueryResult,
  RunGroupResult,
} from "@/platform/types/batch-reports";

import {
  ANSWER_VERDICT,
  EMPTY_REPORT,
  HISTORY_ROWS,
  RUN_TONE,
  SCHEDULE_FILTERS,
  STATUS_FILTERS,
} from "./constants";
import ReportForm, { type ReportDraft } from "./ReportForm";

/**
 * Batch reports — a set of questions run together against one collection.
 *
 * Four screens behind one route, the way the artifact draws them: the list, the
 * form, one report's run history, and one run's answers. They are pages rather
 * than tabs because each is about a narrower thing than the last, and the back
 * link says what you are going back to.
 *
 * Every answer comes from the same chat service the Conversations screen uses,
 * so it is rendered with that screen's own markdown and sources components
 * rather than a second renderer — an answer here reads exactly as it does there,
 * including which documents it came from.
 */

type Page = "list" | "form" | "detail" | "run";

/** "1.4s", "820ms", "—" */
const ms = (value: number | null | undefined) => {
  if (value == null) return "—";
  return value >= 1000
    ? `${(value / 1000).toFixed(1)}s`
    : `${Math.round(value)}ms`;
};

/** "19 Aug 2026, 21:19" */
const absT = (at: string | null | undefined) =>
  at
    ? new Date(at).toLocaleString("en-GB", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

const nfmt = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString();

/** `query_groups` can come back null on a report the worker is still writing. */
const countQuestions = (groups: QueryGroup[] | null | undefined) =>
  (groups ?? []).reduce((total, group) => total + group.queries.length, 0);

/** `token_usage` is a bare total on some runs and a breakdown on others. */
const totalTokens = (result: QueryResult): number | undefined => {
  const usage = (result as Record<string, unknown>).token_usage;
  if (typeof usage === "number") return usage;
  if (usage && typeof usage === "object") {
    const total = (usage as Record<string, unknown>).total_tokens;
    if (typeof total === "number") return total;
  }
  return undefined;
};

function verdictOf(result: QueryResult) {
  if (result.error) return ANSWER_VERDICT.failed;
  if (!result.answer) return ANSWER_VERDICT.pending;
  return ANSWER_VERDICT.answered;
}

/** One question inside a run: its answer, its sources, or why it failed. */
function AnswerCard({ result, index }: { result: QueryResult; index: number }) {
  const verdict = verdictOf(result);
  const tokens = totalTokens(result);
  // The same mapping the dashboard's run viewer uses, so the sources panel here
  // is the one the chat thread shows — file names, passages and all.
  const sources = useMemo(
    () =>
      toChatSources(result.sources_used ?? [], result.retrieved_chunks ?? []),
    [result.sources_used, result.retrieved_chunks],
  );

  return (
    <div className="border-t border-line-soft p-4 first:border-t-0">
      <div className="flex items-start gap-2.5">
        <b className="text-[13.5px] text-ink">{result.question}</b>
        <Pill tone={verdict.tone} className="ml-auto shrink-0">
          {verdict.label}
        </Pill>
      </div>

      <p className="mt-1 text-[11.5px] text-ink-3">
        {ms(result.response_time_ms)}
        {tokens != null ? ` · ${nfmt(tokens)} tokens` : ""}
        {result.relevance_score != null
          ? ` · relevance ${result.relevance_score.toFixed(2)}`
          : ""}
      </p>

      {result.answer ? (
        <div className="di-md mt-2 text-ink">
          <Markdown>{result.answer}</Markdown>
        </div>
      ) : result.error ? (
        <p className="mt-2 rounded-di-md border border-danger-dot/40 bg-danger-bg p-2.5 text-[12px] text-danger-text">
          {result.error}
        </p>
      ) : (
        <p className="mt-2 text-[13px] text-ink-3 italic">
          No answer was recorded for this question.
        </p>
      )}

      {sources.length > 0 && (
        <SourcesList sources={sources} messageIndex={index} />
      )}
    </div>
  );
}

/** One group — independent questions, or the steps of a sequential run. */
function GroupCard({ group, index }: { group: RunGroupResult; index: number }) {
  const sequential = group.type === "sequential";
  return (
    <Card className="mb-3.5 overflow-hidden">
      <div className="flex items-center gap-2.5 border-b border-line px-4 py-3">
        <span className="text-[13.5px] font-bold text-ink">
          Group {index + 1}
        </span>
        <Chip tone="muted">{sequential ? "Sequential" : "Independent"}</Chip>
        <span className="ml-auto text-[11.5px] text-ink-3">
          {group.results.length} {sequential ? "steps" : "questions"}
        </span>
      </div>
      {group.results.length ? (
        group.results.map((result, i) => (
          <AnswerCard
            key={result.query_id ?? i}
            result={result}
            index={index * 100 + i}
          />
        ))
      ) : (
        <div className="p-4">
          <EmptyNote>This group produced no answers.</EmptyNote>
        </div>
      )}
    </Card>
  );
}

export default function ReportsView() {
  const { toast } = useToast();
  // A collection *is* the knowledge base, so the bridge's `agentId` is the id
  // every report route is keyed by.
  const { agentId: collectionId } = useAuth();

  const [page, setPage] = useState<Page>("list");
  const [reportId, setReportId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ReportDraft>(EMPTY_REPORT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Report | null>(null);

  // List controls, as the artifact draws them.
  const [search, setSearch] = useState("");
  const [scheduleFilter, setScheduleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [historyRows, setHistoryRows] = useState(HISTORY_ROWS[0]);
  const [historyPage, setHistoryPage] = useState(1);

  const reports = useReports(collectionId ?? undefined);
  // The service caps how many questions one report may hold and answers 422
  // over it, so the form checks this collection's own cap before sending. A
  // caller who cannot read the setting simply gets no local check.
  const maxQuestions = useReportSettings(collectionId ?? undefined).data
    ?.max_queries_per_report;
  const runs = useRuns(collectionId ?? undefined, reportId);
  // The answers only exist once a run stops moving, so poll the cheap status
  // route and fetch the body when it settles.
  const runStatus = useRunStatus(collectionId ?? undefined, reportId, runId);
  const runFinished = isEffectivelyDone(
    runStatus.data?.status,
    runStatus.data?.progress,
  );
  const run = useRun(collectionId ?? undefined, reportId, runId, runFinished);
  const mutations = useReportMutations(collectionId ?? undefined);

  const report = reports.data?.find((entry) => entry.report_id === reportId);

  /**
   * The last run of every report on the list, which is what the Status column
   * shows and the Status filter narrows on. The service keeps runs under each
   * report, so this is one read per report — see `useLastRuns`.
   */
  const reportIds = useMemo(
    () => (reports.data ?? []).map((entry) => entry.report_id),
    [reports.data],
  );
  const lastRuns = useLastRuns(collectionId ?? undefined, reportIds);

  /** The status a row wears: its last run's, or "never" when there is none. */
  const rowStatus = (reportId: string): string => {
    const run = lastRuns.byReport.get(reportId);
    if (!run) return "never";
    return effectiveRunStatus(run.status, run.progress);
  };

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (reports.data ?? []).filter((entry) => {
      if (term && !entry.name.toLowerCase().includes(term)) return false;
      if (
        scheduleFilter &&
        (scheduleShortLabel(entry.schedule) ?? "Manual").toLowerCase() !==
          scheduleFilter.toLowerCase()
      ) {
        return false;
      }
      // While the runs are still loading every row's status is unknown, so the
      // filter holds its fire rather than hiding rows it cannot judge yet.
      if (statusFilter && !lastRuns.isLoading) {
        const run = lastRuns.byReport.get(entry.report_id);
        const status = run
          ? effectiveRunStatus(run.status, run.progress)
          : "never";
        if (status !== statusFilter) return false;
      }
      return true;
    });
  }, [
    reports.data,
    search,
    scheduleFilter,
    statusFilter,
    lastRuns.isLoading,
    lastRuns.byReport,
  ]);

  const openForm = (entry?: Report) => {
    setEditingId(entry?.report_id ?? null);
    setDraft(
      entry
        ? {
            name: entry.name,
            instructions: entry.report_instructions ?? "",
            analysis: entry.analysis_prompt ?? "",
            groups: (entry.query_groups ?? []).length
              ? (entry.query_groups as QueryGroup[]).map((group) => ({
                  type: group.type,
                  queries: [...group.queries],
                }))
              : EMPTY_REPORT.groups,
            schedule: entry.schedule ?? null,
          }
        : EMPTY_REPORT,
    );
    setPage("form");
  };

  const startRun = async (target: Report) => {
    try {
      const started = await mutations.run.mutateAsync(target.report_id);
      setReportId(target.report_id);
      setRunId(started.run_id ?? null);
      setHistoryPage(1);
      setPage("detail");
      toast.success(`${target.name} is running.`);
    } catch (error) {
      toast.error(friendlyReportError(error, "The run could not be started."));
    }
  };

  const remove = async (target: Report) => {
    try {
      await mutations.remove.mutateAsync(target.report_id);
      toast.success(`${target.name} was deleted.`);
      setPendingDelete(null);
      if (reportId === target.report_id) {
        setReportId(null);
        setPage("list");
      }
    } catch (error) {
      toast.error(friendlyReportError(error, "It could not be deleted."));
    }
  };

  /** The confirmation, shared by every screen that can delete a report. */
  const deleteDialog = (
    <Modal
      open={Boolean(pendingDelete)}
      onClose={() => setPendingDelete(null)}
      title="Delete this report?"
      width="440px"
      footer={
        <>
          <Btn onClick={() => setPendingDelete(null)}>Cancel</Btn>
          <Btn
            variant="primary"
            disabled={mutations.remove.isPending}
            onClick={() => pendingDelete && remove(pendingDelete)}
          >
            Delete report
          </Btn>
        </>
      }
    >
      <p className="text-[13px] text-ink-2">
        <b className="text-ink">{pendingDelete?.name}</b> and its run history go
        with it. The answers it has already produced are deleted too, and this
        cannot be undone.
      </p>
    </Modal>
  );

  const exportRun = async (format: ExportFormat) => {
    if (!reportId || !runId) return;
    try {
      const blob = await mutations.exportRun.mutateAsync({
        reportId,
        runId,
        format,
      });
      // The export is a file, and the browser is the only thing that can save
      // one — so the blob is handed to it rather than rendered.
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${report?.name ?? "report"}-${runId}.${format}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(
        friendlyReportError(error, "This run could not be exported."),
      );
    }
  };

  if (!collectionId) return null;

  /* ─────────────────────────────────────────────────────────────── the list */
  if (page === "list") {
    const total = reports.data?.length ?? 0;
    const filtered = Boolean(search || scheduleFilter || statusFilter);

    return (
      <>
        <PageHead
          title="Batch reports"
          sub="A set of questions run together against this collection. Each answer comes back exactly as it would in a conversation — same answer, same sources."
          action={
            <Btn
              variant="primary"
              startIcon={<Icon name="plus" className="size-4" />}
              onClick={() => openForm()}
            >
              New report
            </Btn>
          }
        />

        <Card className="mb-3.5">
          <div className="flex flex-wrap items-center gap-2.5 p-3.5">
            <div className="relative min-w-[220px] flex-1">
              <Icon
                name="search"
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-3"
              />
              <Input
                aria-label="Search report name"
                placeholder="Search report name"
                className="pl-8"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <label className="flex items-center gap-2 text-[12.5px] font-semibold text-ink-2">
              Schedule
              <Select
                aria-label="Filter by schedule"
                fit
                className="min-w-[150px]"
                value={scheduleFilter}
                onChange={(event) => setScheduleFilter(event.target.value)}
              >
                <option value="">All schedules</option>
                {SCHEDULE_FILTERS.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex items-center gap-2 text-[12.5px] font-semibold text-ink-2">
              Status
              <Select
                aria-label="Filter by the status of the last run"
                fit
                className="min-w-[140px]"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
              >
                <option value="">All statuses</option>
                {STATUS_FILTERS.map((choice) => (
                  <option key={choice.value} value={choice.value}>
                    {choice.label}
                  </option>
                ))}
              </Select>
            </label>
            <Pill tone="count" className="tnum">
              {visible.length} report{visible.length === 1 ? "" : "s"}
            </Pill>
            <Btn
              className="ml-auto"
              size="sm"
              disabled={!filtered}
              onClick={() => {
                setSearch("");
                setScheduleFilter("");
                setStatusFilter("");
              }}
            >
              Clear
            </Btn>
          </div>
        </Card>

        <Card className="overflow-hidden">
          {reports.isLoading ? (
            <div className="p-4">
              <EmptyNote>Loading this collection&apos;s reports…</EmptyNote>
            </div>
          ) : reports.error ? (
            <div className="p-4">
              <EmptyNote>
                {friendlyReportError(
                  reports.error,
                  "The reports for this collection could not be loaded. Try again in a moment.",
                )}
              </EmptyNote>
            </div>
          ) : !visible.length ? (
            <div className="p-4">
              <EmptyNote>
                {total
                  ? "No reports match this search or filter. Clear it, or search for another name."
                  : "No reports yet. Create one to run a set of questions against this collection."}
              </EmptyNote>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-[13px]">
                <thead className="border-b border-line text-[11px] tracking-[0.06em] text-ink-3 uppercase">
                  <tr>
                    <th className="px-3.5 py-2.5">Report</th>
                    <th className="px-3.5 py-2.5">Question groups</th>
                    <th className="px-3.5 py-2.5">Schedule</th>
                    <th className="px-3.5 py-2.5">Last run</th>
                    <th className="px-3.5 py-2.5">Status</th>
                    <th className="px-3.5 py-2.5 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((entry) => (
                    <tr
                      key={entry.report_id}
                      className="border-b border-line-soft last:border-0"
                    >
                      <td className="px-3.5 py-3">
                        <LinkBtn
                          onClick={() => {
                            setReportId(entry.report_id);
                            setHistoryPage(1);
                            setPage("detail");
                          }}
                        >
                          {entry.name}
                        </LinkBtn>
                      </td>
                      <td className="tnum px-3.5 py-3 text-ink-2">
                        {entry.query_groups?.length ?? 0} group
                        {(entry.query_groups?.length ?? 0) === 1
                          ? ""
                          : "s"} · {countQuestions(entry.query_groups)} question
                        {countQuestions(entry.query_groups) === 1 ? "" : "s"}
                      </td>
                      <td className="px-3.5 py-3">
                        <Chip tone="muted">
                          {scheduleShortLabel(entry.schedule) ?? "Manual"}
                        </Chip>
                      </td>
                      <td className="tnum px-3.5 py-3 text-[11.5px] text-ink-3">
                        {lastRuns.isLoading
                          ? "…"
                          : (() => {
                              const run = lastRuns.byReport.get(
                                entry.report_id,
                              );
                              return run
                                ? absT(run.started_at ?? run.created_at)
                                : "—";
                            })()}
                      </td>
                      <td className="px-3.5 py-3">
                        {lastRuns.isLoading ? (
                          <span className="text-[11.5px] text-ink-3">…</span>
                        ) : rowStatus(entry.report_id) === "never" ? (
                          <Pill tone="neutral">Never run</Pill>
                        ) : (
                          <Pill
                            tone={
                              RUN_TONE[rowStatus(entry.report_id)] ?? "neutral"
                            }
                          >
                            {rowStatus(entry.report_id)}
                          </Pill>
                        )}
                      </td>
                      <td className="px-3.5 py-3">
                        <div className="flex justify-end gap-1.5">
                          <Btn size="sm" onClick={() => startRun(entry)}>
                            Run
                          </Btn>
                          <Btn size="sm" onClick={() => openForm(entry)}>
                            Edit
                          </Btn>
                          {/* Asks first: a report is a definition someone
                              wrote, and the service deletes it outright. */}
                          <Btn
                            size="sm"
                            variant="dangerGhost"
                            onClick={() => setPendingDelete(entry)}
                          >
                            Delete
                          </Btn>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="tnum border-t border-line px-3.5 py-2.5 text-[11.5px] text-ink-3">
                Showing {visible.length} of {total} report
                {total === 1 ? "" : "s"}
              </div>
            </div>
          )}
        </Card>
        {deleteDialog}
      </>
    );
  }

  /* ───────────────────────────────────────────────────────── the definition */
  if (page === "form") {
    return (
      <ReportForm
        draft={draft}
        onDraftChange={setDraft}
        editing={Boolean(editingId)}
        maxQuestions={maxQuestions}
        saving={mutations.create.isPending || mutations.update.isPending}
        onCancel={() => setPage("list")}
        onSave={async (runNow) => {
          const groups = draft.groups
            .map((group) => ({
              type: group.type,
              queries: group.queries.filter((query) => query.trim()),
            }))
            .filter((group) => group.queries.length);

          if (!draft.name.trim() || !groups.length) {
            toast.error("A report needs a name and at least one question.");
            return;
          }
          const asked = countQuestions(groups);
          if (maxQuestions != null && asked > maxQuestions) {
            toast.error(
              `This collection allows ${maxQuestions} questions per report — remove ${asked - maxQuestions}.`,
            );
            return;
          }

          try {
            if (editingId) {
              await mutations.update.mutateAsync({
                reportId: editingId,
                payload: {
                  name: draft.name.trim(),
                  query_groups: groups,
                  report_instructions: draft.instructions || null,
                  analysis_prompt: draft.analysis || null,
                  schedule: draft.schedule,
                },
              });
              toast.success("Report saved.");
              // Editing cannot start a run: the update route has no
              // `run_immediately`, so a saved report is run from its own page.
              setPage("list");
              return;
            }
            await mutations.create.mutateAsync({
              name: draft.name.trim(),
              query_groups: groups,
              report_instructions: draft.instructions || undefined,
              analysis_prompt: draft.analysis || undefined,
              schedule: draft.schedule,
              run_immediately: runNow,
            });
            toast.success(
              runNow ? "Report created and running." : "Report created.",
            );
            setPage("list");
          } catch (error) {
            toast.error(
              friendlyReportError(error, "The report could not be saved."),
            );
          }
        }}
      />
    );
  }

  /* ────────────────────────────────────────────────────────── run history */
  if (page === "detail" && report) {
    const history = runs.data ?? [];
    const pages = Math.max(1, Math.ceil(history.length / historyRows));
    const current = Math.min(historyPage, pages);
    const slice = history.slice(
      (current - 1) * historyRows,
      current * historyRows,
    );

    return (
      <>
        <LinkBtn className="mb-3.5" onClick={() => setPage("list")}>
          <Icon name="caretLeft" className="size-4" /> Batch reports
        </LinkBtn>
        <PageHead
          title={report.name}
          sub={`${countQuestions(report.query_groups)} question${countQuestions(report.query_groups) === 1 ? "" : "s"} across ${report.query_groups?.length ?? 0} group${(report.query_groups?.length ?? 0) === 1 ? "" : "s"}`}
          action={
            <div className="flex flex-wrap gap-2">
              <Btn
                startIcon={<Icon name="ref" className="size-4" />}
                onClick={() => runs.refetch()}
              >
                Check status
              </Btn>
              <Btn onClick={() => openForm(report)}>Edit</Btn>
              <Btn
                variant="primary"
                startIcon={<Icon name="play" className="size-4" />}
                disabled={mutations.run.isPending}
                onClick={() => startRun(report)}
              >
                Run now
              </Btn>
            </div>
          }
        />

        <Card className="mb-3.5">
          <dl className="grid gap-3.5 p-4 sm:grid-cols-2">
            <div>
              <SectionLabel>Schedule</SectionLabel>
              <dd className="mt-1 text-[13px] text-ink">
                {describeSchedule(report.schedule)}
              </dd>
            </div>
            <div>
              <SectionLabel>Next run</SectionLabel>
              <dd className="mt-1 text-[13px] text-ink">
                {report.next_run_at
                  ? absT(report.next_run_at)
                  : report.schedule
                    ? "Not reported yet"
                    : "Only when you run it"}
              </dd>
            </div>
            <div>
              <SectionLabel>Report instructions</SectionLabel>
              <dd className="mt-1 text-[13px] text-ink">
                {report.report_instructions || (
                  <span className="text-ink-3">Not set</span>
                )}
              </dd>
            </div>
            <div>
              <SectionLabel>Analysis prompt</SectionLabel>
              <dd className="mt-1 text-[13px] text-ink">
                {report.analysis_prompt || (
                  <span className="text-ink-3">Not set</span>
                )}
              </dd>
            </div>
          </dl>
        </Card>

        <Card className="overflow-hidden">
          {runs.isLoading ? (
            <div className="p-4">
              <EmptyNote>Loading this report&apos;s runs…</EmptyNote>
            </div>
          ) : !history.length ? (
            <div className="p-4">
              <EmptyNote>
                This report has not run yet. &ldquo;Run now&rdquo; answers every
                question in it and keeps the results here.
              </EmptyNote>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-left text-[13px]">
                <thead className="border-b border-line text-[11px] tracking-[0.06em] text-ink-3 uppercase">
                  <tr>
                    <th className="px-3.5 py-2.5">Run</th>
                    <th className="px-3.5 py-2.5">Status</th>
                    <th className="px-3.5 py-2.5 text-right">Questions</th>
                    <th className="px-3.5 py-2.5 text-right">Took</th>
                    <th className="px-3.5 py-2.5 text-right">Answers</th>
                  </tr>
                </thead>
                <tbody>
                  {slice.map((entry) => {
                    // A worker can leave `status` at "pending" once every
                    // answer has landed, so the progress counters decide.
                    const status = effectiveRunStatus(
                      entry.status,
                      entry.progress,
                    );
                    const live = isLiveRun(status);
                    return (
                      <tr
                        key={entry.run_id}
                        className="border-b border-line-soft last:border-0"
                      >
                        {/* created_at, not started_at: the service leaves
                            started_at null until a worker picks the run up. */}
                        <td className="tnum px-3.5 py-3 text-[12px] text-ink-2">
                          {absT(entry.started_at ?? entry.created_at)}
                        </td>
                        <td className="px-3.5 py-3">
                          <Pill tone={RUN_TONE[status] ?? "neutral"}>
                            {status}
                          </Pill>
                        </td>
                        <td className="tnum px-3.5 py-3 text-right text-ink-2">
                          {nfmt(entry.progress?.total_queries ?? 0)}
                          {entry.progress?.failed_queries
                            ? ` · ${entry.progress.failed_queries} failed`
                            : ""}
                        </td>
                        <td className="tnum px-3.5 py-3 text-right text-ink-2">
                          {live ? "—" : ms(entry.total_time_ms)}
                        </td>
                        <td className="px-3.5 py-3 text-right">
                          {live ? (
                            <span className="text-[12px] text-ink-3">
                              In progress
                            </span>
                          ) : (
                            <LinkBtn
                              onClick={() => {
                                setRunId(entry.run_id);
                                setPage("run");
                              }}
                            >
                              View answers →
                            </LinkBtn>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {/* The route returns every run in one response, so the paging
                  here is over rows already loaded. */}
              <div className="flex items-center gap-2 border-t border-line px-3.5 py-2.5 text-[11.5px] text-ink-3">
                <span>Rows</span>
                <Select
                  aria-label="Runs per page"
                  fit
                  className="py-1"
                  value={String(historyRows)}
                  onChange={(event) => {
                    setHistoryRows(Number(event.target.value));
                    setHistoryPage(1);
                  }}
                >
                  {HISTORY_ROWS.map((rows) => (
                    <option key={rows} value={rows}>
                      {rows}
                    </option>
                  ))}
                </Select>
                <span className="ml-auto tnum">
                  Page {current} of {pages}
                </span>
                <IconBtn
                  label="Previous page"
                  disabled={current <= 1}
                  onClick={() => setHistoryPage(current - 1)}
                >
                  <Icon name="caretLeft" className="size-4" />
                </IconBtn>
                <IconBtn
                  label="Next page"
                  disabled={current >= pages}
                  onClick={() => setHistoryPage(current + 1)}
                >
                  <Icon name="caretRight" className="size-4" />
                </IconBtn>
              </div>
            </div>
          )}
        </Card>
      </>
    );
  }

  /* ──────────────────────────────────────────────────────────── one run */
  if (page === "run" && report) {
    // The run route nests every answer under query_groups[].results[] — there
    // is no flat list of answers on the response.
    const groups = run.data?.query_groups ?? [];
    const summary = run.data?.summary;
    const progress = runStatus.data?.progress;
    const status = run.data
      ? effectiveRunStatus(run.data.status, progress)
      : (runStatus.data?.status ?? "pending");

    const stats: Array<[string, string]> = summary
      ? [
          ["Questions", nfmt(summary.total_queries)],
          ["Answered", nfmt(summary.completed_queries)],
          ["Failed", nfmt(summary.failed_queries)],
          ["Average answer", ms(summary.avg_response_time_ms)],
          ["Tokens", nfmt(summary.total_tokens)],
          ["Total time", ms(summary.total_time_ms)],
        ]
      : [];

    return (
      <>
        <LinkBtn className="mb-3.5" onClick={() => setPage("detail")}>
          <Icon name="caretLeft" className="size-4" /> Run history
        </LinkBtn>
        <PageHead
          title="Run results"
          sub={`${report.name} · ${absT(run.data?.started_at ?? run.data?.executed_at)}`}
          action={
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone={RUN_TONE[status] ?? "neutral"}>{status}</Pill>
              {isLiveRun(status) && runId ? (
                <Btn
                  onClick={async () => {
                    try {
                      await mutations.cancel.mutateAsync({
                        reportId: report.report_id,
                        runId,
                      });
                      toast.success("The run was cancelled.");
                    } catch (error) {
                      toast.error(
                        friendlyReportError(
                          error,
                          "The run could not be cancelled.",
                        ),
                      );
                    }
                  }}
                >
                  Cancel run
                </Btn>
              ) : (
                (["csv", "md", "json"] as const).map((format) => (
                  <Btn
                    key={format}
                    size="sm"
                    startIcon={<Icon name="dn" className="size-4" />}
                    disabled={mutations.exportRun.isPending}
                    onClick={() => exportRun(format)}
                  >
                    {format.toUpperCase()}
                  </Btn>
                ))
              )}
            </div>
          }
        />

        {!runFinished ? (
          <Card>
            <div className="p-4">
              <EmptyNote>
                This run is still going.{" "}
                {progress
                  ? `${progress.completed_queries + progress.failed_queries} of ${progress.total_queries} questions answered — the results appear as soon as it finishes.`
                  : "The results appear as soon as it finishes."}
              </EmptyNote>
            </div>
          </Card>
        ) : run.isLoading ? (
          <Card>
            <div className="p-4">
              <EmptyNote>Loading this run&apos;s answers…</EmptyNote>
            </div>
          </Card>
        ) : run.error ? (
          <Card>
            <div className="p-4">
              <EmptyNote>
                {friendlyReportError(
                  run.error,
                  "This run could not be loaded. It may still be starting.",
                )}
              </EmptyNote>
            </div>
          </Card>
        ) : (
          <>
            {stats.length > 0 && (
              <Card className="mb-3.5">
                <div className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-3 lg:grid-cols-6">
                  {stats.map(([label, value]) => (
                    <div key={label}>
                      <SectionLabel>{label}</SectionLabel>
                      <p className="tnum mt-1 text-[17px] font-extrabold text-ink">
                        {value}
                      </p>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {run.data?.failure_reason && (
              <Card className="mb-3.5">
                <p className="p-4 text-[13px] text-danger-text">
                  {run.data.failure_reason}
                </p>
              </Card>
            )}

            {run.data?.overall_summary && (
              <Card className="mb-3.5">
                <div className="border-b border-line px-4 py-3">
                  <span className="text-[13.5px] font-bold text-ink">
                    Overall analysis
                  </span>
                </div>
                <div className="di-md p-4 text-ink">
                  <Markdown>{run.data.overall_summary}</Markdown>
                </div>
              </Card>
            )}

            {groups.length ? (
              groups.map((group, index) => (
                <GroupCard
                  key={group.group_id ?? index}
                  group={group}
                  index={index}
                />
              ))
            ) : (
              <Card>
                <div className="p-4">
                  <EmptyNote>
                    This run finished without recording any answers.
                  </EmptyNote>
                </div>
              </Card>
            )}
          </>
        )}
      </>
    );
  }

  // `detail` and `run` both need a report; if it was deleted underneath, the
  // list is the only honest place to be.
  return (
    <>
      <PageHead title="Batch reports" />
      <Card>
        <div className="p-4">
          <EmptyNote>
            That report is no longer here.{" "}
            <LinkBtn onClick={() => setPage("list")}>Back to the list</LinkBtn>
          </EmptyNote>
        </div>
      </Card>
    </>
  );
}
