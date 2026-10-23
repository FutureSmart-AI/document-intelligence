"use client";

import type { ReactNode } from "react";

import { entries, isArray, isEmpty, map, startCase } from "lodash-es";

import { JsonViewer } from "./JsonViewer";
import { hasRenderableValue, isPlainObject, isStringArray } from "./utils";

/**
 * Renders a nested dictionary as key-value pairs
 * Shows objects in a compact "key: value" format
 */
const renderNestedDictionary = (
  obj: Record<string, any>,
  depth: number = 0,
): ReactNode => {
  if (depth > 5) {
    // Prevent infinite recursion, fall back to JsonViewer for deeply nested structures
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-3">
        <JsonViewer data={obj} defaultExpanded={false} />
      </div>
    );
  }

  return (
    <div
      className={`space-y-1.5 ${depth > 0 ? "ml-4 border-l-2 border-slate-200 pl-3" : ""}`}
    >
      {map(entries(obj), ([key, val]) => {
        const hasValue = hasRenderableValue(val);

        // If value is not renderable, show key with "None"
        if (!hasValue) {
          return (
            <div key={key} className="flex flex-col gap-1">
              <div className="flex items-start gap-2">
                <span className="text-xs font-semibold text-slate-600 min-w-[100px]">
                  {startCase(key)}:
                </span>
                <div className="flex-1 text-sm text-slate-800">
                  <span className="italic text-slate-400">None</span>
                </div>
              </div>
            </div>
          );
        }

        const isNestedObject = isPlainObject(val);
        const isNestedArray =
          isArray(val) && !isEmpty(val) && isPlainObject(val[0]);

        return (
          <div key={key} className="flex flex-col gap-1">
            <div className="flex items-start gap-2">
              <span className="text-xs font-semibold text-slate-600 min-w-[100px]">
                {startCase(key)}:
              </span>
              <div className="flex-1 text-sm text-slate-800">
                {isNestedObject ? (
                  <div className="mt-0.5">
                    {renderNestedDictionary(val, depth + 1)}
                  </div>
                ) : isNestedArray ? (
                  <div className="space-y-2">
                    {map(val, (item, idx) => (
                      <div
                        key={idx}
                        className="rounded border border-slate-200 bg-slate-50/50 p-2"
                      >
                        <div className="mb-1 text-xs font-medium text-slate-500">
                          Item {idx + 1}
                        </div>
                        {isPlainObject(item)
                          ? renderNestedDictionary(item, depth + 1)
                          : renderMetadataValue(item)}
                      </div>
                    ))}
                  </div>
                ) : (
                  <span className="text-slate-700">
                    {renderMetadataValue(val)}
                  </span>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export const renderMetadataValue = (value: any): ReactNode => {
  if (
    value === null ||
    value === undefined ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return <span className="italic text-slate-400">Not available</span>;
  }

  if (typeof value === "boolean") {
    return (
      <span className="font-medium text-indigo-600">
        {value ? "True" : "False"}
      </span>
    );
  }

  if (typeof value === "number") {
    return <span className="font-medium text-slate-700">{value}</span>;
  }

  if (isStringArray(value)) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {map(value, (item, index) => (
          <span
            key={`${item}-${index}`}
            className="rounded-sm bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-200"
          >
            {item}
          </span>
        ))}
      </div>
    );
  }

  if (isArray(value)) {
    // Check if array contains objects
    const containsObjects = !isEmpty(value) && isPlainObject(value[0]);

    if (containsObjects) {
      // For arrays of objects, render each as key-value pairs
      return (
        <div className="space-y-2">
          {map(value, (item, index) => (
            <div
              key={`item-${index}-${JSON.stringify(item).slice(0, 50)}`}
              className="rounded-lg border border-slate-200 bg-slate-50/50 p-3"
            >
              <div className="mb-2 text-xs font-semibold text-slate-500">
                Item {index + 1}
              </div>
              <div className="text-sm">{renderNestedDictionary(item, 0)}</div>
            </div>
          ))}
        </div>
      );
    }

    // For arrays of primitives, render as before
    return (
      <div className="space-y-2">
        {map(value, (item, index) => (
          <div
            key={`primitive-${index}-${String(item).slice(0, 30)}`}
            className="rounded-lg border border-slate-200 bg-white p-2"
          >
            <div className="text-sm text-slate-800">
              {renderMetadataValue(item)}
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (isPlainObject(value)) {
    // Render nested dictionary as structured sub-fields
    // Each piece is rendered separately for better visibility
    return <div className="w-full">{renderNestedDictionary(value, 0)}</div>;
  }

  return (
    <span className="whitespace-pre-wrap text-slate-700">{String(value)}</span>
  );
};
