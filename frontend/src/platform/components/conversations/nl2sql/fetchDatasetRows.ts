import { csvToRows } from "./csvToRows";
import { isSafeHttpUrl } from "./urlSafety";

const looksLikeCsv = (url: string, contentType: string | null): boolean => {
  if (contentType && /csv/i.test(contentType)) return true;
  if (contentType && /json/i.test(contentType)) return false;
  // No useful content-type: fall back to the URL extension (ignore query/hash).
  return /\.csv($|[?#])/i.test(url);
};

export const fetchDatasetRows = async (
  url: string | undefined,
  signal?: AbortSignal,
): Promise<Record<string, unknown>[]> => {
  if (!isSafeHttpUrl(url)) {
    throw new Error("Unsafe or missing dataset URL");
  }
  const res = await fetch(url as string, { signal });
  if (!res.ok) {
    throw new Error(`Failed to load dataset (HTTP ${res.status})`);
  }

  const contentType = res.headers?.get?.("content-type") ?? null;
  if (looksLikeCsv(url as string, contentType)) {
    return csvToRows(await res.text());
  }

  const payload = await res.json();
  const rows = Array.isArray(payload)
    ? payload
    : Array.isArray((payload as { rows?: unknown })?.rows)
      ? (payload as { rows: unknown[] }).rows
      : null;
  if (!rows) {
    throw new Error("Unexpected dataset format");
  }
  return rows as Record<string, unknown>[];
};
