"use client";

import { useState } from "react";

import { BarChart3 } from "lucide-react";

import { ChatChart } from "@/platform/chat-core/charts";
import type { ChartData } from "@/platform/chat-core/types";
import Button from "@/platform/components/ui/Button";
import { Modal } from "@/platform/components/ui/Modal";

import type { Nl2sqlChartView } from "./normalizeNl2sql";

interface ChartMetaPreviewProps {
  chart?: Nl2sqlChartView;
  previewRows?: Record<string, unknown>[];
}

type ChartMetadata = NonNullable<ChartData["metadata"]>;

// chart.data arrives in two valid shapes (see ChartData["data"]):
//   • row array:   [{ x, y }, ...]
//   • columnar:    { x: [...], y: [...] }
// Scatter/line charts use the columnar form, so an `Array.isArray` check alone
// silently drops them. Treat data as renderable when either shape has values.
const hasChartData = (data: unknown): boolean => {
  if (Array.isArray(data)) return data.length > 0;
  if (data && typeof data === "object") {
    const x = (data as Record<string, unknown>).x;
    return Array.isArray(x) && x.length > 0;
  }
  return false;
};

// A flat key/value row or an axis group that keeps the display label and the
// underlying field name as distinct properties.
type MetaRow =
  | { kind: "kv"; label: string; value: string }
  | { kind: "axis"; label: string; axisLabel?: string; field?: string };

const toAxisRow = (
  label: string,
  axis?: { label?: string; field?: string },
): MetaRow | null => {
  if (!axis) return null;
  const axisLabel = axis.label || undefined;
  const field = axis.field || undefined;
  if (!axisLabel && !field) return null;
  return { kind: "axis", label, axisLabel, field };
};

const kvRow = (label: string, value?: string | null): MetaRow | null =>
  value ? { kind: "kv", label, value } : null;

// The backend returns complete chart metadata (title, axes, etc.) for every
// chart type. Surface it so users can understand the generated chart without
// opening the viewer. Axis label and field are shown as separate properties so
// it's clear which is the display label and which is the underlying column.
// Legend and colors are intentionally omitted — we don't render a legend, so
// that metadata only adds noise here (per product review).
const buildMetaRows = (metadata: ChartMetadata): MetaRow[] => {
  const rows: Array<MetaRow | null> = [
    kvRow("Title", metadata.title),
    toAxisRow("X-axis", metadata.x_axis),
    toAxisRow("Y-axis", metadata.y_axis),
    kvRow("Orientation", metadata.chart_orientation),
    kvRow("Group by", metadata.group?.field),
    kvRow(
      "Series",
      metadata.series_labels?.length ? metadata.series_labels.join(", ") : null,
    ),
    kvRow(
      "Trend line",
      metadata.trend_line?.show === true
        ? metadata.trend_line.color
          ? `Shown (${metadata.trend_line.color})`
          : "Shown"
        : null,
    ),
  ];
  return rows.filter((r): r is MetaRow => !!r);
};

const isMetadata = (value: unknown): value is ChartMetadata =>
  !!value && typeof value === "object";

export const ChartMetaPreview = ({
  chart,
  previewRows,
}: ChartMetaPreviewProps) => {
  const [open, setOpen] = useState(false);
  if (!chart || (!chart.suggestion && !chart.data)) return null;

  // chart.data is renderable in either row-array or columnar shape. When the
  // backend omits it entirely (e.g. some scatter suggestions) fall back to the
  // SQL result rows so the viewer isn't empty.
  const safeData = hasChartData(chart.data)
    ? chart.data
    : Array.isArray(previewRows)
      ? previewRows
      : [];
  const showData = hasChartData(safeData);

  const metaRows = isMetadata(chart.metadata)
    ? buildMetaRows(chart.metadata)
    : [];

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-slate-700">
        <BarChart3 className="w-3.5 h-3.5 text-indigo-600" />
        <span className="font-medium">Suggested chart:</span>
        <span>{chart.suggestion ?? "—"}</span>
      </div>
      {metaRows.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          {metaRows.map((row) =>
            row.kind === "axis" ? (
              <div key={row.label} className="contents">
                <dt className="font-medium text-slate-500">{row.label}</dt>
                <dd className="text-slate-700 break-words">
                  {row.axisLabel && (
                    <div>
                      <span className="text-slate-500">Label:</span>{" "}
                      {row.axisLabel}
                    </div>
                  )}
                  {row.field && (
                    <div>
                      <span className="text-slate-500">Field:</span> {row.field}
                    </div>
                  )}
                </dd>
              </div>
            ) : (
              <div key={row.label} className="contents">
                <dt className="font-medium text-slate-500">{row.label}</dt>
                <dd className="text-slate-700 break-words">{row.value}</dd>
              </div>
            ),
          )}
        </dl>
      )}
      {showData && (
        <pre className="text-xs bg-slate-50 border border-slate-100 rounded p-2 overflow-x-auto">
          {JSON.stringify(safeData, null, 2)}
        </pre>
      )}
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Open in chart viewer
      </Button>
      <Modal
        open={open}
        title="Chart Viewer"
        showFooter={false}
        onCancel={() => setOpen(false)}
        maxWidth="3xl"
      >
        {open && (
          <ChatChart
            chartData={
              { data: safeData, metadata: chart.metadata } as Parameters<
                typeof ChatChart
              >[0]["chartData"]
            }
            chartSuggestion={chart.suggestion}
          />
        )}
      </Modal>
    </div>
  );
};

export default ChartMetaPreview;
