"use client";

import Link from "next/link";

import Icon from "@/components/Icon";
import { Card, EmptyNote, Pill, SectionLabel } from "@/components/ui";
import { useLimitsScreen, type LimitItem } from "@/data/usage";
import { apiMessage } from "@/lib/apiError";
import { ROUTES } from "@/lib/routes";
import { cn } from "@/platform/utils";

import { PROVIDER_LABELS, WINDOW_NOTES } from "./constants";
import { comma, money } from "./UsageBits";

/**
 * The Limits tab, from `GET /v1/owners/{owner_id}/usage/limits/screen`.
 *
 * One read for the whole tab, and every judgement on it is the service's
 * rather than this screen's — `used`, `limit`, `percent`, `lifted`, `enforced`
 * and the wording of each row all arrive resolved. That matters more than the
 * saved arithmetic: `percent` here is the same number the service used to
 * decide `exceeded`, so the bar and the banner above it cannot disagree, and a
 * row whose enforcement flips on the server says so on the next load without a
 * release here.
 *
 * Neither the period control nor the filters apply to this tab. An allowance is
 * counted over its own `window` — a standing count of what is held right now,
 * the calendar month, the calendar day or the account's lifetime — and a
 * ceiling anchored to a window the reader can change means nothing, so
 * `UsageView` hides both controls while this tab is open.
 */

function providerLabel(key: string): string {
  return PROVIDER_LABELS[key] ?? key;
}

/** "Groq", "Groq and OpenAI", "Groq, OpenAI and Google". */
function joinList(values: string[]): string {
  if (values.length < 2) return values[0] ?? "";
  return `${values.slice(0, -1).join(", ")} and ${values[values.length - 1]}`;
}

/**
 * Whether this row's figure is money.
 *
 * The estimated-spend row comes back with its unit as `USD`, so the match is on
 * the unit rather than on a key kept in a list here — and a money figure
 * carries its currency in the `$` already, which is why `unitSuffix` then drops
 * the unit instead of printing "$3.80 USD".
 */
function isMoney(item: LimitItem): boolean {
  return item.unit?.toLowerCase() === "usd" || item.key.includes("cost");
}

function formatFigure(item: LimitItem, value: number): string {
  return isMoney(item) ? money(value) : comma(value);
}

function unitSuffix(item: LimitItem): string {
  return isMoney(item) || !item.unit ? "" : ` ${item.unit}`;
}

/**
 * When the row's count starts again — read per row, because these do not agree.
 *
 * A standing count ("documents you hold") never resets, a daily allowance
 * resets tonight and a monthly one on the 1st; saying "this month" over all of
 * them would misdate three rows out of four.
 */
/**
 * One allowance: the figure, a bar where there is a ceiling worth drawing
 * against, the ceiling in words, what it counts and when it starts again.
 *
 * The bar is drawn only when the service sends a `percent` for a real ceiling
 * that is not lifted. Its width is clamped to full while the label is not:
 * being at 143% of an allowance is worth reading, and stopping the bar at full
 * is what a bar is for.
 */
function LimitRow({ item, exceeded }: { item: LimitItem; exceeded: boolean }) {
  const uncapped = item.limit === null || item.limit === undefined;
  const percent = item.percent ?? 0;
  const showBar =
    !item.lifted &&
    !uncapped &&
    item.percent !== null &&
    item.percent !== undefined;

  const figure = item.lifted
    ? `${formatFigure(item, item.used)}${unitSuffix(item)} · not capped`
    : uncapped
      ? `${formatFigure(item, item.used)}${unitSuffix(item)} · Unlimited`
      : `${formatFigure(item, item.used)} of ${formatFigure(item, item.limit as number)}${unitSuffix(item)}`;

  return (
    <div className="border-b border-line-soft py-3.5 last:border-b-0">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="min-w-40 flex-1 text-[13.5px] font-bold text-ink">
          {item.label}
        </span>
        {exceeded && (
          <Pill tone={item.enforced ? "danger" : "warn"}>
            <Icon name="warn" className="size-3" />
            {item.enforced ? "Blocking" : "No headroom left"}
          </Pill>
        )}
        {item.lifted && (
          <Pill tone="success">
            <Icon name="key" className="size-3" />
            Lifted by your own key
          </Pill>
        )}
        {!item.enforced && !item.lifted && (
          <Pill tone="neutral">Reported, never blocks</Pill>
        )}
        <span className="font-mono text-[12.5px] font-bold whitespace-nowrap text-ink-2 tabular-nums">
          {figure}
        </span>
      </div>

      {showBar && (
        <div
          className="my-2 h-[7px] overflow-hidden rounded-full border border-line-soft bg-sunken"
          role="img"
          aria-label={`${figure} used, ${Math.round(percent)}%`}
        >
          <span
            className={cn(
              "block h-full rounded-full transition-[width] duration-500",
              percent >= 90
                ? "bg-danger-dot"
                : percent >= 70
                  ? "bg-warn-dot"
                  : "bg-accent",
            )}
            style={{ width: `${Math.min(100, Math.max(percent, 0))}%` }}
          />
        </div>
      )}

      <p className="mt-1 text-[11.5px] font-semibold text-ink-2">
        Maximum: {item.maximum}
        {item.lifted ? " — not applied to you." : "."}
      </p>
      <p className="mt-0.5 text-[11.5px] leading-relaxed text-ink-3">
        {item.note}
        {WINDOW_NOTES[item.window] ? ` ${WINDOW_NOTES[item.window]}` : ""}
      </p>
    </div>
  );
}

function OwnCredentialsPill({ providers }: { providers: string[] }) {
  if (!providers.length) {
    return (
      <Pill tone="warn">
        <Icon name="globe" className="size-3" />
        Running entirely on FutureSmart keys
      </Pill>
    );
  }
  return (
    <Pill tone="success">
      <Icon name="key" className="size-3" />
      Using your own keys for {joinList(providers.map(providerLabel))}
    </Pill>
  );
}

export default function PlanLimits() {
  const limits = useLimitsScreen();

  if (limits.isLoading) {
    return (
      <Card className="p-4 text-[13px] text-ink-2">Reading your plan…</Card>
    );
  }

  if (limits.isError) {
    return (
      <EmptyNote>
        {apiMessage(
          limits.error,
          "Your plan could not be read for this account. Try again in a moment.",
        )}
      </EmptyNote>
    );
  }

  const data = limits.data;
  if (!data?.groups?.length) {
    return <EmptyNote>No allowances are set on this account.</EmptyNote>;
  }

  const exceeded = new Set(data.exceeded ?? []);
  const rows = data.groups.flatMap((group) => group.items);

  /**
   * Only an enforced row that is out of headroom gets the banner. A row at 100%
   * that changes nothing must not read like one that stops answers, so the
   * banner is gated on `enforced` rather than on membership of `exceeded`.
   */
  const blocking = rows.filter(
    (item) => item.enforced && exceeded.has(item.key),
  );

  return (
    <div data-demo="plan-limits">
      <Card className="mb-4 grid gap-2.5 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Pill tone="accent">
            <Icon name="bolt" className="size-3" />
            {data.tier.name} plan
          </Pill>
          <OwnCredentialsPill providers={data.own_credentials ?? []} />
        </div>
        <p className="max-w-[80ch] text-[13px] text-ink-2">
          Every allowance below is counted across every collection on this
          account. Each row says when it starts again — some are a standing
          count of what you hold, others reset daily or monthly.
        </p>
        <p className="max-w-[80ch] text-[11.5px] leading-relaxed text-ink-3">
          <b>The reporting period does not apply on this tab</b>, and neither do
          the filters: an allowance is counted over its own window, whichever
          key paid for the work.
        </p>
        <div className="mt-1">
          <Link
            href={ROUTES.credentials}
            className="inline-flex items-center gap-1.5 rounded-di-md border border-line bg-card px-3 py-1.5 text-[12px] font-semibold text-ink-2 no-underline hover:bg-sunken"
          >
            <Icon name="key" className="size-3.5" />
            Add your own credentials
          </Link>
        </div>
      </Card>

      {blocking.map((item) => (
        <div
          key={item.key}
          className="mb-4 rounded-di-md border border-danger-dot/40 bg-danger-bg p-3.5 text-[12.5px] text-danger-text"
          role="status"
        >
          <b>{item.label} reached.</b> New requests are being refused for this
          account until this allowance frees up. Maximum: {item.maximum}.
        </div>
      ))}

      {data.groups.map((group) => (
        <div key={group.group}>
          <SectionLabel className="mb-2">{group.group}</SectionLabel>
          <Card className="mb-4 px-4 py-1">
            {group.items.map((item) => (
              <LimitRow
                key={item.key}
                item={item}
                exceeded={exceeded.has(item.key)}
              />
            ))}
          </Card>
        </div>
      ))}
    </div>
  );
}
