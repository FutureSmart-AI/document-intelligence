import { Clock, Database, Rows3 } from "lucide-react";

interface ExecMetadataProps {
  executionTimeMs?: number;
  rowCount?: number;
  rowCountIsPreview?: boolean;
  tablesUsed: string[];
}

export const ExecMetadata = ({
  executionTimeMs,
  rowCount,
  rowCountIsPreview,
  tablesUsed = [],
}: ExecMetadataProps) => (
  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600">
    {executionTimeMs !== undefined && (
      <span className="inline-flex items-center gap-1.5">
        <Clock className="w-3.5 h-3.5 text-indigo-600" />
        {`${executionTimeMs} ms`}
      </span>
    )}
    {rowCount !== undefined && (
      <span className="inline-flex items-center gap-1.5">
        <Rows3 className="w-3.5 h-3.5 text-indigo-600" />
        {`${rowCount} rows${rowCountIsPreview ? " (preview)" : ""}`}
      </span>
    )}
    {tablesUsed.length > 0 && (
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        <Database className="w-3.5 h-3.5 text-indigo-600" />
        {tablesUsed.map((t, i) => (
          <span
            key={`${t}-${i}`}
            className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-700"
          >
            {t}
          </span>
        ))}
      </span>
    )}
  </div>
);

export default ExecMetadata;
