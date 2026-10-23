import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ShareView from "../ShareView";

/**
 * The Share screen as a set of disclosure rows.
 *
 * What is worth testing here is not the styling but the two promises the shape
 * makes: nothing is rendered — and so nothing is fetched — for a row nobody has
 * opened, and the things a reader needs *before* deciding to open one (what the
 * row is, whether it is live, how many people are already in) are on the row.
 *
 * The appearance draft is the third: the editor and the preview are separate
 * rows, so the draft cannot live in either of them — closing the editor would
 * throw the edits away.
 */

const invited = vi.fn(() => [
  { email: "owner@futuresmart.ai", role: "owner", since: "" },
  { email: "kate@example.com", role: "viewer", since: "2026-08-01" },
]);

vi.mock("@/platform/hooks/useAuth", () => ({
  useAuth: () => ({ agentId: "k-1" }),
}));

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({ toast: { success: vi.fn(), error: vi.fn() } }),
}));

// The Activity tab reads the analytics question log through the scope the
// analytics screen injects; this suite is about the Sharing tab's rows.
vi.mock("@/platform/analytics/AnalyticsScope", () => ({
  AnalyticsScopeProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));
vi.mock("../ShareActivity", () => ({
  default: () => <p>What people asked</p>,
}));

/**
 * The two public flags and their allowlists, as `…/chat/config` stores them.
 * `save` is what the switch actually calls, so the assertions can read the body.
 */
const save = vi.fn(async () => ({ message: "ok" }));
let stored: Record<string, unknown> = {
  is_public: false,
  allowed_domains: [],
  fullscreen_is_public: true,
  fullscreen_allowed_domains: ["acme.com"],
};

vi.mock("@/data/chatConfig", () => ({
  useCollectionSettings: () => ({ data: stored, isLoading: false }),
  useSaveCollectionSettings: () => ({ mutateAsync: save, isPending: false }),
}));

vi.mock("@/data/session", () => ({
  useOwnerId: () => "owner@futuresmart.ai",
}));
vi.mock("@/data/people", () => ({
  usePeople: () => ({ data: invited(), isLoading: false }),
  usePeopleMutations: () => ({ invite: { mutateAsync: vi.fn() } }),
}));

// Both bodies that would talk to the service on mount. Their text is what the
// assertions look for when checking a row is (or is not) rendered.
vi.mock("../ShareApi", () => ({ default: () => <p>Generate a key</p> }));
vi.mock("../SharePeople", () => ({ default: () => <p>Invite by email</p> }));
vi.mock("../RemoteAsset", () => ({ default: () => null }));

vi.mock("@/data/widgetSettings", async () => {
  const actual = await vi.importActual<typeof import("@/data/widgetSettings")>(
    "@/data/widgetSettings",
  );
  return {
    ...actual,
    useWidgetSettings: () => ({
      settings: { data: undefined, isLoading: false },
      icons: { data: [], isLoading: false },
      avatars: { data: [], isLoading: false },
      save: { mutateAsync: vi.fn(), isPending: false },
      reset: { mutateAsync: vi.fn(), isPending: false },
    }),
  };
});

const row = (name: RegExp) => screen.getByRole("button", { name });

beforeEach(() => {
  invited.mockClear();
  save.mockClear();
  save.mockImplementation(async () => ({ message: "ok" }));
  stored = {
    is_public: false,
    allowed_domains: [],
    fullscreen_is_public: true,
    fullscreen_allowed_domains: ["acme.com"],
  };
});

describe("Share screen rows", () => {
  it("opens with every feature collapsed and none of the bodies rendered", () => {
    render(<ShareView />);

    for (const name of [
      /^API/,
      /chat bubble on your website/i,
      /full-page chat link/i,
      /^People/,
      /^Appearance/,
      /live preview/i,
    ])
      expect(row(name)).toHaveAttribute("aria-expanded", "false");

    expect(screen.queryByText("Generate a key")).toBeNull();
    expect(screen.queryByText("Invite by email")).toBeNull();
    expect(screen.getByText(/nothing open/i)).toBeInTheDocument();
  });

  it("says what each closed row is and what state it is in", () => {
    render(<ShareView />);

    expect(within(row(/^API/)).getByText("Live")).toBeInTheDocument();
    // Each hosted surface says whether it is switched on, from its own flag.
    expect(
      within(row(/chat bubble on your website/i)).getByText("Off"),
    ).toBeInTheDocument();
    expect(
      within(row(/full-page chat link/i)).getByText("On"),
    ).toBeInTheDocument();
    // The owner is not an invitation, so the count is the people invited.
    expect(within(row(/^People/)).getByText(/1 invited/)).toBeInTheDocument();
  });

  it("opens one row without opening its neighbours", async () => {
    render(<ShareView />);
    await userEvent.click(row(/^People/));

    expect(screen.getByText("Invite by email")).toBeInTheDocument();
    expect(screen.queryByText("Generate a key")).toBeNull();
    expect(row(/^API/)).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText(/1 open/)).toBeInTheDocument();
  });

  it("expands and collapses all six from the toolbar", async () => {
    render(<ShareView />);
    await userEvent.click(screen.getByRole("button", { name: /expand all/i }));

    expect(screen.getByText("Generate a key")).toBeInTheDocument();
    expect(screen.getByText("Invite by email")).toBeInTheDocument();
    expect(screen.getByText(/6 open/)).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /collapse all/i }),
    );
    expect(screen.queryByText("Generate a key")).toBeNull();
    expect(screen.getByText(/nothing open/i)).toBeInTheDocument();
  });

  it("switches the preview mode from the closed row and keeps it on open", async () => {
    render(<ShareView />);
    await userEvent.click(screen.getByRole("tab", { name: "Full page" }));

    // Choosing what the preview shows does not open the row it belongs to.
    expect(row(/live preview/i)).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("tab", { name: "Full page" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    await userEvent.click(row(/live preview/i));
    expect(screen.getByRole("tab", { name: "Full page" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("keeps an appearance edit when its row is closed, and shows it in the preview", async () => {
    render(<ShareView />);
    await userEvent.click(row(/^Appearance/));

    const heading = screen.getByLabelText(/heading/i, {
      selector: "#share-heading",
    });
    await userEvent.clear(heading);
    await userEvent.type(heading, "Ask the contracts");

    // The editor is unmounted while closed — a draft owned by it would go with
    // it, and the preview would have nothing to draw.
    await userEvent.click(row(/^Appearance/));
    await userEvent.click(row(/live preview/i));
    expect(screen.getByText("Ask the contracts")).toBeInTheDocument();

    await userEvent.click(row(/^Appearance/));
    expect(
      screen.getByLabelText(/heading/i, { selector: "#share-heading" }),
    ).toHaveValue("Ask the contracts");
  });
});

describe("Share sections", () => {
  it("opens on Sharing, with Activity one tab away", async () => {
    render(<ShareView />);
    expect(screen.getByRole("tab", { name: "Sharing" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByText("What people asked")).toBeNull();

    await userEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByText("What people asked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^People/ })).toBeNull();
  });

  it("keeps which rows were open across a trip to Activity", async () => {
    render(<ShareView />);
    await userEvent.click(row(/^People/));
    await userEvent.click(screen.getByRole("tab", { name: "Activity" }));
    await userEvent.click(screen.getByRole("tab", { name: "Sharing" }));

    expect(row(/^People/)).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Invite by email")).toBeInTheDocument();
  });
});

describe("Share channel switches", () => {
  it("switches a channel on without opening its row, and says so on the row", async () => {
    render(<ShareView />);
    await userEvent.click(
      screen.getByRole("switch", { name: /enable the website bubble/i }),
    );

    expect(save).toHaveBeenCalledWith({ is_public: true });
    // The switch is a sibling of the header button, so it cannot expand it.
    expect(row(/chat bubble on your website/i)).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(
      within(row(/chat bubble on your website/i)).getByText("On"),
    ).toBeInTheDocument();
  });

  it("keeps the two switches independent", async () => {
    render(<ShareView />);
    await userEvent.click(
      screen.getByRole("switch", { name: /enable the full-page link/i }),
    );

    expect(save).toHaveBeenCalledWith({ fullscreen_is_public: false });
    expect(
      within(row(/chat bubble on your website/i)).getByText("Off"),
    ).toBeInTheDocument();
  });

  it("puts the switch back when the service refuses the change", async () => {
    save.mockRejectedValueOnce(new Error("nope"));
    render(<ShareView />);
    await userEvent.click(
      screen.getByRole("switch", { name: /enable the website bubble/i }),
    );

    expect(
      screen.getByRole("switch", { name: /enable the website bubble/i }),
    ).toHaveAttribute("aria-checked", "false");
    expect(
      within(row(/chat bubble on your website/i)).getByText("Off"),
    ).toBeInTheDocument();
  });

  it("adds a domain to the channel's own list, host only", async () => {
    render(<ShareView />);
    await userEvent.click(row(/chat bubble on your website/i));
    await userEvent.type(
      screen.getByLabelText(/domain allowed to load this/i),
      "https://Shop.acme.com/pricing",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(save).toHaveBeenCalledWith({ allowed_domains: ["shop.acme.com"] });
  });

  it("removes a domain from the list it belongs to", async () => {
    render(<ShareView />);
    await userEvent.click(row(/full-page chat link/i));
    await userEvent.click(
      screen.getByRole("button", { name: /remove acme.com/i }),
    );

    expect(save).toHaveBeenCalledWith({ fullscreen_allowed_domains: [] });
  });
});
