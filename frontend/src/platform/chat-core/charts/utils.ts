import {
  find,
  first,
  get,
  includes,
  isArray,
  isNil,
  keys,
  lowerCase,
  map,
} from "lodash-es";

import type {
  ChartData,
  DataRecord,
  NormalizedData,
  ResolvedAxes,
  XAxisLayout,
} from "./types";

/**
 * Resolves axis configuration with sensible defaults
 */
export function resolveAxes(metadata: ChartData["metadata"]): ResolvedAxes {
  return {
    x: {
      label: metadata?.x_axis?.label ?? "Category",
      field: metadata?.x_axis?.field ?? "label",
    },
    y: {
      label: metadata?.y_axis?.label ?? "Value",
      field: metadata?.y_axis?.field ?? "value",
    },
  };
}

/**
 * Normalizes different data formats into a consistent structure
 */
export function normalizeData(
  rawData: ChartData["data"],
  axes: ResolvedAxes,
): NormalizedData | null {
  if (isNil(rawData)) return null;

  // Format 1: Legacy {x: [], y: []} format
  if (
    get(rawData, "x") &&
    get(rawData, "y") &&
    isArray(get(rawData, "x")) &&
    isArray(get(rawData, "y"))
  ) {
    const xData = get(rawData, "x", []) as (string | number)[];
    const yData = get(rawData, "y", []) as (string | number)[];
    if (xData.length !== yData.length) return null;

    const data = map(xData, (xVal, idx) => ({
      [axes.x.field]: xVal,
      [axes.y.field]: yData[idx],
    })) as DataRecord[];

    return { data, xField: axes.x.field, yField: axes.y.field, series: [] };
  }

  if (!isArray(rawData)) return null;

  const firstItem = first(rawData);
  if (!firstItem || typeof firstItem !== "object") return null;

  // Format 2: Segmented format for stacked charts
  // [{index: "Country", segments: [{category: "City", value: 1}]}]
  if (get(firstItem, "index") && isArray(get(firstItem, "segments"))) {
    const allCategories = new Set<string>();

    // Collect all unique categories across all items
    map(rawData, (item: any) => {
      const segments = get(item, "segments", []);
      if (isArray(segments)) {
        map(segments, (seg: any) => {
          const category = get(seg, "category");
          if (category) allCategories.add(String(category));
        });
      }
    });

    // Transform to flat structure
    const data = map(rawData, (item: any) => {
      const indexValue = get(item, "index");
      const result: DataRecord = { [axes.x.field]: indexValue };
      const segments = get(item, "segments", []);

      if (isArray(segments)) {
        map(segments, (seg: any) => {
          const category = String(get(seg, "category", ""));
          const value = Number(get(seg, "value", 0));
          result[category] = value;
        });
      }

      // Fill missing categories with 0
      allCategories.forEach((category) => {
        if (!(category in result)) result[category] = 0;
      });

      return result;
    }) as DataRecord[];

    return {
      data,
      xField: axes.x.field,
      yField: axes.y.field,
      series: Array.from(allCategories),
    };
  }

  // Format 3: Standard array of objects
  const dataKeys = keys(firstItem);

  // Find x-axis field
  const xField =
    find(dataKeys, (k) => lowerCase(k) === lowerCase(axes.x.field)) ||
    find(dataKeys, (k) =>
      includes(["category", "name", "label", "index"], lowerCase(k)),
    ) ||
    first(dataKeys);

  // Find y-axis field
  const yField =
    find(dataKeys, (k) => lowerCase(k) === lowerCase(axes.y.field)) ||
    find(dataKeys, (k) => includes(["value", "y", "count"], lowerCase(k))) ||
    find(dataKeys, (k) => typeof get(firstItem, k) === "number") ||
    first(dataKeys);

  if (!xField || !yField) return null;

  return {
    data: rawData as DataRecord[],
    xField: xField as string,
    yField: yField as string,
    series: [],
  };
}

/**
 * Computes optimal x-axis layout (rotation angle, label truncation,
 * axis height, font size) based on the data labels to prevent overlapping.
 */
export function computeXAxisLayout(labels: (string | number)[]): XAxisLayout {
  const stringLabels = labels.map((l) => String(l ?? ""));
  const maxLen = stringLabels.reduce((max, l) => Math.max(max, l.length), 0);
  const count = labels.length;

  let angle: number;
  let maxLabelLength: number; // 0 = no truncation
  let fontSize: number;

  if (maxLen <= 6 && count <= 12) {
    // Short labels, reasonable count — display horizontally
    angle = 0;
    maxLabelLength = 0;
    fontSize = 12;
  } else if (maxLen <= 10 && count <= 6) {
    // Medium-short labels, few items — display horizontally
    angle = 0;
    maxLabelLength = 0;
    fontSize = 12;
  } else if (maxLen <= 20) {
    // Moderate-length labels — mild rotation, no truncation
    angle = -30;
    maxLabelLength = 0;
    fontSize = 12;
  } else if (maxLen <= 35) {
    // Long labels — steeper rotation, light truncation
    angle = -35;
    maxLabelLength = 25;
    fontSize = 11;
  } else {
    // Very long labels — aggressive truncation
    angle = -40;
    maxLabelLength = 20;
    fontSize = 11;
  }

  // Compute display length for height calculation (account for "..." suffix)
  const displayLen =
    maxLabelLength > 0 ? Math.min(maxLen, maxLabelLength + 3) : maxLen;
  const charWidth = fontSize <= 11 ? 6.5 : 7;

  let tickHeight: number;
  if (angle === 0) {
    tickHeight = fontSize + 10;
  } else {
    const radians = Math.abs(angle) * (Math.PI / 180);
    tickHeight = Math.ceil(displayLen * charWidth * Math.sin(radians));
  }

  // Clamp tick height to reasonable bounds
  tickHeight = Math.max(tickHeight, 25);
  tickHeight = Math.min(tickHeight, 120);

  // Total height = tick labels + space for axis title
  const height = tickHeight + 30;

  return { angle, maxLabelLength, height, fontSize };
}

/**
 * Detects a "group" field in the data – a string/categorical column that is
 * neither the x-axis field nor the y-axis field.  When present it identifies
 * the series each row belongs to (e.g. "Python", "Java", "JavaScript").
 *
 * Returns the field name, or `null` if none is found.
 */
export function detectGroupField(
  data: DataRecord[],
  xField: string,
  yField: string,
): string | null {
  if (data.length === 0) return null;

  const firstItem = data[0];
  const candidates = keys(firstItem).filter((k) => {
    if (k === xField || k === yField || k.startsWith("_")) return false;
    // Must be a string (categorical) value in most rows
    return typeof firstItem[k] === "string";
  });

  if (candidates.length === 0) return null;

  // Pick the candidate that has at least 2 distinct values and whose values
  // are short-ish strings (likely labels, not descriptions).
  for (const field of candidates) {
    const uniqueValues = new Set(data.map((d) => String(d[field])));
    if (uniqueValues.size >= 2 && uniqueValues.size <= data.length) {
      const avgLen =
        [...uniqueValues].reduce((sum, v) => sum + v.length, 0) /
        uniqueValues.size;
      if (avgLen <= 50) return field;
    }
  }

  return candidates[0] ?? null;
}

/**
 * Pivots flat data that contains a group/series field into a columnar format
 * that Recharts can render as grouped bars, multi-line, etc.
 *
 * Input:  [{category:"Bengaluru", value:50, skill:"Python"},
 *          {category:"Bengaluru", value:30, skill:"Java"}, ...]
 *
 * Output: { data: [{category:"Bengaluru", Python:50, Java:30, ...}, ...],
 *           series: ["Python", "Java", "JavaScript"] }
 */
export function pivotByGroup(
  data: DataRecord[],
  xField: string,
  yField: string,
  groupField: string,
): { data: DataRecord[]; series: string[] } {
  // Collect unique series values in order of first appearance
  const seriesSet = new Set<string>();
  const categoryOrder: string[] = [];
  const pivotMap = new Map<string, DataRecord>();

  data.forEach((item) => {
    const category = String(item[xField]);
    const group = String(item[groupField]);
    const value = item[yField];

    seriesSet.add(group);

    if (!pivotMap.has(category)) {
      pivotMap.set(category, { [xField]: category });
      categoryOrder.push(category);
    }

    pivotMap.get(category)![group] = value;
  });

  const series = Array.from(seriesSet);

  // Fill missing series values with 0
  const pivotedData = map(categoryOrder, (cat) => {
    const record = pivotMap.get(cat)!;
    series.forEach((s) => {
      if (!(s in record)) record[s] = 0;
    });
    return record;
  });

  return { data: pivotedData, series };
}

/**
 * Formats a numeric value into a compact, human-readable string.
 * Examples: 1234 → "1.2K", 1500000 → "1.5M", 0.5 → "0.5", 42 → "42"
 */
export function formatValue(value: number | string): string {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return String(value);

  const abs = Math.abs(num);
  if (abs >= 1_000_000_000) return `${(num / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${(num / 1_000).toFixed(1)}K`;
  if (Number.isInteger(num)) return num.toLocaleString();
  return num.toFixed(1);
}

/**
 * Resolves candidate series names (e.g. metadata.legend.items) against actual
 * data record keys. Returns the actual keys present in the data along with a
 * display label for each (the original candidate name).
 *
 * Matching strategy per candidate:
 *  1. exact key match
 *  2. case-insensitive key match
 *  3. snake_case'd key match ("Order Count" → "order_count")
 *
 * Candidates that match a key already used as the x-axis field are skipped so
 * a chart with x="order_date" never accidentally plots itself.
 */
export function resolveSeriesKeys(
  data: DataRecord[],
  candidates: (string | null | undefined)[],
  xField?: string,
): { key: string; label: string }[] {
  if (data.length === 0) return [];
  const dataKeys = keys(data[0] as DataRecord);
  const seen = new Set<string>();
  const result: { key: string; label: string }[] = [];

  for (const raw of candidates) {
    if (raw == null || raw === "") continue;
    const name = String(raw);
    const lower = lowerCase(name).trim();
    const snake = lower.replace(/\s+/g, "_");

    const match =
      find(dataKeys, (k) => k === name) ??
      find(dataKeys, (k) => lowerCase(k).trim() === lower) ??
      find(dataKeys, (k) => lowerCase(k).trim().replace(/\s+/g, "_") === snake);

    if (!match) continue;
    if (xField && match === xField) continue;
    if (seen.has(match)) continue;
    seen.add(match);
    result.push({ key: match, label: name });
  }
  return result;
}

/**
 * Heuristic: are all numeric values in the given field integer-valued?
 * Used to lock the Y axis to integer ticks (no "0.8" / "1.5" tick marks for
 * count-style data).
 */
export function isIntegerSeries(
  data: DataRecord[],
  fieldNames: string[],
): boolean {
  if (data.length === 0 || fieldNames.length === 0) return false;
  for (const row of data) {
    for (const f of fieldNames) {
      const v = row[f];
      if (v == null) continue;
      const n = typeof v === "string" ? Number(v) : (v as number);
      if (!Number.isFinite(n)) return false;
      if (!Number.isInteger(n)) return false;
    }
  }
  return true;
}
