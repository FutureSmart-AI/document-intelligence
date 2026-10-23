import { Info } from "lucide-react";

/**
 * Why a busy agent can show zeros everywhere.
 *
 * Analytics records a query when it is asked; there is no historical backfill,
 * so an agent that answered hundreds of questions before analytics shipped
 * reports nothing until it is asked something new. Without this note the screen
 * is indistinguishable from a broken one — every KPI reads 0 and every chart is
 * empty, and the reader has no way to tell "nothing happened" from
 * "nothing was recorded".
 *
 * Rendered only when the scorecard 404s, which is the API's way of saying this
 * agent has never produced a recorded query.
 */
export default function NoAnalyticsYetBanner() {
  return (
    <div
      role="status"
      className="flex items-start gap-2.5 rounded-lg border border-slate-200 bg-slate-50 p-3"
    >
      <Info className="mt-0.5 size-4 shrink-0 text-slate-400" />
      <div className="min-w-0 text-sm">
        <span className="font-semibold text-slate-700">
          No analytics recorded for this agent yet
        </span>
        <span className="ml-1.5 text-slate-500">
          Analytics starts from when a query is asked — earlier conversations
          are not backfilled, so an agent can have a full history here and still
          report zero. Ask it something and this page fills in.
        </span>
      </div>
    </div>
  );
}
