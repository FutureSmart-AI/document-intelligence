import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Credential, ProviderOptions } from "@/data/credentials";

import CredentialsView from "../CredentialsView";

/**
 * The credentials board.
 *
 * What it must never do is misreport whose key is answering: an account with a
 * key of its own that reads as "platform", or a board that claims no keys while
 * the request is still in flight, both send someone to a provider dashboard for
 * nothing. Deleting is guarded here too — a stored key cannot be read back, so
 * a mis-click costs a trip to the provider for a new one.
 */

const remove = vi.fn();
const success = vi.fn();

let credentials: Credential[] = [];
let providerOptions: ProviderOptions | undefined;
let credentialsLoading = false;

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({ toast: { success, error: vi.fn() } }),
}));

vi.mock("@/data/credentials", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/data/credentials")>();
  return {
    ...actual,
    useCredentials: () => ({
      data: credentials,
      isLoading: credentialsLoading,
    }),
    useCredentialProviders: () => ({ data: providerOptions }),
    useSaveCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteCredential: () => ({ mutateAsync: remove, isPending: false }),
  };
});

const openai: Credential = {
  provider: "openai",
  mask: "sk-…4f2a",
  is_valid: true,
  updated_at: "2026-08-18T18:14:51.376203",
};

beforeEach(() => {
  vi.clearAllMocks();
  credentials = [openai];
  credentialsLoading = false;
  providerOptions = {
    llm_providers: [
      "openai",
      "anthropic",
      "google",
      "groq",
      "deepseek",
      "minimax",
    ],
    document_and_web_providers: ["llamaparse", "firecrawl"],
  };
  remove.mockResolvedValue({
    status: "success",
    message: "Credential removed",
  });
});

describe("the configured providers", () => {
  it("covers the stored key until the eye is clicked, and never shows more than the mask", async () => {
    render(<CredentialsView />);
    const row = screen.getByRole("row", { name: /openai/ });
    expect(within(row).queryByText("sk-…4f2a")).not.toBeInTheDocument();

    await userEvent.click(
      within(row).getByRole("button", { name: /Show the openai key/ }),
    );
    // The service answers with a mask and nothing else, so this is all there is
    // to uncover — there is no plaintext key on this page to leak.
    expect(within(row).getByText("sk-…4f2a")).toBeInTheDocument();

    await userEvent.click(
      within(row).getByRole("button", { name: /Hide the openai key/ }),
    );
    expect(within(row).queryByText("sk-…4f2a")).not.toBeInTheDocument();
  });

  it("dates each key the day the service last stored it", () => {
    render(<CredentialsView />);
    const row = screen.getByRole("row", { name: /openai/ });
    expect(within(row).getByText("Aug 18, 2026")).toBeInTheDocument();
  });

  it("flags a key the service could not validate", () => {
    credentials = [{ ...openai, is_valid: false }];
    render(<CredentialsView />);
    expect(screen.getByText("Rejected")).toBeInTheDocument();
  });

  it("invites a first key when the account has none", () => {
    credentials = [];
    render(<CredentialsView />);
    expect(screen.getByText("No API keys configured yet")).toBeInTheDocument();
  });

  it("waits rather than claiming the account has no keys", () => {
    credentialsLoading = true;
    credentials = [];
    render(<CredentialsView />);
    expect(screen.getByText("Reading…")).toBeInTheDocument();
    expect(
      screen.queryByText("No API keys configured yet"),
    ).not.toBeInTheDocument();
  });

  it("counts the keys, and which of them answer questions", () => {
    credentials = [openai, { ...openai, provider: "llamaparse" }];
    render(<CredentialsView />);
    expect(screen.getByText("2 providers configured")).toBeInTheDocument();
    // One answer provider, one document reader.
    const stat = screen.getByText("Providers that write your answers")
      .parentElement as HTMLElement;
    expect(within(stat).getByText("1")).toBeInTheDocument();
  });
});

describe("deleting a key", () => {
  it("asks before removing anything", async () => {
    render(<CredentialsView />);
    await userEvent.click(
      screen.getByRole("button", { name: /Delete the openai credential/ }),
    );
    expect(remove).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: /Delete the openai credential\?/ }),
    ).toBeInTheDocument();
  });

  it("removes the key on confirmation and repeats what the service said", async () => {
    render(<CredentialsView />);
    await userEvent.click(
      screen.getByRole("button", { name: /Delete the openai credential/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Delete credential" }),
    );

    expect(remove).toHaveBeenCalledWith("openai");
    await waitFor(() =>
      expect(success).toHaveBeenCalledWith("Credential removed"),
    );
  });

  it("words the removal itself when the service sends no message", async () => {
    remove.mockResolvedValue({ status: "success" });
    render(<CredentialsView />);
    await userEvent.click(
      screen.getByRole("button", { name: /Delete the openai credential/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Delete credential" }),
    );
    await waitFor(() =>
      expect(success).toHaveBeenCalledWith(
        expect.stringContaining("back on FutureSmart platform credentials"),
      ),
    );
  });

  it("keeps the key when the confirmation is dismissed", async () => {
    render(<CredentialsView />);
    await userEvent.click(
      screen.getByRole("button", { name: /Delete the openai credential/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(remove).not.toHaveBeenCalled();
  });
});

describe("which key each service is using", () => {
  /** The question the screen exists to answer: what is in effect right now. */
  it("marks the provider with a stored key, and the rest as platform", () => {
    render(<CredentialsView />);
    const grid = screen.getByText("Which key each service is using")
      .nextElementSibling as HTMLElement;

    expect(within(grid).getAllByText("Your key")).toHaveLength(1);
    // Seven of the eight providers are still on the platform's keys.
    expect(within(grid).getAllByText("Platform")).toHaveLength(7);
  });

  it("matches a stored provider whatever case the service returns it in", () => {
    credentials = [{ ...openai, provider: "OpenAI" }];
    render(<CredentialsView />);
    const grid = screen.getByText("Which key each service is using")
      .nextElementSibling as HTMLElement;
    expect(within(grid).getAllByText("Your key")).toHaveLength(1);
  });

  /**
   * With no catalogue there is no list of services to draw — the board says so
   * rather than inventing one out of a copy of the provider names.
   */
  it("says the catalogue is unavailable rather than listing a guess", () => {
    providerOptions = undefined;
    render(<CredentialsView />);
    expect(
      screen.getByText("Which key each service is using"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Platform")).not.toBeInTheDocument();
    expect(
      screen.getByText("The service did not list any providers."),
    ).toBeInTheDocument();
  });
});
