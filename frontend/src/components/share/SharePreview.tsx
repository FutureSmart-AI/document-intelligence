"use client";

import Icon from "@/components/Icon";
import type { WidgetAppearance } from "@/data/widgetSettings";

/**
 * What the people you share with will see — the bubble, and the full page.
 *
 * A drawing, not an embed: it renders the stored appearance rather than loading
 * the real surface, so it updates as the fields are edited and needs nothing
 * public to exist yet. Everything shown is a field on this screen; the answer
 * text is obviously a sample and says so.
 *
 * Which of the two is drawn is chosen on the row header rather than here, so the
 * switch is reachable without opening the row.
 */
export default function SharePreview({
  appearance,
  mode,
}: {
  appearance: WidgetAppearance;
  mode: "bubble" | "full";
}) {
  const brand = appearance.color || "#2563eb";

  return (
    <div className="grid gap-2.5">
      <div className="rounded-di-lg border border-line bg-sunken p-3.5">
        <div className="overflow-hidden rounded-di-md border border-line bg-surface">
          <div
            className="flex items-center gap-2.5 px-3.5 py-3"
            style={{ background: brand }}
          >
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-white/20 text-white">
              <Icon name="chat" className="size-4" />
            </span>
            <span className="grid">
              <span className="text-[13px] font-bold text-white">
                {appearance.heading || "Ask these documents"}
              </span>
              <span className="text-[11px] text-white/80">
                {appearance.sub}
              </span>
            </span>
          </div>

          <div
            className={
              mode === "full"
                ? "grid gap-2.5 p-3.5 min-h-[240px]"
                : "grid gap-2.5 p-3.5 min-h-[150px]"
            }
          >
            <p className="max-w-[42ch] rounded-di-md bg-sunken px-3 py-2 text-[12.5px] text-ink">
              {appearance.greet}
            </p>
            <p
              className="ml-auto max-w-[30ch] rounded-di-md px-3 py-2 text-[12.5px] text-white"
              style={{ background: brand }}
            >
              Which candidates can start immediately?
            </p>
            <p className="max-w-[42ch] rounded-di-md bg-sunken px-3 py-2 text-[12.5px] text-ink">
              Three of them can — a sample answer, so you can see the shape of
              one.
            </p>
            {appearance.show_sources && (
              <p className="flex items-center gap-1.5 text-[11px] font-semibold text-ink-2">
                <Icon name="book" className="size-3.5" /> 2 source documents
              </p>
            )}
          </div>

          <div className="flex items-center gap-2 border-t border-line p-2.5">
            <span className="flex-1 rounded-full border border-line bg-sunken px-3 py-1.5 text-[11.5px] text-ink-3">
              Ask a question…
            </span>
            <span
              className="grid size-7 place-items-center rounded-full text-white"
              style={{ background: brand }}
            >
              <Icon name="caretRight" className="size-4" />
            </span>
          </div>
        </div>

        {mode === "bubble" && (
          <div className="mt-3 flex justify-end">
            <span
              className="grid size-11 place-items-center rounded-full text-white shadow-pop"
              style={{ background: brand }}
            >
              <Icon name="chat" className="size-5" />
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
