import { expect, test, type Page } from "@playwright/test";

import { KNOWLEDGE_ID } from "./fixtures";
import {
  COLUMN_COUNT,
  COMPLETED_COUNT,
  DOC_COUNT,
  FAILED_COUNT,
  FIELD_COUNT,
  mockRichKnowledge,
  PROCESSING_COUNT,
} from "./knowledgeFixture";

/**
 * Every control on the Knowledge screen, exercised.
 *
 * The unit suite already covers the table's arithmetic — what `selectRows`
 * keeps, what `facetCounts` counts, what the CSV holds. What it cannot cover is
 * whether the control that drives that arithmetic is reachable and does what it
 * says in a browser: a dropdown that never opens, a badge that never updates, a
 * panel that closes the popover under it. That is what these are for, so each
 * one drives a real control and asserts on what the reader can see afterwards.
 */

const url = `/document-intelligence/knowledge/?collection=${KNOWLEDGE_ID}`;

const grid = (page: Page) => page.getByRole("table");
const footer = (page: Page) => page.getByText(/^Showing /);
const bodyRows = (page: Page) => grid(page).locator("tbody tr");

async function openKnowledge(page: Page) {
  await mockRichKnowledge(page);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "Knowledge" })).toBeVisible();
  // The table is built from two routes; wait for the join, not just the shell.
  await expect(footer(page)).toContainText(`of ${DOC_COUNT} documents`);
  // Every row on one page, so a test never has to know which page a document
  // landed on. The pager has its own test, below.
  await page.getByLabel("Rows per page").selectOption("25");
  await expect(bodyRows(page)).toHaveCount(DOC_COUNT);
}

/** The screen as it opens: ten rows a page, which is what the pager reads. */
async function openTenPerPage(page: Page) {
  await mockRichKnowledge(page);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(url);
  await expect(footer(page)).toContainText(`of ${DOC_COUNT} documents`);
}

test.describe("Knowledge — the one table, control by control", () => {
  test("the add strip offers the three ways in, and folds away", async ({
    page,
  }) => {
    await openKnowledge(page);

    await expect(page.getByRole("button", { name: "Files" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Paste text" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Website" })).toBeVisible();
    await expect(page.getByText("Add knowledge")).toBeVisible();

    // Each tile opens its own dialog, and the dialog closes again.
    await page.getByRole("button", { name: "Website" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const toggle = page.getByTitle("Show or hide the ways to add a document");
    await toggle.click();
    await expect(page.getByRole("button", { name: "Files" })).toHaveCount(0);
    await expect(toggle).toHaveText(/Add documents/);
    await toggle.click();
    await expect(page.getByRole("button", { name: "Files" })).toBeVisible();
  });

  test("search narrows on a name, an object ID and an extracted value", async ({
    page,
  }) => {
    await openKnowledge(page);
    const search = page.getByPlaceholder(
      "Search a name, an object ID, or any extracted value",
    );

    await search.fill("helios");
    await expect(footer(page)).toContainText("of 2 documents");

    // A value that appears in no file name — this is the extracted-field path.
    await search.fill("warehousing");
    await expect(footer(page)).toContainText("of 1 document");
    await expect(grid(page)).toContainText("northwind-logistics-msa.pdf");

    await search.fill("11111111-1111-4111-8111-111111111104");
    await expect(footer(page)).toContainText("of 1 document");

    await search.fill("nothing matches this");
    await expect(grid(page)).toContainText("No document matches");

    await search.fill("");
    await expect(footer(page)).toContainText(`of ${DOC_COUNT} documents`);
  });

  test("the status strip counts the three buckets and filters by one", async ({
    page,
  }) => {
    await openKnowledge(page);

    const done = page.getByTitle("Show only the documents that have been read");
    const reading = page.getByTitle("Show only the documents still being read");
    const failed = page.getByTitle(
      "Show only the documents that could not be read",
    );

    await expect(done).toContainText(String(COMPLETED_COUNT));
    await expect(reading).toContainText(String(PROCESSING_COUNT));
    await expect(failed).toContainText(String(FAILED_COUNT));

    await failed.click();
    await expect(footer(page)).toContainText(`of ${FAILED_COUNT} document`);
    await expect(grid(page)).toContainText("scanned-supplier-fax.pdf");

    // Clicking the active bucket clears it rather than doing nothing.
    await failed.click();
    await expect(footer(page)).toContainText(`of ${DOC_COUNT} documents`);
  });

  test("the type control keeps one way in", async ({ page }) => {
    await openKnowledge(page);

    const typeButton = page.getByRole("button", { name: "All types" });
    await typeButton.click();
    const panel = page
      .locator("div")
      .filter({ hasText: /^All typesTextFileWebsiteClickUp$/ })
      .last();
    await panel.getByRole("button", { name: "Website" }).click();
    await expect(footer(page)).toContainText("of 2 documents");
    await expect(grid(page)).toContainText("suppliers.example.com/terms");

    await page
      .getByRole("button", { name: "Website", exact: true })
      .last()
      .click();
    await page.getByRole("button", { name: "All types" }).click();
    await expect(footer(page)).toContainText(`of ${DOC_COUNT} documents`);
  });

  test("Show hides a whole column group, and the presets put it back", async ({
    page,
  }) => {
    await openKnowledge(page);
    await expect(grid(page)).toContainText("Extracted fields");

    await page.getByRole("button", { name: /^Show: All/ }).click();
    await page.getByRole("button", { name: "Status only" }).click();
    await expect(grid(page)).toContainText("Status details");
    await expect(grid(page)).not.toContainText("Extracted fields");
    // The document group is locked, so the row keeps its name.
    await expect(grid(page)).toContainText("vantage-security-retainer.pdf");

    await page.getByRole("button", { name: "Fields only" }).click();
    await expect(grid(page)).toContainText("Extracted fields");
    await expect(grid(page)).not.toContainText("Status details");

    await page.getByRole("button", { name: "All", exact: true }).click();
    await expect(grid(page)).toContainText("Status details");
    await expect(grid(page)).toContainText("Extracted fields");
    await page.keyboard.press("Escape");
  });

  test("the group header's eye hides its own group", async ({ page }) => {
    await openKnowledge(page);

    await page
      .getByRole("button", { name: "Hide the extracted fields columns" })
      .click();
    await expect(grid(page)).not.toContainText("Extracted fields");
    await expect(
      page.getByRole("button", { name: /^Show: Status only/ }),
    ).toBeVisible();
  });

  test("Columns carries its count and switches one column off", async ({
    page,
  }) => {
    await openKnowledge(page);
    const columns = page.getByTitle("Choose which columns are on screen");
    await expect(columns).toContainText(`${COLUMN_COUNT}/${COLUMN_COUNT}`);

    await columns.click();
    await page.getByRole("checkbox", { name: /^Owner/ }).uncheck();
    await expect(columns).toContainText(`${COLUMN_COUNT - 1}/${COLUMN_COUNT}`);
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await expect(grid(page)).not.toContainText("Owner");

    // The name and how it arrived cannot be switched off.
    await columns.click();
    await expect(page.getByRole("checkbox", { name: /^Name/ })).toBeDisabled();
    await expect(
      page.getByRole("checkbox", { name: /^Added via/ }),
    ).toBeDisabled();

    // Search, then a group's own All, then the panel's Select all.
    await page.getByPlaceholder("Search columns…").fill("region");
    await expect(page.getByRole("checkbox", { name: /^Region/ })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: /^Vendor/ })).toHaveCount(
      0,
    );
    await page.getByPlaceholder("Search columns…").fill("");
    await page.getByRole("button", { name: "Select all", exact: true }).click();
    await expect(columns).toContainText(`${COLUMN_COUNT}/${COLUMN_COUNT}`);
  });

  test("a filter rule keeps only what matches, and says how many rules are on", async ({
    page,
  }) => {
    await openKnowledge(page);

    await page
      .getByTitle("Keep only the documents that match your rules")
      .click();
    await page.getByRole("button", { name: "+ Add rule" }).click();

    await page
      .getByLabel("Column to filter on")
      .selectOption({ label: "Vendor" });
    // Live, not draft-then-apply: the band writes into this same list, so a
    // rule typed here takes effect as it is typed.
    await page.getByPlaceholder("Value…").fill("Acme Cloud");
    await expect(footer(page)).toContainText("of 2 documents");
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await expect(
      page.getByTitle("Keep only the documents that match your rules"),
    ).toContainText("1");

    await page
      .getByTitle("Keep only the documents that match your rules")
      .click();
    await page.getByRole("button", { name: "Clear all" }).click();
    await expect(footer(page)).toContainText(`of ${DOC_COUNT} documents`);
  });

  test("sorting runs from the header and from the panel", async ({ page }) => {
    await openKnowledge(page);

    // The header sort: click once for ascending, twice for descending.
    const nameHeader = grid(page).getByRole("button", { name: /^Name/ });
    await nameHeader.click();
    await expect(bodyRows(page).first()).toContainText(
      "acme-cloud-hosting.pdf",
    );
    await nameHeader.click();
    await expect(bodyRows(page).first()).toContainText(
      "vantage-security-retainer.pdf",
    );
    await nameHeader.click();
    await expect(bodyRows(page).first()).toContainText(
      "scanned-supplier-fax.pdf",
    );

    // The panel: a rule, applied, counted on the badge.
    await page.getByTitle("Order the rows by one column or several").click();
    await page.getByRole("button", { name: "+ Add sort" }).click();
    await page
      .getByLabel("Column to sort by")
      .selectOption({ label: "Vendor" });
    // Descending, so the leading row is a document that *has* a vendor: the
    // two with none sort to the top of an ascending run, which is right and
    // makes for a poor assertion.
    await page.getByRole("button", { name: "Descending" }).click();
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await expect(
      page.getByTitle("Order the rows by one column or several"),
    ).toContainText("1");
    await expect(bodyRows(page).first()).toContainText("vantage-security");
  });

  test("Group & chart bands the collection and writes a real filter", async ({
    page,
  }) => {
    await openKnowledge(page);
    const explore = page.getByTitle(
      "Group the documents by a column and chart it",
    );

    await explore.click();
    const groupBy = page.getByRole("button", { name: /^Group by:/ });
    await expect(groupBy).toBeVisible();

    await groupBy.click();
    await page.getByTitle("Group the documents by Vendor").click();
    await expect(groupBy).toContainText("Vendor");

    // A value chip carries its count, and clicking it writes a filter rule the
    // Filters panel then owns — that is the whole point of the band.
    const chip = page.getByTitle(
      "Also keep documents where Vendor is Acme Cloud",
    );
    await expect(chip).toContainText("2");
    await chip.click();
    await expect(footer(page)).toContainText("of 2 documents");
    await expect(
      page.getByTitle("Keep only the documents that match your rules"),
    ).toContainText("1");
    await expect(explore).toContainText("1");

    // A second value widens the set rather than contradicting the first: two
    // `Equals` rules on one column would be ANDed and answer with nothing.
    await page
      .getByTitle("Also keep documents where Vendor is Northwind Logistics")
      .click();
    // Two vendors with two documents each: the union, not the intersection.
    await expect(footer(page)).toContainText("of 4 documents");
    // Still one rule — one field, one set — and the panel shows both values.
    await expect(
      page.getByTitle("Keep only the documents that match your rules"),
    ).toContainText("1");
    await page
      .getByTitle("Keep only the documents that match your rules")
      .click();
    await expect(page.getByLabel("Condition")).toHaveValue("Is any of");
    await expect(page.getByLabel("Stop keeping Acme Cloud")).toBeVisible();
    await expect(
      page.getByLabel("Stop keeping Northwind Logistics"),
    ).toBeVisible();
    await page.getByRole("button", { name: "Done", exact: true }).click();

    await page.getByTitle("Remove every filter").click();
    await expect(footer(page)).toContainText(`of ${DOC_COUNT} documents`);
  });

  test("the four chart modes, including the one that needs numbers", async ({
    page,
  }) => {
    await openKnowledge(page);
    await page
      .getByTitle("Group the documents by a column and chart it")
      .click();

    await page.getByRole("button", { name: "Share" }).click();
    await expect(page.locator("svg circle").first()).toBeVisible();

    await page.getByRole("button", { name: "Coverage" }).click();
    await expect(
      page.getByText(/documents have a value|Coverage/i).first(),
    ).toBeVisible();

    // Distribution is refused on a text column and offered on a numeric one.
    const distribution = page.getByRole("button", { name: "Distribution" });
    await page.getByRole("button", { name: /^Group by:/ }).click();
    await page.getByTitle("Group the documents by Vendor").click();
    await expect(distribution).toBeDisabled();

    await page.getByRole("button", { name: /^Group by:/ }).click();
    await page.getByTitle("Group the documents by Contract Value").click();
    await expect(distribution).toBeEnabled();
    await distribution.click();
  });

  test("the group-by picker offers every column, prose included", async ({
    page,
  }) => {
    await openKnowledge(page);
    await page
      .getByTitle("Group the documents by a column and chart it")
      .click();
    await page.getByRole("button", { name: /^Group by:/ }).click();

    for (const label of [
      "Name",
      "Added via",
      "Status",
      "Created",
      "Updated",
      "Fields filled",
      "Vendor",
      "Region",
      "Renewal Date",
      "Notice Period Days",
      "Contract Value",
      "Services",
      "Owner",
      // The prose column the type guess used to hide.
      "Summary",
    ]) {
      await expect(
        page.getByTitle(`Group the documents by ${label}`),
      ).toBeVisible();
    }
  });

  test("See all values searches the whole field, not just the chips", async ({
    page,
  }) => {
    await openKnowledge(page);
    await page
      .getByTitle("Group the documents by a column and chart it")
      .click();
    await page.getByRole("button", { name: /^Group by:/ }).click();
    await page.getByTitle("Group the documents by Vendor").click();

    await page.getByTitle("Search every value of this field").click();
    await expect(page.getByText(/unique values · \d+ documents/)).toBeVisible();
    await page.getByPlaceholder("Search values…").fill("helios");
    await expect(page.getByText(/1 of \d+ unique values match/)).toBeVisible();
    await page.getByPlaceholder("Search values…").fill("zzz");
    await expect(
      page.getByText("No value in this column contains that."),
    ).toBeVisible();
  });

  test("density and full screen are toggles that report their state", async ({
    page,
  }) => {
    await openKnowledge(page);

    const density = page.getByTitle("Fit more rows on the screen");
    await density.click();
    await expect(page.getByTitle("Go back to roomy rows")).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    const full = page.getByTitle("Give the table the whole page");
    await full.click();
    await expect(
      page.getByRole("button", { name: "Exit full screen" }),
    ).toHaveAttribute("aria-pressed", "true");
    // Full screen takes the page head and the add strip with it.
    await expect(page.getByRole("heading", { name: "Knowledge" })).toHaveCount(
      0,
    );

    await page.getByRole("button", { name: "Exit full screen" }).click();
    await expect(
      page.getByRole("heading", { name: "Knowledge" }),
    ).toBeVisible();

    // Escape is the other way out.
    await page.getByTitle("Give the table the whole page").click();
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("heading", { name: "Knowledge" }),
    ).toBeVisible();
  });

  test("Refresh re-reads both routes", async ({ page }) => {
    // Counted off the wire rather than off the fixture's recorder: this file's
    // own routes answer `/documents` before the recorder ever sees it.
    const hits = { list: 0, metadata: 0 };
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (path.endsWith("/documents/metadata/all")) hits.metadata += 1;
      else if (path.endsWith("/documents")) hits.list += 1;
    });

    await openKnowledge(page);
    const before = { ...hits };

    await page.getByRole("button", { name: "Refresh" }).click();
    await expect.poll(() => hits.list).toBeGreaterThan(before.list);
    await expect.poll(() => hits.metadata).toBeGreaterThan(before.metadata);
  });

  test("Export offers the view and the whole collection, and downloads", async ({
    page,
  }) => {
    await openKnowledge(page);

    await page.getByRole("button", { name: "Export" }).click();
    await expect(page.getByText("Export what I am looking at")).toBeVisible();
    await expect(page.getByText("Export everything")).toBeVisible();

    const download = page.waitForEvent("download");
    await page.getByText("Export everything").click();
    expect((await download).suggestedFilename()).toMatch(
      /^documents-all.*\.csv$/,
    );
  });

  test("the grid prints what each kind of cell is", async ({ page }) => {
    await openKnowledge(page);

    const row = bodyRows(page).filter({
      hasText: "northwind-logistics-msa.pdf",
    });
    // Name over object ID.
    await expect(row).toContainText("11111111-1111-4111-8111-111111111101");
    // How it arrived, and whether it was read.
    await expect(row).toContainText("File");
    await expect(row).toContainText("Completed");
    // Every field found: the denominator is the collection's own field count.
    await expect(row).toContainText(`${FIELD_COUNT}/${FIELD_COUNT}`);
    // A list cell is chips, with a count for the ones it did not draw.
    await expect(row).toContainText("freight");
    await expect(row).toContainText("+");

    // The document with one field filled says so, and names the empty ones.
    const sparse = bodyRows(page).filter({
      hasText: "unsigned-vendor-nda.pdf",
    });
    await expect(sparse).toContainText(`1/${FIELD_COUNT}`);
    await expect(sparse.getByText("Not found").first()).toBeVisible();

    // A document still being read has no fields at all.
    const pending = bodyRows(page).filter({
      hasText: "orbital-freight-amendment-3.pdf",
    });
    await expect(pending).toContainText("Processing");
    await expect(pending).toContainText(`0/${FIELD_COUNT}`);

    // Both header rows, and the type under each column name.
    await expect(grid(page)).toContainText("Document");
    await expect(grid(page)).toContainText("List[str]");
  });

  test("row actions are on screen without hovering", async ({ page }) => {
    await openKnowledge(page);
    const row = bodyRows(page).filter({
      hasText: "northwind-logistics-msa.pdf",
    });

    for (const label of [
      "Download the original",
      "Download the parsed text",
      "Delete this document",
    ]) {
      const action = row.getByRole("button", { name: label });
      await expect(action).toBeVisible();
      // Visible means visible — not a zero-opacity button waiting for a hover.
      await expect(action).toHaveCSS("opacity", "1");
    }

    await row.getByRole("button", { name: "Delete this document" }).click();
    await expect(page.getByRole("dialog")).toContainText(/delete/i);
    await page.keyboard.press("Escape");
  });

  test("a row opens the document beside the table, and steps to the next", async ({
    page,
  }) => {
    await openKnowledge(page);

    await page.getByText("northwind-logistics-msa.pdf").first().click();
    // The panel opens beside the table on the row that was clicked, and says
    // where in the current view that row is.
    // Where the open row sits in the current view — not the table's own
    // "Showing 1-14 of 14", which says the same numbers about the page.
    await expect(
      page.getByText(`${DOC_COUNT} of ${DOC_COUNT}`, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /next|previous/i }).first(),
    ).toBeVisible();
  });

  test("the footer pages through the collection", async ({ page }) => {
    await openTenPerPage(page);
    await expect(footer(page)).toContainText(`Showing 1–10 of ${DOC_COUNT}`);
    await expect(bodyRows(page)).toHaveCount(10);

    await page.getByTitle("Next page").click();
    await expect(footer(page)).toContainText(`Showing 11–${DOC_COUNT}`);
    await expect(page.getByTitle("Previous page")).toBeEnabled();

    await page.getByTitle("Previous page").click();
    await expect(footer(page)).toContainText("Showing 1–10");

    await page.getByLabel("Rows per page").selectOption("25");
    await expect(bodyRows(page)).toHaveCount(DOC_COUNT);
    await expect(page.getByTitle("Next page")).toBeDisabled();
  });

  test("a filter that empties the last page does not strand the reader", async ({
    page,
  }) => {
    await openTenPerPage(page);
    await page.getByTitle("Next page").click();
    await expect(footer(page)).toContainText("Showing 11–");

    await page
      .getByPlaceholder("Search a name, an object ID, or any extracted value")
      .fill("helios");
    await expect(footer(page)).toContainText("Showing 1–2 of 2 documents");
  });

  test("every request this screen makes goes to the knowledge service", async ({
    page,
  }) => {
    const recorder = await mockRichKnowledge(page);
    await page.goto(url);
    await expect(footer(page)).toContainText(`of ${DOC_COUNT} documents`);
    expect(recorder.violations).toEqual([]);
  });
});
