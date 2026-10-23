"use client";

import { useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useAnalyticsScope } from "../AnalyticsScope";
import { parseWindowParams } from "../constants";
import type { AnalyticsTabKey, AnalyticsWindow } from "../types";

const TABS: AnalyticsTabKey[] = [
  "overview",
  "quality",
  "performance",
  "cost",
  "querylogs",
];

/**
 * `?period`/`?start`/`?end`/`?tab`/`?bucket` as page state.
 *
 * `validTabs` is a parameter because the NL2SQL product renders a sixth tab
 * ("failures") the agent dashboard has no endpoint for — without it, a deep
 * link to that tab would be rejected here and silently open Overview instead.
 *
 * `bucket` is the scorecard's usage granularity. It is `undefined` for a
 * backend whose scope publishes no bucket vocabulary, and stays out of the
 * request entirely in that case: analytics parameters are whitelisted
 * server-side, so sending one an endpoint does not declare is a 422 rather
 * than a harmless extra.
 */
export function useAnalyticsWindow<K extends string = AnalyticsTabKey>(
  validTabs: readonly K[] = TABS as unknown as readonly K[],
) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const window = parseWindowParams(
    new URLSearchParams(searchParams.toString()),
  );
  const tabParam = searchParams.get("tab") as K | null;
  const activeTab: K =
    tabParam && validTabs.includes(tabParam) ? tabParam : validTabs[0];

  // Only a value the backend published is honoured, and only once the reader
  // has chosen one: absent means "whatever the server defaults to", which is
  // both correct and one fewer parameter to get wrong. A hand-edited
  // `?bucket=fortnight` therefore reverts rather than 422-ing every read.
  const { filterOptions } = useAnalyticsScope();
  const bucketParam = searchParams.get("bucket");
  const bucket =
    bucketParam && filterOptions.buckets.some((b) => b.value === bucketParam)
      ? bucketParam
      : undefined;

  const push = useCallback(
    (mutate: (p: URLSearchParams) => void) => {
      const p = new URLSearchParams(searchParams.toString());
      mutate(p);
      router.replace(`${pathname}?${p.toString()}`, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  const setWindow = useCallback(
    (w: AnalyticsWindow) =>
      push((p) => {
        p.delete("period");
        p.delete("start");
        p.delete("end");
        if (w.mode === "preset") p.set("period", w.period);
        else {
          p.set("start", w.start);
          p.set("end", w.end);
        }
      }),
    [push],
  );

  const setTab = useCallback((t: K) => push((p) => p.set("tab", t)), [push]);

  const setBucket = useCallback(
    (b: string | undefined) =>
      push((p) => (b ? p.set("bucket", b) : p.delete("bucket"))),
    [push],
  );

  return { window, setWindow, activeTab, setTab, bucket, setBucket };
}
