"use client";

import { map, startCase } from "lodash-es";
import { ChevronDownIcon, Loader, RefreshCw } from "lucide-react";

import Button from "@/platform/components/ui/Button";

import { JsonViewer } from "./JsonViewer";
import { renderMetadataValue } from "./MetadataValueRenderer";
import type { MetadataSectionConfig } from "./types";

interface DocumentMetadataSectionProps {
  config: MetadataSectionConfig;
  documentState: {
    data: Record<string, any> | null;
    loading: boolean;
    error: string | null;
  };
  documentMetadataEntries: Array<[string, any]>;
  targetId: string | null;
  onRetry: () => void;
}

export const DocumentMetadataSection = ({
  config,
  documentState,
  documentMetadataEntries,
  targetId,
  onRetry,
}: DocumentMetadataSectionProps) => {
  return (
    <section>
      <div className="flex items-center justify-between pb-3">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-slate-900">
            {config.title}
          </h3>
          <span className="inline-flex items-center rounded-md bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-200">
            {documentState.error
              ? "Error"
              : documentState.loading
                ? "Loading\u2026"
                : `${documentMetadataEntries.length} fields`}
          </span>
        </div>
        {documentState.error && targetId ? (
          <Button
            variant="outline"
            size="sm"
            onClick={onRetry}
            startIcon={<RefreshCw className="h-3.5 w-3.5" />}
          >
            Retry
          </Button>
        ) : null}
      </div>

      {config.description ? (
        <p className="pb-3 text-xs text-slate-500">{config.description}</p>
      ) : null}

      {documentState.loading ? (
        <div className="flex items-center gap-2 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-sm text-slate-500">
          <Loader className="h-4 w-4 animate-spin text-indigo-500" />
          {config.loadingMessage}
        </div>
      ) : documentState.error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          {documentState.error}
        </div>
      ) : documentState.data ? (
        <>
          {documentMetadataEntries.length > 0 ? (
            <div className="overflow-hidden rounded-lg border border-slate-200">
              <table className="min-w-full text-sm">
                <tbody className="divide-y divide-slate-100">
                  {map(documentMetadataEntries, ([key, value], idx) => (
                    <tr
                      key={key}
                      className={idx % 2 === 0 ? "bg-white" : "bg-slate-50/60"}
                    >
                      <td className="w-44 whitespace-nowrap px-4 py-2.5 align-top text-xs font-medium text-slate-500">
                        {startCase(key)}
                      </td>
                      <td className="px-4 py-2.5 text-sm text-slate-800 break-words">
                        {renderMetadataValue(value)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
              {config.emptyMessage}
            </div>
          )}

          <details className="group mt-3 rounded-lg border border-slate-200">
            <summary className="flex cursor-pointer select-none items-center justify-between px-3 py-2 text-xs font-medium text-slate-500 hover:text-slate-700">
              {config.rawJsonLabel}
              <span className="transition-transform duration-200 group-open:rotate-180">
                <ChevronDownIcon className="size-3.5" />
              </span>
            </summary>
            <div className="border-t px-3 py-3">
              <JsonViewer data={documentState.data} />
            </div>
          </details>
        </>
      ) : (
        <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
          {config.emptyMessage}
        </div>
      )}
    </section>
  );
};
