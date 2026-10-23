"use client";

import {
  operationChoices,
  operationLabeller,
  useServiceOptions,
  type AnalyticsResponse,
} from "@/data/usage";

import { comma, Donut, money, TableShell } from "./UsageBits";

/**
 * Operation Breakdown — where the window's spend went.
 *
 * Grouped by `operation`, not by `service_type`: every event a Document
 * Intelligence account records carries the same service type, so that ring
 * would be one full-circle slice labelled "knowledge". The operations —
 * reading a document, embedding it, writing the vectors, answering a question —
 * are what differ, and what the reader is trying to tell apart.
 *
 * It reads the same answer the breakdown table below it reads, rather than
 * asking for it again: two queries with the same body are two chances for the
 * ring and the table to disagree about one window.
 *
 * Cost is the artifact's measure and the right one: these operations are not
 * priced alike, so a token split says nothing about the bill. But an account
 * whose whole window costs nothing — a new one, or one entirely on platform
 * keys — would get an empty ring, so the card falls back to token share and
 * says in its own heading which of the two it is drawing.
 *
 * Every operation this service records is listed, including the ones the
 * measure scores at zero: parsing a document and writing its vectors often cost
 * the account nothing while the model call costs something, and a ring showing
 * only the model call reads as though nothing else ran this window. Those rows
 * sit under the ring at 0% — they have no arc to draw.
 *
 * The roster is the same union the Operation picker files under Custom filters
 * — the catalogue's operations plus any the grouped answer carries that the
 * catalogue does not — so the ring and the picker never disagree about which
 * operations exist.
 */
export default function ServiceDonut({
  response,
  isLoading,
}: {
  response: AnalyticsResponse | undefined;
  isLoading: boolean;
}) {
  const { data: serviceOptions } = useServiceOptions();
  const nameOf = operationLabeller(serviceOptions);

  // Totalled per operation rather than read off each row: the answer is grouped
  // by service type as well, so one operation recorded under two services
  // arrives as two rows and the ring wants their sum.
  const measured = new Map<string, { cost: number; tokens: number }>();
  for (const point of response?.data ?? []) {
    const operation = point.dimensions.operation;
    if (!operation) continue;
    const running = measured.get(operation) ?? { cost: 0, tokens: 0 };
    measured.set(operation, {
      cost: running.cost + (point.metrics.cost?.total_cost_usd ?? 0),
      tokens: running.tokens + (point.metrics.total_tokens ?? 0),
    });
  }

  const rows = operationChoices(serviceOptions, response).map((choice) => ({
    label: nameOf(choice.value) || choice.label,
    cost: measured.get(choice.value)?.cost ?? 0,
    tokens: measured.get(choice.value)?.tokens ?? 0,
  }));

  const anyCost = rows.some((row) => row.cost > 0);
  const slices = rows.map((row) => ({
    label: row.label,
    value: anyCost ? row.cost : row.tokens,
  }));
  const hasArc = slices.some((slice) => slice.value > 0);

  return (
    <TableShell title="Operation Breakdown" note="Estimated cost by operation">
      {isLoading ? (
        <p className="m-0 py-12 text-center text-[13px] text-ink-2">Reading…</p>
      ) : !hasArc ? (
        <p className="m-0 py-12 text-center text-[13px] text-ink-2">
          No operation-level detail for this period yet.
        </p>
      ) : (
        <Donut
          slices={slices}
          showEmpty
          formatValue={anyCost ? money : comma}
          label={
            anyCost ? "Estimated cost by operation" : "Token share by operation"
          }
        />
      )}
    </TableShell>
  );
}
