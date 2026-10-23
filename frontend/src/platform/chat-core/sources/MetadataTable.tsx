import { isPlainObject } from "lodash-es";

import { cn } from "../utils/cn";
import { useSourcesTheme } from "./context";

interface MetadataTableProps {
  data?: Record<string, unknown>;
}

const MAX_DEPTH = 3;

// The approved design shows metadata keys verbatim (snake_case), so the label
// is the raw key with surrounding whitespace trimmed.
const formatKey = (key: string) => key.trim();

const isEmpty = (v: unknown) => v === null || v === undefined || v === "";

const isUrl = (v: unknown): v is string =>
  typeof v === "string" && /^https?:\/\/\S+$/i.test(v.trim());

const isPrimitive = (v: unknown) =>
  v === null || (typeof v !== "object" && typeof v !== "function");

/** IDs, hashes, dates and numbers read better in a monospace face. */
const isMonoish = (v: unknown) =>
  typeof v === "number" ||
  (typeof v === "string" &&
    (/^[\w-]{10,}$/.test(v.trim()) || // uuid / long id / hash
      /^\d{4}-\d{2}-\d{2}/.test(v.trim()) || // iso date
      /^[\d.,\s→-]+$/.test(v.trim()))); // numeric ranges / offsets

/** Filtered entries of a plain object, or null when there is nothing to show. */
const objectEntries = (value: unknown): [string, unknown][] | null => {
  if (!isPlainObject(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>).filter(
    ([, v]) => !isEmpty(v),
  );
  return entries.length ? entries : null;
};

const MetaValue = ({ value, depth }: { value: unknown; depth: number }) => {
  const theme = useSourcesTheme();

  // URLs render as truncating links.
  if (isUrl(value)) {
    const label = value.replace(/^https?:\/\//i, "");
    return (
      <a
        href={value}
        target="_blank"
        rel="noreferrer"
        title={value}
        className={cn(
          "block max-w-full truncate underline underline-offset-2",
          theme.accentText,
        )}
      >
        {label}
      </a>
    );
  }

  // Primitives.
  if (isPrimitive(value)) {
    return (
      <span
        className={cn(
          theme.textBody,
          isMonoish(value) && "font-mono text-[11.5px]",
        )}
      >
        {String(value)}
      </span>
    );
  }

  // Arrays.
  if (Array.isArray(value)) {
    const items = value.filter((v) => !isEmpty(v));
    if (items.length === 0) {
      return <span className={theme.textMuted}>—</span>;
    }

    // A flat list of primitives → compact brand pills.
    if (items.every((v) => isPrimitive(v) && !isUrl(v))) {
      return (
        <div className="flex min-w-0 max-w-full flex-wrap gap-1">
          {items.map((v, i) => (
            <span
              key={i}
              className={cn(
                "inline-flex min-w-0 max-w-full items-center break-all rounded-full px-2 py-[3px] text-[11px] font-medium",
                theme.tagBrand,
              )}
            >
              {String(v)}
            </span>
          ))}
        </div>
      );
    }

    // Mixed / object arrays → one stacked row per item.
    return (
      <ul className="space-y-1.5">
        {items.map((v, i) => (
          <li
            key={i}
            className={cn("rounded-md border px-2 py-1.5", theme.surfaceMuted)}
          >
            <MetaValue value={v} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }

  // Plain objects → nested key/value, until we hit the depth cap.
  const entries = objectEntries(value);
  if (entries && depth < MAX_DEPTH) {
    return (
      <dl className="grid grid-cols-[minmax(0,max-content)_minmax(0,1fr)] gap-x-3 gap-y-1">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt
              className={cn("min-w-0 break-words font-medium", theme.textFaint)}
            >
              {formatKey(k)}
            </dt>
            <dd className="min-w-0 break-words">
              <MetaValue value={v} depth={depth + 1} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  // Fallback (too deep or non-plain object): readable JSON.
  return (
    <span className={cn("break-all font-mono text-[11px]", theme.textMuted)}>
      {JSON.stringify(value)}
    </span>
  );
};

const MetadataTable = ({ data }: MetadataTableProps) => {
  const theme = useSourcesTheme();

  const entries = objectEntries(data);
  if (!entries) return null;

  return (
    <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div
          key={key}
          className={cn(
            // Fixed key column keeps every row's value aligned; min-w-0 +
            // break-words on the key let long snake_case keys (e.g.
            // clickup_folder_name) wrap instead of overflowing onto the value.
            // items-start aligns a wrapped multi-line key with its value.
            "grid min-w-0 grid-cols-[8.5rem_minmax(0,1fr)] items-start gap-3 border-b border-dashed py-[7px] text-[12.5px] last:border-b-0",
            theme.border,
          )}
        >
          <span
            className={cn("min-w-0 break-words font-medium", theme.textFaint)}
          >
            {formatKey(key)}
          </span>
          <span className="min-w-0 break-words">
            <MetaValue value={value} depth={1} />
          </span>
        </div>
      ))}
    </div>
  );
};

export default MetadataTable;
