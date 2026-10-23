"use client";

import { useState } from "react";

import Icon from "@/components/Icon";

import { LOG_DOT, LOG_LABEL } from "./constants";
import { formatCost, formatMs, type RunLog, type RunLogStep } from "./runLog";

/**
 * Everything the run did in the background, folded away under the answer.
 *
 * The strip above an answer says what came out of the run — documents read, a
 * chart built. This says how: the question as the service rewrote it, what the
 * search matched and at what score, which model wrote the answer and what it
 * cost, and what the chart step decided. Every line is a field the service
 * reported; a run that reports less simply logs fewer steps.
 */
export default function DiRunLog({ log }: { log: RunLog }) {
  const [open, setOpen] = useState(false);
  const [unfolded, setUnfolded] = useState<Record<string, boolean>>({});

  const summary = [
    ...(log.runType ? [log.runType] : []),
    ...(log.totals?.latencyMs ? [formatMs(log.totals.latencyMs)] : []),
    ...(log.totals?.totalTokens
      ? [`${log.totals.totalTokens.toLocaleString()} tokens`]
      : []),
    ...(log.totals?.costUsd ? [formatCost(log.totals.costUsd)] : []),
  ];

  return (
    <div className="overflow-hidden rounded-di-lg border border-line bg-surface">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full cursor-pointer items-center gap-2 border-0 bg-transparent px-3 py-2 text-left hover:bg-sunken"
      >
        <Icon
          name="caretRight"
          className={`size-3.5 flex-none text-ink-3 transition-transform duration-200 ${
            open ? "rotate-90" : ""
          }`}
        />
        <span className="text-[12px] font-bold text-ink">Full logs</span>
        <span className="truncate text-[11px] text-ink-3">
          {log.steps.length === 1 ? "1 step" : `${log.steps.length} steps`}
        </span>
      </button>

      {open && (
        <div className="border-t border-line">
          {/* The run as a whole. A stored turn keeps its steps but not its
              timings, so it says that rather than showing an empty bar. */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-sunken px-3 py-1.5">
            {summary.length ? (
              summary.map((entry, index) => (
                <span
                  key={entry}
                  className={`text-[11px] ${index === 0 ? "font-semibold text-ink-2" : "text-ink-3"}`}
                >
                  {entry}
                </span>
              ))
            ) : (
              <span className="text-[11px] text-ink-3">
                Timings and cost are reported while the answer streams and are
                not stored, so this thread logs what ran but not what it took.
              </span>
            )}
          </div>

          <ol className="flex list-none flex-col gap-0 p-0">
            {log.steps.map((step, index) => (
              <LogStep
                key={step.key}
                step={step}
                index={index + 1}
                open={!!unfolded[step.key]}
                onToggle={() =>
                  setUnfolded((current) => ({
                    ...current,
                    [step.key]: !current[step.key],
                  }))
                }
              />
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

function LogStep({
  step,
  index,
  open,
  onToggle,
}: {
  step: RunLogStep;
  index: number;
  open: boolean;
  onToggle: () => void;
}) {
  const lines = step.lines ?? [];
  const cost = [
    ...(step.call?.latencyMs !== undefined
      ? [formatMs(step.call.latencyMs)]
      : []),
    ...(step.call?.inputTokens !== undefined &&
    step.call.outputTokens !== undefined
      ? [
          `${step.call.inputTokens.toLocaleString()} in / ${step.call.outputTokens.toLocaleString()} out`,
        ]
      : []),
    ...(step.call?.costUsd !== undefined
      ? [formatCost(step.call.costUsd)]
      : []),
  ];

  // A step with nothing underneath is a line, not a fold — an arrow that opens
  // onto an empty box is worse than no arrow.
  const Row = lines.length ? "button" : "div";

  return (
    <li className="border-b border-line-soft last:border-b-0">
      <Row
        {...(lines.length
          ? {
              type: "button" as const,
              onClick: onToggle,
              "aria-expanded": open,
            }
          : {})}
        className={`flex w-full items-center gap-2 border-0 bg-transparent px-3 py-2 text-left ${
          lines.length ? "cursor-pointer hover:bg-sunken" : ""
        }`}
      >
        <span className="w-4 flex-none font-mono text-[10px] text-ink-3">
          {index}
        </span>
        <span
          className={`grid size-3.5 flex-none place-items-center rounded-full ${LOG_DOT[step.state]}`}
        >
          <Icon
            name={
              step.state === "failed"
                ? "x"
                : step.state === "skipped"
                  ? "ref"
                  : step.state === "flagged"
                    ? "warn"
                    : "chk"
            }
            className="size-2.5"
          />
        </span>
        <span className={`flex-none text-[12px] ${LOG_LABEL[step.state]}`}>
          {step.label}
        </span>
        {step.detail && (
          <span className="truncate text-[11px] text-ink-3" title={step.detail}>
            {step.detail}
          </span>
        )}
        <span className="flex-1" />
        {cost.map((entry) => (
          <span
            key={entry}
            className="flex-none font-mono text-[10.5px] text-ink-3"
          >
            {entry}
          </span>
        ))}
        {!!lines.length && (
          <Icon
            name="caretRight"
            className={`size-3 flex-none text-ink-3 transition-transform duration-200 ${
              open ? "rotate-90" : ""
            }`}
          />
        )}
      </Row>

      {open && !!lines.length && (
        <pre className="m-0 max-h-56 overflow-auto border-t border-line-soft bg-sunken px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-ink-2">
          {lines.join("\n")}
        </pre>
      )}
    </li>
  );
}
