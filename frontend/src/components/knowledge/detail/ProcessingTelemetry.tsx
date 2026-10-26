"use client";

import { useState } from "react";

import { formatCost, formatMs } from "@/components/conversations/runLog";
import Icon from "@/components/Icon";

import type { ProcessingTelemetry as Telemetry } from "./readProcessing";

/**
 * How this document was processed, folded into one line under the header.
 *
 * Closed, it says the total time, the model that extracted the fields (flagged
 * when a fallback did it) and the estimated cost. Open, it lists each step —
 * reading the file, extracting, embedding — with its own time, tokens and cost.
 * A model with no price shows "—" and the total says it covers priced steps
 * only; it is never shown as free.
 */
export default function ProcessingTelemetry({
  telemetry,
}: {
  telemetry: Telemetry;
}) {
  const [open, setOpen] = useState(false);
  const summary = [
    ...(telemetry.totalMs !== undefined ? [formatMs(telemetry.totalMs)] : []),
    ...(telemetry.extractionModel ? [telemetry.extractionModel] : []),
    ...(telemetry.costUsd !== undefined
      ? [
          `${telemetry.costComplete ? "≈" : "≥"}${formatCost(telemetry.costUsd)}`,
        ]
      : []),
  ];

  return (
    <div
      className="border-b border-line bg-surface"
      data-demo="document-processing"
    >
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full cursor-pointer items-center gap-2 border-0 bg-transparent px-4 py-1.5 text-left hover:bg-sunken"
      >
        <Icon
          name="caretRight"
          className={`size-3.5 flex-none text-ink-3 transition-transform duration-200 ${
            open ? "rotate-90" : ""
          }`}
        />
        <span className="text-[12px] font-bold text-ink">Processing</span>
        {summary.map((entry) => (
          <span key={entry} className="truncate text-[11px] text-ink-3">
            {entry}
          </span>
        ))}
        {telemetry.usedFallback && (
          <span className="rounded-sm bg-warn-bg px-1 text-[10px] text-warn-text">
            fallback model
          </span>
        )}
      </button>

      {open && (
        <div className="overflow-x-auto border-t border-line-soft">
          <table className="w-full border-collapse text-left text-[11px]">
            <caption className="sr-only">
              Processing steps of this document
            </caption>
            <thead>
              <tr className="text-ink-3">
                <th scope="col" className="px-4 py-1.5 font-semibold">
                  Step
                </th>
                <th scope="col" className="px-2 py-1.5 font-semibold">
                  How
                </th>
                <th
                  scope="col"
                  className="px-2 py-1.5 text-right font-semibold"
                >
                  Time
                </th>
                <th
                  scope="col"
                  className="px-2 py-1.5 text-right font-semibold"
                >
                  Tokens in / out
                </th>
                <th
                  scope="col"
                  className="px-4 py-1.5 text-right font-semibold"
                >
                  ≈Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {telemetry.steps.map((step) => (
                <tr key={step.key} className="border-t border-line-soft">
                  <td className="px-4 py-1.5 text-ink-2">
                    {step.label}
                    {(step.calls ?? 0) > 1 && (
                      <span className="ml-1 text-ink-3">×{step.calls}</span>
                    )}
                    {!!step.failedCalls && (
                      <span className="ml-1 text-danger-text">
                        ({step.failedCalls} failed)
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 font-mono text-[10.5px] text-ink-2">
                    {step.detail || "—"}
                    {step.fallbackFrom && (
                      <span
                        className="ml-1.5 rounded-sm bg-warn-bg px-1 font-sans text-[10px] text-warn-text"
                        title={step.fallbackReason}
                      >
                        fallback from {step.fallbackFrom}
                      </span>
                    )}
                    {!step.fallbackFrom && step.fallbackReason && (
                      <span className="ml-1.5 font-sans text-[10px] text-ink-3">
                        {step.fallbackReason}
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-[10.5px] whitespace-nowrap text-ink-3">
                    {step.latencyMs !== undefined
                      ? formatMs(step.latencyMs)
                      : "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-[10.5px] whitespace-nowrap text-ink-3">
                    {step.inputTokens !== undefined
                      ? `${step.inputTokens.toLocaleString()} / ${(step.outputTokens ?? 0).toLocaleString()}`
                      : "—"}
                    {!!step.cachedInputTokens && (
                      <span className="ml-1">
                        ({step.cachedInputTokens.toLocaleString()} cached)
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-1.5 text-right font-mono text-[10.5px] whitespace-nowrap text-ink-3">
                    {step.costUsd === null ? (
                      <span title="No price for this model">—</span>
                    ) : step.costUsd !== undefined ? (
                      formatCost(step.costUsd)
                    ) : (
                      ""
                    )}
                  </td>
                </tr>
              ))}
              <tr className="border-t border-line font-semibold text-ink-2">
                <td className="px-4 py-1.5" colSpan={2}>
                  Total
                  {telemetry.priceTable && (
                    <span className="ml-1.5 font-normal text-ink-3">
                      prices {telemetry.priceTable}
                    </span>
                  )}
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-[10.5px] whitespace-nowrap">
                  {telemetry.totalMs !== undefined
                    ? formatMs(telemetry.totalMs)
                    : "—"}
                </td>
                <td />
                <td className="px-4 py-1.5 text-right font-mono text-[10.5px] whitespace-nowrap">
                  {telemetry.costUsd !== undefined
                    ? `${telemetry.costComplete ? "" : "≥"}${formatCost(telemetry.costUsd)}`
                    : "—"}
                </td>
              </tr>
            </tbody>
          </table>
          {!telemetry.costComplete && (
            <p
              role="note"
              className="m-0 border-t border-line-soft bg-warn-bg px-4 py-1.5 text-[11px] text-warn-text"
            >
              Cost not available for every model (
              {telemetry.unpricedModels.join(", ") || "unpriced"}) — the total
              covers the priced steps only.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
