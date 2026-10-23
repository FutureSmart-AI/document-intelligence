import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AddDocumentsDialog from "../AddDocumentsDialog";

/**
 * What the website dialog actually asks for, and what that turns into.
 *
 * Worth a test because every mistake here is silent: the service ignores a field
 * a source has no use for and answers 200, so a depth that never leaves the
 * dialog, or one sent alongside a scrape, looks exactly like success.
 */
const mocks = vi.hoisted(() => ({
  ingestWebsite: vi.fn(),
}));

// `useApiError` reports a failed add and links to the Limits tab.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/data/ingest", () => ({
  ingestWebsite: (...args: unknown[]) => mocks.ingestWebsite(...args),
  ingestFiles: vi.fn(),
  ingestPlainText: vi.fn(),
}));

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({
    toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  }),
}));

vi.mock("@/platform/components/rag/dashboard/knowledge/clickup", () => ({
  ClickUpWizard: () => null,
}));

const K = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";

const openDialog = () =>
  render(
    <AddDocumentsDialog
      knowledgeId={K}
      source="url"
      onClose={vi.fn()}
      onDone={vi.fn()}
    />,
  );

describe("AddDocumentsDialog — website", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.ingestWebsite.mockResolvedValue({ status: "processing" });
    window.localStorage.clear();
  });

  /** The scraper picker is gone: `scrape_using` has no effect on the service. */
  it("offers no scraper picker", () => {
    openDialog();
    expect(screen.queryByLabelText(/scraper/i)).not.toBeInTheDocument();
  });

  /**
   * The depth control is on screen from the moment the website form is — a
   * control that only appears once another select is changed reads as missing.
   * It still belongs to a crawl and only a crawl, so a scrape disables it.
   */
  it("keeps the depth control on screen, enabled only for a crawl", async () => {
    openDialog();
    expect(screen.getByLabelText(/levels/i)).toBeDisabled();

    await userEvent.selectOptions(
      screen.getByLabelText(/what to read/i),
      "crawl",
    );
    expect(screen.getByLabelText(/levels/i)).toBeEnabled();

    await userEvent.selectOptions(
      screen.getByLabelText(/what to read/i),
      "scrape",
    );
    expect(screen.getByLabelText(/levels/i)).toBeDisabled();
  });

  /** A scrape ignores the depth: nothing about it reaches the request. */
  it("sends no depth for a scrape", async () => {
    openDialog();

    await userEvent.type(
      screen.getByLabelText(/website url/i),
      "https://example.com/page",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(mocks.ingestWebsite).toHaveBeenCalledWith(
      K,
      "https://example.com/page",
      expect.objectContaining({ method: "scrape" }),
    );
  });

  it("sends the method and the depth, and asks nothing else", async () => {
    openDialog();

    await userEvent.type(
      screen.getByLabelText(/website url/i),
      "https://example.com/page",
    );
    await userEvent.selectOptions(
      screen.getByLabelText(/what to read/i),
      "crawl",
    );
    await userEvent.selectOptions(screen.getByLabelText(/levels/i), "2");

    // How a page has to be read is the service's own call now, so there is no
    // advanced disclosure to open and no processing mode to send.
    expect(
      screen.queryByRole("button", { name: /advanced/i }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(mocks.ingestWebsite).toHaveBeenCalledWith(
      K,
      "https://example.com/page",
      { method: "crawl", maxDepth: 2 },
    );
  });

  /** A full address is what the route takes; anything else never leaves here. */
  it("keeps Add disabled until the address is a full URL", async () => {
    openDialog();
    const add = screen.getByRole("button", { name: "Add" });
    expect(add).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/website url/i), "example.com");
    expect(add).toBeDisabled();
  });
});
