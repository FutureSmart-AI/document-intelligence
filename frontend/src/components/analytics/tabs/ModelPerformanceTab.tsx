"use client";

import { useEffect, useMemo, useState } from "react";

import type { UseQueryResult } from "@tanstack/react-query";

import Icon from "@/components/Icon";
import { EmptyNote, Pill, Select } from "@/components/ui";
import { humanizeCode } from "@/data/analytics";
import {
  modelRows,
  stepModelRows,
  stepNames,
  stepVerdict,
  superlatives,
  type ModelRow,
  type StepModelRow,
} from "@/data/analyticsModels";
import ChartCard from "@/platform/analytics/components/ChartCard";
import DataState from "@/platform/analytics/components/DataState";
import KpiCard from "@/platform/analytics/components/KpiCard";
import KpiGrid from "@/platform/analytics/components/KpiGrid";
import type { Scorecard } from "@/platform/analytics/types";

import { COMPARE_BAR_SERIES } from "../constants";

/**
 * Model performance — which answer model this collection runs on, and how each
 * one does.
 *
 * Everything here comes from the scorecard's per-(step, model) aggregates
 * (`perf.by_node` / `cost.by_node`) and its per-model cost roll-up; the
 * arithmetic lives in `data/analyticsModels.ts`. Two columns the mock draws are
 * deliberately named differently, because the service meters something
 * narrower than the mock assumed:
 *
 * - a model's overall row reports **its slowest step's p95**, not an average of
 *   percentiles, which is not a percentile;
 * - the reliability column is **error-free calls**, not answer rate: errors are
 *   metered per call, and a question can go unanswered with every call fine.
 *
 * Tokens, not words: the service counts tokens, and quietly relabelling them
 * would be a made-up unit.
 */

const ms = (value: number | null) =>
  value === null
    ? "—"
    : value >= 1000
      ? `${(value / 1000).toFixed(1)}s`
      : `${Math.round(value)}ms`;

const usd = (value: number | null, digits = 5) =>
  value === null ? "—" : `$${value.toFixed(digits)}`;

const pct = (value: number | null, digits = 1) =>
  value === null ? "—" : `${value.toFixed(digits)}%`;

const nfmt = (value: number | null) =>
  value === null ? "—" : Math.round(value).toLocaleString();

const TH = "px-2 py-2.5 text-[11px] font-bold tracking-[0.04em] uppercase";
const TD = "px-2 py-2.5";

/** One row of the three side-by-side comparisons. */
function CompareBar({
  rows,
  value,
  format,
  max,
  colorIndex,
}: {
  rows: StepModelRow[];
  value: (row: StepModelRow) => number | null;
  format: (value: number) => string;
  max: number;
  colorIndex: number;
}) {
  return (
    <div className="grid gap-1.5">
      {rows.map((row) => {
        const reading = value(row);
        return (
          <div key={row.key} className="flex items-center gap-2">
            <span className="w-[104px] shrink-0 truncate text-[11.5px] text-ink-2">
              {row.model}
            </span>
            <span className="h-2 flex-1 overflow-hidden rounded-full bg-sunken">
              <span
                className="block h-full rounded-full"
                style={{
                  width:
                    reading === null || max <= 0
                      ? 0
                      : `${Math.max(2, (reading / max) * 100)}%`,
                  background:
                    COMPARE_BAR_SERIES[colorIndex % COMPARE_BAR_SERIES.length],
                }}
              />
            </span>
            <span className="tnum w-[68px] shrink-0 text-right text-[11.5px] text-ink-2">
              {reading === null ? "—" : format(reading)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default function ModelPerformanceTab({
  scorecard,
}: {
  scorecard: UseQueryResult<Scorecard>;
  agentId?: string | null;
}) {
  const rows = useMemo(() => modelRows(scorecard.data), [scorecard.data]);
  const steps = useMemo(() => stepNames(scorecard.data), [scorecard.data]);
  const best = useMemo(() => superlatives(rows), [rows]);

  const [step, setStep] = useState("");
  // The steps come with the window, so a step that is no longer reported must
  // not leave the comparison pointing at nothing.
  useEffect(() => {
    if (!steps.length) return;
    if (!step || !steps.includes(step)) setStep(steps[0]);
  }, [steps, step]);

  const compare = useMemo(
    () => stepModelRows(scorecard.data, step),
    [scorecard.data, step],
  );

  const providers = [...new Set(rows.map((row) => row.provider))];
  const maxMs = Math.max(...compare.map((row) => row.avgMs ?? 0), 0);
  const maxCost = Math.max(...compare.map((row) => row.costPerRun ?? 0), 0);

  const badges = (row: ModelRow) => (
    <>
      {best.fastest?.key === row.key && (
        <Pill tone="success">
          <Icon name="bolt" className="size-3" /> Fastest
        </Pill>
      )}
      {best.cheapest?.key === row.key && <Pill tone="accent">Cheapest</Pill>}
      {best.mostReliable?.key === row.key && (
        <Pill tone="warn">Fewest errors</Pill>
      )}
    </>
  );

  return (
    <div className="space-y-4">
      <div>
        <h3 className="di-display m-0 text-[15px] font-extrabold text-ink">
          Answer models used by this collection
        </h3>
        <p className="m-0 mt-0.5 max-w-[70ch] text-[12.5px] text-ink-2">
          Every answer model this collection has run on — which is faster, which
          is cheaper, and which errors least.
        </p>
      </div>

      <KpiGrid>
        <KpiCard
          label="Answer models used"
          value={scorecard.isLoading ? "…" : String(rows.length)}
          valueSize="lg"
          icon={<Icon name="layers" className="size-4 text-accent" />}
          iconBg="bg-accent-soft"
          hint={
            providers.length
              ? `Across ${providers.length} provider${providers.length === 1 ? "" : "s"} · ${providers.join(", ")}`
              : "No model has answered in this period"
          }
          isLoading={scorecard.isLoading}
        />
        <KpiCard
          label="Fastest model"
          value={best.fastest?.model ?? "—"}
          valueSize="md"
          icon={<Icon name="bolt" className="size-4 text-success-text" />}
          iconBg="bg-success-bg"
          hint={
            best.fastest
              ? `${best.fastest.provider} · ${ms(best.fastest.avgMs)} per call`
              : "No latency was recorded for any model"
          }
          isLoading={scorecard.isLoading}
        />
        <KpiCard
          label="Cheapest model"
          value={best.cheapest?.model ?? "—"}
          valueSize="md"
          icon={<Icon name="spark" className="size-4 text-accent" />}
          iconBg="bg-accent-soft"
          hint={
            best.cheapest
              ? `${best.cheapest.provider} · ${usd(best.cheapest.costPerAnswer)} per answer`
              : "Nothing was priced in this period"
          }
          isLoading={scorecard.isLoading}
        />
        <KpiCard
          label="Fewest errors"
          value={best.mostReliable?.model ?? "—"}
          valueSize="md"
          icon={<Icon name="chk" className="size-4 text-warn-text" />}
          iconBg="bg-warn-bg"
          hint={
            best.mostReliable
              ? `${best.mostReliable.provider} · ${pct(best.mostReliable.errorFreeRate)} of calls without error`
              : rows.length
                ? "Every model ran without an error here"
                : "No calls were metered in this period"
          }
          isLoading={scorecard.isLoading}
        />
      </KpiGrid>

      <ChartCard
        title="All answer models — full breakdown"
        subtitle="Totalled across every step a model ran on, sorted by answers given"
      >
        <DataState
          isLoading={scorecard.isLoading}
          isError={scorecard.isError}
          isEmpty={!rows.length}
          emptyText="No question was answered in this period, so no model has anything to compare."
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1040px] text-left text-[13px]">
              <thead className="border-b border-line text-ink-3">
                <tr>
                  <th className={TH}>Provider</th>
                  <th className={TH}>Model</th>
                  <th className={`${TH} text-right`}>Answers</th>
                  <th className={`${TH} text-right`}>Share</th>
                  <th className={`${TH} text-right`}>Avg / call</th>
                  <th className={TH}>Slowest step (p95)</th>
                  <th className={`${TH} text-right`}>Cost / answer</th>
                  <th className={`${TH} text-right`}>Total cost</th>
                  <th className={`${TH} text-right`}>Error-free</th>
                  <th className={`${TH} text-right`}>Tokens in</th>
                  <th className={`${TH} text-right`}>Tokens out</th>
                  <th className={TH}>Steps used on</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.key}
                    className="border-b border-line-soft last:border-0"
                  >
                    <td className={TD}>
                      <Pill tone="neutral">{row.provider}</Pill>
                    </td>
                    <td className={TD}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-[12.5px] text-ink">
                          {row.model}
                        </span>
                        {badges(row)}
                      </div>
                    </td>
                    <td className={`tnum ${TD} text-right text-ink`}>
                      {nfmt(row.answers)}
                    </td>
                    <td className={`tnum ${TD} text-right text-ink-2`}>
                      {pct(row.freq, 0)}
                    </td>
                    <td className={`tnum ${TD} text-right text-ink`}>
                      {ms(row.avgMs)}
                    </td>
                    <td className={`${TD} text-[12px] text-ink-2`}>
                      {row.slowest ? (
                        <>
                          {humanizeCode(row.slowest.step)}{" "}
                          <span className="tnum text-ink-3">
                            {ms(row.slowest.p95Ms)}
                          </span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className={`tnum ${TD} text-right text-ink-2`}>
                      {usd(row.costPerAnswer)}
                    </td>
                    <td className={`tnum ${TD} text-right text-ink`}>
                      {usd(row.totalCost, 2)}
                    </td>
                    <td className={`tnum ${TD} text-right text-ink-2`}>
                      {pct(row.errorFreeRate)}
                    </td>
                    <td className={`tnum ${TD} text-right text-ink-2`}>
                      {nfmt(row.tokensInPerCall)}
                    </td>
                    <td className={`tnum ${TD} text-right text-ink-2`}>
                      {nfmt(row.tokensOutPerCall)}
                    </td>
                    <td className={TD}>
                      <div className="flex flex-wrap gap-1">
                        {row.steps.length ? (
                          row.steps.map((name) => (
                            <Pill key={name} tone="neutral">
                              {humanizeCode(name)}
                            </Pill>
                          ))
                        ) : (
                          <span className="text-[12px] text-ink-3">
                            Not broken down by step
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </ChartCard>

      <div>
        <h3 className="di-display m-0 text-[15px] font-extrabold text-ink">
          Step × model comparison
        </h3>
        <p className="m-0 mt-0.5 max-w-[70ch] text-[12.5px] text-ink-2">
          How each answer model does on one step of answering — speed, cost and
          errors side by side.
        </p>
      </div>

      <ChartCard title="Compare models on one step">
        {steps.length ? (
          <div className="space-y-4">
            <label className="grid max-w-[320px] gap-1.5">
              <span className="text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                Compare models on step
              </span>
              <Select
                aria-label="Compare models on step"
                value={step}
                onChange={(event) => setStep(event.target.value)}
              >
                {steps.map((name) => (
                  <option key={name} value={name}>
                    {humanizeCode(name)}
                  </option>
                ))}
              </Select>
            </label>

            <div className="grid gap-5 lg:grid-cols-3">
              <div>
                <div className="mb-2 text-[12.5px] font-bold text-ink">
                  Speed on “{humanizeCode(step)}”
                </div>
                <CompareBar
                  rows={compare}
                  value={(row) => row.avgMs}
                  format={(value) => ms(value)}
                  max={maxMs}
                  colorIndex={0}
                />
              </div>
              <div>
                <div className="mb-2 text-[12.5px] font-bold text-ink">
                  Cost per run
                </div>
                <CompareBar
                  rows={compare}
                  value={(row) => row.costPerRun}
                  format={(value) => usd(value)}
                  max={maxCost}
                  colorIndex={1}
                />
              </div>
              <div>
                <div className="mb-2 text-[12.5px] font-bold text-ink">
                  Error-free calls
                </div>
                <CompareBar
                  rows={compare}
                  value={(row) => row.errorFreeRate}
                  format={(value) => pct(value, 0)}
                  max={100}
                  colorIndex={3}
                />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-left text-[13px]">
                <thead className="border-b border-line text-ink-3">
                  <tr>
                    <th className={TH}>Model</th>
                    <th className={TH}>Provider</th>
                    <th className={`${TH} text-right`}>Runs</th>
                    <th className={`${TH} text-right`}>Share</th>
                    <th className={`${TH} text-right`}>Avg time</th>
                    <th className={`${TH} text-right`}>P50</th>
                    <th className={`${TH} text-right`}>P95</th>
                    <th className={`${TH} text-right`}>Cost / run</th>
                    <th className={`${TH} text-right`}>Error-free</th>
                    <th className={`${TH} text-right`}>Tokens in</th>
                    <th className={`${TH} text-right`}>Tokens out</th>
                    <th className={TH}>Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {compare.map((row) => {
                    const verdict = stepVerdict(row, compare);
                    return (
                      <tr
                        key={row.key}
                        className="border-b border-line-soft last:border-0"
                      >
                        <td
                          className={`${TD} font-mono text-[12.5px] text-ink`}
                        >
                          {row.model}
                        </td>
                        <td className={TD}>
                          <Pill tone="neutral">{row.provider}</Pill>
                        </td>
                        <td className={`tnum ${TD} text-right text-ink`}>
                          {nfmt(row.runs)}
                        </td>
                        <td className={`tnum ${TD} text-right text-ink-2`}>
                          {pct(row.freq, 0)}
                        </td>
                        <td className={`tnum ${TD} text-right text-ink`}>
                          {ms(row.avgMs)}
                        </td>
                        <td className={`tnum ${TD} text-right text-ink-2`}>
                          {ms(row.p50Ms)}
                        </td>
                        <td className={`tnum ${TD} text-right text-ink-2`}>
                          {ms(row.p95Ms)}
                        </td>
                        <td className={`tnum ${TD} text-right text-ink-2`}>
                          {usd(row.costPerRun)}
                        </td>
                        <td className={`tnum ${TD} text-right text-ink-2`}>
                          {pct(row.errorFreeRate)}
                        </td>
                        <td className={`tnum ${TD} text-right text-ink-2`}>
                          {nfmt(row.tokensIn)}
                        </td>
                        <td className={`tnum ${TD} text-right text-ink-2`}>
                          {nfmt(row.tokensOut)}
                        </td>
                        <td className={TD}>
                          {verdict ? (
                            <Pill tone={verdict.tone}>{verdict.label}</Pill>
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <EmptyNote>
            {rows.length
              ? "This period records what each model cost, but not which step it ran on — so there is nothing to compare step by step yet."
              : "No step recorded a model in this period."}
          </EmptyNote>
        )}
      </ChartCard>
    </div>
  );
}
