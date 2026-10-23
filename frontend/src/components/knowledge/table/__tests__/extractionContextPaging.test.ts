import { describe, expect, it, vi } from "vitest";

import { fetchAllDataViewRows } from "@/platform/components/rag/dashboard/data-view/exportDataView";
import type { DocumentExtractionContext } from "@/platform/types/data-view";

import { buildTable } from "../buildTable";

const recorded: DocumentExtractionContext = {
  status: "recorded",
  event_key: "success-A",
  processed_at: "2026-10-20T00:00:00Z",
  processed_fields: ["loyalty", "damage"],
  definition_history: "unknown",
};
const unknown: DocumentExtractionContext = {
  status: "unknown",
  event_key: null,
  processed_at: null,
  processed_fields: null,
  definition_history: "unknown",
};

describe("extraction context propagation", () => {
  it("keeps per-document evidence across every cursor page", async () => {
    const fetchCursorPage = vi
      .fn()
      .mockResolvedValueOnce({
        columns: [],
        rows: [{ object_id: "A", damage: [] }],
        total_count: 2,
        current_count: 1,
        has_more: true,
        next_page_offset: "next",
        extraction_context: { A: recorded },
      })
      .mockResolvedValueOnce({
        columns: [],
        rows: [{ object_id: "B", damage: null }],
        total_count: 2,
        current_count: 1,
        has_more: false,
        extraction_context: { B: unknown },
      });
    const result = await fetchAllDataViewRows({
      knowledgeId: "kb",
      request: {},
      fetchCursorPage,
    });
    expect(result.rows).toEqual([
      { object_id: "A", damage: [] },
      { object_id: "B", damage: null },
    ]);
    expect(result.extractionContext).toEqual({ A: recorded, B: unknown });
  });

  it("passes unknown legacy context to CSV without inferring it from values", () => {
    const table = buildTable({
      documents: [
        {
          object_id: "legacy",
          status: "completed",
          file_name: "old.pdf",
        } as never,
      ],
      metadataColumns: [
        { name: "amount", original_field: "amount", data_type: "float" },
      ],
      metadataRows: [{ object_id: "legacy", amount: 0 }],
      extractionContext: { legacy: unknown },
    });
    expect(table.rows[0].values.amount).toBe(0);
    expect(table.rows[0].extractionContext).toEqual(unknown);
    expect(table.rows[0].notExtracted.size).toBe(0);
  });

  it("keeps v2 fallback evidence when the cursor route is unavailable", async () => {
    const result = await fetchAllDataViewRows({
      knowledgeId: "kb",
      request: {},
      fetchCursorPage: vi
        .fn()
        .mockRejectedValue({ isAxiosError: true, response: { status: 404 } }),
      fetchPage: vi.fn().mockResolvedValue({
        columns: [],
        rows: [{ object_id: "A" }],
        total_count: 1,
        current_count: 1,
        current_page: 1,
        total_pages: 1,
        extraction_context: { A: recorded },
      }),
    });
    expect(result.extractionContext).toEqual({ A: recorded });
  });
});
