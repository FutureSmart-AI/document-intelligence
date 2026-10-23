import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";

import DiDocumentsPanel from "../DiDocumentsPanel";

const doc = (over: Partial<KnowledgeItem> = {}): KnowledgeItem =>
  ({
    id: "1",
    object_id: "1",
    file_name: "master-services-agreement.pdf",
    status: "completed",
    url: null,
    created_time: "2026-08-20T10:00:00Z",
    content_type: "file",
    s3_url: "",
    parent_url: null,
    ...over,
  }) as KnowledgeItem;

const DOCUMENTS = [
  doc(),
  doc({
    id: "2",
    object_id: "2",
    file_name: "vendor-policy.docx",
    status: "failed",
    error_detail: "The file is password protected.",
  }),
  doc({
    id: "3",
    object_id: "3",
    file_name: "",
    url: "https://example.com/terms",
    content_type: "url",
    status: "processing",
  }),
];

const props = {
  collectionName: "Vendor contracts",
  documents: DOCUMENTS,
  isLoading: false,
  onClose: () => {},
};

describe("DiDocumentsPanel", () => {
  it("lists the documents the answers may draw on", () => {
    render(<DiDocumentsPanel {...props} />);
    expect(screen.getByText("Vendor contracts")).toBeInTheDocument();
    expect(
      screen.getByText("master-services-agreement.pdf"),
    ).toBeInTheDocument();
    // A link has no file name; it is listed by the address it was read from.
    expect(screen.getByText("https://example.com/terms")).toBeInTheDocument();
  });

  it("opens a document onto what the service knows about it", () => {
    render(<DiDocumentsPanel {...props} />);
    expect(screen.queryByText(/^File · completed/)).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /master-services-agreement/ }),
    );
    expect(screen.getByText("File · completed")).toBeInTheDocument();
  });

  it("says why a document failed, since answers never draw on it", () => {
    render(<DiDocumentsPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: /vendor-policy/ }));
    expect(
      screen.getByText("The file is password protected."),
    ).toBeInTheDocument();
  });

  it("warns that a document still being read is not answerable yet", () => {
    render(<DiDocumentsPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: /example.com/ }));
    expect(
      screen.getByText("Still being read — answers do not draw on it yet."),
    ).toBeInTheDocument();
  });

  it("searches the list by name", () => {
    render(<DiDocumentsPanel {...props} />);
    fireEvent.change(screen.getByLabelText("Search documents"), {
      target: { value: "vendor" },
    });
    expect(screen.getByText("vendor-policy.docx")).toBeInTheDocument();
    expect(
      screen.queryByText("master-services-agreement.pdf"),
    ).not.toBeInTheDocument();
  });

  it("says a search matched nothing in the words of the search", () => {
    render(<DiDocumentsPanel {...props} />);
    fireEvent.change(screen.getByLabelText("Search documents"), {
      target: { value: "invoice" },
    });
    expect(
      screen.getByText("No document matches “invoice”."),
    ).toBeInTheDocument();
  });

  it("says an empty collection is empty rather than drawing nothing", () => {
    render(<DiDocumentsPanel {...props} documents={[]} />);
    expect(
      screen.getByText("This collection holds no documents yet."),
    ).toBeInTheDocument();
  });

  it("closes", () => {
    const onClose = vi.fn();
    render(<DiDocumentsPanel {...props} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Hide documents" }));
    expect(onClose).toHaveBeenCalled();
  });
});
