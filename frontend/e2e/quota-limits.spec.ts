import { expect, test } from "@playwright/test";

import { KNOWLEDGE_ID, mockKnowledgeService, OWNER } from "./fixtures";

/**
 * Two changes, both about what a reader is shown.
 *
 * Analytics is out of the rail while the screen itself stays reachable, so the
 * assertion is on the navigation rather than on the route — a spec that only
 * checked the URL would pass with the link still drawn.
 *
 * The quota refusal is the opposite problem: the service answers a full account
 * with an object rather than a sentence, and the catch behind Create used to
 * swallow it, so the button did nothing at all. The toast has to name the
 * allowance that refused and carry the reader to the tab that shows its
 * ceiling.
 */

/** A full account, exactly as the knowledge service answers one. */
const QUOTA_REFUSAL = {
  error: "quota_exceeded",
  message:
    "You have reached one of your account's usage limits. Delete unused items or upgrade your plan.",
  remaining_quota: {
    indexed_characters: -25212694,
    knowledge_items: -27261,
    agents: -1,
    conversations: 100,
    messages: 500,
  },
  would_exceed: [
    { limit_name: "max_agents", current: 10, limit: 10, requested: 1 },
  ],
};

test.describe("Document Intelligence — Analytics is hidden", () => {
  test("the rail no longer offers it, and the board card no longer links to it", async ({
    page,
  }) => {
    await mockKnowledgeService(page);
    await page.goto(
      `/document-intelligence/knowledge?collection=${KNOWLEDGE_ID}`,
    );

    const rail = page.getByRole("navigation", { name: "Collection" });
    await expect(rail.getByRole("link", { name: "Knowledge" })).toBeVisible();
    await expect(rail.getByRole("link", { name: "Analytics" })).toHaveCount(0);

    await page.goto("/document-intelligence");
    await expect(page.getByText("Resumes")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Analytics for Resumes" }),
    ).toHaveCount(0);
    // The card still offers the rest of its actions — this hid one, not all.
    await expect(
      page.getByRole("button", { name: "Settings for Resumes" }),
    ).toHaveCount(1);
  });

  test("the screen itself still answers on its own route", async ({ page }) => {
    await mockKnowledgeService(page);
    await page.goto(
      `/document-intelligence/analytics?collection=${KNOWLEDGE_ID}`,
    );

    // The screen itself rather than a redirect: its own first card is drawn.
    await expect(
      page.getByRole("heading", { name: "Query Volume" }),
    ).toBeVisible();
  });
});

test.describe("Document Intelligence — a full account", () => {
  test("names the allowance that refused and links to the Limits tab", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    // Registered after the fixture, so it wins for this one route.
    await page.route("**/api/knowledge/v1", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      return route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify(QUOTA_REFUSAL),
      });
    });

    await page.goto("/document-intelligence");
    await page
      .getByRole("button", { name: /create document collection/i })
      .click();

    // Creating a collection is a screen now, so the refusal has to survive
    // three steps rather than arriving inside a dialog that is still open.
    await page.getByRole("button", { name: /Contracts/ }).click();
    await page.getByLabel(/collection name/i).fill("Contracts");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: /looks right/i }).click();
    // The sample document is optional, and this spec is about the refusal.
    await page.getByRole("button", { name: /create it empty/i }).click();

    const toast = page.getByRole("status").filter({ hasText: "usage limits" });
    await expect(toast).toContainText(
      "You have reached one of your account's usage limits.",
    );
    await expect(toast).toContainText("Agents: 10 of 10 used.");

    await toast.getByRole("button", { name: /view plan limits/i }).click();

    // `trailingSlash` is on, so the pushed route lands as `/usage/?tab=limits`.
    await expect(page).toHaveURL(
      /\/document-intelligence\/usage\/?\?tab=limits$/,
    );
    // The Limits tab itself, not Overview: its heading and its own route.
    await expect(
      page.getByRole("heading", { name: "Your Plan" }),
    ).toBeVisible();
    // The tab's own route, which only the Limits tab reads.
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/owners/${OWNER}/usage/limits/screen`,
    );
  });
});
