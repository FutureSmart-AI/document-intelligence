"use client";

import { useEffect, useState } from "react";

import { ChevronDown, Clock, Database } from "lucide-react";

import { cn } from "@/platform/utils";

import Button from "../ui/Button";
import Tabs, { type TabItem } from "../ui/Tabs";
import CodeBlock from "./CodeBlock";
import ChartMetaPreview from "./nl2sql/ChartMetaPreview";
import ExecMetadata from "./nl2sql/ExecMetadata";
import FullDatasetModal from "./nl2sql/FullDatasetModal";
import type { Nl2sqlView } from "./nl2sql/normalizeNl2sql";
import PreviewTable from "./nl2sql/PreviewTable";
import ResultActions from "./nl2sql/ResultActions";

interface NL2SQLPanelProps {
  view: Nl2sqlView;
  showQuery: boolean;
  showResults: boolean;
}

export const NL2SQLPanel = ({
  view,
  showQuery,
  showResults,
}: NL2SQLPanelProps) => {
  const [open, setOpen] = useState(false);
  const [datasetOpen, setDatasetOpen] = useState(false);

  useEffect(() => {
    setDatasetOpen(false);
  }, [view]);

  const showSqlTab = showQuery && !!view.sqlQuery;
  const showResultTab = showResults && !!view.previewRows;
  const showChartTab = !!view.chart;

  const items: TabItem[] = [];
  if (showSqlTab) {
    items.push({
      key: "sql",
      label: "Executed SQL",
      content: (
        <CodeBlock language="sql" wrap>
          {view.sqlQuery!}
        </CodeBlock>
      ),
    });
  }
  if (showResultTab) {
    items.push({
      key: "result",
      label: "Result Data",
      content: (
        <div className="space-y-3">
          <ExecMetadata
            executionTimeMs={view.executionTimeMs}
            rowCount={view.rowCount}
            rowCountIsPreview={view.rowCountIsPreview}
            tablesUsed={view.tablesUsed}
          />
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-slate-600">
              {view.rowCount !== undefined &&
              view.previewRows &&
              view.rowCount > view.previewRows.length
                ? `Preview (first ${view.previewRows.length} of ${view.rowCount.toLocaleString()} rows)`
                : `${(view.previewRows?.length ?? 0).toLocaleString()} rows`}
            </span>
            <ResultActions
              previewRows={view.previewRows}
              resultS3Url={view.resultS3Url}
              onViewFullDataset={() => setDatasetOpen(true)}
            />
          </div>
          <PreviewTable rows={view.previewRows ?? []} />
          <FullDatasetModal
            open={datasetOpen}
            resultS3Url={view.resultS3Url}
            fallbackRows={view.previewRows}
            onClose={() => setDatasetOpen(false)}
          />
        </div>
      ),
    });
  }
  if (showChartTab) {
    items.push({
      key: "chart",
      label: "Chart Metadata",
      content: (
        <ChartMetaPreview chart={view.chart} previewRows={view.previewRows} />
      ),
    });
  }

  if (items.length === 0) return null;

  return (
    <div className="mt-4">
      <Button
        variant="outline"
        size="sm"
        className="group"
        startIcon={
          <div className="flex items-center gap-1.5">
            <Database className="size-3.5 text-indigo-600" />
            <ChevronDown
              className={cn(
                "size-4 transition-transform",
                open && "rotate-180",
              )}
            />
          </div>
        }
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="font-medium">
          Data Sources &amp; Execution Details
        </span>
        {view.executionTimeMs !== undefined && (
          <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
            <Clock className="w-3 h-3" />
            {(view.executionTimeMs / 1000).toFixed(2)}s
          </span>
        )}
      </Button>

      {open && (
        <div className="mt-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <Tabs items={items} />
        </div>
      )}
    </div>
  );
};

export default NL2SQLPanel;
