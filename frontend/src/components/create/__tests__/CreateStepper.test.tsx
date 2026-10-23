import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CreateStepper from "../CreateStepper";

/**
 * Creating a collection, driven end to end.
 *
 * What a render test is the only cheap way to hold, and what these specs are
 * therefore about:
 *
 * - the kinds on offer and the types in the dropdown are the **service's**, so
 *   a catalogue that grows shows up here rather than being frozen in a
 *   constant;
 * - the list that leaves this screen is the list that was on it — the fields
 *   are sent as fields, not folded back into prose for something else to
 *   re-derive;
 * - nothing is created until the last step, and the sample step proves the
 *   fields work before that point;
 * - a name clash, a table with no columns, and a document that cannot be read
 *   are all stopped or explained here rather than by the service.
 */

const mocks = vi.hoisted(() => ({
  createCollection: vi.fn(),
  generateSchema: vi.fn(),
  previewSample: vi.fn(),
  ingestFiles: vi.fn(),
  ingestPlainText: vi.fn(),
  ingestWebsite: vi.fn(),
  push: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  reportError: vi.fn(),
  templates: vi.fn(),
  fieldTypes: vi.fn(),
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
    useFieldTemplates: () => mocks.templates(),
    useFieldTypes: () => mocks.fieldTypes(),
  };
});

vi.mock("@/data/schemaDraft", () => ({
  generateSchema: (...args: unknown[]) => mocks.generateSchema(...args),
  previewSample: (...args: unknown[]) => mocks.previewSample(...args),
  uploadSampleForSchema: vi.fn(),
}));

vi.mock("@/data/ingest", () => ({
  ingestFiles: (...args: unknown[]) => mocks.ingestFiles(...args),
  ingestPlainText: (...args: unknown[]) => mocks.ingestPlainText(...args),
  ingestWebsite: (...args: unknown[]) => mocks.ingestWebsite(...args),
}));

vi.mock("@/lib/useApiError", () => ({
  useApiError: () => mocks.reportError,
}));

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({
    toast: { success: vi.fn(), error: mocks.error, info: mocks.info },
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

const K = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";

/** `GET /v1/field-templates`, trimmed to what this screen reads. */
const TEMPLATES = [
  {
    key: "resume",
    display_name: "Resumes / CVs",
    description: "People applying for a role.",
    default_collection_name: "Candidate Resumes",
    default_service_description: "Resumes sent in for open roles.",
    default_extraction_instructions:
      "These are resumes. Read the person's own words, not the job description they pasted in.",
    fields: [
      {
        field_name: "candidate_name",
        description: "Full name of the candidate.",
        field_type: "str",
        field_type_label: "Text",
      },
      {
        field_name: "years_of_experience",
        description: "Total years worked.",
        field_type: "int",
        field_type_label: "Number",
      },
    ],
  },
  {
    key: "invoice",
    display_name: "Invoices",
    description: "Bills and receipts.",
    fields: [
      {
        field_name: "invoice_number",
        description: "The number on the invoice.",
        field_type: "str",
        field_type_label: "Text",
      },
    ],
  },
  // "Something else" is `default` on the wire, and comes back with no fields.
  { key: "default", display_name: "Something else", fields: [] },
];

/** `GET /v1/field-types`, as published. */
const TYPES = [
  { field_type: "str", field_type_label: "Text", requires_schema: false },
  {
    field_type: "List[str]",
    field_type_label: "Text (multiple values)",
    requires_schema: false,
  },
  { field_type: "int", field_type_label: "Number", requires_schema: false },
  { field_type: "date", field_type_label: "Date", requires_schema: false },
  {
    field_type: "List[dict]",
    field_type_label: "Table",
    requires_schema: true,
  },
];

const query = <T,>(data: T, over: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isError: false,
  ...over,
});

/** The run control above the result column, not the one inside its note. */
const runButton = () =>
  document.querySelector('[data-demo="sample-run"]') as HTMLButtonElement;

const step1Continue = () => screen.getByRole("button", { name: "Continue" });
/** The fields step's own continue, whether or not a sample has been run. */
const step2Continue = () =>
  screen.getByRole("button", { name: /looks right|these fields work/i });

const nameInputs = () =>
  screen
    .getAllByLabelText("Field name")
    .map((input) => (input as HTMLInputElement).value);

/** Step one with the resume template picked, which is the fast path. */
async function toFields() {
  render(<CreateStepper />);
  await userEvent.click(screen.getByRole("button", { name: /Resumes \/ CVs/ }));
  await userEvent.click(step1Continue());
}

/**
 * Past the fields, to the last step.
 *
 * No sample is run: trying one is optional, and the specs below are about what
 * the fields and the documents do.
 */
async function toDocuments() {
  await userEvent.click(step2Continue());
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.templates.mockReturnValue(query(TEMPLATES));
  mocks.fieldTypes.mockReturnValue(query(TYPES));
  mocks.createCollection.mockResolvedValue({
    collectionId: K,
    knowledgeId: K,
    createdFields: ["candidate_name"],
    fieldsFailed: false,
  });
});

describe("CreateStepper — step one", () => {
  it("shows all three steps from the first one, with the first current", () => {
    render(<CreateStepper />);
    const steps = screen.getAllByRole("listitem");
    expect(steps).toHaveLength(3);
    expect(steps[0]).toHaveAttribute("aria-current", "step");
    expect(steps[1]).toHaveTextContent("Check the fields");
    expect(steps[2]).toHaveTextContent("Add documents");
  });

  /**
   * The kinds are the service's catalogue. Hardcoding them is what this screen
   * used to do, and a picker that cannot grow is wrong the day the backend
   * adds the sixth one.
   */
  it("offers the kinds the service publishes, not a built-in list", () => {
    render(<CreateStepper />);
    for (const entry of TEMPLATES)
      expect(
        screen.getByRole("button", { name: new RegExp(entry.display_name) }),
      ).toBeInTheDocument();
  });

  it("carries on without the catalogue when it cannot be loaded", async () => {
    mocks.templates.mockReturnValue(query([], { isError: true }));
    render(<CreateStepper />);

    expect(
      screen.getByText(/could not load the ready-made kinds/i),
    ).toBeInTheDocument();

    // Describing it yourself is the general path and needs none of it.
    await userEvent.type(
      screen.getByLabelText(/what are these documents about/i),
      "Invoices from suppliers.",
    );
    expect(step1Continue()).toBeEnabled();
  });

  it("will not continue until something has been said", async () => {
    render(<CreateStepper />);
    expect(step1Continue()).toBeDisabled();

    await userEvent.type(
      screen.getByLabelText(/what do you want pulled out/i),
      "the invoice number",
    );
    expect(step1Continue()).toBeEnabled();
  });

  /**
   * The three boxes are filled from the template's *published defaults*, not
   * from the card's own labels: `display_name` names the thing you clicked and
   * is not an answer to "what should this collection be called?".
   */
  it("fills all three boxes from the template's published wording", async () => {
    render(<CreateStepper />);
    await userEvent.click(
      screen.getByRole("button", { name: /Resumes \/ CVs/ }),
    );

    expect(screen.getByLabelText(/collection name/i)).toHaveValue(
      "Candidate Resumes",
    );
    expect(
      screen.getByLabelText(/what are these documents about/i),
    ).toHaveValue("Resumes sent in for open roles.");
    expect(screen.getByLabelText(/what do you want pulled out/i)).toHaveValue(
      TEMPLATES[0].default_extraction_instructions,
    );
  });

  /** A template that publishes none of them still fills what it can. */
  it("falls back to the card's own words when a template has no defaults", async () => {
    render(<CreateStepper />);
    await userEvent.click(
      screen.getByRole("button", { name: /Resumes \/ CVs/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: /Invoices/ }));

    expect(screen.getByLabelText(/collection name/i)).toHaveValue("Invoices");
    expect(
      screen.getByLabelText(/what are these documents about/i),
    ).toHaveValue("Bills and receipts.");
    // And the kind we just left does not leave its instructions behind.
    expect(screen.getByLabelText(/what do you want pulled out/i)).toHaveValue(
      "",
    );
  });

  it("never overwrites a name someone typed themselves", async () => {
    render(<CreateStepper />);
    const name = screen.getByLabelText(/collection name/i);
    await userEvent.type(name, "Q3 applications");

    await userEvent.click(
      screen.getByRole("button", { name: /Resumes \/ CVs/ }),
    );
    expect(name).toHaveValue("Q3 applications");
  });

  /** A template's fields are published with the catalogue — no second call. */
  it("takes a template's fields straight from the catalogue", async () => {
    await toFields();

    expect(nameInputs()).toEqual(["candidate_name", "years_of_experience"]);
    expect(mocks.generateSchema).not.toHaveBeenCalled();
  });

  /**
   * The schema call runs before any collection exists, and its answer is what
   * the board shows — proposals as rows, rejections as a note. Its verdicts on
   * the optional defaults are ignored: those fields are neither offered nor
   * created.
   */
  it("asks the service for fields when the collection is described in words", async () => {
    mocks.generateSchema.mockResolvedValue({
      proposed_fields: [
        {
          field_name: "vendor_name",
          description: "Who sent it.",
          field_type: "str",
        },
        // Already on the board from the template: the backend does not reject
        // a duplicate name, so the merge has to.
        { field_name: "candidate_name", field_type: "str" },
      ],
      default_field_verdicts: [
        { field_name: "topics", keep: true, reason: "Useful for grouping." },
        { field_name: "entities", keep: false, reason: "Rarely carried." },
      ],
      rejected_fields: [
        { field_name: "salary", reason: "Not in the documents." },
      ],
    });

    render(<CreateStepper />);
    await userEvent.click(
      screen.getByRole("button", { name: /Resumes \/ CVs/ }),
    );
    // Their own words, in place of the template's: the template's published
    // fields answer its own instructions, so only a change is worth a call.
    const wanted = screen.getByLabelText(/what do you want pulled out/i);
    await userEvent.clear(wanted);
    await userEvent.type(wanted, "who sent it");
    await userEvent.click(step1Continue());

    expect(mocks.generateSchema).toHaveBeenCalledWith(
      expect.objectContaining({
        collectionName: "Candidate Resumes",
        serviceDescription: "Resumes sent in for open roles.",
        extractionInstructions: "who sent it",
      }),
    );

    // No `topics`, no `entities`: the verdicts came back and went nowhere.
    expect(nameInputs()).toEqual([
      "candidate_name",
      "years_of_experience",
      "vendor_name",
    ]);
    expect(
      screen.getByText(/salary \(Not in the documents\.\)/),
    ).toBeInTheDocument();
  });

  /**
   * A refused schema is not a dead end: a template's fields still stand, and
   * an empty board can be filled in by hand.
   */
  it("moves on with what it has when the schema call fails", async () => {
    mocks.generateSchema.mockRejectedValue(new Error("500"));

    render(<CreateStepper />);
    await userEvent.click(
      screen.getByRole("button", { name: /Resumes \/ CVs/ }),
    );
    await userEvent.type(
      screen.getByLabelText(/what do you want pulled out/i),
      "anything",
    );
    await userEvent.click(step1Continue());

    expect(mocks.reportError).toHaveBeenCalled();
    expect(nameInputs()).toEqual(["candidate_name", "years_of_experience"]);
  });
});

describe("CreateStepper — the field board", () => {
  /**
   * The dropdown is the catalogue, in the service's own words. A label this
   * app invented is a label that disagrees with what Settings prints for the
   * same field.
   */
  it("offers the types the service publishes", async () => {
    await toFields();

    const options = within(screen.getAllByRole("row")[1])
      .getByRole("combobox")
      .querySelectorAll("option");
    // The catalogue and nothing else: every row names a type, so there is no
    // "work it out" placeholder to pick by accident.
    expect([...options].map((option) => option.textContent)).toEqual(
      TYPES.map((type) => type.field_type_label),
    );
  });

  it("keeps the type the service gave each field", async () => {
    await toFields();

    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByRole("combobox")).toHaveValue("str");
    expect(within(rows[1]).getByRole("combobox")).toHaveValue("int");
  });

  /**
   * `requires_schema` is the flag to branch on, not a hardcoded pair of type
   * names — and a table with no columns is a field the service will create and
   * nothing will ever fill.
   */
  it("opens a column builder for a type that needs one, and insists on a column", async () => {
    await toFields();

    const row = screen.getAllByRole("row")[1];
    await userEvent.selectOptions(
      within(row).getByRole("combobox"),
      "List[dict]",
    );

    expect(
      screen.getByText(/one row per entry — its columns/i),
    ).toBeInTheDocument();
    expect(step2Continue()).toBeDisabled();
    expect(
      screen.getByText(/every sub-field needs a name/i),
    ).toBeInTheDocument();

    await userEvent.type(
      screen.getByLabelText(/sub-field 1 of candidate_name/i),
      "company_name",
    );
    expect(step2Continue()).toBeEnabled();
  });

  /** A table's columns cannot themselves be tables. */
  it("never offers a nested table as a column type", async () => {
    await toFields();

    const row = screen.getAllByRole("row")[1];
    await userEvent.selectOptions(
      within(row).getByRole("combobox"),
      "List[dict]",
    );

    const columnType = screen.getByLabelText(/what .* is read as/i, {
      selector: "select.w-32",
    });
    expect(
      [...columnType.querySelectorAll("option")].map((o) => o.value),
    ).toEqual(["str", "List[str]", "int", "date"]);
  });

  it("stops two fields sharing a name, and says how many to fix", async () => {
    await toFields();
    const [first, second] = screen.getAllByLabelText("Field name");
    await userEvent.clear(second);
    await userEvent.type(second, (first as HTMLInputElement).value);

    expect(step2Continue()).toBeDisabled();
    expect(screen.getAllByText(/already called/i).length).toBeGreaterThan(0);
  });

  it("drops a field from the board when it is removed", async () => {
    await toFields();
    await userEvent.click(
      screen.getByRole("button", { name: /do not pull out candidate_name/i }),
    );
    expect(nameInputs()).toEqual(["years_of_experience"]);
  });

  it("adds a blank field, ready to be renamed", async () => {
    await toFields();
    await userEvent.click(screen.getByRole("button", { name: /add a field/i }));
    expect(nameInputs()).toContain("new_field");
  });

  /**
   * A row somebody started and did not finish is not a mistake to be stopped
   * over: it says what it is missing, is left out of the create, and the step
   * carries on. Only a conflict between fields that *were* written blocks.
   */
  it("leaves an unfinished field out instead of holding the step up", async () => {
    await toFields();
    await userEvent.click(screen.getByRole("button", { name: /add a field/i }));

    expect(
      screen.getByText(
        /say what this field should capture, or it is left out/i,
      ),
    ).toBeInTheDocument();
    expect(step2Continue()).toBeEnabled();

    await toDocuments();
    expect(mocks.info).toHaveBeenCalledWith(
      expect.stringMatching(/leaving out "new_field"/i),
    );

    await userEvent.click(
      screen.getByRole("button", { name: /create it empty/i }),
    );
    const sent = mocks.createCollection.mock.calls[0][0];
    expect(
      sent.fields.map((f: { field_name: string }) => f.field_name),
    ).not.toContain("new_field");
  });

  /**
   * The service's optional defaults are not this screen's to offer. The config
   * PUT already carries `seed_optional_defaults: false`, so a row for one here
   * would be a field somebody confirmed without ever asking for it.
   */
  it("never puts the service's optional defaults on the board", async () => {
    mocks.generateSchema.mockResolvedValue({
      proposed_fields: [{ field_name: "vendor_name", field_type: "str" }],
      default_field_verdicts: [
        { field_name: "topics", keep: true, reason: "Useful for grouping." },
        { field_name: "chunk_topic", keep: true },
      ],
      rejected_fields: [],
    });

    render(<CreateStepper />);
    await userEvent.type(
      screen.getByLabelText(/what are these documents about/i),
      "Invoices.",
    );
    await userEvent.click(step1Continue());

    expect(nameInputs()).toEqual(["vendor_name"]);
    expect(screen.queryByDisplayValue("topics")).not.toBeInTheDocument();
  });
});

describe("CreateStepper — how we should read these documents", () => {
  const openBox = () =>
    userEvent.click(
      screen.getByRole("button", {
        name: /how we should read these documents/i,
      }),
    );

  const instructionsBox = () =>
    screen.getByLabelText(/how we should read these documents/i);

  /**
   * The instructions are a property of the collection, not of a step, and they
   * are the only input still editable past step one — so both of the steps
   * that can act on them open the same box against the same value.
   */
  it("opens on the field board, holding what step one collected", async () => {
    await toFields();
    await openBox();

    expect(instructionsBox()).toHaveValue(
      TEMPLATES[0].default_extraction_instructions,
    );
  });

  it("keeps what was typed in it across the steps", async () => {
    await toFields();
    await openBox();
    await userEvent.clear(instructionsBox());
    await userEvent.type(instructionsBox(), "Read the left column first.");

    await userEvent.click(step2Continue());
    await userEvent.click(screen.getByRole("button", { name: /← back/i }));

    // Still open, and still holding the edit: the instructions belong to the
    // collection, not to a visit to the step.
    expect(instructionsBox()).toHaveValue("Read the left column first.");
  });

  /**
   * A box you can type in that quietly does nothing is worse than no box:
   * editing the instructions re-asks the service, and the board is rebuilt
   * from that answer — the picked template's published fields plus what the
   * call now proposes.
   */
  it("re-reads the fields from edited instructions", async () => {
    mocks.generateSchema.mockResolvedValue({
      proposed_fields: [
        { field_name: "notice_period", description: "When they can start." },
      ],
      default_field_verdicts: [],
      rejected_fields: [],
    });

    await toFields();
    expect(mocks.generateSchema).not.toHaveBeenCalled();

    await openBox();
    await userEvent.type(instructionsBox(), " Also when they can start.");
    await userEvent.click(
      screen.getByRole("button", { name: /re-read the fields/i }),
    );

    expect(mocks.generateSchema).toHaveBeenCalledWith(
      expect.objectContaining({
        collectionName: "Candidate Resumes",
        extractionInstructions: expect.stringContaining(
          "Also when they can start.",
        ),
      }),
    );
    expect(nameInputs()).toEqual([
      "candidate_name",
      "years_of_experience",
      "notice_period",
    ]);
  });

  /**
   * The board is the service's current answer, not every answer it has ever
   * given. Merging kept rows alive that nothing had proposed since the brief
   * was reworded — and they were created with the collection all the same.
   */
  it("rebuilds the board from the new answer instead of adding to it", async () => {
    mocks.generateSchema.mockResolvedValueOnce({
      proposed_fields: [{ field_name: "notice_period" }],
      rejected_fields: [],
    });

    await toFields();
    await openBox();
    await userEvent.type(instructionsBox(), " Also when they can start.");
    await userEvent.click(
      screen.getByRole("button", { name: /re-read the fields/i }),
    );
    expect(nameInputs()).toContain("notice_period");

    // A hand-added row is on the board too, and the next answer does not
    // propose either it or `notice_period`.
    await userEvent.click(screen.getByRole("button", { name: /add a field/i }));
    expect(nameInputs()).toContain("new_field");

    mocks.generateSchema.mockResolvedValueOnce({
      proposed_fields: [{ field_name: "expected_salary" }],
      rejected_fields: [],
    });
    await userEvent.type(instructionsBox(), " And what they want paid.");
    await userEvent.click(
      screen.getByRole("button", { name: /re-read the fields/i }),
    );

    expect(nameInputs()).toEqual([
      "candidate_name",
      "years_of_experience",
      "expected_salary",
    ]);
  });

  /** The template's own instructions are answered by its published fields. */
  it("does not spend a call on instructions nobody changed", async () => {
    await toFields();
    expect(mocks.generateSchema).not.toHaveBeenCalled();
    expect(nameInputs()).toEqual(["candidate_name", "years_of_experience"]);
  });
});

describe("CreateStepper — the sample step", () => {
  const preview = {
    extracted: { candidate_name: "Ananya Rao" },
    fields_run: [
      { field_name: "candidate_name", status: "extracted" as const },
      { field_name: "years_of_experience", status: "not_found" as const },
    ],
    suggested_fields: [
      {
        field_name: "linkedin_url",
        description: "Profile link.",
        field_type: "str",
        reason: "Present on the document but not requested",
      },
    ],
    document: { file_name: "ananya.pdf", text_truncated: false },
  };

  /**
   * Step three with a document chosen and read.
   *
   * Choosing it is the whole instruction — the first read happens on its own,
   * and the button says "Run again" from there, for edits made afterwards.
   */
  async function toSample() {
    await toFields();
    await userEvent.upload(
      document.querySelector('input[type="file"]') as HTMLInputElement,
      new File(["cv"], "ananya.pdf", { type: "application/pdf" }),
    );
  }

  /**
   * The whole reason this step exists: a real run, against the fields as they
   * stand, before a collection exists to hold either.
   */
  it("runs the current fields against one document and shows what came out", async () => {
    mocks.previewSample.mockResolvedValue(preview);
    await toSample();
    expect(mocks.previewSample).toHaveBeenCalledWith(
      [
        {
          field_name: "candidate_name",
          description: "Full name of the candidate.",
          field_type: "str",
        },
        {
          field_name: "years_of_experience",
          description: "Total years worked.",
          field_type: "int",
        },
      ],
      expect.objectContaining({ file: expect.any(File) }),
    );

    expect(await screen.findByText("Ananya Rao")).toBeInTheDocument();
    // A field the document does not carry is a correct answer, not an error.
    expect(
      screen.getByText(/not on this document — the field is still fine/i),
    ).toBeInTheDocument();
  });

  it("adds a field the document carried that nobody asked for", async () => {
    mocks.previewSample.mockResolvedValue(preview);
    await toSample();
    await userEvent.click(
      await screen.findByRole("button", { name: "Add linkedin_url" }),
    );

    // Straight onto the board beside it — there is no other step to go to.
    expect(nameInputs()).toContain("linkedin_url");
  });

  /** A scan with no text is not a broken field list, and 422 says so. */
  it("explains an unreadable document rather than blaming the fields", async () => {
    mocks.previewSample.mockRejectedValue({ response: { status: 422 } });
    await toSample();
    expect(await screen.findByRole("alert")).toHaveTextContent(/OCR/);
    expect(mocks.reportError).not.toHaveBeenCalled();
  });

  /** A real extraction failure is not the same as a field not being found. */
  it("separates a failed extraction from a field that was not found", async () => {
    mocks.previewSample.mockRejectedValue({ response: { status: 502 } });
    await toSample();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /extraction itself failed/i,
    );
  });

  it("creates nothing on the way through", async () => {
    mocks.previewSample.mockResolvedValue(preview);
    await toSample();
    expect(mocks.createCollection).not.toHaveBeenCalled();
  });

  /**
   * Before a run the results column says what it is about to fill in, rather
   * than being a dropzone beside a field list it looks unrelated to.
   */
  it("lists the fields waiting for a value before anything has run", async () => {
    await toFields();

    expect(
      screen.getByText(/2 fields waiting for a value/i),
    ).toBeInTheDocument();
    expect(mocks.previewSample).not.toHaveBeenCalled();
    // Nothing to run against yet.
    expect(document.querySelector('[data-demo="sample-run"]')).toBeDisabled();
  });

  /** Choosing a document is not the run: the button under it is. */
  it("reads the document on sight, then leaves the re-runs to the reader", async () => {
    mocks.previewSample.mockResolvedValue(preview);
    await toFields();
    await userEvent.upload(
      document.querySelector('input[type="file"]') as HTMLInputElement,
      new File(["cv"], "ananya.pdf", { type: "application/pdf" }),
    );

    expect(await screen.findByText("Ananya Rao")).toBeInTheDocument();
    expect(mocks.previewSample).toHaveBeenCalledTimes(1);
    // From here on it is a re-run, and nothing runs until it is pressed.
    expect(runButton()).toHaveTextContent(/run again/i);
    expect(mocks.previewSample).toHaveBeenCalledTimes(1);
  });

  /**
   * A row still being written is left out of a run, so running while one is on
   * the board would answer for fewer fields than the board shows — and that
   * row is usually the very field being tested.
   */
  it("holds the run while a field has no description", async () => {
    mocks.previewSample.mockResolvedValue(preview);
    await toSample();
    await screen.findByText("Ananya Rao");

    await userEvent.click(screen.getByRole("button", { name: /add a field/i }));

    expect(runButton()).toBeDisabled();
    expect(runButton()).toHaveAttribute(
      "title",
      expect.stringContaining("new_field has no description yet"),
    );
    expect(
      screen.getByText(/new_field has no description yet/i),
    ).toBeInTheDocument();

    // Saying what it captures hands the run back.
    await userEvent.type(
      screen.getByLabelText("What new_field should capture"),
      "The desk they sit at.",
    );
    expect(runButton()).toBeEnabled();
    await userEvent.click(runButton());
    expect(mocks.previewSample).toHaveBeenCalledTimes(2);
  });

  /**
   * The loop this step exists for: the board is editable here, and editing it
   * says the values beside it are out of date rather than leaving them looking
   * current.
   */
  it("marks the run stale when a field is edited, and runs again with the edit", async () => {
    mocks.previewSample.mockResolvedValue(preview);
    await toSample();
    await screen.findByText("Ananya Rao");

    const [first] = screen.getAllByLabelText("Field name");
    await userEvent.clear(first);
    await userEvent.type(first, "full_name");

    expect(screen.getByText(/you changed the fields/i)).toBeInTheDocument();

    await userEvent.click(runButton());

    expect(mocks.previewSample).toHaveBeenLastCalledWith(
      [
        expect.objectContaining({ field_name: "full_name" }),
        expect.objectContaining({ field_name: "years_of_experience" }),
      ],
      expect.objectContaining({ file: expect.any(File) }),
    );
    await waitFor(() =>
      expect(screen.queryByText(/you changed the fields/i)).toBeNull(),
    );
  });

  /** The document can be taken back, not only swapped for another one. */
  it("removes the document and puts the step back where it started", async () => {
    mocks.previewSample.mockResolvedValue(preview);
    await toSample();
    await screen.findByText("Ananya Rao");

    await userEvent.click(
      screen.getByRole("button", { name: /Remove ananya\.pdf/i }),
    );

    expect(screen.queryByText("Ananya Rao")).toBeNull();
    expect(screen.queryByText("ananya.pdf")).toBeNull();
    expect(
      screen.getByText(/2 fields waiting for a value/i),
    ).toBeInTheDocument();
    // Nothing left to run it against.
    expect(document.querySelector('[data-demo="sample-run"]')).toBeDisabled();
  });

  /** A suggestion turned down goes away without being added to the list. */
  it("drops a suggested field that is not needed", async () => {
    mocks.previewSample.mockResolvedValue(preview);
    await toSample();
    await userEvent.click(
      await screen.findByRole("button", { name: /not needed/i }),
    );

    expect(
      screen.queryByRole("button", { name: "Add linkedin_url" }),
    ).toBeNull();
    expect(nameInputs()).not.toContain("linkedin_url");
  });
});

describe("CreateStepper — creating", () => {
  /**
   * The list that was on the board is the list that gets built: named,
   * described and typed exactly as it stood, with no prose in between for
   * anything to re-derive it from.
   */
  it("sends the board — including an edit — as the fields themselves", async () => {
    await toFields();

    const [first] = screen.getAllByLabelText("Field name");
    await userEvent.clear(first);
    await userEvent.type(first, "full_name");

    await toDocuments();
    await userEvent.click(
      screen.getByRole("button", { name: /create it empty/i }),
    );

    expect(mocks.createCollection).toHaveBeenCalledTimes(1);
    const sent = mocks.createCollection.mock.calls[0][0];
    expect(sent.name).toBe("Candidate Resumes");
    expect(sent.structuredExtraction).toBe(true);
    expect(sent.fields).toEqual([
      {
        field_name: "full_name",
        description: "Full name of the candidate.",
        field_type: "str",
      },
      {
        field_name: "years_of_experience",
        description: "Total years worked.",
        field_type: "int",
      },
    ]);
    expect(mocks.ingestFiles).not.toHaveBeenCalled();
  });

  /**
   * What gets created is the board and nothing else — the optional defaults
   * never reach it, so they are never sent either.
   */
  it("creates only the fields on the board", async () => {
    mocks.generateSchema.mockResolvedValue({
      proposed_fields: [{ field_name: "vendor_name", field_type: "str" }],
      default_field_verdicts: [
        { field_name: "topics", keep: true, reason: "Useful." },
      ],
      rejected_fields: [],
    });

    render(<CreateStepper />);
    await userEvent.type(
      screen.getByLabelText(/what are these documents about/i),
      "Invoices.",
    );
    await userEvent.type(
      screen.getByLabelText(/collection name/i),
      "Supplier Invoices",
    );
    await userEvent.click(step1Continue());
    await toDocuments();
    await userEvent.click(
      screen.getByRole("button", { name: /create it empty/i }),
    );

    const sent = mocks.createCollection.mock.calls[0][0];
    expect(
      sent.fields.map((f: { field_name: string }) => f.field_name),
    ).toEqual(["vendor_name"]);
  });

  it("sends the documents when there are any, after the collection exists", async () => {
    mocks.ingestFiles.mockResolvedValue({ failed: [] });
    await toFields();
    await toDocuments();

    const file = new File(["cv"], "ananya.pdf", { type: "application/pdf" });
    await userEvent.upload(
      document.querySelector('input[type="file"]') as HTMLInputElement,
      file,
    );
    expect(screen.getByText(/1 file ready to send/i)).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /create and add my documents/i }),
    );

    expect(mocks.createCollection).toHaveBeenCalledTimes(1);
    expect(mocks.ingestFiles).toHaveBeenCalledWith(K, [file]);
  });

  it("shows what was created, and opens the collection from there", async () => {
    await toFields();
    await toDocuments();
    await userEvent.click(
      screen.getByRole("button", { name: /create it empty/i }),
    );

    expect(await screen.findByText(/is ready/)).toBeInTheDocument();
    expect(screen.getByText("candidate_name")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /open collection/i }),
    );
    expect(mocks.push).toHaveBeenCalledWith(
      `/document-intelligence/knowledge?collection=${K}`,
    );
  });

  /**
   * A collection whose fields did not save is recoverable in Settings, and
   * saying so beats letting somebody find it there.
   */
  it("says when the collection was created without its fields", async () => {
    mocks.createCollection.mockResolvedValue({
      collectionId: K,
      knowledgeId: K,
      createdFields: [],
      fieldsFailed: true,
    });

    await toFields();
    await toDocuments();
    await userEvent.click(
      screen.getByRole("button", { name: /create it empty/i }),
    );

    expect(mocks.error).toHaveBeenCalledWith(
      expect.stringContaining("its fields were not"),
    );
    expect(await screen.findByText(/fields not saved/i)).toBeInTheDocument();
  });

  it("keeps the collection when the documents fail, and says where to retry", async () => {
    mocks.ingestFiles.mockRejectedValue(new Error("network"));
    await toFields();
    await toDocuments();
    await userEvent.upload(
      document.querySelector('input[type="file"]') as HTMLInputElement,
      new File(["cv"], "ananya.pdf", { type: "application/pdf" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /create and add my documents/i }),
    );

    expect(mocks.error).toHaveBeenCalledWith(
      expect.stringContaining("Add them from Knowledge"),
    );
    // The collection still stands, so the summary is what comes next.
    expect(await screen.findByText(/is ready/)).toBeInTheDocument();
  });
});
