import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Credential, ProviderOptions } from "@/data/credentials";

import CredentialFormModal from "../CredentialFormModal";

/**
 * The add / edit credential dialog.
 *
 * Two things here have already gone wrong in front of a user, and both are
 * pinned below: a picker built only from the catalogue route, which rendered
 * empty and offered no way to add a key at all when that request did not
 * answer; and a key field that accepted anything, when the service rejects
 * anything under ten characters.
 */

const save = vi.fn();
const success = vi.fn();

let credentials: Credential[] = [];
let providerOptions: ProviderOptions | undefined;
let saving = false;

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({ toast: { success, error: vi.fn() } }),
}));

vi.mock("@/data/credentials", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/data/credentials")>();
  return {
    ...actual,
    useCredentials: () => ({ data: credentials, isLoading: false }),
    useCredentialProviders: () => ({ data: providerOptions }),
    useSaveCredential: () => ({ mutateAsync: save, isPending: saving }),
  };
});

const openai: Credential = {
  provider: "openai",
  mask: "sk-…4f2a",
  is_valid: true,
  updated_at: "2026-08-18T18:14:51.376203",
};

const open = (props: { editProvider?: string; onClose?: () => void } = {}) =>
  render(
    <CredentialFormModal
      open
      onClose={props.onClose ?? vi.fn()}
      editProvider={props.editProvider}
    />,
  );

const providerSelect = () => screen.getByLabelText(/Provider service/);
const keyField = () => screen.getByLabelText(/API key/);
const addButton = () => screen.getByRole("button", { name: "Add Credential" });

beforeEach(() => {
  vi.clearAllMocks();
  credentials = [openai];
  saving = false;
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
  save.mockResolvedValue({ status: "success" });
});

describe("the provider picker", () => {
  it("offers exactly what the catalogue returned, grouped", () => {
    credentials = [];
    open();
    const options = within(providerSelect())
      .getAllByRole("option")
      .map((option) => option.textContent);

    expect(options).toEqual([
      "Select a provider",
      "openai",
      "anthropic",
      "google",
      "groq",
      "deepseek",
      "minimax",
      "llamaparse",
      "firecrawl",
    ]);

    const groups = providerSelect().querySelectorAll("optgroup");
    expect(Array.from(groups).map((group) => group.label)).toEqual([
      "Answer providers",
      "Document & website reading",
    ]);
  });

  /**
   * One key per provider per account. A provider that already has one stays in
   * the list, disabled and saying where its key lives, rather than vanishing.
   */
  it("disables a provider that already has a key, and says why", () => {
    open();
    const taken = screen.getByRole("option", { name: /openai/ });
    expect(taken).toBeDisabled();
    expect(taken.textContent).toContain("already added, edit it on the board");
    expect(screen.getByRole("option", { name: /anthropic/ })).toBeEnabled();
  });

  /**
   * The catalogue is the only source, so an unread one leaves nothing to
   * choose. The dialog has to say that outright — an empty dropdown with a
   * "Select a provider" placeholder reads as "your account has no providers".
   */
  it("says so plainly when the catalogue could not be read", () => {
    providerOptions = undefined;
    open();
    expect(within(providerSelect()).getAllByRole("option")).toHaveLength(1);
    expect(providerSelect()).toBeDisabled();
    expect(screen.getByText("No providers available")).toBeInTheDocument();
  });

  it("offers a provider the service has just added, with no release here", () => {
    providerOptions = {
      llm_providers: ["openai", "brand-new-llm"],
      document_and_web_providers: [],
    };
    open();
    const options = within(providerSelect())
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toContain("brand-new-llm");
    expect(options.filter((name) => name?.startsWith("openai"))).toHaveLength(
      1,
    );
  });

  /** Editing is about the key: the provider is what identifies the row. */
  it("fixes the provider when a stored key is being replaced", () => {
    open({ editProvider: "openai" });
    expect(providerSelect()).toBeDisabled();
    expect(providerSelect()).toHaveValue("openai");
    expect(
      screen.getByRole("button", { name: "Save changes" }),
    ).toBeInTheDocument();
  });
});

describe("adding a key", () => {
  it("will not submit without a provider", async () => {
    credentials = [];
    open();
    await userEvent.type(keyField(), "sk-ant-long-enough");
    expect(addButton()).toBeDisabled();
  });

  /** `api_key` carries `minLength: 10` — a shorter key is a 422, not a save. */
  it("refuses a key shorter than the service accepts, and says why", async () => {
    open();
    await userEvent.selectOptions(providerSelect(), "anthropic");
    await userEvent.type(keyField(), "sk-short");

    expect(addButton()).toBeDisabled();
    expect(
      screen.getByText(/at least 10 characters — this one is too short/),
    ).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("accepts the key once it is long enough", async () => {
    open();
    await userEvent.selectOptions(providerSelect(), "anthropic");
    await userEvent.type(keyField(), "sk-ant-not-a-real-key");

    expect(addButton()).toBeEnabled();
    expect(
      screen.getByText(/first four and last two characters only/),
    ).toBeInTheDocument();
  });

  it("sends the provider and the trimmed key", async () => {
    open();
    await userEvent.selectOptions(providerSelect(), "anthropic");
    await userEvent.type(keyField(), "  sk-ant-not-a-real-key  ");
    await userEvent.click(addButton());

    expect(save).toHaveBeenCalledWith({
      provider: "anthropic",
      apiKey: "sk-ant-not-a-real-key",
    });
  });

  it("confirms in the service's own terms and closes", async () => {
    const onClose = vi.fn();
    open({ onClose });
    await userEvent.selectOptions(providerSelect(), "anthropic");
    await userEvent.type(keyField(), "sk-ant-not-a-real-key");
    await userEvent.click(addButton());

    await waitFor(() =>
      expect(success).toHaveBeenCalledWith(
        expect.stringContaining("anthropic credential added"),
      ),
    );
    expect(onClose).toHaveBeenCalled();
  });

  /** A rejected save must not claim success, close, or wipe what was typed. */
  it("keeps the form when the save fails", async () => {
    const onClose = vi.fn();
    save.mockRejectedValue(new Error("nope"));
    open({ onClose });
    await userEvent.selectOptions(providerSelect(), "anthropic");
    await userEvent.type(keyField(), "sk-ant-not-a-real-key");
    await userEvent.click(addButton());

    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(success).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(keyField()).toHaveValue("sk-ant-not-a-real-key");
  });

  it("says it is working and blocks a second submit while saving", async () => {
    saving = true;
    open();
    await userEvent.selectOptions(providerSelect(), "anthropic");
    await userEvent.type(keyField(), "sk-ant-not-a-real-key");

    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  });
});

describe("the dialog itself", () => {
  it("renders nothing until it is opened", () => {
    render(<CredentialFormModal open={false} onClose={vi.fn()} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes on Cancel without saving", async () => {
    const onClose = vi.fn();
    open({ onClose });
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
  });

  it("never renders the key as readable text", async () => {
    open();
    await userEvent.type(keyField(), "sk-ant-not-a-real-key");
    expect(keyField()).toHaveAttribute("type", "password");
  });
});
