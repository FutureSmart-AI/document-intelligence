import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";
import type { ColumnSchema } from "@/platform/types/data-view";

import KnowledgeView from "../KnowledgeView";

/**
 * The one screen that used to be three.
 *
 * These tests are about the joins and the controls composing, not about the
 * markup: that a document's status and its extracted fields sit in one row,
 * that the status counts describe the collection while the footer describes the
 * view, that a value clicked in the Explore band becomes a real filter rule the
 * footer and the export both obey, and that hiding a column group leaves the
 * Columns panel's own choices alone.
 */

const DOCUMENTS: KnowledgeItem[] = [
  {
    object_id: "obj-a",
    file_name: "Ananya_Rao_Resume.pdf",
    status: "completed",
    content_type: "file",
  },
  {
    object_id: "obj-b",
    file_name: "Marcus_Webb.pdf",
    status: "queued",
    content_type: "file",
  },
  {
    object_id: "obj-c",
    file_name: "scan.pdf",
    status: "failed",
    content_type: "file",
  },
].map(
  (entry) =>
    ({
      ...entry,
      id: entry.object_id,
      url: null,
      s3_url: "",
      parent_url: null,
      created_time: "2026-07-28T10:00:00Z",
      updated_time: "2026-07-28T10:00:00Z",
    }) as KnowledgeItem,
);

const METADATA_COLUMNS: ColumnSchema[] = [
  { name: "candidate", original_field: "candidate", data_type: "str" },
  { name: "role", original_field: "role", data_type: "str" },
  { name: "skills", original_field: "skills", data_type: "List[str]" },
];

const METADATA_ROWS: Record<string, unknown>[] = [
  {
    object_id: "obj-a",
    candidate: "Ananya Rao",
    role: "ML Engineer",
    skills: ["Python", "Kubernetes"],
  },
  {
    object_id: "obj-b",
    candidate: "Marcus Webb",
    role: "DevOps Engineer",
    skills: ["Kubernetes"],
  },
  { object_id: "obj-c", candidate: "", role: "", skills: [] },
];

const mocks = vi.hoisted(() => ({
  refetch: vi.fn(),
  refetchMetadata: vi.fn(),
  handleDelete: vi.fn(),
  extractedFields: undefined as Map<string, Set<string>> | undefined,
}));

vi.mock("@/data/extractionLog", () => ({
  useExtractedFields: () => ({
    extractedFields: mocks.extractedFields,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/platform/hooks/useAuth", () => ({
  useAuth: () => ({ knowledgeId: "k-1", isKnowledgeEnabled: true }),
}));

vi.mock("@/platform/hooks/useKnowledgeData", () => ({
  useKnowledgeData: () => ({
    data: { allData: DOCUMENTS, urls: [], texts: [], files: DOCUMENTS },
    isLoading: false,
    isFetching: false,
    refetch: mocks.refetch,
  }),
}));

vi.mock("@/data/knowledgeTable", () => ({
  useKnowledgeMetadata: () => ({
    metadata: {
      columns: METADATA_COLUMNS,
      rows: METADATA_ROWS,
      documentCount: 3,
      truncated: false,
    },
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: mocks.refetchMetadata,
  }),
}));

vi.mock(
  "@/platform/components/rag/dashboard/knowledge/hooks/useAutoRefresh",
  () => ({ useAutoRefresh: () => undefined }),
);

vi.mock(
  "@/platform/components/rag/dashboard/knowledge/list-view/useKnowledgeDelete",
  () => ({
    useKnowledgeDelete: () => ({
      handleDelete: mocks.handleDelete,
      confirmDelete: vi.fn(),
      closeModal: vi.fn(),
      deleteModalOpen: false,
      deleting: false,
      deleteRecord: null,
    }),
  }),
);

vi.mock(
  "@/platform/components/rag/dashboard/knowledge/list-view/DeleteKnowledgeDialog",
  () => ({ DeleteKnowledgeDialog: () => null }),
);

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({
    toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  }),
}));

// The document's own screen is this product's now, so it is rendered for real
// here — only the two requests behind it are stubbed.
vi.mock("@/data/documentDetail", () => ({
  useDocumentSections: () => ({
    data: [{ id: "c1", text: "Marcus Webb — DevOps Engineer, 6 yrs." }],
    isLoading: false,
  }),
  useEnrichmentFields: () => ({ data: [] }),
}));

vi.mock("../AddDocumentsDialog", () => ({ default: () => null }));

const footer = () => screen.getByText(/^Showing /);

/** The grid itself — several of these labels also appear inside the popovers. */
const grid = () => screen.getByRole("table");

describe("KnowledgeView", () => {
  afterEach(() => {
    mocks.extractedFields = undefined;
  });

  it("shows the document facts and the extracted fields as one row", () => {
    render(<KnowledgeView />);

    // The three column groups, each named once in the top header row.
    expect(within(grid()).getByText("Document")).toBeInTheDocument();
    expect(within(grid()).getByText("Status details")).toBeInTheDocument();
    expect(within(grid()).getByText("Extracted fields")).toBeInTheDocument();

    // A document's status and its extracted values in the same row.
    const row = screen.getByText("Ananya_Rao_Resume.pdf").closest("tr")!;
    expect(within(row).getByText("Completed")).toBeInTheDocument();
    expect(within(row).getByText("Ananya Rao")).toBeInTheDocument();
    expect(within(row).getByText("Python")).toBeInTheDocument();
    // Every field the collection defines landed for this document.
    expect(within(row).getByText("3/3")).toBeInTheDocument();

    expect(footer()).toHaveTextContent("Showing 1–3 of 3 documents");
  });

  it("spans each group heading across exactly the columns it names", () => {
    render(<KnowledgeView />);

    const span = (label: string) =>
      Number(
        within(grid()).getByText(label).closest("th")!.getAttribute("colspan"),
      );

    // Name + Added via; Status, Created, Updated, Fields filled; and the three
    // metadata columns — plus the row-actions cell, which is the last cell in
    // every row and so belongs to the last group, not the first.
    expect(span("Document")).toBe(2);
    expect(span("Status details")).toBe(4);
    expect(span("Extracted fields")).toBe(4);
  });

  it("says which fields the extraction found nothing for", () => {
    render(<KnowledgeView />);
    const row = screen.getByText("scan.pdf").closest("tr")!;
    expect(within(row).getByText("Failed")).toBeInTheDocument();
    expect(within(row).getAllByText("Not found").length).toBeGreaterThan(0);
    expect(within(row).getByText("0/3")).toBeInTheDocument();
  });

  it("counts the whole collection in the status control and filters on a click", async () => {
    const user = userEvent.setup();
    render(<KnowledgeView />);

    const failed = screen.getByTitle(
      "Show only the documents that could not be read",
    );
    expect(failed).toHaveTextContent("Failed1");

    await user.click(failed);
    expect(footer()).toHaveTextContent("Showing 1–1 of 1 document");
    // The counts still describe the collection, not the filtered view.
    expect(failed).toHaveTextContent("Failed1");
    expect(screen.queryByText("Ananya_Rao_Resume.pdf")).not.toBeInTheDocument();
  });

  it("searches the extracted values, not just the file names", async () => {
    const user = userEvent.setup();
    render(<KnowledgeView />);

    await user.type(screen.getByLabelText("Search documents"), "python");
    expect(footer()).toHaveTextContent("Showing 1–1 of 1 document");
    expect(screen.getByText("Ananya_Rao_Resume.pdf")).toBeInTheDocument();
  });

  it("turns a value clicked in the Explore band into a filter rule", async () => {
    const user = userEvent.setup();
    render(<KnowledgeView />);

    await user.click(
      screen.getByTitle("Group the documents by a column and chart it"),
    );

    // The band opens on the first extracted field, and offers its values.
    const groupBy = screen.getByRole("button", { name: /^Group by:/ });
    expect(groupBy).toHaveTextContent("Candidate");
    await user.click(groupBy);
    await user.click(screen.getByTitle("Group the documents by Role"));

    await user.click(
      screen.getByTitle("Also keep documents where Role is ML Engineer"),
    );

    expect(footer()).toHaveTextContent("Showing 1–1 of 1 document");
    // Written as a real rule, which is what the Filters badge counts.
    expect(
      screen.getByTitle("Keep only the documents that match your rules"),
    ).toHaveTextContent("Filters1");
  });

  it("keeps both values when a second one is picked, and drops the rule when the last goes", async () => {
    const user = userEvent.setup();
    render(<KnowledgeView />);

    await user.click(
      screen.getByTitle("Group the documents by a column and chart it"),
    );
    await user.click(screen.getByRole("button", { name: /^Group by:/ }));
    await user.click(screen.getByTitle("Group the documents by Role"));

    await user.click(
      screen.getByTitle("Also keep documents where Role is ML Engineer"),
    );
    expect(footer()).toHaveTextContent("Showing 1–1 of 1 document");

    // The second pick means "and also this one". Two `Equals` rules would be
    // ANDed and answer with nothing, which is what this guards.
    await user.click(
      screen.getByTitle("Also keep documents where Role is DevOps Engineer"),
    );
    expect(footer()).toHaveTextContent("Showing 1–2 of 2 documents");
    // Still one rule: one field, one set.
    expect(
      screen.getByTitle("Keep only the documents that match your rules"),
    ).toHaveTextContent("Filters1");

    // Unpicking the last value takes the rule away rather than leaving an
    // empty one behind for the badge to count.
    await user.click(screen.getByTitle("Stop keeping ML Engineer"));
    await user.click(screen.getByTitle("Stop keeping DevOps Engineer"));
    expect(footer()).toHaveTextContent("Showing 1–3 of 3 documents");
    expect(
      screen.getByTitle("Keep only the documents that match your rules"),
    ).not.toHaveTextContent("Filters1");
  });

  it("hides a whole column group without losing the Columns choices", async () => {
    const user = userEvent.setup();
    render(<KnowledgeView />);

    expect(within(grid()).getByText("Extracted fields")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Show: All/ }));
    await user.click(screen.getByRole("button", { name: "Status only" }));

    expect(
      within(grid()).queryByText("Extracted fields"),
    ).not.toBeInTheDocument();
    expect(within(grid()).getByText("Status details")).toBeInTheDocument();
    // The Document group is locked, so the row still has its anchor.
    expect(
      within(grid()).getByText("Ananya_Rao_Resume.pdf"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All" }));
    expect(within(grid()).getByText("Extracted fields")).toBeInTheDocument();
    expect(within(grid()).getByText("Ananya Rao")).toBeInTheDocument();
  });

  it("opens the document over the table, text beside values, when a row is clicked", async () => {
    const user = userEvent.setup();
    render(<KnowledgeView />);

    await user.click(screen.getByText("Marcus_Webb.pdf"));

    const detail = screen.getByRole("dialog");
    // Both halves of the screen: what the extraction read, and what it pulled
    // out. A drawer could only ever show one of the two.
    expect(detail).toHaveTextContent("Marcus Webb — DevOps Engineer, 6 yrs.");
    expect(within(detail).getByText("Extracted fields")).toBeInTheDocument();
    expect(within(detail).getByText("Document details")).toBeInTheDocument();
    expect(within(detail).getByText("obj-b")).toBeInTheDocument();

    // And it closes back onto the table it was opened from.
    await user.click(
      within(detail).getByRole("button", { name: /close this document/i }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  /**
   * A field added to the schema after a document was processed was never
   * looked for in it. Printing "Not found" there made every older document
   * read as a failed extraction.
   */
  it("leaves out fields added after a document was processed", async () => {
    mocks.extractedFields = new Map([["obj-c", new Set(["candidate"])]]);
    const user = userEvent.setup();
    render(<KnowledgeView />);

    const row = screen.getByText("scan.pdf").closest("tr")!;
    // Measured against the one field it ran under, not the three there are now.
    expect(within(row).getByText("0/1")).toBeInTheDocument();
    expect(within(row).getAllByText("Not found")).toHaveLength(1);
    expect(within(row).getAllByText("Not extracted")).toHaveLength(2);

    await user.click(screen.getByText("scan.pdf"));
    const detail = screen.getByRole("dialog");
    expect(detail).toHaveTextContent("0 of 1 fields filled");
    expect(detail).toHaveTextContent(
      "2 fields were added to this collection after this document was processed",
    );
    expect(within(detail).queryByText("Role")).not.toBeInTheDocument();
    expect(within(detail).getByText("Candidate")).toBeInTheDocument();
  });
});
