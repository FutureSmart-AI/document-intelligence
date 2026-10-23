import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Collection } from "@/data/collections";

import HomeView from "../HomeView";

/**
 * The board's two tabs.
 *
 * Worth a test rather than a read-through because the split is invisible in the
 * data: every collection arrives on the same list route, and only the `shared`
 * flag — the account's own answer from the service's validate route — decides
 * which tab a card belongs on. A card on the wrong tab reads as somebody else's
 * documents having become yours.
 */
const collection = (id: string, name: string, shared: boolean): Collection => ({
  id,
  knowledgeId: id,
  name,
  knowledgeEnabled: true,
  about: null,
  shared,
  updatedAt: "2026-08-20T10:00:00+00:00",
  createdAt: "2026-08-01T10:00:00+00:00",
});

const collections = [
  collection("k-mine", "Resumes", false),
  collection("k-theirs", "Contracts", true),
];

const mocks = vi.hoisted(() => ({
  useCollections: vi.fn(),
  push: vi.fn(),
  mutateAsync: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  copyText: vi.fn(),
}));

vi.mock("@/data/collections", () => ({
  useCollections: () => mocks.useCollections(),
  useCollectionStats: () => ({ data: undefined, isLoading: false }),
  useDeleteCollection: () => ({
    mutateAsync: mocks.mutateAsync,
    isPending: false,
  }),
}));

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({
    toast: { success: mocks.toastSuccess, error: mocks.toastError },
  }),
}));

vi.mock("@/lib/clipboard", () => ({ copyText: mocks.copyText }));

// The account cards read usage and credentials, which this screen's tabs have
// nothing to do with.
vi.mock("../AccountCards", () => ({ default: () => null }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

describe("HomeView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useCollections.mockReturnValue({
      collections,
      isLoading: false,
      isError: false,
    });
    mocks.mutateAsync.mockResolvedValue(undefined);
    mocks.copyText.mockResolvedValue(true);
  });

  it("draws the board's own outline while the collections load", () => {
    mocks.useCollections.mockReturnValue({
      collections: [],
      isLoading: true,
      isError: false,
    });
    render(<HomeView />);

    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Loading your collections")).toBeInTheDocument();
    // The create tile is not a placeholder: it needs no data, and on a new
    // account it is the only thing there is to do.
    expect(
      screen.getByRole("button", { name: /create document collection/i }),
    ).toBeInTheDocument();
    // And nothing claims a count it has not read yet.
    expect(screen.queryByText("Resumes")).not.toBeInTheDocument();
  });

  it("opens the create screen rather than a dialog", async () => {
    render(<HomeView />);
    await userEvent.click(
      screen.getByRole("button", { name: /create document collection/i }),
    );
    // A screen, not a modal: the steps are worth seeing before the first one is
    // committed to, and nothing here should open a dialog any more.
    expect(mocks.push).toHaveBeenCalledWith("/document-intelligence/create");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows only the collections this account owns on My collections", () => {
    render(<HomeView />);

    expect(screen.getByText("Resumes")).toBeInTheDocument();
    expect(screen.queryByText("Contracts")).not.toBeInTheDocument();
    // The create tile belongs to the account's own board.
    expect(
      screen.getByRole("button", { name: /create document collection/i }),
    ).toBeInTheDocument();
  });

  it("shows the invited ones, marked, on Shared with me", async () => {
    render(<HomeView />);
    await userEvent.click(screen.getByRole("tab", { name: /shared with me/i }));

    expect(screen.getByText("Contracts")).toBeInTheDocument();
    expect(screen.queryByText("Resumes")).not.toBeInTheDocument();
    expect(screen.getByText(/shared with you/i)).toBeInTheDocument();
    // Nothing to create here: this tab is other people's collections.
    expect(
      screen.queryByRole("button", { name: /create document collection/i }),
    ).not.toBeInTheDocument();
  });

  it("offers the artifact card actions without opening the card", async () => {
    render(<HomeView />);

    await userEvent.click(
      screen.getByRole("button", { name: "Settings for Resumes" }),
    );
    expect(mocks.push).toHaveBeenCalledWith(
      "/document-intelligence/settings?collection=k-mine",
    );
    expect(mocks.push).toHaveBeenCalledTimes(1);

    await userEvent.click(
      screen.getByRole("button", { name: "Delete Resumes" }),
    );
    expect(
      screen.getByRole("heading", { name: "Delete Resumes?" }),
    ).toBeInTheDocument();
    expect(mocks.push).toHaveBeenCalledTimes(1);

    await userEvent.click(
      screen.getByRole("button", { name: "Delete collection" }),
    );
    expect(mocks.mutateAsync).toHaveBeenCalledWith(collections[0]);
    expect(mocks.toastSuccess).toHaveBeenCalledWith("Resumes deleted.");
  });

  it("copies the id from a shared card", async () => {
    render(<HomeView />);
    await userEvent.click(screen.getByRole("tab", { name: /shared with me/i }));
    await userEvent.click(
      screen.getByRole("button", { name: "Copy the ID of Contracts" }),
    );

    expect(mocks.copyText).toHaveBeenCalledWith("k-theirs");
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      "Collection ID for Contracts copied.",
    );
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("keeps both tabs when nothing has been shared", async () => {
    collections.pop();
    render(<HomeView />);
    await userEvent.click(screen.getByRole("tab", { name: /shared with me/i }));

    expect(
      screen.getByText(/nothing shared with you yet/i),
    ).toBeInTheDocument();
  });
});
