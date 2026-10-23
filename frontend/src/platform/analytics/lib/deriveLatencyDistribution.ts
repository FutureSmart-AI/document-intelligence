export interface LatencyBucket {
  label: string;
  count: number;
}

const EDGES: { label: string; maxMs: number }[] = [
  { label: "<1s", maxMs: 1000 },
  { label: "1-2s", maxMs: 2000 },
  { label: "2-3s", maxMs: 3000 },
  { label: "3-5s", maxMs: 5000 },
  { label: "5-8s", maxMs: 8000 },
  { label: "8-12s", maxMs: 12000 },
  { label: "12-20s", maxMs: 20000 },
  { label: "20-30s", maxMs: 30000 },
  { label: ">30s", maxMs: Infinity },
];

export function deriveLatencyDistribution(
  rows: { latency_ms: number }[],
): LatencyBucket[] {
  const counts = new Array(EDGES.length).fill(0) as number[];
  for (const { latency_ms } of rows) {
    const idx = EDGES.findIndex((e) => latency_ms < e.maxMs);
    counts[idx === -1 ? EDGES.length - 1 : idx] += 1;
  }
  return EDGES.map((e, i) => ({ label: e.label, count: counts[i] }));
}
