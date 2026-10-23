import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GeneralPane from "../GeneralPane";

/**
 * The answer model the screen shows.
 *
 * A collection with no chat config of its own still answers — on the service's
 * own default model. The screen used to show an empty picker there, so anyone
 * saving anything else on the tab sent `llm_model: undefined` and the answer
 * model became whatever the service defaulted to next. What the catalogue calls
 * `default_model` is the model that node actually runs, so it is what the
 * picker preselects, and it is never named in this build: the backend moved it
 * from gpt-5.4-mini to gpt-5.6-luna without a UI release.
 */

const saveSpy = vi.fn();

let settings: Record<string, unknown> | undefined;
let providers: Array<{
  provider: string;
  defaultModel: string | null;
  models: string[];
}> = [];

vi.mock("@/data/chatConfig", () => ({
  useCollectionSettings: () => ({ data: settings, isLoading: false }),
  useAnswerModels: () => ({ data: providers }),
  useSaveCollectionSettings: () => ({
    mutateAsync: saveSpy,
    isPending: false,
  }),
}));

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({
    toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  }),
}));

const collectionId = "5b6a1f2e-2c4b-4a9d-9a1e-2f0c7d8e4b31";

const catalogue = [
  {
    provider: "openai",
    defaultModel: "gpt-5.6-luna",
    models: ["gpt-5.6-luna", "gpt-4o"],
  },
  {
    provider: "anthropic",
    defaultModel: "claude-sonnet-4-6",
    models: ["claude-sonnet-4-6"],
  },
];

const paneFor = () => <GeneralPane collectionId={collectionId} />;

describe("GeneralPane — answer model", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    saveSpy.mockResolvedValue({ message: "Settings saved" });
    providers = catalogue;
    settings = undefined;
  });

  it("preselects the catalogue's default model when the collection has no chat config", async () => {
    settings = { configured: false };
    render(paneFor());

    await waitFor(() =>
      expect(screen.getByText("gpt-5.6-luna")).toBeInTheDocument(),
    );
    expect(screen.getByText("openai")).toBeInTheDocument();
  });

  it("sends that model on save, so the shown model is the stored one", async () => {
    settings = { configured: false };
    render(paneFor());

    await waitFor(() =>
      expect(screen.getByText("gpt-5.6-luna")).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(saveSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        llm_provider: "openai",
        llm_model: "gpt-5.6-luna",
      }),
    );
  });

  it("leaves a stored model alone", async () => {
    settings = {
      configured: true,
      llm_provider: "openai",
      llm_model: "gpt-4o",
    };
    render(paneFor());

    await waitFor(() => expect(screen.getByText("gpt-4o")).toBeInTheDocument());
    expect(screen.queryByText("gpt-5.6-luna")).not.toBeInTheDocument();
  });

  /** No catalogue means no claim: the collection keeps the model it has. */
  it("invents nothing when the catalogue could not be read", async () => {
    providers = [];
    settings = { configured: false };
    render(paneFor());

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(saveSpy).toHaveBeenCalledWith(
      expect.objectContaining({ llm_model: undefined }),
    );
  });
});
