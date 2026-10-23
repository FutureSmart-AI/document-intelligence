import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CreateStepper from "../CreateStepper";

/**
 * What a full account is told, and what a website source actually sends.
 *
 * Both behaviours were covered against the create *dialog*, which this screen
 * replaced; the dialog is gone and these came with the flow rather than with
 * the file. The refusal one matters most: the service answers with an object
 * rather than a sentence, and a catch that swallows it is a Create button that
 * does nothing at all — twice, three times, with no way to find out why.
 */

const mocks = vi.hoisted(() => ({
  createCollection: vi.fn(),
  generateSchema: vi.fn(),
  ingestWebsite: vi.fn(),
  ingestFiles: vi.fn(),
  ingestPlainText: vi.fn(),
  push: vi.fn(),
  open: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/data/createCollection", () => ({
  useCreateCollection: () => ({
    mutateAsync: mocks.createCollection,
    isPending: false,
  }),
}));

vi.mock("@/data/fieldCatalog", async () => {
  const actual = await vi.importActual<typeof import("@/data/fieldCatalog")>(
    "@/data/fieldCatalog",
  );
  return {
    ...actual,
    useFieldTemplates: () => ({
      data: [
        {
          key: "resume",
          display_name: "Resumes / CVs",
          description: "People applying for a role.",
          fields: [
            {
              field_name: "candidate_name",
              description: "Full name.",
              field_type: "str",
              field_type_label: "Text",
            },
          ],
        },
      ],
      isLoading: false,
      isError: false,
    }),
    useFieldTypes: () => ({
      data: [
        { field_type: "str", field_type_label: "Text", requires_schema: false },
      ],
      isLoading: false,
      isError: false,
    }),
  };
});

vi.mock("@/data/schemaDraft", () => ({
  generateSchema: (...args: unknown[]) => mocks.generateSchema(...args),
  previewSample: vi.fn(),
  uploadSampleForSchema: vi.fn(),
}));

vi.mock("@/data/ingest", () => ({
  ingestWebsite: (...args: unknown[]) => mocks.ingestWebsite(...args),
  ingestFiles: (...args: unknown[]) => mocks.ingestFiles(...args),
  ingestPlainText: (...args: unknown[]) => mocks.ingestPlainText(...args),
}));

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({
    toast: {
      success: vi.fn(),
      info: vi.fn(),
      error: mocks.error,
      open: mocks.open,
      destroy: vi.fn(),
    },
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

const K = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";

const quotaRefusal = {
  response: {
    status: 403,
    data: {
      error: "quota_exceeded",
      message:
        "You have reached one of your account's usage limits. Delete unused items or upgrade your plan.",
      remaining_quota: { agents: -1, conversations: 100, messages: 500 },
      would_exceed: [
        { limit_name: "max_agents", current: 10, limit: 10, requested: 1 },
      ],
    },
  },
};

/** Through all three steps to the button that commits them. */
async function toTheEnd() {
  render(<CreateStepper />);
  await userEvent.click(screen.getByRole("button", { name: /Resumes \/ CVs/ }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  // Trying a document is optional; these specs are about what happens after.
  await userEvent.click(screen.getByRole("button", { name: /looks right/i }));
}

describe("CreateStepper — a full account", () => {
  beforeEach(() => vi.clearAllMocks());

  it("says which allowance refused, and links to the Limits tab", async () => {
    mocks.createCollection.mockRejectedValue(quotaRefusal);
    await toTheEnd();
    await userEvent.click(
      screen.getByRole("button", { name: /create it empty/i }),
    );

    expect(mocks.open).toHaveBeenCalledTimes(1);
    const toast = mocks.open.mock.calls[0][0];
    expect(toast.type).toBe("error");

    render(toast.content);
    expect(
      screen.getByText(/reached one of your account's usage limits/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Agents: 10 of 10 used\./)).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /view plan limits/i }),
    );
    expect(mocks.push).toHaveBeenCalledWith(
      "/document-intelligence/usage?tab=limits",
    );
  });

  /**
   * The schema call is metered too, and it is refused *before* it spends
   * anything. Step one has to stop there rather than walking somebody through
   * three more steps to be refused again at the end.
   */
  it("stops at step one when the schema call is refused", async () => {
    mocks.generateSchema.mockRejectedValue(quotaRefusal);

    render(<CreateStepper />);
    await userEvent.type(
      screen.getByLabelText(/what are these documents about/i),
      "Resumes sent in for engineering roles.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(mocks.open).toHaveBeenCalledTimes(1);
    // Still on step one: the fields could not be worked out, so there is
    // nothing to confirm.
    expect(screen.getAllByRole("listitem")[0]).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(mocks.createCollection).not.toHaveBeenCalled();
  });

  it("says the service's own sentence for any other failure", async () => {
    mocks.createCollection.mockRejectedValue({
      response: { status: 422, data: { detail: "That name is already taken" } },
    });
    await toTheEnd();
    await userEvent.click(
      screen.getByRole("button", { name: /create it empty/i }),
    );

    expect(mocks.open).not.toHaveBeenCalled();
    expect(mocks.error).toHaveBeenCalledWith(
      "That name is already taken",
      expect.anything(),
    );
  });
});

describe("CreateStepper — a website as the first document", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createCollection.mockResolvedValue({
      collectionId: K,
      knowledgeId: K,
      createdFields: ["candidate_name"],
      fieldsFailed: false,
    });
    mocks.ingestWebsite.mockResolvedValue({ status: "processing" });
  });

  it("keeps the depth control on screen, enabled only for a crawl", async () => {
    await toTheEnd();
    await userEvent.click(screen.getByRole("button", { name: "Website" }));

    // On screen either way: a control that appears only after another select is
    // changed reads as missing.
    expect(screen.getByLabelText(/how many levels/i)).toBeDisabled();
    await userEvent.selectOptions(
      screen.getByLabelText(/what to read/i),
      "crawl",
    );
    expect(screen.getByLabelText(/how many levels/i)).toBeEnabled();
  });

  it("sends the method and the depth it was given", async () => {
    await toTheEnd();
    await userEvent.click(screen.getByRole("button", { name: "Website" }));
    await userEvent.type(
      screen.getByPlaceholderText("https://example.com/page"),
      "https://example.com/page",
    );
    await userEvent.selectOptions(
      screen.getByLabelText(/what to read/i),
      "crawl",
    );
    await userEvent.selectOptions(
      screen.getByLabelText(/how many levels/i),
      "2",
    );

    await userEvent.click(
      screen.getByRole("button", { name: /create and add my documents/i }),
    );

    expect(mocks.ingestWebsite).toHaveBeenCalledWith(
      K,
      "https://example.com/page",
      { method: "crawl", maxDepth: 2 },
    );
  });
});
