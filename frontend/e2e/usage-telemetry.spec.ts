import { expect, test } from "@playwright/test";

import { KNOWLEDGE_ID, mockKnowledgeService } from "./fixtures";
import { mockRichKnowledge } from "./knowledgeFixture";

/**
 * What a request took and cost, where an operator reads it.
 *
 * A reloaded conversation's Full logs lists every model call by operation —
 * the model that served it (a fallback flagged), time, tokens and ≈cost — from
 * the usage block the service stores with the turn. A document's detail shows
 * how it was processed. Neither ever shows an accuracy figure.
 */

const SHOTS = process.env.E2E_SHOTS_DIR;

test.describe("Usage telemetry", () => {
  test("a stored answer's Full logs lists every operation and the total", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.goto(
      `/document-intelligence/conversations?collection=${KNOWLEDGE_ID}`,
    );
    await page.getByText("Who has Python experience?").first().click();
    await expect(
      page.getByText("Priya Sharma lists six years of Python."),
    ).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: /Full logs/ }).click();
    const table = page.getByRole("table", {
      name: "Model calls of this answer, by operation",
    });
    await expect(table).toBeVisible();
    await expect(table.getByText("Retrieval agent")).toBeVisible();
    await expect(table.getByText("fallback from gpt-5.6-luna")).toBeVisible();
    await expect(table.getByText("Embeddings")).toBeVisible();
    await expect(table.getByText("4.20s")).toBeVisible();
    await expect(page.getByText("≈$0.0062").first()).toBeVisible();
    await expect(page.getByText(/accuracy/i)).toHaveCount(0);
    if (SHOTS) {
      await table.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${SHOTS}/full-logs-stored-turn.png` });
    }
    expect(recorder.violations).toEqual([]);
  });

  test("a document shows how it was processed", async ({ page }) => {
    await mockRichKnowledge(page);
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.goto(
      `/document-intelligence/knowledge/?collection=${KNOWLEDGE_ID}`,
    );
    await expect(page.getByText(/^Showing /)).toBeVisible();
    await page.getByLabel("Rows per page").selectOption("25");
    await page.getByText("northwind-logistics-msa.pdf").first().click();
    const detail = page.getByRole("dialog");

    const bar = detail.getByRole("button", { name: /Processing/ });
    await expect(bar).toContainText("9.10s");
    await expect(bar).toContainText("gpt-5.4-nano");
    await expect(bar).toContainText("fallback model");
    await bar.click();
    const steps = detail.getByRole("table", {
      name: "Processing steps of this document",
    });
    await expect(steps.getByText("Read the document")).toBeVisible();
    await expect(
      steps.getByText("fallback from gemini-3.1-flash-lite"),
    ).toBeVisible();
    await expect(steps.getByText("Embedded for search")).toBeVisible();
    await expect(steps.getByText("$0.0037")).toBeVisible();
    if (SHOTS) {
      await page.screenshot({ path: `${SHOTS}/document-processing.png` });
    }
  });
});
