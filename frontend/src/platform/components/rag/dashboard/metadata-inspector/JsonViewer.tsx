"use client";

import { useCallback, useMemo, useState } from "react";

import { isArray, isNil, keys, map } from "lodash-es";
import { Check, ChevronRightIcon, Copy } from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Syntax-highlighted, collapsible JSON tree viewer                  */
/* ------------------------------------------------------------------ */

interface JsonNodeProps {
  data: any;
  name?: string | number;
  depth: number;
  defaultExpanded: boolean;
  isLast: boolean;
}

const JsonNode = ({
  data,
  name,
  depth,
  defaultExpanded,
  isLast,
}: JsonNodeProps) => {
  const isObject = data && typeof data === "object" && !isArray(data);
  const isDataArray = isArray(data);
  const isExpandable = isObject || isDataArray;

  const [expanded, setExpanded] = useState(defaultExpanded);

  const childKeys = isObject ? keys(data) : null;
  const childCount = isDataArray
    ? data.length
    : childKeys
      ? childKeys.length
      : 0;
  const openBracket = isDataArray ? "[" : "{";
  const closeBracket = isDataArray ? "]" : "}";
  const comma = isLast ? "" : ",";

  /* ---- Primitive value ---- */
  if (!isExpandable) {
    return (
      <div
        className="flex items-baseline leading-6"
        style={{ paddingLeft: depth * 20 }}
      >
        {name !== undefined && (
          <span className="mr-1 select-none text-indigo-600">
            &quot;{name}&quot;<span className="text-slate-400">: </span>
          </span>
        )}
        <PrimitiveValue value={data} />
        {!isLast && <span className="text-slate-400">,</span>}
      </div>
    );
  }

  /* ---- Empty object/array ---- */
  if (childCount === 0) {
    return (
      <div
        className="flex items-baseline leading-6"
        style={{ paddingLeft: depth * 20 }}
      >
        {name !== undefined && (
          <span className="mr-1 select-none text-indigo-600">
            &quot;{name}&quot;<span className="text-slate-400">: </span>
          </span>
        )}
        <span className="text-slate-500">
          {openBracket}
          {closeBracket}
        </span>
        {!isLast && <span className="text-slate-400">,</span>}
      </div>
    );
  }

  /* ---- Expandable object/array ---- */
  return (
    <div>
      <div
        className="group/node flex cursor-pointer items-baseline leading-6 select-none"
        style={{ paddingLeft: depth * 20 }}
        onClick={() => setExpanded((e) => !e)}
      >
        <ChevronRightIcon
          className={`mr-1 inline-block size-3 flex-shrink-0 text-slate-400 transition-transform duration-150 ${
            expanded ? "rotate-90" : ""
          }`}
        />
        {name !== undefined && (
          <span className="mr-1 text-indigo-600">
            &quot;{name}&quot;<span className="text-slate-400">: </span>
          </span>
        )}
        <span className="text-slate-500">{openBracket}</span>
        {!expanded && (
          <>
            <span className="mx-1 text-xs text-slate-400">
              {childCount} {childCount === 1 ? "item" : "items"}
            </span>
            <span className="text-slate-500">
              {closeBracket}
              {comma}
            </span>
          </>
        )}
      </div>

      {expanded && (
        <>
          {isDataArray
            ? map(data, (item: any, i: number) => (
                <JsonNode
                  key={i}
                  data={item}
                  name={i}
                  depth={depth + 1}
                  defaultExpanded={depth < 1}
                  isLast={i === data.length - 1}
                />
              ))
            : map(childKeys!, (k, i) => (
                <JsonNode
                  key={k}
                  data={data[k]}
                  name={k}
                  depth={depth + 1}
                  defaultExpanded={depth < 1}
                  isLast={i === childKeys!.length - 1}
                />
              ))}
          <div className="leading-6" style={{ paddingLeft: depth * 20 }}>
            <span className="text-slate-500">
              {closeBracket}
              {comma}
            </span>
          </div>
        </>
      )}
    </div>
  );
};

/* ---- Primitive value renderer ---- */

const PrimitiveValue = ({ value }: { value: any }) => {
  if (isNil(value)) {
    return <span className="text-slate-400">null</span>;
  }
  if (typeof value === "boolean") {
    return <span className="text-amber-600">{String(value)}</span>;
  }
  if (typeof value === "number") {
    return <span className="text-emerald-600">{String(value)}</span>;
  }
  // String values
  return <span className="text-rose-600">&quot;{String(value)}&quot;</span>;
};

/* ---- Main JsonViewer component ---- */

interface JsonViewerProps {
  data: any;
  defaultExpanded?: boolean;
}

export const JsonViewer = ({
  data,
  defaultExpanded = true,
}: JsonViewerProps) => {
  const [copied, setCopied] = useState(false);

  const formattedJson = useMemo(() => {
    try {
      return JSON.stringify(data, null, 2);
    } catch {
      return String(data);
    }
  }, [data]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(formattedJson).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [formattedJson]);

  return (
    <div className="relative rounded-lg border border-slate-200 bg-slate-50/50">
      {/* Copy button */}
      <button
        type="button"
        onClick={handleCopy}
        className="absolute right-2 top-2 flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-500 shadow-sm transition-colors hover:bg-slate-50 hover:text-slate-700"
        title="Copy JSON"
      >
        {copied ? (
          <>
            <Check className="size-3 text-emerald-500" />
            <span className="text-emerald-600">Copied</span>
          </>
        ) : (
          <>
            <Copy className="size-3" />
            <span>Copy</span>
          </>
        )}
      </button>

      {/* JSON tree */}
      <div className="overflow-x-auto p-3 pr-20 font-mono text-xs leading-relaxed">
        <JsonNode
          data={data}
          depth={0}
          defaultExpanded={defaultExpanded}
          isLast
        />
      </div>
    </div>
  );
};
