import { expect, test, type Page } from "@playwright/test";

import { mockKnowledgeService } from "./fixtures";

/**
 * The create screen, step by step.
 *
 * `knowledge-only.spec.ts` already proves the calls this flow makes. What is
 * left — and what a dialog could never have offered — is the shape of the
 * decision: every step visible from the first one, a field list that came from
 * the service and goes back to it unchanged, a real document run against those
 * fields before anything exists, and a board that refuses to hand the service a
 * schema it would silently accept and never fill.
 */

async function openCreate(page: Page) {
  await mockKnowledgeService(page);
  await page.setViewportSize({ width: 1400, height: 1000 });
  await page.goto("/document-intelligence");
  await page
    .getByRole("button", { name: /create document collection/i })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "New document collection" }),
  ).toBeVisible();
}

test.describe("Creating a collection", () => {
  test("is a screen with every step on it, and nothing created yet", async ({
    page,
  }) => {
    await openCreate(page);
    await expect(page).toHaveURL(/\/document-intelligence\/create\/?$/);

    const steps = page.getByRole("listitem");
    await expect(steps).toHaveCount(3);
    await expect(steps.nth(0)).toContainText("Describe");
    await expect(steps.nth(1)).toContainText("Check the fields");
    await expect(steps.nth(2)).toContainText("Add documents");
    await expect(steps.nth(0)).toHaveAttribute("aria-current", "step");

    // Workspace scope: you are not inside a collection until it exists.
    await expect(
      page.getByRole("navigation", { name: "Collection" }),
    ).toHaveCount(0);
  });

  /**
   * The kinds are `GET /v1/field-templates`, not a list this screen ships — so
   * a template the service adds appears here without a release.
   */
  test("offers the kinds the service publishes, as starting points", async ({
    page,
  }) => {
    await openCreate(page);
    const cont = page.getByRole("button", { name: "Continue" });
    await expect(cont).toBeDisabled();

    await expect(
      page.getByRole("button", { name: /Resumes \/ CVs/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Something else/ }),
    ).toBeVisible();

    await page.getByRole("button", { name: /Resumes \/ CVs/ }).click();
    await expect(page.getByLabel(/collection name/i)).toHaveValue(
      "Resumes / CVs",
    );
    await expect(cont).toBeEnabled();

    // A second template replaces what the first one wrote…
    await page.getByRole("button", { name: /Contracts/ }).click();
    await expect(page.getByLabel(/collection name/i)).toHaveValue("Contracts");

    // …but never what someone typed themselves.
    await page.getByLabel(/collection name/i).fill("Q3 agreements");
    await page.getByRole("button", { name: /Something else/ }).click();
    await expect(page.getByLabel(/collection name/i)).toHaveValue(
      "Q3 agreements",
    );
  });

  /**
   * A template's fields arrive with the catalogue, already typed. Nothing is
   * re-derived in the browser and no metered call is spent to land in the same
   * place.
   */
  test("starts from a template's own fields, with its own types", async ({
    page,
  }) => {
    await openCreate(page);
    await page.getByRole("button", { name: /Resumes \/ CVs/ }).click();
    await page.getByRole("button", { name: "Continue" }).click();

    const names = page.getByLabel("Field name");
    await expect(names).toHaveCount(3);
    await expect(names.nth(0)).toHaveValue("candidate_name");
    await expect(
      page.getByLabel("What years_of_experience is read as"),
    ).toHaveValue("int");
    await expect(page.getByLabel("What work_history is read as")).toHaveValue(
      "List[dict]",
    );

    // A table publishes its columns, folded under the row until asked for, and
    // they are editable in place.
    await page.getByRole("button", { name: "3 columns" }).click();
    await expect(
      page.getByText(/one row per entry — its columns/i),
    ).toBeVisible();
    await expect(page.getByLabel(/sub-field 1 of work_history/i)).toHaveValue(
      "company_name",
    );
  });

  /**
   * The wording path: the service proposes the fields and says what it threw
   * out, both on the board before anything is created. Its verdicts on the
   * optional defaults are ignored — those fields are neither offered here nor
   * created with the collection.
   */
  test("shows what the service proposed and left out", async ({ page }) => {
    await openCreate(page);
    await page
      .getByLabel(/what are these documents about/i)
      .fill("Agreements signed with vendors.");
    await page
      .getByLabel(/what do you want pulled out/i)
      .fill("the other party\nwhen it expires\nthe total contract value");
    await page.getByRole("button", { name: "Continue" }).click();

    const names = page.getByLabel("Field name");
    await expect(names.nth(0)).toHaveValue("other_party");
    await expect(page.getByLabel("What expires_on is read as")).toHaveValue(
      "date",
    );
    await expect(page.getByLabel("What contract_value is read as")).toHaveValue(
      "float",
    );

    // The three proposals, and nothing else: the fixture's verdicts name
    // `topics` and `entities`, and neither reaches the board.
    await expect(names).toHaveCount(3);

    // And what it rejected is said, with the service's own reason.
    await expect(
      page.getByText(/salary \(Not in the documents\.\)/),
    ).toBeVisible();
  });

  /** Two fields cannot share a name — the service does not check, so this does. */
  test("refuses a name clash before the service sees it", async ({ page }) => {
    await openCreate(page);
    await page.getByRole("button", { name: /Resumes \/ CVs/ }).click();
    await page.getByRole("button", { name: "Continue" }).click();

    const names = page.getByLabel("Field name");
    const next = page.getByRole("button", { name: /looks right/i });
    await expect(next).toBeEnabled();

    await names.nth(1).fill("candidate_name");
    await expect(
      page.getByText(/already called candidate_name/).first(),
    ).toBeVisible();
    await expect(next).toBeDisabled();

    await names.nth(1).fill("years_of_experience");
    await expect(next).toBeEnabled();
  });

  /**
   * Step three is a real run. The fields as they stand go to
   * `POST /v1/enrichment/preview` with one document, and what came back is
   * shown — values, fields the document does not carry, and fields it carries
   * that nobody asked for.
   */
  test("runs the fields against one document before anything exists", async ({
    page,
  }) => {
    await openCreate(page);
    await page.getByRole("button", { name: /Resumes \/ CVs/ }).click();
    await page.getByRole("button", { name: "Continue" }).click();

    // The sample sits beside the fields on the same step.
    await expect(page.getByText("What we read out of it")).toBeVisible();

    await page.locator('input[type="file"]').setInputFiles({
      name: "ananya.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("resume"),
    });
    // Choosing the document is what runs it.

    await expect(page.getByText("Ananya Rao")).toBeVisible();
    // A field this document does not carry is a correct answer, not an error.
    await expect(
      page.getByText(/not on this document — the field is still fine/i),
    ).toBeVisible();

    // A field the document carries that nobody asked for is one click away.
    await page.getByRole("button", { name: "Add linkedin_url" }).click();
    await expect(page.getByLabel("Field name").nth(3)).toHaveValue(
      "linkedin_url",
    );
  });

  test("goes back without losing what was typed", async ({ page }) => {
    await openCreate(page);
    await page.getByLabel(/collection name/i).fill("Claims 2026");
    await page
      .getByLabel(/what are these documents about/i)
      .fill("Insurance claims.");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByLabel("Field name").first()).toBeVisible();

    await page.getByRole("button", { name: "← Back" }).click();
    await expect(page.getByLabel(/collection name/i)).toHaveValue(
      "Claims 2026",
    );
    await expect(
      page.getByLabel(/what are these documents about/i),
    ).toHaveValue("Insurance claims.");
  });

  test("offers documents or an empty collection on the last step", async ({
    page,
  }) => {
    await openCreate(page);
    await page.getByRole("button", { name: /Resumes \/ CVs/ }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: /looks right/i }).click();

    await expect(page.getByText("Add them now")).toBeVisible();
    await expect(
      page.getByText("Or send them from your own code"),
    ).toBeVisible();
    // Nothing is created until this button, and it says which of the two it is
    // about to do.
    await expect(
      page.getByRole("button", { name: /create it empty/i }),
    ).toBeVisible();
    await expect(page.getByText(/Nothing has been created yet/)).toBeVisible();
  });
});
