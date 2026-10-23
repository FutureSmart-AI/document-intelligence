import { ExportFormat, QueryGroupType } from "@/platform/types/batch-reports";

export const GROUP_TYPE_OPTIONS: { label: string; value: QueryGroupType }[] = [
  { label: "Independent (parallel)", value: "independent" },
  { label: "Sequential (follow-up)", value: "sequential" },
];

// Live run viewer: fast poll for near-real-time progress.
export const RUN_POLL_INTERVAL_MS = 3000;

// Run history list: slower poll, just keeps the status column fresh.
export const RUN_LIST_POLL_INTERVAL_MS = 30000;

export const EXPORT_FORMATS: ExportFormat[] = ["md", "json", "csv"];

export const SCHEDULE_OPTIONS = [
  { value: "", label: "No schedule (manual runs only)" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "cron", label: "Custom cron" },
] as const;

export const SAMPLE_CSV = `group_id,group_name,group_type,query_id,query_text
g1,PDF conversion,independent,q1,"Which API best converts PDFs to Markdown?"
g1,PDF conversion,independent,q2,"Which tools preserve tables properly?"
g2,Follow-up flow,sequential,q3,"Which tools are best for complex PDFs?"
g2,Follow-up flow,sequential,q4,"Among these which handles scanned PDFs?"`;

export const SAMPLE_JSON = `{
  "query_groups": [
    {
      "type": "independent",
      "queries": [
        "Which API best converts PDFs to Markdown?",
        "Which tools preserve tables properly?"
      ]
    },
    {
      "type": "sequential",
      "queries": [
        "Which tools are best for complex PDFs?",
        "Among these which handles scanned PDFs?"
      ]
    }
  ]
}`;
