import { expect, test } from "@playwright/test";

import {
  KNOWLEDGE_ID,
  mockKnowledgeService,
  SECOND_KNOWLEDGE_ID,
  SESSION_ID,
  UNNAMED_KNOWLEDGE_ID,
} from "./fixtures";

/**
 * Document Intelligence, end to end, against a stubbed knowledge service.
 *
 * The point of these specs is the product rule: every request this product makes
 * goes to `/api/knowledge/…` (the auth service's profile read aside). Each one
 * therefore asserts both the behaviour on screen and the calls that produced it,
 * and the fixture blocks and records anything aimed elsewhere.
 */

const collectionUrl = (route: string, id = KNOWLEDGE_ID) =>
  `/document-intelligence${route}?collection=${id}`;

test.describe("Document Intelligence — knowledge service only", () => {
  test("the board lists the named knowledge bases and nothing else", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto("/document-intelligence");

    await expect(page.getByText("Resumes")).toBeVisible();
    await expect(page.getByText("Invoices")).toBeVisible();
    // The unnamed knowledge base belongs to another product's agent.
    await expect(page.getByText("Untitled collection")).toHaveCount(0);

    // The shell's way out of the product. Since 75a14927 that is the marketing
    // site's own page for this product, not the agent host's root — the brand
    // beside it goes to this product's own Home, so leaving needs its own
    // control either way.
    await expect(
      page.locator(
        'header a[href="https://futuresmart.ai/solutions/document-intelligence"]',
      ),
    ).toBeVisible();

    expect(recorder.violations).toEqual([]);
    expect(recorder.calls).toContain("GET /api/knowledge/v1");
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${KNOWLEDGE_ID}/config`,
    );
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${SECOND_KNOWLEDGE_ID}/config`,
    );
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${UNNAMED_KNOWLEDGE_ID}/config`,
    );
    // The owner scopes the list; the old agents route is gone.
    const list = recorder.urls.find((url) =>
      url.includes("/api/knowledge/v1?"),
    );
    expect(list).toContain("owner_id=");
  });

  test("a collection's documents come from the v2 list", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/knowledge"));

    await expect(page.getByText("priya-sharma-resume.pdf")).toBeVisible();
    expect(recorder.violations).toEqual([]);
    expect(
      recorder.calls.some((call) =>
        call.startsWith(`GET /api/knowledge/v2/${KNOWLEDGE_ID}/documents`),
      ),
    ).toBe(true);
  });

  test("the extracted fields come from the enrichment route", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/settings"));
    await page.getByRole("button", { name: "Extracted fields" }).click();

    await expect(page.getByText("candidate_name")).toBeVisible();

    // The type in the service's own words, never the wire type: "List[dict]" is
    // not something to show anyone who asked for a table. The first field
    // carries a `field_type_label`; the second is an older row without one, and
    // has to read as words all the same.
    await expect(page.getByRole("cell", { name: "Text" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Table" })).toBeVisible();
    await expect(page.getByText("List[dict]")).toHaveCount(0);
    await expect(page.getByText("str", { exact: true })).toHaveCount(0);

    expect(recorder.violations).toEqual([]);
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${KNOWLEDGE_ID}/enrichment`,
    );
  });

  /**
   * Editing a field does not offer to re-type it, because the service will not.
   *
   * Probed live 2026-09-16: `PATCH …/enrichment` with a `field_type` answers
   * "Cannot change field_type for 'x'." **and skips the whole row**, taking the
   * other edits in that call with it; a new `field_type_label` on its own is
   * stored as the label while the type stays as it was, which is how a `str`
   * field ends up labelled "Number". So the dialog shows the type, says it is
   * fixed, and sends neither.
   */
  test("editing a field shows its type as fixed and sends no type at all", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/settings"));
    await page.getByRole("button", { name: "Extracted fields" }).click();
    await page.getByRole("button", { name: "Edit candidate_name" }).click();

    const dialog = page.getByRole("dialog", { name: "Edit field" });
    await expect(dialog.getByText("Read this as")).toBeVisible();
    await expect(dialog.getByText(/Fixed once the field exists/)).toBeVisible();
    // Nothing to pick: the only type control on an existing field is a label.
    await expect(
      dialog.getByRole("combobox", { name: "What this field is read as" }),
    ).toHaveCount(0);
    // The name is the service's to keep, too.
    await expect(dialog.getByLabel("Field name")).toBeDisabled();

    await dialog
      .getByLabel("What should this field capture?")
      .fill("The candidate's full name, as written on the CV.");
    await dialog.getByRole("button", { name: "Save changes" }).click();

    const patch = `PATCH /api/knowledge/v1/${KNOWLEDGE_ID}/enrichment`;
    await expect.poll(() => recorder.bodies[patch]?.length ?? 0).toBe(1);
    const [sent] = (
      recorder.bodies[patch][0] as { fields: Array<Record<string, unknown>> }
    ).fields;
    expect(sent.description).toBe(
      "The candidate's full name, as written on the CV.",
    );
    expect(sent).not.toHaveProperty("field_type");
    // A schema is only sent when there is one: `schema: null` on a plain field
    // comes back as a warning the screen would show verbatim.
    expect(sent).not.toHaveProperty("schema");
    expect(recorder.violations).toEqual([]);
  });

  /**
   * Adding a field leaves the type to the service unless someone picked one.
   *
   * Live-checked the same day: `POST …/enrichment` honours a `field_type` sent
   * by hand, and with none it resolves the type from the description and
   * answers with the reasoning. Sending a guessed type would pin the wrong one;
   * sending only a label would store a label the type does not match.
   */
  test("adding a field sends no type when the service is to work it out", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/settings"));
    await page.getByRole("button", { name: "Extracted fields" }).click();
    await page.getByRole("button", { name: "Add field" }).click();

    const dialog = page.getByRole("dialog", { name: "Add a field" });
    await dialog.getByLabel("Field name").fill("github_url");
    await dialog
      .getByLabel("What should this field capture?")
      .fill("Link to the profile where their code lives.");
    await dialog.getByRole("button", { name: "Add field" }).click();

    const post = `POST /api/knowledge/v1/${KNOWLEDGE_ID}/enrichment`;
    await expect.poll(() => recorder.bodies[post]?.length ?? 0).toBe(1);
    const [sent] = (
      recorder.bodies[post][0] as { fields: Array<Record<string, unknown>> }
    ).fields;
    expect(sent.field_name).toBe("github_url");
    expect(sent.level).toBe("document");
    expect(sent).not.toHaveProperty("field_type");
    expect(recorder.violations).toEqual([]);
  });

  /** …and sends it as a type, not a label, when somebody did pick one. */
  test("adding a field sends the type that was picked", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/settings"));
    await page.getByRole("button", { name: "Extracted fields" }).click();
    await page.getByRole("button", { name: "Add field" }).click();

    const dialog = page.getByRole("dialog", { name: "Add a field" });
    await dialog.getByLabel("Field name").fill("notice_period");
    await dialog
      .getByLabel("What should this field capture?")
      .fill("How much notice they have to give.");
    // The dropdown is the service's own catalogue of types, read from
    // `GET /v1/field-types`.
    await dialog
      .getByRole("combobox", { name: "What this field is read as" })
      .selectOption("str");
    await dialog.getByRole("button", { name: "Add field" }).click();

    const post = `POST /api/knowledge/v1/${KNOWLEDGE_ID}/enrichment`;
    await expect.poll(() => recorder.bodies[post]?.length ?? 0).toBe(1);
    const [sent] = (
      recorder.bodies[post][0] as { fields: Array<Record<string, unknown>> }
    ).fields;
    expect(sent.field_type).toBe("str");
    expect(sent.field_type_label ?? null).toBeNull();
    expect(recorder.violations).toEqual([]);
  });

  /**
   * The service publishes `int` and `float` under one label, "Number", and the
   * dropdown used to fold them into one entry that sent `int` — whole numbers
   * only, so a per-mile rate of 0.18 came back as 0. Both are offered now.
   */
  test("adding a field can pick a decimal number", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/settings"));
    await page.getByRole("button", { name: "Extracted fields" }).click();
    await page.getByRole("button", { name: "Add field" }).click();

    const dialog = page.getByRole("dialog", { name: "Add a field" });
    await dialog.getByLabel("Field name").fill("fuel_rate_per_mile");
    await dialog
      .getByLabel("What should this field capture?")
      .fill("Fuel charged per mile driven.");
    const picker = dialog.getByRole("combobox", {
      name: "What this field is read as",
    });
    await expect(
      picker.getByRole("option", { name: "Number (whole)" }),
    ).toHaveCount(1);
    await picker.selectOption({ label: "Number (decimal)" });
    await dialog.getByRole("button", { name: "Add field" }).click();

    const post = `POST /api/knowledge/v1/${KNOWLEDGE_ID}/enrichment`;
    await expect.poll(() => recorder.bodies[post]?.length ?? 0).toBe(1);
    const [sent] = (
      recorder.bodies[post][0] as { fields: Array<Record<string, unknown>> }
    ).fields;
    expect(sent.field_type).toBe("float");
    expect(sent.field_type_label ?? null).toBeNull();
    expect(recorder.violations).toEqual([]);
  });

  test("the extraction toggle sends the one flag the service derives the levels from", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/settings"));
    await page.getByRole("button", { name: "Extracted fields" }).click();
    await page
      .getByRole("switch", { name: "Enable structured extraction" })
      .click();

    const put = `PUT /api/knowledge/v1/${KNOWLEDGE_ID}/config`;
    await expect.poll(() => recorder.bodies[put]?.length ?? 0).toBe(1);

    // The metadata levels, the embedding model, hybrid search and the
    // extraction LLM are the backend's own — it turns both levels off from this
    // flag, and sending any of the rest would freeze today's default into a
    // released UI.
    const body = recorder.bodies[put][0] as Record<string, unknown>;
    expect(body).toEqual({ enrichment_enabled: false });
    expect(recorder.violations).toEqual([]);
  });

  test("answer settings read and write the collection's chat config", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/settings"));

    // The model list is this service's own catalogue.
    await expect(page.getByText("gpt-5.6-luna").first()).toBeVisible();
    expect(recorder.calls).toContain("GET /api/knowledge/v1/chat-models");
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${KNOWLEDGE_ID}/chat/config`,
    );

    await page.getByText("Suggest follow-up questions").click();
    await page.getByRole("button", { name: "Save changes" }).click();

    await expect(page.getByText(/saved/i).first()).toBeVisible();
    const saves =
      recorder.bodies[`PUT /api/knowledge/v1/${KNOWLEDGE_ID}/chat/config`];
    expect(saves?.length).toBeGreaterThan(0);
    // Flat fields under this service's own names — never the chat service's
    // nested `default_response_node`.
    const saved = saves[saves.length - 1] as Record<string, unknown>;
    expect(saved).toHaveProperty("follow_up", false);
    expect(saved).not.toHaveProperty("default_response_node");
    expect(recorder.violations).toEqual([]);
  });

  test("asking a question streams from the collection's chat route", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/conversations"));

    const composer = page.getByPlaceholder("Send a message…");
    await composer.fill("Who has Python experience?");
    await composer.press("Enter");

    await expect(
      page.getByText("Priya Sharma lists six years of Python."),
    ).toBeVisible();

    expect(recorder.violations).toEqual([]);
    expect(recorder.calls).toContain(
      `POST /api/knowledge/v1/${KNOWLEDGE_ID}/chat`,
    );
    const asked = recorder.bodies[
      `POST /api/knowledge/v1/${KNOWLEDGE_ID}/chat`
    ][0] as Record<string, unknown>;
    // The body this route takes: the question and a stream, nothing that would
    // override the collection's saved answer settings.
    expect(asked).toEqual({
      query: "Who has Python experience?",
      stream: true,
    });
  });

  test("a stored conversation is read back from the same service", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/conversations"));

    await page.getByText("Who has Python experience?").first().click();

    // Longer than the default: opening a session is a second request whose
    // answer then renders, and under a loaded dev server five seconds was thin
    // enough to fail on a full run of this directory while passing on its own.
    await expect(
      page.getByText("Priya Sharma lists six years of Python."),
    ).toBeVisible({ timeout: 15_000 });
    // And the evidence the answer carried, since the collection has sources on.
    // The service calls it `relevant_sources`; the chat stack reads `sources`,
    // which is what the transport's adapter renames it to. It sits on the
    // result card's Sources tab, which carries the count and stays unopened
    // until it is asked for.
    const sources = page.getByRole("tab", { name: /Sources/ });
    await expect(sources).toBeVisible();
    await expect(
      page.getByRole("tab", { name: /Sources/ }).getByText("1"),
    ).toBeVisible();
    await sources.click();
    await expect(
      page.getByText("priya-sharma-resume.pdf").first(),
    ).toBeVisible();

    expect(recorder.violations).toEqual([]);
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${KNOWLEDGE_ID}/sessions`,
    );
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${KNOWLEDGE_ID}/sessions/${SESSION_ID}`,
    );
  });

  test("creating a collection mints a knowledge base and configures it", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto("/document-intelligence");
    await page
      .getByRole("button", { name: "Create document collection" })
      .first()
      .click();

    // The create screen: describe it, confirm the fields, skip the sample, then
    // hand over the documents — or none, which is what this collection is
    // created with.
    await page.getByPlaceholder(/Candidate Resumes/).fill("Contracts");
    await page
      .getByLabel(/what are these documents about/i)
      .fill("Agreements signed with vendors.");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: /looks right/i }).click();
    await page.getByRole("button", { name: /create it empty/i }).click();

    await expect
      .poll(() => recorder.calls.filter((c) => c === "POST /api/knowledge/v1"))
      .toHaveLength(1);

    const created = recorder.bodies["POST /api/knowledge/v1"][0] as Record<
      string,
      unknown
    >;
    expect(created).toMatchObject({ collection_name: "Contracts" });
    await expect
      .poll(() =>
        recorder.calls.filter(
          (call) => call === "PUT /api/knowledge/v1/new-collection-id/config",
        ),
      )
      .toHaveLength(1);

    // What the person typed, plus the one flag that stops the service seeding
    // fields nobody asked for. The embedding model, hybrid search, the metadata
    // levels and the enrichment settings are the service's own defaults —
    // sending them from here would freeze today's values into a released UI,
    // and there is no picker for any of them on this screen.
    const configured = recorder.bodies[
      "PUT /api/knowledge/v1/new-collection-id/config"
    ][0] as Record<string, unknown>;
    expect(Object.keys(configured).sort()).toEqual([
      "collection_name",
      "seed_optional_defaults",
      "service_description",
    ]);
    // Without this, the config call force-creates all nine document-level
    // defaults regardless of what was confirmed — 13 template fields came back
    // as 22 the last time it went out without it.
    expect(configured.seed_optional_defaults).toBe(false);
    // And never `field_template`: it is not stored, and with the flag off it
    // seeds nothing either.
    expect(configured).not.toHaveProperty("field_template");

    // And the chat config is left alone: opening the new collection's Knowledge
    // screen reads it to render the collection status, but creation must not
    // write it. Unconfigured chat already shows sources and offers follow-ups,
    // which is what this product promises.
    expect(
      recorder.calls.filter(
        (call) => call.startsWith("PUT ") && call.includes("/chat/config"),
      ),
    ).toEqual([]);
    expect(recorder.violations).toEqual([]);
  });

  /**
   * The schema, worked out before anything exists — and written afterwards.
   *
   * This is the property the whole flow turns on. `POST /v1/enrichment/generate`
   * is unscoped and saves nothing, so the fields can be proposed, argued with
   * and edited while there is still no collection to carry a mistake. The list
   * that was on screen is then created by one `POST …/{id}/enrichment` call,
   * every entry `is_custom: true` — which the route requires — so what somebody
   * confirmed is what gets built rather than something re-derived from prose.
   */
  test("the schema is worked out before the collection, then written to it", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto("/document-intelligence");
    await page
      .getByRole("button", { name: "Create document collection" })
      .first()
      .click();

    await page.getByPlaceholder(/Candidate Resumes/).fill("Contracts");
    await page
      .getByLabel(/what are these documents about/i)
      .fill("Agreements signed with vendors.");
    await page
      .getByLabel(/what do you want pulled out/i)
      .fill("the other party\nwhen it expires\nthe total value");
    await page.getByRole("button", { name: "Continue" }).click();

    // The schema call ran, and it ran against no collection at all.
    const generate = "POST /api/knowledge/v1/enrichment/generate";
    await expect
      .poll(() => recorder.calls.filter((call) => call === generate))
      .toHaveLength(1);
    expect(
      recorder.calls.filter((call) => call === "POST /api/knowledge/v1"),
    ).toEqual([]);

    // What it proposed is on the board, editable, with the types it gave.
    await expect(page.getByLabel("Field name").first()).toHaveValue(
      "other_party",
    );
    await expect(page.getByLabel("What expires_on is read as")).toHaveValue(
      "date",
    );
    await page.getByLabel("Field name").nth(2).fill("agreement_value");

    await page.getByRole("button", { name: /looks right/i }).click();
    await page.getByRole("button", { name: /create it empty/i }).click();

    const write = "POST /api/knowledge/v1/new-collection-id/enrichment";
    await expect
      .poll(() => recorder.calls.filter((call) => call === write))
      .toHaveLength(1);

    // One call for the whole list, after the config PUT.
    expect(
      recorder.calls.indexOf("PUT /api/knowledge/v1/new-collection-id/config"),
    ).toBeLessThan(recorder.calls.indexOf(write));

    const written = recorder.bodies[write][0] as {
      fields: Array<Record<string, unknown>>;
    };
    // The edit made on the board reached the request — a board whose edits do
    // not travel is a board that decorates a decision made elsewhere.
    expect(written.fields.map((field) => field.field_name)).toEqual([
      "other_party",
      "expires_on",
      "agreement_value",
    ]);
    // Required on every field: omitting it is a 422 naming
    // `body.fields.0.is_custom`.
    expect(written.fields.every((field) => field.is_custom === true)).toBe(
      true,
    );
    // The optional defaults' verdicts are ignored: neither `topics` (kept) nor
    // `entities` (dropped) is created.
    expect(
      written.fields.some((field) =>
        ["topics", "entities"].includes(String(field.field_name)),
      ),
    ).toBe(false);

    await expect(page.getByText(/Contracts.* is ready/)).toBeVisible();
    expect(recorder.violations).toEqual([]);
  });

  test("the collection rail marks a collection that has stopped answering", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page, {
      chatConfigOverrides: { chat_enabled: false },
    });

    await page.goto(collectionUrl("/knowledge"));

    // Whether a collection answers is its chat config's `chat_enabled`, read
    // once a collection is open rather than once per card on the board.
    await expect(page.locator(".bg-warn-dot").first()).toBeVisible();
    expect(recorder.violations).toEqual([]);
  });

  /**
   * The website dialog's two fields, end to end.
   *
   * The depth is on screen from the moment the form is — a control that appears
   * only after another select is changed reads as missing — and it belongs to a
   * crawl and only a crawl, so a scrape disables it and sends nothing for it.
   * Neither the scraper picker nor the processing-mode disclosure exists any
   * more: `scrape_using` is deprecated, and the service works out how a page has
   * to be read by itself.
   */
  test("a website is added with its method and depth, and nothing else", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);

    await page.goto(collectionUrl("/knowledge"));
    await page.getByText("Website", { exact: true }).first().click();

    const depth = page.getByLabel(/levels/i);
    await expect(depth).toBeVisible();
    await expect(depth).toBeDisabled();
    await expect(page.getByLabel(/scraper/i)).toHaveCount(0);

    await page.getByLabel(/website url/i).fill("https://example.com/docs");
    await page.getByLabel(/what to read/i).selectOption("crawl");
    await expect(depth).toBeEnabled();
    await depth.selectOption("2");

    await expect(page.getByRole("button", { name: /advanced/i })).toHaveCount(
      0,
    );
    // Scoped to the dialog: the Knowledge screen behind it has an add-options
    // toggle and an "Added via" column header, so a bare accessible-name match
    // on "Add" is ambiguous.
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Add", exact: true })
      .click();

    const key = `POST /api/knowledge/v1/${KNOWLEDGE_ID}/documents`;
    await expect
      .poll(() => recorder.bodies[key]?.length ?? 0)
      .toBeGreaterThan(0);
    const body = String(recorder.bodies[key][0]);
    expect(body).toContain("https://example.com/docs");
    expect(body).toContain("crawl");
    expect(body).toContain("max_depth");
    // Both are the service's own business: `scrape_using` is deprecated, and the
    // processing mode is detected from the page it fetched.
    expect(body).not.toContain("scrape_using");
    expect(body).not.toContain("document_processing_mode");
    expect(recorder.violations).toEqual([]);
  });
});
