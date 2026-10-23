import { expect, test } from "@playwright/test";

import { mockKnowledgeService } from "./fixtures";

/**
 * Usage, and the credentials board — both reached from the dashboard.
 *
 * Both are account-level: the service scopes this whole surface to the owner
 * (`/v1/owners/{owner_id}/usage/…`) rather than to a knowledge base. That is
 * the part worth holding onto, because the app previously called the
 * knowledge-scoped paths, which the service does not publish — so every
 * assertion below runs against a fixture that only answers the owner-scoped
 * shape, and a regression to per-collection URLs fails here rather than
 * silently rendering zeroes.
 *
 * Credentials used to be a dialog opened from the board; it is now a screen of
 * its own at `…/dashboard/credentials`, with the add form as the dialog. The
 * allowances have a section of their own, read from `limits/screen` — the one
 * route that resolves each row's percentage, wording and enforcement, so the
 * tab asserts what the service decided rather than what the screen worked out.
 */
const APP = "/document-intelligence";
const CREDENTIALS = `${APP}/credentials`;

/** Usage sections are plain buttons, not a tablist. */
const section = (page: import("@playwright/test").Page, name: string) =>
  page.getByRole("button", { name, exact: true });

test.describe("Document Intelligence — usage and credentials", () => {
  test("the board's cards open usage and credentials", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(APP);

    await expect(page.getByText("Usage & Limits")).toBeVisible();
    await expect(page.getByText("Add credentials")).toBeVisible();
    // The card's headline is the account's own month-to-date total, read in
    // one request rather than summed over the collections.
    await expect(page.getByText("3,000")).toBeVisible();
    // And the credentials card says how much of the account is on its own keys.
    await expect(
      page.getByText("1 of 6 services on your own keys"),
    ).toBeVisible();

    await page.getByRole("link", { name: /Add credentials/ }).click();
    await expect(
      page.getByRole("heading", { name: "Add Credentials" }),
    ).toBeVisible();

    await page.goto(APP);
    await page.getByRole("link", { name: /Usage & Limits/ }).click();
    await expect(
      page.getByRole("heading", { name: "Account Overview" }),
    ).toBeVisible();
    expect(recorder.violations).toEqual([]);
  });

  test("usage shows the account's analytics", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(`${APP}/usage`);

    // The KPIs come from the analytics query's own totals.
    await expect(page.getByText("Files processed")).toBeVisible();
    await expect(page.getByText("24", { exact: true })).toBeVisible();
    await expect(
      page.getByText("1,200", { exact: true }).first(),
    ).toBeVisible();
    // Cost is the service's own breakdown, not an estimate from a page rate.
    // The by-service table foots to the same figure the breakdown adds up to.
    await expect(page.getByText(/\$1\.20/).first()).toBeVisible();

    // Every usage call goes to the owner-scoped surface.
    const usageCalls = recorder.urls.filter((url) => url.includes("/usage/"));
    expect(usageCalls.length).toBeGreaterThan(0);
    for (const url of usageCalls) {
      expect(new URL(url).pathname).toContain("/v1/owners/");
    }

    // The window and the window before it, so the deltas are a real comparison.
    const analytics = Object.entries(recorder.bodies).find(([key]) =>
      key.endsWith("/usage/analytics/query"),
    );
    const ranges = (analytics?.[1] ?? []).map(
      (body) =>
        (body as { date_range: { start: string; end: string } }).date_range,
    );
    expect(ranges.length).toBeGreaterThan(1);
    for (const range of ranges) {
      expect(range.start).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(range.end).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
    // Two distinct windows were asked for, not the same one twice.
    expect(new Set(ranges.map((range) => range.start)).size).toBeGreaterThan(1);

    expect(recorder.violations).toEqual([]);
  });

  test("the collections section splits the account by knowledge base", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(`${APP}/usage`);

    await section(page, "Collections").click();

    // Both collections are named, from `group_by: ["knowledge_id"]`.
    await expect(page.getByRole("row", { name: /Resumes/ })).toBeVisible();
    await expect(page.getByRole("row", { name: /Invoices/ })).toBeVisible();

    const grouped = Object.entries(recorder.bodies).find(([key]) =>
      key.endsWith("/usage/analytics/query"),
    );
    expect(
      (grouped?.[1] ?? []).some((body) =>
        (body as { group_by?: string[] }).group_by?.includes("knowledge_id"),
      ),
    ).toBe(true);

    expect(recorder.violations).toEqual([]);
  });

  test("the limits section reads the account's allowances", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(`${APP}/usage`);

    await section(page, "Limits").click();
    await expect(
      page.getByRole("heading", { name: "Your Plan" }),
    ).toBeVisible();

    // The rows are the service's own, drawn as sent.
    await expect(page.getByText("Documents you can hold")).toBeVisible();
    await expect(page.getByText("9 of 500 documents")).toBeVisible();
    // A cap awaiting its backfill reads "Unlimited", never a 0% bar.
    await expect(page.getByText("2 links · Unlimited")).toBeVisible();
    // A daily allowance says when it starts again; a display-only one says it
    // never blocks.
    await expect(page.getByText(/Resets at 00:00 UTC/)).toBeVisible();
    await expect(page.getByText("$3.80 of $25.00")).toBeVisible();
    await expect(
      page.getByText("Reported, never blocks").first(),
    ).toBeVisible();

    // One read, and the period control is not offered against fixed windows.
    const screenCalls = recorder.urls.filter((url) =>
      url.includes("/usage/limits/screen"),
    );
    expect(screenCalls.length).toBeGreaterThan(0);
    for (const url of screenCalls) {
      expect(new URL(url).pathname).toContain("/v1/owners/");
    }
    await expect(
      page.getByRole("button", { name: "Last 30 days" }),
    ).toBeHidden();

    expect(recorder.violations).toEqual([]);
  });

  test("a key can be added and removed for the account", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(CREDENTIALS);

    // Stored keys come back masked, never as the key itself — and the board
    // covers even the mask until the row is uncovered.
    const openaiRow = page.getByRole("row", { name: /openai/ });
    await expect(openaiRow).toBeVisible();
    await expect(page.getByText("sk-…4f2a")).toBeHidden();
    await page.getByRole("button", { name: "Show the openai key" }).click();
    await expect(page.getByText("sk-…4f2a")).toBeVisible();

    // The grid says which key each service is actually on.
    await expect(page.getByText("Your key")).toBeVisible();
    await expect(page.getByText("Platform").first()).toBeVisible();

    await page.getByRole("button", { name: "Add New Credentials" }).click();
    const dialog = page.getByRole("dialog", { name: "Add New Credential" });
    const add = dialog.getByRole("button", { name: "Add Credential" });
    await expect(add).toBeDisabled();

    await dialog.getByLabel(/Provider service/).selectOption("anthropic");
    await dialog.getByLabel(/API key/).fill("sk-ant-not-a-real-key");
    await expect(add).toBeEnabled();
    await add.click();
    await expect(page.getByText(/anthropic credential added/)).toBeVisible();

    const postKey = Object.keys(recorder.bodies).find(
      (key) => key.startsWith("POST") && key.endsWith("/usage/credentials"),
    );
    expect(postKey).toContain("/v1/owners/");
    expect(recorder.bodies[postKey as string]?.[0]).toEqual({
      provider: "anthropic",
      api_key: "sk-ant-not-a-real-key",
    });

    // Removing a key is confirmed first: a stored key cannot be read back, so
    // deleting one means pasting it in full to restore it.
    await page
      .getByRole("button", { name: "Delete the openai credential" })
      .click();
    const confirm = page.getByRole("dialog", {
      name: "Delete the openai credential?",
    });
    await confirm.getByRole("button", { name: "Delete credential" }).click();
    await expect(page.getByText("Credential removed")).toBeVisible();
    expect(
      recorder.calls.some(
        (call) =>
          call.startsWith("DELETE") &&
          call.includes("/v1/owners/") &&
          call.includes("credentials"),
      ),
    ).toBe(true);

    expect(recorder.violations).toEqual([]);
  });

  test("switching the reporting period re-reads over the new window", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(`${APP}/usage`);
    await expect(page.getByText("Files processed")).toBeVisible();

    const windows = () => {
      const key = Object.keys(recorder.bodies).find((name) =>
        name.endsWith("/usage/analytics/query"),
      );
      return (recorder.bodies[key as string] ?? []).map(
        (body) => (body as { date_range: { start: string } }).date_range.start,
      );
    };
    const before = new Set(windows());

    await page.getByRole("button", { name: "Last 7 days" }).click();
    await expect(
      page.getByRole("button", { name: "Last 7 days" }),
    ).toHaveAttribute("aria-pressed", "true");

    // A narrower window is a different request, not the same one relabelled.
    await expect
      .poll(() => windows().some((start) => !before.has(start)))
      .toBe(true);
    expect(recorder.violations).toEqual([]);
  });

  test("the custom filters reach the request body", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(`${APP}/usage`);

    await section(page, "Custom Filters").click();
    // Both pickers carry the catalogue's own values and display names.
    await expect(
      page.getByLabel("Operation", { exact: true }).locator("option"),
    ).toHaveText([
      "All operations",
      "Data ingestion",
      "LLM call",
      "Embedding generation",
      "Vector DB write",
      "LLM generation",
    ]);
    await page
      .getByLabel("Operation", { exact: true })
      .selectOption("llm_generation");
    // Labels are nested under their operation, and narrow with it.
    await expect(
      page.getByLabel("Functional label", { exact: true }).locator("option"),
    ).toHaveText(["All labels", "Generate answer"]);
    await page
      .getByLabel("Functional label", { exact: true })
      .selectOption("generate_answer");

    const sent = () => {
      const key = Object.keys(recorder.bodies).find((name) =>
        name.endsWith("/usage/analytics/query"),
      );
      return (recorder.bodies[key as string] ?? []) as Array<{
        filters?: {
          operations?: string[];
          labels?: string[];
          key_type?: string;
        };
        key_filter?: string;
      }>;
    };

    await expect
      .poll(() =>
        sent().some(
          (body) =>
            body.filters?.operations?.includes("llm_generation") &&
            body.filters?.labels?.includes("generate_answer"),
        ),
      )
      .toBe(true);
    expect(recorder.violations).toEqual([]);
  });

  /**
   * The Source control is the screen's, not a tab's, and `key_type` belongs
   * inside `filters`. Sent as a top-level `key_filter` the route answered 200
   * over the whole window, so the control read as working and narrowed nothing.
   */
  test("the consumption source files key_type inside filters", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(`${APP}/usage`);

    const sent = () => {
      const key = Object.keys(recorder.bodies).find((name) =>
        name.endsWith("/usage/analytics/query"),
      );
      return (recorder.bodies[key as string] ?? []) as Array<{
        filters?: { key_type?: string };
        key_filter?: string;
      }>;
    };

    // Total is its own unfiltered call — never platform plus owner, because
    // events carrying no key type answer to neither filter.
    await expect.poll(() => sent().length).toBeGreaterThan(0);
    expect(sent().every((body) => body.filters?.key_type === undefined)).toBe(
      true,
    );

    await page
      .getByLabel("Consumption source", { exact: true })
      .selectOption("owner");

    await expect
      .poll(() => sent().some((body) => body.filters?.key_type === "owner-key"))
      .toBe(true);
    expect(sent().every((body) => body.key_filter === undefined)).toBe(true);
    // It narrows the Overview too, not only the Custom Filters tab.
    await expect(page.getByText(/Owner Consumptions only/)).toBeVisible();
    expect(recorder.violations).toEqual([]);
  });

  test("the collections table sorts on a column heading", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(`${APP}/usage`);
    await section(page, "Collections").click();

    const names = async () =>
      page.locator("tbody tr td:first-child").allTextContents();
    // The fixture answers Resumes (higher cost) before Invoices.
    await expect.poll(names).toEqual(["Resumes", "Invoices"]);

    await page.getByRole("button", { name: /Cost/ }).click();
    await expect.poll(names).toEqual(["Invoices", "Resumes"]);

    await page.getByRole("button", { name: /Cost/ }).click();
    await expect.poll(names).toEqual(["Resumes", "Invoices"]);

    await page.getByRole("button", { name: /Cost/ }).click();
    await expect(page.getByText(/· unsorted/)).toBeVisible();
    expect(recorder.violations).toEqual([]);
  });

  test("the add-credential dialog offers every provider and guards the key", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(CREDENTIALS);
    await page.getByRole("button", { name: "Add New Credentials" }).click();

    const dialog = page.getByRole("dialog", { name: "Add New Credential" });
    const add = dialog.getByRole("button", { name: "Add Credential" });

    // Six providers plus the placeholder — the catalogue's own two groups.
    const providers = dialog.getByLabel(/Provider service/);
    await expect(providers.locator("option")).toHaveCount(7);
    // The one already stored cannot be picked twice; it is edited on the board.
    await expect(
      providers.locator("option", { hasText: "already added" }),
    ).toBeDisabled();

    await providers.selectOption("anthropic");
    await dialog.getByLabel(/API key/).fill("sk-short");
    await expect(add).toBeDisabled();
    await expect(dialog.getByText(/at least 10 characters/)).toBeVisible();

    await dialog.getByLabel(/API key/).fill("sk-ant-not-a-real-key");
    await expect(add).toBeEnabled();
    expect(recorder.violations).toEqual([]);
  });
});
