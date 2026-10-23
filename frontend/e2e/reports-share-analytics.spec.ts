import { expect, test } from "@playwright/test";

import {
  API_KEY_PREFIX,
  INVITED_EMAIL,
  KNOWLEDGE_ID,
  mockKnowledgeService,
  OWNER_USER_ID,
} from "./fixtures";

/**
 * The three screens this product gained alongside NL2SQL's: batch reports,
 * Share and Analytics.
 *
 * Each assertion checks what reached the *service*, not only what reached the
 * screen. Every one of these routes ignores a query parameter it does not know
 * rather than rejecting it — a filter sent under the wrong name comes back as a
 * full, unfiltered page, which looks exactly like a working filter on data this
 * small. So the specs read `recorder.calls` and the URLs beside the rendering.
 */

const url = (screen: string) =>
  `/document-intelligence/${screen}?collection=${KNOWLEDGE_ID}`;

test.describe("Document Intelligence — batch reports", () => {
  test("lists a report, opens its run, and shows the answers with their sources", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("batch-query-reports"));

    // The list: the report, what it holds, and its schedule.
    await expect(
      page.getByRole("button", {
        name: "Weekly candidate pool review",
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByText("2 groups · 3 questions")).toBeVisible();
    // The stored expression is `0 9 * * 1`, which the shared schedule helpers
    // read back as the weekly schedule it is rather than as raw cron.
    // Scoped to the table: the schedule filter is built from the reports on
    // screen, so "Weekly" is also one of its options.
    await expect(
      page.getByRole("table").getByText("Weekly", { exact: true }),
    ).toBeVisible();

    // Reports are read from this collection's own route, not an agent's.
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${KNOWLEDGE_ID}/reports`,
    );
    expect(recorder.violations).toEqual([]);

    // The report's own page: its definition, then its runs.
    await page
      .getByRole("button", {
        name: "Weekly candidate pool review",
        exact: true,
      })
      .click();
    await expect(
      page.getByText("Weekly on Monday · 09:00 · UTC"),
    ).toBeVisible();
    await expect(page.getByText("Answer from the resumes only.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Run now" })).toBeVisible();

    // One run, finished, with a link to its answers.
    await page.getByRole("button", { name: "View answers →" }).click();

    // The summary the run reports, and the two answers under their group.
    await expect(page.getByText("Two of the three questions")).toBeVisible();
    await expect(
      page.getByText("has six years of Python", { exact: false }),
    ).toBeVisible();
    // A question that failed shows why, rather than an empty answer.
    await expect(
      page.getByText("No availability is recorded in these documents."),
    ).toBeVisible();
    // And the answer carries the documents behind it, in the same collapsed
    // panel the chat thread uses.
    await page.getByRole("button", { name: "1 Source" }).click();
    await expect(
      page.getByText("priya-sharma-resume.pdf").first(),
    ).toBeVisible();

    // The body is only fetched once the run has stopped moving.
    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${KNOWLEDGE_ID}/reports/rep-7f21/runs/run-1a2b`,
    );
  });

  test("filters by schedule and by the last run's status", async ({ page }) => {
    await mockKnowledgeService(page);
    await page.goto(url("batch-query-reports"));

    const row = page.getByRole("button", {
      name: "Weekly candidate pool review",
      exact: true,
    });
    await expect(row).toBeVisible();
    // The last run and its status come from the report's own runs.
    await expect(page.getByRole("table").getByText("completed")).toBeVisible();

    // Both dropdowns offer the whole vocabulary, not only what is on screen:
    // this collection has one weekly report, and "Monthly" is still offered.
    const schedule = page.getByLabel("Filter by schedule");
    await expect(schedule.getByRole("option")).toHaveText([
      "All schedules",
      "Manual",
      "Daily",
      "Weekly",
      "Monthly",
      "Cron",
    ]);
    const status = page.getByLabel("Filter by the status of the last run");
    await expect(status.getByRole("option")).toHaveText([
      "All statuses",
      "Completed",
      "Running",
      "Pending",
      "Failed",
      "Cancelled",
      "Never run",
    ]);

    // A schedule the report is not on empties the list, and its own keeps it.
    await schedule.selectOption("Monthly");
    await expect(row).toHaveCount(0);
    await expect(page.getByText("0 reports")).toBeVisible();
    await schedule.selectOption("Weekly");
    await expect(row).toBeVisible();

    // Same for the status of its last run.
    await status.selectOption("failed");
    await expect(row).toHaveCount(0);
    await status.selectOption("completed");
    await expect(row).toBeVisible();

    // Clear puts both back.
    await page.getByRole("button", { name: "Clear" }).click();
    await expect(schedule).toHaveValue("");
    await expect(status).toHaveValue("");
  });

  test("asks before it deletes a report", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("batch-query-reports"));

    await page.getByRole("button", { name: "Delete" }).click();
    // Nothing has been sent yet — the row's button opens the question.
    expect(recorder.calls.filter((call) => call.startsWith("DELETE"))).toEqual(
      [],
    );
    await expect(
      page.getByText("and its run history go with it"),
    ).toBeVisible();

    await page.getByRole("button", { name: "Delete report" }).click();
    await expect
      .poll(() => recorder.calls.filter((call) => call.startsWith("DELETE")))
      .toEqual([`DELETE /api/knowledge/v1/${KNOWLEDGE_ID}/reports/rep-7f21`]);
  });

  test("creates a report from an uploaded CSV, parsed in the browser", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("batch-query-reports"));
    await page.getByRole("button", { name: "New report" }).click();

    await page.getByLabel("Report name").fill("Imported review");
    await page.getByRole("button", { name: "Upload JSON or CSV" }).click();
    await page.setInputFiles('input[type="file"]', {
      name: "questions.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        `group_id,group_type,question_text\ng1,independent,"Who knows Python?"\ng2,sequential,"Of those, who has led a team?"\n`,
      ),
    });

    // Two groups, three… no: two questions, one per group — and the quota
    // counter reads the collection's own cap rather than a number typed here.
    await expect(page.getByText("2 / 20 used")).toBeVisible();
    await expect(page.getByLabel("Question 1 of group 1")).toHaveValue(
      "Who knows Python?",
    );

    await page.getByRole("button", { name: "Save report" }).click();

    // Polled, not read once: the click and the request are not the same tick,
    // and reading `bodies` immediately made this assert on an empty map under
    // parallel load.
    await expect
      .poll(
        () =>
          recorder.bodies[`POST /api/knowledge/v1/${KNOWLEDGE_ID}/reports`]
            ?.length ?? 0,
      )
      .toBeGreaterThan(0);
    const body = recorder.bodies[
      `POST /api/knowledge/v1/${KNOWLEDGE_ID}/reports`
    ]?.[0] as {
      name: string;
      query_groups: { type: string; queries: string[] }[];
      run_immediately: boolean;
    };
    expect(body.name).toBe("Imported review");
    expect(body.query_groups).toEqual([
      { type: "independent", queries: ["Who knows Python?"] },
      { type: "sequential", queries: ["Of those, who has led a team?"] },
    ]);
    // "Save report" must not start a run; that is what "Save & run" is for.
    expect(body.run_immediately).toBe(false);
  });

  test("sends a picked schedule as the cron the service stores", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("batch-query-reports"));
    await page.getByRole("button", { name: "New report" }).click();

    await page.getByLabel("Report name").fill("Daily check");
    await page
      .getByLabel("Question 1 of group 1")
      .fill("Who joined this week?");
    await page.getByLabel("Schedule").selectOption("daily");
    await page.getByLabel("Time", { exact: true }).fill("07:30");
    await page.getByRole("button", { name: "Save & run" }).click();

    await expect
      .poll(
        () =>
          recorder.bodies[`POST /api/knowledge/v1/${KNOWLEDGE_ID}/reports`]
            ?.length ?? 0,
      )
      .toBeGreaterThan(0);
    const body = recorder.bodies[
      `POST /api/knowledge/v1/${KNOWLEDGE_ID}/reports`
    ]?.[0] as {
      schedule: { frequency: string; cron: string };
      run_immediately: boolean;
    };
    // The service has no field for a time of day: the picked time is encoded
    // into the expression, which is why `frequency` is always "cron".
    expect(body.schedule.frequency).toBe("cron");
    expect(body.schedule.cron).toBe("30 7 * * *");
    expect(body.run_immediately).toBe(true);
  });
});

/**
 * Every feature on Share is a disclosure row, closed on arrival, and a closed
 * row renders nothing — so a spec has to open the one it is about. The header
 * button is addressed by what it controls rather than by its text, because its
 * accessible name carries the row's state pill and one-line purpose too.
 */
const shareRow = (page: import("@playwright/test").Page, id: string) =>
  page.locator(`button[aria-controls="share-${id}"]`);

/**
 * Opens a row and waits until it says it is open.
 *
 * The header renders in the server HTML before React has hydrated it, so a click
 * that arrives in that window lands on an element with no handler yet and is
 * simply lost. Retrying the click until `aria-expanded` flips is what makes this
 * deterministic instead of a race the suite loses about one run in three.
 */
const openRow = async (
  page: import("@playwright/test").Page,
  id: string,
): Promise<void> => {
  const row = shareRow(page, id);
  await expect(async () => {
    await row.click();
    await expect(row).toHaveAttribute("aria-expanded", "true");
  }).toPass({ timeout: 15_000 });
};

test.describe("Document Intelligence — Share", () => {
  test("shows the API channel, its key, and the people who can get in", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("share"));

    await openRow(page, "api");
    // The snippet names this collection's own chat route.
    await expect(
      page.getByText(`/api/knowledge/v1/${KNOWLEDGE_ID}/chat`),
    ).toBeVisible();

    // Only the key that can read this collection is listed; the one minted for
    // another collection is not.
    await expect(page.getByText(`${API_KEY_PREFIX}…`)).toBeVisible();
    await expect(page.getByText("fsk_live_zzzz…")).toHaveCount(0);

    // The people card: the owner, then whoever was invited.
    await openRow(page, "people");
    await expect(page.getByText("e2e@futuresmart.ai")).toBeVisible();
    await expect(page.getByText(INVITED_EMAIL)).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: `Role for ${INVITED_EMAIL}` }),
    ).toHaveValue("viewer");

    // Each hosted surface reads its own flag off the chat config — the fixture
    // stores the bubble off and the full-page link on.
    await expect(
      page.getByRole("switch", { name: "Enable the website bubble" }),
    ).toHaveAttribute("aria-checked", "false");
    await expect(
      page.getByRole("switch", { name: "Enable the full-page link" }),
    ).toHaveAttribute("aria-checked", "true");

    expect(recorder.calls).toContain(
      `GET /api/knowledge/v1/${KNOWLEDGE_ID}/invitations`,
    );
    expect(recorder.calls).toContain("GET /api/knowledge/v1/api-keys");
    expect(recorder.violations).toEqual([]);
  });

  test("invites someone with the role that was picked", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("share"));

    await openRow(page, "people");
    await page
      .getByLabel("Email address to invite")
      .fill("newcomer@futuresmart.ai");
    await page
      .getByLabel("Role for the person being invited")
      .selectOption("editor");
    await page.getByRole("button", { name: "Invite", exact: true }).click();

    await expect
      .poll(
        () =>
          recorder.bodies[`POST /api/knowledge/v1/${KNOWLEDGE_ID}/invitations`]
            ?.length ?? 0,
      )
      .toBeGreaterThan(0);
    const body = recorder.bodies[
      `POST /api/knowledge/v1/${KNOWLEDGE_ID}/invitations`
    ]?.[0] as { invitations: { email: string; role: string }[] };
    expect(body.invitations).toEqual([
      { email: "newcomer@futuresmart.ai", role: "editor" },
    ]);
  });

  test("saves the appearance as form fields, and previews it", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("share"));

    await openRow(page, "appearance");
    // The stored appearance is what the form and the preview open on.
    await expect(page.getByLabel("Heading *", { exact: true })).toHaveValue(
      "Ask the resumes",
    );
    // The preview is a row of its own, drawing the same draft — it moves as the
    // form is edited rather than reloading a saved surface.
    await openRow(page, "preview");
    await expect(
      page.getByRole("paragraph").filter({ hasText: "Hello! Ask me about" }),
    ).toBeVisible();

    await page
      .getByLabel("Heading *", { exact: true })
      .fill("Ask the candidate pool");
    await page.getByRole("button", { name: "Save appearance" }).click();

    // Multipart, not JSON: this route stores nothing from a JSON body and still
    // answers 200, so the request shape is the assertion that matters.
    const sent =
      recorder.bodies[
        `POST /api/knowledge/v1/${KNOWLEDGE_ID}/widget-config`
      ]?.[0];
    expect(String(sent)).toContain("Ask the candidate pool");
    expect(String(sent)).toContain('name="heading"');
  });

  test("keeps a stored brand colour that is none of the presets", async ({
    page,
  }) => {
    await mockKnowledgeService(page);
    await page.goto(url("share"));

    await openRow(page, "appearance");
    // The fixture stores #0f9488 — a preset — so five swatches and one pressed.
    await expect(
      page.getByRole("button", { name: /^Brand colour/ }),
    ).toHaveCount(5);
    await expect(
      page.getByRole("button", { name: "Brand colour Teal" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  test("shows every launcher icon and avatar choice, asset or not", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("share"));

    await openRow(page, "appearance");
    // The defaults the service offers are choosable, and each one is visible
    // even when its asset cannot be painted — these fixture URLs never load, so
    // this is the fallback path the real `binary/octet-stream` assets take when
    // even the CORS retry fails.
    const icons = page.getByRole("button", { name: /^Launcher icon \d$/ });
    await expect(icons).toHaveCount(2);
    for (const icon of await icons.all()) {
      await expect(icon.locator("svg")).toBeVisible();
    }

    const avatars = page.getByRole("button", { name: /^Chat avatar \d$/ });
    await expect(avatars).toHaveCount(1);
    await expect(avatars.first().locator("svg")).toBeVisible();

    // Picking one marks it, so the choice is not invisible either.
    await icons.nth(1).click();
    await expect(icons.nth(1)).toHaveAttribute("aria-pressed", "true");
    await expect(icons.first()).toHaveAttribute("aria-pressed", "false");

    // And it is the chosen URL that gets saved, not the glyph standing in for
    // it — and as `widget_icon_url`, the field a default goes in.
    await page.getByRole("button", { name: "Save appearance" }).click();
    await expect
      .poll(() =>
        String(
          recorder.bodies[
            `POST /api/knowledge/v1/${KNOWLEDGE_ID}/widget-config`
          ]?.[0] ?? "",
        ),
      )
      .toContain("https://assets.invalid/icons/spark.svg");
    expect(
      String(
        recorder.bodies[
          `POST /api/knowledge/v1/${KNOWLEDGE_ID}/widget-config`
        ]?.[0] ?? "",
      ),
    ).toContain('name="widget_icon_url"');
  });

  test("Activity asks the service for one origin per tab", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("share"));
    await page.getByRole("tab", { name: "Activity" }).click();

    // Shared users: a dashboard conversation somebody else had. The owner's own
    // is absent — that is what the Conversations screen is.
    await expect(
      page.getByRole("button", { name: /Which candidates know Kubernetes\?/ }),
    ).toBeVisible();
    await expect(page.getByText("priya.nair@futuresmart.ai")).toBeVisible();
    await expect(page.getByText("Who has Python experience?")).toHaveCount(0);

    await page.getByRole("tab", { name: /^Widget/ }).click();
    await expect(
      page.getByRole("button", {
        name: /How many candidates have Go experience\?/,
      }),
    ).toBeVisible();
    await expect(page.getByText("Anonymous website visitor")).toBeVisible();

    // The third way in. Before the service recorded an origin this was
    // indistinguishable from a bubble visitor, and was listed as one.
    await page.getByRole("tab", { name: /^API/ }).click();
    await expect(
      page.getByRole("button", { name: /notice period under 30 days/ }),
    ).toBeVisible();
    await expect(page.getByText("Through an API key")).toBeVisible();

    // One tab is one query: the origin and the owner exclusion are sent, and
    // nothing is split or re-counted in the browser.
    const sources = recorder.urls
      .filter((entry) => entry.includes("/sessions?"))
      .map((entry) => new URL(entry).searchParams);
    expect(new Set(sources.map((params) => params.get("source")))).toEqual(
      new Set(["shared", "widget", "api", null]),
    );
    for (const params of sources) {
      expect(params.get("exclude_user_id")).toBe(OWNER_USER_ID);
      // Never narrowed to one account: this screen is everybody else's.
      expect(params.get("user_id")).toBeNull();
    }
  });

  test("Activity counts each tab, and owns up to what no tab can show", async ({
    page,
  }) => {
    await mockKnowledgeService(page);
    await page.goto(url("share"));
    await page.getByRole("tab", { name: "Activity" }).click();

    // The counts are the service's totals for that origin, not the rows in
    // hand: one shared, one bubble, one key.
    await expect(
      page.getByRole("tab", { name: /^Shared users\s*1$/ }),
    ).toBeVisible();
    await expect(page.getByRole("tab", { name: /^Widget\s*1$/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /^API\s*1$/ })).toBeVisible();

    // The fixture holds one session from before the service recorded an
    // origin. It matches no `?source=`, so it is on no tab — and the screen
    // says so rather than quietly listing fewer conversations.
    await expect(
      page.getByText(/1 older conversation is not listed on any tab/),
    ).toBeVisible();
  });

  test("Activity sorts on the service, not on the page in hand", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("share"));
    await page.getByRole("tab", { name: "Activity" }).click();

    const heading = page.getByRole("button", { name: "Questions asked" });
    await heading.click();
    await expect
      .poll(() =>
        recorder.urls.some((entry) => {
          const params = new URL(entry).searchParams;
          return (
            params.get("sort") === "questions" && params.get("order") === "asc"
          );
        }),
      )
      .toBe(true);

    // Second click is descending; the third clears it, and an unsorted list
    // sends no sort at all — the route's own default is what this screen wants.
    await heading.click();
    await expect
      .poll(() =>
        recorder.urls.some(
          (entry) => new URL(entry).searchParams.get("sort") === "questions",
        ),
      )
      .toBe(true);
  });

  test("opens one of those conversations read-only", async ({ page }) => {
    await mockKnowledgeService(page);
    await page.goto(url("share"));
    await page.getByRole("tab", { name: "Activity" }).click();
    await page
      .getByRole("button", { name: /Which candidates know Kubernetes\?/ })
      .click();

    // The thread is the same chat stack the owner's own screen renders, with
    // the composer replaced rather than merely disabled.
    await expect(page.getByText("Read-only", { exact: true })).toBeVisible();
    await expect(
      page.getByText(/this is someone else.s conversation\./),
    ).toBeVisible();
    await expect(page.getByPlaceholder("Send a message…")).toHaveCount(0);

    await page.getByRole("button", { name: "Back", exact: true }).click();
    await expect(
      page.getByRole("tab", { name: /^Shared users/ }),
    ).toBeVisible();
  });

  test("opens with every row closed, and opens one without its neighbours", async ({
    page,
  }) => {
    await mockKnowledgeService(page);
    await page.goto(url("share"));

    // Six features, none expanded — the whole point of the row treatment is
    // that the screen fits before anything is opened.
    await expect(page.getByText("Nothing open")).toBeVisible();
    await expect(
      page.locator('button[aria-controls^="share-"][aria-expanded="true"]'),
    ).toHaveCount(0);
    // A closed row renders nothing, so the invite field is not merely hidden.
    await expect(page.getByLabel("Email address to invite")).toHaveCount(0);

    await openRow(page, "people");
    await expect(page.getByLabel("Email address to invite")).toBeVisible();
    await expect(shareRow(page, "api")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  test("switches a hosted surface on without expanding its row", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("share"));

    const bubble = page.getByRole("switch", {
      name: "Enable the website bubble",
    });
    // Same hydration window as the row headers — click until it takes.
    await expect(async () => {
      await bubble.click();
      await expect(bubble).toHaveAttribute("aria-checked", "true");
    }).toPass({ timeout: 15_000 });

    // The switch is a sibling of the header button, never a child, so using it
    // cannot open the row it sits on.
    await expect(shareRow(page, "widget")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await expect
      .poll(
        () =>
          recorder.bodies[`PUT /api/knowledge/v1/${KNOWLEDGE_ID}/chat/config`]
            ?.length ?? 0,
      )
      .toBeGreaterThan(0);
    // Only its own field: the route merges, and the full-page link is a
    // separate flag that this switch must not disturb.
    expect(
      recorder.bodies[`PUT /api/knowledge/v1/${KNOWLEDGE_ID}/chat/config`]?.[0],
    ).toEqual({ is_public: true });
  });
});

test.describe("Document Intelligence — Analytics", () => {
  test("renders the shared analytics page on this service's numbers", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("analytics"));

    // Overview: the window's own totals.
    await expect(page.getByText("60%", { exact: false }).first()).toBeVisible();
    await expect(
      page.getByRole("tab", { name: "Model performance" }),
    ).toBeVisible();

    const scorecardCall = recorder.urls.find((entry) =>
      entry.includes("/health/scorecard"),
    );
    // The rolling window goes as `period`, and `top_n` comes from the spec.
    const params = new URL(scorecardCall as string).searchParams;
    expect(params.get("period")).toBe("week");
    expect(params.get("top_n")).toBe("10");
    // No `bucket`: this route publishes no such parameter.
    expect(params.has("bucket")).toBe(false);
    expect(recorder.violations).toEqual([]);
  });

  test("asks trends for buckets, never for a period", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("analytics"));
    // Trends is read by the Overview tab's charts.
    await page.waitForFunction(
      () =>
        performance
          .getEntriesByType("resource")
          .some((entry) => entry.name.includes("/health/trends")),
      undefined,
      { timeout: 15000 },
    );

    const call = recorder.urls.find((entry) =>
      entry.includes("/health/trends"),
    );
    const params = new URL(call as string).searchParams;
    expect(params.get("bucket")).toBe("day");
    expect(params.get("buckets")).toBe("7");
    expect(params.has("period")).toBe(false);
    // `metrics` belongs to the shared hook, which asks for what its own charts
    // plot; the window builder must not fight it for that parameter.
    expect(params.get("metrics")).toContain("success_rate");
  });

  test("Model performance ranks the answer models and compares one step", async ({
    page,
  }) => {
    await mockKnowledgeService(page);
    await page.goto(url("analytics"));
    await page.getByRole("tab", { name: "Model performance" }).click();

    // Two models across two providers, busiest first.
    await expect(
      page.getByRole("heading", { name: /Answer models used by this/ }),
    ).toBeVisible();
    const breakdown = page
      .getByRole("table")
      .filter({ hasText: "Steps used on" });
    await expect(breakdown.getByText("gpt-5.6-terra")).toBeVisible();
    await expect(breakdown.getByText("claude-haiku")).toBeVisible();

    // Haiku is both the fastest and the cheapest of the two, and says so.
    await expect(page.getByText("Fastest").first()).toBeVisible();
    await expect(page.getByText("Cheapest").first()).toBeVisible();

    // The overall row names the slowest step rather than averaging percentiles,
    // and the step names are humanised from what the service sent.
    // "Compose answer 7.0s" — the step's own p95, not a mean of percentiles.
    await expect(
      breakdown.getByRole("cell", { name: /Compose answer 7\.0s/ }),
    ).toBeVisible();

    // The comparison starts on the first step reported and can be switched.
    const stepSelect = page.getByLabel("Compare models on step");
    await expect(stepSelect).toHaveValue("understand_question");
    await expect(stepSelect.getByRole("option")).toHaveText([
      "Understand question",
      "Compose answer",
    ]);

    // Only the models that ran on the chosen step are compared.
    const compare = page.getByRole("table").filter({ hasText: "Verdict" });
    await expect(compare.getByRole("row")).toHaveCount(3); // header + 2 models
    await stepSelect.selectOption("compose_answer");
    await expect(compare.getByRole("row")).toHaveCount(2); // header + 1 model

    // No request of its own: this tab is the scorecard, cut a different way.
    await expect(page.getByRole("tab", { name: "Failures" })).toHaveCount(0);
  });

  test("the question history sends the status the dropdown offers", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    await page.goto(url("analytics"));
    await page.getByRole("tab", { name: "Question history" }).click();
    await expect(
      page.getByText("show me all added document list").first(),
    ).toBeVisible();

    // "Answered" is `status=success` on this service — not `outcome=`, which it
    // ignores. Picking it must therefore narrow the log to nothing here.
    await page.getByLabel("Status").selectOption("success");
    await expect(page.getByText("show me all added document list")).toHaveCount(
      0,
    );

    const call = recorder.urls
      .filter((entry) => entry.includes("/health/queries"))
      .at(-1);
    const params = new URL(call as string).searchParams;
    expect(params.get("status")).toBe("success");
    expect(params.has("outcome")).toBe(false);
  });
});
