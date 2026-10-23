"use client";

import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { isEffectivelyDone } from "@/platform/components/batch-reports/runStatus";
import type {
  CreateReportRequest,
  CreateReportResponse,
  ExportFormat,
  MessageResponse,
  QueryGroupInput,
  ReportSummary,
  RunListItem,
  RunResult,
  RunStatus,
  RunStatusResponse,
  ScheduleInput,
  UpdateReportRequest,
} from "@/platform/types/batch-reports";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * Batch reports, from the knowledge service's own routes:
 *
 * - `GET|POST      /v1/{knowledge_id}/reports`
 * - `GET|PUT       …/reports/settings`
 * - `GET|PUT|DELETE …/reports/{report_id}`
 * - `GET|POST      …/reports/{report_id}/runs`
 * - `GET           …/runs/{run_id}` · `…/status` · `…/export?format=`
 * - `POST          …/runs/{run_id}/cancel`
 *
 * Collection-scoped by path, like everything else in this product: a report
 * belongs to one collection and there is no `agent_id` anywhere on it.
 *
 * The wire shapes live in `@/platform/types/batch-reports` and are not restated
 * here. That file describes the same reports feature on the dashboard service,
 * and copying a shape by hand is exactly how the dashboard's run page came to
 * read `results` and `overall_analysis`, neither of which the API has ever
 * sent. Only the fields this service genuinely differs on are re-declared
 * below.
 */

export type GroupType = QueryGroupInput["type"];
export type QueryGroup = QueryGroupInput;
export type ReportSchedule = ScheduleInput;

/**
 * One report as this service answers it.
 *
 * `agent_id` is absent (a collection is not an agent) and `query_groups` can
 * come back null on a report the worker has not finished writing, so both are
 * loosened rather than trusted.
 */
export type Report = Omit<
  ReportSummary,
  "agent_id" | "user_id" | "query_groups" | "updated_at"
> & {
  knowledge_id?: string | null;
  /** The creator, as this service names it — `owner_id`, not `user_id`. */
  owner_id?: string | null;
  user_id?: string | null;
  query_groups?: QueryGroupInput[] | null;
  /** Present when the report has a schedule: when it next fires. */
  next_run_at?: string | null;
  /** Null on a report that has never been edited (verified live). */
  updated_at?: string | null;
};

export type ReportRun = RunListItem;
export type { RunResult, RunStatusResponse };

/** Run-created / run-cancelled: a run id, and a status that may be absent. */
type RunActionResponse = {
  run_id: string;
  status?: RunStatus | null;
};

/** The PUT echoes the settings back, so there is no separate message shape. */
export type ReportSettings = {
  max_queries_per_report: number;
  default_max_queries_per_report: number;
};

/** How often a live run is re-read — the cadence the dashboard's page uses. */
const RUN_POLL_INTERVAL_MS = 3000;

const reportsUrl = (knowledgeId: string) =>
  buildApiUrl(APIS.REPORTS, { knowledge_id: knowledgeId });

const reportUrl = (knowledgeId: string, reportId: string) =>
  buildApiUrl(APIS.REPORT, {
    knowledge_id: knowledgeId,
    report_id: reportId,
  });

const runsUrl = (knowledgeId: string, reportId: string) =>
  buildApiUrl(APIS.REPORT_RUNS, {
    knowledge_id: knowledgeId,
    report_id: reportId,
  });

/** The four run routes all take the same three path parameters. */
type RunTemplate =
  | typeof APIS.REPORT_RUN
  | typeof APIS.REPORT_RUN_STATUS
  | typeof APIS.REPORT_RUN_EXPORT
  | typeof APIS.REPORT_RUN_CANCEL;

const runUrl = (
  template: RunTemplate,
  knowledgeId: string,
  reportId: string,
  runId: string,
) =>
  buildApiUrl(template, {
    knowledge_id: knowledgeId,
    report_id: reportId,
    run_id: runId,
  });

export function useReports(collectionId: string | undefined) {
  return useQuery({
    queryKey: ["reports", collectionId],
    enabled: Boolean(collectionId),
    queryFn: async (): Promise<Report[]> => {
      const response = await axios.get<{ reports?: Report[] }>(
        reportsUrl(collectionId as string),
      );
      return response.data.reports ?? [];
    },
  });
}

/**
 * The cap on how many questions one report may hold, plus the system default so
 * nothing here hardcodes it. The service answers 422 over the cap, so the form
 * checks it before sending; a caller without access to the setting just gets an
 * error and the cap stays unknown, which the form treats as "no local check".
 */
export function useReportSettings(collectionId: string | undefined) {
  return useQuery({
    queryKey: ["report-settings", collectionId],
    enabled: Boolean(collectionId),
    staleTime: 5 * 60 * 1000,
    queryFn: async (): Promise<ReportSettings> => {
      const response = await axios.get<ReportSettings>(
        buildApiUrl(APIS.REPORT_SETTINGS, {
          knowledge_id: collectionId as string,
        }),
      );
      return response.data;
    },
  });
}

export function useRuns(
  collectionId: string | undefined,
  reportId: string | null,
) {
  return useQuery({
    queryKey: ["report-runs", collectionId, reportId],
    enabled: Boolean(collectionId && reportId),
    queryFn: async (): Promise<ReportRun[]> => {
      const response = await axios.get<{ runs?: ReportRun[] }>(
        runsUrl(collectionId as string, reportId as string),
      );
      return response.data.runs ?? [];
    },
    // Keep the history fresh while any run in it is still going.
    refetchInterval: (query) =>
      (query.state.data ?? []).some(
        (run) => !isEffectivelyDone(run.status, run.progress),
      )
        ? RUN_POLL_INTERVAL_MS
        : false,
    refetchIntervalInBackground: true,
  });
}

/**
 * Each report's most recent run, for the list's "Last run" and "Status"
 * columns — and for the Status filter above them, which cannot exist without
 * this.
 *
 * One request per report, because that is what the service offers: runs live
 * under `…/reports/{report_id}/runs` and the list route carries nothing about
 * them. They share the same query keys `useRuns` uses, so opening a report
 * afterwards reads the cache rather than the network, and a report the list has
 * already loaded costs nothing again.
 *
 * The rows are ordered newest-first here rather than trusted to arrive that
 * way: "last run" being actually the last one is the whole point of the column.
 */
export function useLastRuns(
  collectionId: string | undefined,
  reportIds: string[],
) {
  const queries = useQueries({
    queries: reportIds.map((reportId) => ({
      queryKey: ["report-runs", collectionId, reportId],
      enabled: Boolean(collectionId),
      queryFn: async (): Promise<ReportRun[]> => {
        const response = await axios.get<{ runs?: ReportRun[] }>(
          runsUrl(collectionId as string, reportId),
        );
        return response.data.runs ?? [];
      },
      refetchInterval: (query: { state: { data?: ReportRun[] } }) =>
        (query.state.data ?? []).some(
          (run) => !isEffectivelyDone(run.status, run.progress),
        )
          ? RUN_POLL_INTERVAL_MS
          : false,
    })),
  });

  const byReport = new Map<string, ReportRun | undefined>();
  reportIds.forEach((reportId, index) => {
    const runs = [...(queries[index]?.data ?? [])].sort((a, b) =>
      (b.started_at ?? b.created_at ?? "").localeCompare(
        a.started_at ?? a.created_at ?? "",
      ),
    );
    byReport.set(reportId, runs[0]);
  });

  return {
    byReport,
    // "Loading", not "no runs": until every read has settled, a report with no
    // entry here is unknown rather than never-run, and the Status filter must
    // not hide rows on the strength of a value it does not have yet.
    isLoading: queries.some((query) => query.isLoading),
  };
}

/**
 * Poll one run's status. Stops once the run is terminal — or effectively done,
 * since a worker can leave `status` at "pending" after the last answer lands.
 */
export function useRunStatus(
  collectionId: string | undefined,
  reportId: string | null,
  runId: string | null,
) {
  return useQuery({
    queryKey: ["report-run-status", collectionId, reportId, runId],
    enabled: Boolean(collectionId && reportId && runId),
    queryFn: async (): Promise<RunStatusResponse> => {
      const response = await axios.get<RunStatusResponse>(
        runUrl(
          APIS.REPORT_RUN_STATUS,
          collectionId as string,
          reportId as string,
          runId as string,
        ),
      );
      return response.data;
    },
    refetchInterval: (query) =>
      isEffectivelyDone(query.state.data?.status, query.state.data?.progress)
        ? false
        : RUN_POLL_INTERVAL_MS,
    refetchIntervalInBackground: true,
  });
}

/**
 * The full result of a run. `enabled` gates it on the run being finished — a
 * run still in flight has no answers to show.
 */
export function useRun(
  collectionId: string | undefined,
  reportId: string | null,
  runId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: ["report-run", collectionId, reportId, runId],
    enabled: enabled && Boolean(collectionId && reportId && runId),
    queryFn: async (): Promise<RunResult> => {
      const response = await axios.get<RunResult>(
        runUrl(
          APIS.REPORT_RUN,
          collectionId as string,
          reportId as string,
          runId as string,
        ),
      );
      return response.data;
    },
  });
}

export function useReportMutations(collectionId: string | undefined) {
  const client = useQueryClient();
  const invalidate = () => {
    client.invalidateQueries({ queryKey: ["reports", collectionId] });
    client.invalidateQueries({ queryKey: ["report-runs", collectionId] });
  };

  const create = useMutation({
    mutationFn: async (
      payload: CreateReportRequest,
    ): Promise<CreateReportResponse> => {
      const response = await axios.post<CreateReportResponse>(
        reportsUrl(collectionId as string),
        payload,
      );
      return response.data;
    },
    onSuccess: invalidate,
  });

  const update = useMutation({
    mutationFn: async ({
      reportId,
      payload,
    }: {
      reportId: string;
      payload: UpdateReportRequest;
    }): Promise<MessageResponse> => {
      const response = await axios.put<MessageResponse>(
        reportUrl(collectionId as string, reportId),
        payload,
      );
      return response.data;
    },
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: async (reportId: string): Promise<MessageResponse> => {
      const response = await axios.delete<MessageResponse>(
        reportUrl(collectionId as string, reportId),
      );
      return response.data;
    },
    onSuccess: invalidate,
  });

  const run = useMutation({
    mutationFn: async (reportId: string): Promise<RunActionResponse> => {
      // 202 — a run always starts "pending"; poll `useRunStatus` from there.
      const response = await axios.post<RunActionResponse>(
        runsUrl(collectionId as string, reportId),
        undefined,
      );
      return response.data;
    },
    onSuccess: invalidate,
  });

  const cancel = useMutation({
    mutationFn: async ({
      reportId,
      runId,
    }: {
      reportId: string;
      runId: string;
    }): Promise<RunActionResponse> => {
      const response = await axios.post<RunActionResponse>(
        runUrl(APIS.REPORT_RUN_CANCEL, collectionId as string, reportId, runId),
        undefined,
      );
      return response.data;
    },
    onSuccess: invalidate,
  });

  /** One run's answers as a downloadable file — json, md or csv. */
  const exportRun = useMutation({
    mutationFn: async ({
      reportId,
      runId,
      format = "csv",
    }: {
      reportId: string;
      runId: string;
      format?: ExportFormat;
    }): Promise<Blob> => {
      const response = await axios.get<Blob>(
        runUrl(APIS.REPORT_RUN_EXPORT, collectionId as string, reportId, runId),
        { params: { format }, responseType: "blob" },
      );
      return response.data;
    },
  });

  /** PUT echoes the settings back, so the cache is set from the response. */
  const setMaxQueries = useMutation({
    mutationFn: async (
      maxQueriesPerReport: number,
    ): Promise<ReportSettings> => {
      const response = await axios.put<ReportSettings>(
        buildApiUrl(APIS.REPORT_SETTINGS, {
          knowledge_id: collectionId as string,
        }),
        { max_queries_per_report: maxQueriesPerReport },
      );
      return response.data;
    },
    onSuccess: (settings) =>
      client.setQueryData(["report-settings", collectionId], settings),
  });

  return { create, update, remove, run, cancel, exportRun, setMaxQueries };
}
