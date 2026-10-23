import dayjs from "dayjs";
import { TrendingDown } from "lucide-react";

import type { TrendsResponse } from "../types";

/**
 * The success-rate regression the trends endpoint flags.
 *
 * `regression.detected` marks a **≥5pp drop** in success rate between adjacent
 * buckets. It is one-way by design — an improvement is never flagged — so this
 * only ever renders as a warning, and renders nothing at all the rest of the
 * time.
 *
 * It was fetched and thrown away before: the field was typed and parsed, and no
 * component read it, which meant the one thing on the page that actively tells
 * you something broke was invisible.
 */
export default function RegressionBanner({
  regression,
}: {
  regression?: TrendsResponse["regression"];
}) {
  if (!regression?.detected) return null;

  const { from_bucket, to_bucket, delta, note } = regression;
  const day = (d?: string) => (d ? dayjs(d).format("MMM D") : null);
  const span =
    day(from_bucket) && day(to_bucket)
      ? `${day(from_bucket)} → ${day(to_bucket)}`
      : null;
  // Reported in percentage points, and negative — it is a drop.
  const points =
    typeof delta === "number" ? `${Math.abs(delta).toFixed(1)}pp` : null;

  return (
    <div
      role="status"
      className="flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 p-3"
    >
      <TrendingDown className="mt-0.5 size-4 shrink-0 text-amber-600" />
      <div className="min-w-0 text-sm">
        <span className="font-semibold text-amber-900">
          Success rate dropped
          {points ? ` ${points}` : ""}
          {span ? ` (${span})` : ""}
        </span>
        {note && <span className="ml-1.5 text-amber-800">{note}</span>}
      </div>
    </div>
  );
}
