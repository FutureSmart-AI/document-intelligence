import { expect, test, type Page } from "@playwright/test";

import { KNOWLEDGE_ID } from "./fixtures";
import { mockRichKnowledge } from "./knowledgeFixture";

/**
 * The document, opened from the table.
 *
 * It replaced a 560px drawer, and the reason is the one thing worth asserting:
 * the text the extraction read and the values it produced have to be on screen
 * together, because checking one against the other is what this screen is for.
 */

const url = `/document-intelligence/knowledge/?collection=${KNOWLEDGE_ID}`;

/** A completed document with values in every field, so both panes have content. */
const DOC = "northwind-logistics-msa.pdf";

async function openDocument(
  page: Page,
  name: string,
  /** Routes that must answer before the fixture's — registered after it. */
  override?: (page: Page) => Promise<void>,
) {
  await mockRichKnowledge(page);
  await override?.(page);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(url);
  await expect(page.getByText(/^Showing /)).toBeVisible();
  // Every document on one page, so the spec never has to know which page a
  // given one landed on — the pager has its own test in knowledge-features.
  await page.getByLabel("Rows per page").selectOption("25");
  await page.getByText(name).first().click();
  return page.getByRole("dialog");
}

test.describe("A document", () => {
  test("opens full width, with its text beside its values", async ({
    page,
  }) => {
    const detail = await openDocument(page, DOC);

    await expect(
      detail.getByRole("heading", { name: "northwind-logistics-msa.pdf" }),
    ).toBeVisible();

    // Left: what the extraction actually read, on the tab it opens on. Scoped
    // to the viewer's own control group — the header carries a *download*
    // button of the same name, and the two must stay tellable apart.
    const views = detail.getByRole("group", {
      name: "What to show of this document",
    });
    await expect(
      views.getByRole("button", { name: "Extracted text" }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(detail.getByText(/PROFESSIONAL SUMMARY/)).toBeVisible();

    // Right: the values, and the document's own facts under them.
    await expect(detail.getByText("Extracted fields")).toBeVisible();
    await expect(detail.getByText("Document details")).toBeVisible();
    await expect(detail.getByText("Northwind Logistics").first()).toBeVisible();

    // It is the screen, not a panel beside the table: the grid is behind it.
    const box = await detail.boundingBox();
    const viewport = page.viewportSize()!;
    expect(box!.width).toBeGreaterThan(viewport.width * 0.9);
  });

  test("switches to the original and back", async ({ page }) => {
    const detail = await openDocument(page, DOC);

    const views = detail.getByRole("group", {
      name: "What to show of this document",
    });
    await views.getByRole("button", { name: "Original" }).click();
    // The original is offered honestly: either embedded, or named with a way to
    // open it. What must never happen is a blank frame with no explanation.
    await expect(
      detail.locator("object").or(detail.getByText(/open it in a new tab/i)),
    ).toBeVisible();

    await views.getByRole("button", { name: "Extracted text" }).click();
    await expect(detail.getByText(/PROFESSIONAL SUMMARY/)).toBeVisible();
  });

  test("steps through the list it was opened from, and closes back onto it", async ({
    page,
  }) => {
    // The first row, so there is somewhere to step to.
    const detail = await openDocument(page, "scanned-supplier-fax.pdf");

    await expect(detail.getByText(/^1 of /)).toBeVisible();
    await detail.getByRole("button", { name: "Next document" }).click();
    await expect(detail.getByText(/^2 of /)).toBeVisible();
    await expect(
      detail.getByRole("heading", { name: "scanned-supplier-fax.pdf" }),
    ).toHaveCount(0);

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // The table is exactly as it was left.
    await expect(page.getByText(/^Showing /)).toBeVisible();
  });

  /**
   * "Show in text" — the source link the ticket asked for. The service keeps no
   * per-field source span, so the value is found in the extracted text and the
   * viewer is pointed at it; a value the text does not contain gets no control.
   */
  test("points the extracted text at a value", async ({ page }) => {
    const detail = await openDocument(page, DOC, async (target) => {
      await target.route(/\/documents\/[^/]+\/chunks(\?|$)/, (route) =>
        route.fulfill({
          json: {
            chunks: [
              { id: "c1", text: "MASTER SERVICES AGREEMENT\n\nParties." },
              {
                id: "c2",
                text: "Supplier: Northwind Logistics.\nEither party may end this with 90 days notice.",
              },
            ],
          },
        }),
      );
    });

    // Start on the original, to prove the control brings the text back.
    const views = detail.getByRole("group", {
      name: "What to show of this document",
    });
    await views.getByRole("button", { name: "Original" }).click();

    await detail
      .getByRole("button", {
        name: /Show Notice Period Days in the extracted text/,
      })
      .click();
    await expect(
      views.getByRole("button", { name: "Extracted text" }),
    ).toHaveAttribute("aria-pressed", "true");
    const mark = detail.locator("mark");
    await expect(mark).toHaveText("90");
    await expect(mark).toBeInViewport();

    // Owner "Priya Sharma" is nowhere in this text: nothing to point at.
    await expect(
      detail.getByRole("button", { name: /Show Owner in the extracted text/ }),
    ).toHaveCount(0);
  });
});
