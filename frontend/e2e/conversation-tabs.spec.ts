import { expect, test } from "@playwright/test";

import { KNOWLEDGE_ID, mockKnowledgeService, OWNER_USER_ID } from "./fixtures";

/**
 * Only the signed-in account's conversations belong in this chat view. Other
 * users and widget visitors are monitored from Share → Activity instead.
 */
const conversationsUrl = `/document-intelligence/conversations?collection=${KNOWLEDGE_ID}`;

test.describe("Document Intelligence — conversation tabs", () => {
  test("shows only the signed-in account's conversations", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(conversationsUrl);
    // Scoped to the list: the same titles appear as suggested questions in the
    // chat pane beside it.
    const list = page.getByRole("complementary", { name: "Conversations" });

    // Mine: the signed-in account's own thread, and only it.
    await expect(list.getByText("Who has Python experience?")).toBeVisible();
    await expect(
      list.getByText("Which candidates know Kubernetes?"),
    ).toHaveCount(0);
    const mineCall = recorder.urls.find((url) => url.includes("/sessions?"));
    expect(new URL(mineCall as string).searchParams.get("user_id")).toBe(
      OWNER_USER_ID,
    );

    await expect(page.getByRole("tab", { name: "Users" })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Widget" })).toHaveCount(0);
    await expect(page.getByPlaceholder("Send a message…")).toBeVisible();

    expect(recorder.violations).toEqual([]);
  });
});
