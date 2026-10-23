import { expect, test } from "@playwright/test";

import { KNOWLEDGE_ID, mockKnowledgeService } from "./fixtures";

/**
 * The two panels on Conversations — the collection rail and the conversation
 * list — collapse independently, and each is remembered.
 *
 * Persistence is the part worth a test: the state is read from `localStorage`
 * after mount rather than during the first render, because these pages are
 * prerendered and seeding it any earlier makes the server and client markup
 * disagree.
 */
test.describe("Document Intelligence — collapsible panels", () => {
  test("the rail and the conversation list collapse, and stay collapsed", async ({
    page,
  }) => {
    await mockKnowledgeService(page);
    await page.goto(
      `/document-intelligence/conversations?collection=${KNOWLEDGE_ID}`,
    );

    await expect(page.getByRole("link", { name: "Knowledge" })).toBeVisible();

    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await page.getByRole("button", { name: "Collapse conversations" }).click();

    // The rail keeps its icons — and their names, from `title`, so the links
    // stay reachable — but drops every label and the byline under them.
    await expect(page.getByRole("link", { name: "Knowledge" })).toHaveText("");
    await expect(page.getByText("Same APIs · same backend")).toHaveCount(0);
    // The conversation list is dropped rather than hidden, so nothing under it
    // can take a click or a tab stop.
    await expect(page.getByPlaceholder("Search conversations…")).toHaveCount(0);

    await page.reload();
    await expect(
      page.getByRole("button", { name: "Expand sidebar" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Expand conversations" }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Expand sidebar" }).click();
    await page.getByRole("button", { name: "Expand conversations" }).click();
    await expect(page.getByRole("link", { name: "Knowledge" })).toBeVisible();
    await expect(page.getByPlaceholder("Search conversations…")).toBeVisible();
  });
});
