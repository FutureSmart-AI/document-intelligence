"use client";

import Icon from "@/components/Icon";

import { THINKING_DOTS, TRACE_DOT, TRACE_LABEL } from "./constants";
import type { TraceRow } from "./deriveTrace";

/** What the run did, as the answer arrives and once it lands. */
export default function DiRunTrace({ rows }: { rows: TraceRow[] }) {
  if (!rows.length) return null;

  return (
    <div
      className="overflow-hidden rounded-di-lg border border-line bg-surface shadow-card"
      aria-live="polite"
    >
      <ul className="flex flex-col gap-2.5 px-3.5 py-3">
        {rows.map((row) => (
          <li key={row.key} className="flex items-center gap-2.5 text-[12.5px]">
            {/* A stage still running shows a plain filled dot: a check beside
                "Thinking" reads as a step that already finished. */}
            <span
              className={`grid size-3.5 flex-none place-items-center rounded-full ${TRACE_DOT[row.state]}`}
            >
              {row.state === "active" ? (
                <span className="size-1.5 rounded-full bg-accent" />
              ) : (
                <Icon
                  name={row.state === "failed" ? "x" : "chk"}
                  className="size-2.5"
                />
              )}
            </span>
            <span className={TRACE_LABEL[row.state]}>{row.label}</span>
            {row.state === "active" && (
              <span className="flex items-center gap-[3px]" aria-hidden="true">
                {THINKING_DOTS.map((dot) => (
                  <span
                    key={dot}
                    className={`size-[3px] rounded-full bg-accent ${dot}`}
                  />
                ))}
              </span>
            )}
            {row.detail && (
              <span
                className="truncate font-mono text-[11.5px] text-ink-3"
                title={row.detail}
              >
                {row.detail}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
