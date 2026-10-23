"use client";

import type React from "react";

import dayjs from "dayjs";
import { Network } from "lucide-react";

import { cn } from "@/platform/utils";

import { normalizeFeedback } from "../lib/normalizeFeedback";
import type { NodeDatum, QueryRow, ServiceCall } from "../types";

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-gray-200 bg-white p-2.5">
      <div className="text-lg font-bold text-slate-900">{value}</div>
      <div className="text-[11px] text-slate-500">{label}</div>
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div
        className={cn(
          "text-sm text-slate-700 break-all",
          mono && "font-mono text-xs",
        )}
      >
        {value}
      </div>
    </div>
  );
}

const feedbackLabel = (fb: QueryRow["feedback"]) => {
  const rating = normalizeFeedback(fb);
  if (rating === "positive") return "👍 Positive";
  if (rating === "negative") return "👎 Negative";
  return "—";
};

const fmtNodeMs = (ms?: number) => {
  if (ms == null) return "—";
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
};

type NodeRow = {
  name: string;
  model: string | null;
  calls?: number;
  latency_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  total_cost?: number;
};

// Merge the LLM cost/token breakdown (node_data, from LangSmith) with the
// our-end per-call latency (service_calls), keyed by node name. A node can
// appear in either or both — sub-services carry cost but no model, and some
// calls carry only latency. Columns: node / model / calls / latency / input /
// output / total tokens / cost.
function mergeNodes(
  nodeData?: NodeDatum[] | null,
  serviceCalls?: ServiceCall[] | null,
): NodeRow[] {
  const byName = new Map<string, NodeRow>();

  // How many service_calls each node produced — the call-count fallback for
  // nodes that node_data doesn't report a `calls` figure for.
  const callCounts = new Map<string, number>();
  for (const c of serviceCalls ?? []) {
    const key = c.node_name ?? c.service;
    if (key) callCounts.set(key, (callCounts.get(key) ?? 0) + 1);
  }

  for (const n of nodeData ?? []) {
    byName.set(n.node_name, {
      name: n.node_name,
      model: n.model,
      calls: n.calls,
      // Prefer explicit per-node latency; fall back to total run latency.
      latency_ms: n.latency_ms ?? n.total_latency_ms,
      input_tokens: n.input_tokens,
      output_tokens: n.output_tokens,
      total_tokens: n.total_tokens,
      total_cost: n.total_cost,
    });
  }

  for (const c of serviceCalls ?? []) {
    const key = c.node_name ?? c.service;
    if (!key) continue;
    const existing = byName.get(key) ?? { name: key, model: null };
    // service_calls is our-end timing — authoritative, so it wins when present.
    if (c.latency_ms != null) existing.latency_ms = c.latency_ms;
    if (existing.input_tokens == null) existing.input_tokens = c.input_tokens;
    if (existing.output_tokens == null)
      existing.output_tokens = c.output_tokens;
    if (existing.total_tokens == null) existing.total_tokens = c.total_tokens;
    byName.set(key, existing);
  }

  // Fill call counts node_data didn't provide: prefer the our-end service_calls
  // count, else 1 (a node in the breakdown ran at least once).
  for (const [key, row] of byName) {
    if (row.calls == null) row.calls = callCounts.get(key) ?? 1;
  }

  return [...byName.values()];
}

function NodeBreakdown({ rows }: { rows: NodeRow[] }) {
  return (
    <div className="mt-3 border-t border-gray-100 pt-3">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        <Network className="size-3.5 text-slate-400" />
        Per-Node Breakdown
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] table-fixed text-xs">
          <thead>
            <tr className="border-b border-slate-100 text-[10px] uppercase tracking-wide text-slate-400">
              <th className="w-[15%] py-1.5 pr-3 text-left font-medium">
                Node
              </th>
              <th className="w-[17%] py-1.5 pr-3 text-left font-medium">
                Model
              </th>
              <th className="w-[8%] py-1.5 pr-3 text-left font-medium">
                Calls
              </th>
              <th className="w-[12%] py-1.5 pr-3 text-left font-medium">
                Latency
              </th>
              <th className="w-[13%] py-1.5 pr-3 text-left font-medium">
                Input Tokens
              </th>
              <th className="w-[13%] py-1.5 pr-3 text-left font-medium">
                Output Tokens
              </th>
              <th className="w-[13%] py-1.5 pr-3 text-left font-medium">
                Total Tokens
              </th>
              <th className="w-[9%] py-1.5 text-left font-medium">Cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((n) => (
              <tr
                key={n.name}
                className="border-b border-slate-50 last:border-0"
              >
                <td className="py-1.5 pr-3 font-medium text-slate-700">
                  {n.name}
                </td>
                <td className="py-1.5 pr-3 text-slate-500">{n.model ?? "—"}</td>
                <td className="py-1.5 pr-3 tabular-nums text-slate-600">
                  {n.calls == null ? "—" : n.calls.toLocaleString()}
                </td>
                <td className="py-1.5 pr-3 tabular-nums text-slate-600">
                  {fmtNodeMs(n.latency_ms)}
                </td>
                <td className="py-1.5 pr-3 tabular-nums text-slate-600">
                  {n.input_tokens == null
                    ? "—"
                    : n.input_tokens.toLocaleString()}
                </td>
                <td className="py-1.5 pr-3 tabular-nums text-slate-600">
                  {n.output_tokens == null
                    ? "—"
                    : n.output_tokens.toLocaleString()}
                </td>
                <td className="py-1.5 pr-3 tabular-nums text-slate-600">
                  {n.total_tokens == null
                    ? "—"
                    : n.total_tokens.toLocaleString()}
                </td>
                <td className="py-1.5 tabular-nums text-slate-600">
                  {n.total_cost == null ? "—" : `$${n.total_cost.toFixed(5)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function QueryRowDetail({
  row,
  footer,
  showNodeTrace = true,
}: {
  row: QueryRow;
  // Per-table extras (e.g. "Per-node trace coming soon").
  footer?: React.ReactNode;
  // Tables with a Node Trace toggle pass its state; default keeps the
  // breakdown visible for tables without the toggle.
  showNodeTrace?: boolean;
}) {
  const m = row.metrics;
  const nodeRows = mergeNodes(m.node_data, m.service_calls);

  return (
    <div className="rounded-md border border-gray-200 bg-white p-3.5">
      <div className="mb-2.5 text-xs text-slate-600">
        <span className="font-semibold text-slate-700">Query: </span>
        {row.query}
      </div>
      <div className="mb-2.5 text-xs text-slate-600">
        <span className="font-semibold text-slate-700">Refined: </span>
        {row.refined_query || "—"}
      </div>
      {row.services_used?.length > 0 && (
        <div className="mb-2.5 flex flex-wrap gap-1.5">
          {row.services_used.map((s) => (
            <span
              key={s}
              className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700"
            >
              {s}
            </span>
          ))}
        </div>
      )}

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
        <Cell label="Latency" value={`${(m.latency_ms / 1000).toFixed(1)}s`} />
        <Cell
          label={m.cost_complete ? "Cost" : "Cost (lower bound)"}
          value={`${m.cost_complete ? "" : "~"}$${m.cost_usd.toFixed(3)}`}
        />
        <Cell label="Input Tokens" value={m.input_tokens.toLocaleString()} />
        <Cell label="Output Tokens" value={m.output_tokens.toLocaleString()} />
        <Cell label="Total Tokens" value={m.total_tokens.toLocaleString()} />
        <Cell label="Retries" value={String(m.retry_count)} />
      </div>

      {/* Per-node breakdown — model + latency + tokens + cost, joined from
          node_data (LangSmith) and service_calls (our-end latency) */}
      {showNodeTrace && nodeRows.length > 0 && (
        <NodeBreakdown rows={nodeRows} />
      )}

      {/* All metadata */}
      <div className="mt-3 grid grid-cols-2 gap-x-8 gap-y-2.5 border-t border-gray-100 pt-3 sm:grid-cols-3 lg:grid-cols-4">
        <Field label="Chat ID" value={row.chat_id || "—"} mono />
        <Field label="Session ID" value={row.session_id ?? "—"} mono />
        <Field label="Trace ID" value={row.trace_id ?? "—"} mono />
        <Field label="User ID" value={row.user_id ?? "—"} mono />
        <Field label="Status" value={row.status || "—"} />
        <Field label="Outcome" value={row.outcome || "—"} />
        <Field label="Answered" value={row.is_answered ? "Yes" : "No"} />
        <Field label="Confidence" value={row.confidence ?? "—"} />
        <Field label="Route" value={row.route ?? "—"} />
        <Field label="Provider" value={row.provider ?? "—"} />
        <Field label="Model" value={row.model ?? "—"} />
        <Field label="Stop Reason" value={row.stop_reason ?? "—"} />
        <Field label="From Widget" value={row.from_widget ? "Yes" : "No"} />
        <Field label="Key Type" value={row.key_type ?? "—"} />
        <Field label="Feedback" value={feedbackLabel(row.feedback)} />
        <Field
          label="Date & Time"
          value={dayjs(row.timestamp).format("MMM D, YYYY h:mm A")}
        />
      </div>

      {!m.cost_complete && (
        <div className="mt-3 flex items-center gap-2 text-xs text-amber-600">
          <Network className="size-3.5" />
          Cost is a lower bound — refresh costs to sync the LangSmith figure.
        </div>
      )}

      {footer}
    </div>
  );
}
