import { expect, test, type Page, type Route } from "@playwright/test";

import { KNOWLEDGE_ID, mockKnowledgeService, type Recorder } from "./fixtures";

/**
 * A first run, end to end, and what the screen says when it goes wrong.
 *
 * The other specs each prove one screen. This one walks the path a new account
 * actually takes — an empty board, a collection made on the create screen, one
 * file uploaded, the file finishing, a question answered from it — in one test,
 * so a break anywhere between two screens shows up here even when each screen
 * still passes on its own. It ends on the network trace: everything the journey
 * did went to the knowledge service, and nothing reached the chat, dashboard or
 * usage services this product used to call.
 *
 * The tests after it are the unhappy halves of the same steps. Each asserts what
 * a person is told, not only that nothing crashed: an upload that failed says
 * so, a document that failed says why, one still being read is not called
 * ready, and a session that is gone goes to sign-in.
 */

/** What `POST /v1` answers in the shared fixture. */
const NEW_ID = "new-collection-id";
const COLLECTION_NAME = "Candidate resumes";
const BATCH_ID = "batch-7c1e";
const OBJECT_ID = "5f0f1c2e-2d3b-4a55-9d0e-7f1b2c3d4e5f";
const FILE_NAME = "ananya-rao-resume.pdf";
const ANSWER = "Ananya Rao has seven years of Python.";
const PASSAGE = "Seven years of Python, most of it on data platforms.";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

const pathOf = (url: string) => {
  const { pathname } = new URL(url);
  const start = pathname.indexOf("/api/");
  return start === -1 ? pathname : pathname.slice(start);
};

/** The synthetic upload: a few bytes that look like a PDF to the picker. */
const syntheticPdf = {
  name: FILE_NAME,
  mimeType: "application/pdf",
  buffer: Buffer.from("%PDF-1.4\nAnanya Rao — seven years of Python.\n%%EOF"),
};

const documentRow = (
  knowledgeId: string,
  status: string,
  overrides: Record<string, unknown> = {},
) => ({
  id: "2001",
  object_id: OBJECT_ID,
  knowledge_id: knowledgeId,
  file_name: FILE_NAME,
  status,
  error_detail: null,
  content_type: "file",
  created_time: "2026-09-28T09:00:00.000000",
  updated_time: "2026-09-28T09:00:00.000000",
  source_presigned_url: null,
  extracted_s3_url: null,
  ...overrides,
});

/** The v2 list shape the shared fixture answers with. */
const documentPage = (rows: unknown[]) => ({
  document: rows,
  total_count: rows.length,
  current_page: 1,
  total_pages: 1,
});

/**
 * The batch upload pair, the S3 put between them, and a stateful documents list
 * for one collection — the parts of the knowledge service the shared fixture
 * leaves to the spec.
 *
 * Registered after `mockKnowledgeService`, so Playwright tries these first; a
 * request this does not answer falls back to the fixture. Every request this
 * answers is recorded on the fixture's own recorder, in its `METHOD path`
 * format, so the trace at the end is one list.
 */
async function mockUpload(
  page: Page,
  recorder: Recorder,
  knowledgeId: string,
  options: {
    putStatus?: number;
    registerStatus?: number;
  } = {},
) {
  /**
   * `none` until the batch is registered; then `processing` until the spec says
   * the service has finished reading it.
   */
  const state = { document: "none" as "none" | "processing" | "completed" };
  const s3Puts: string[] = [];

  const record = (route: Route) => {
    const request = route.request();
    recorder.urls.push(request.url());
    recorder.calls.push(`${request.method()} ${pathOf(request.url())}`);
  };

  await page.route("https://s3.test/**", (route) => {
    s3Puts.push(`${route.request().method()} ${route.request().url()}`);
    return route.fulfill({ status: options.putStatus ?? 200, body: "" });
  });

  const base = `/api/knowledge/v1/${knowledgeId}`;
  await page.route("**/api/knowledge/**", (route) => {
    const request = route.request();
    const path = pathOf(request.url());
    const method = request.method();

    if (path === `${base}/upload-urls/batch` && method === "POST") {
      record(route);
      const files = (request.postDataJSON()?.files ?? []) as Array<{
        file_name: string;
      }>;
      return json(route, {
        batch_id: BATCH_ID,
        uploads: files.map((file) => ({
          object_id: OBJECT_ID,
          file_name: file.file_name,
          presigned_url: `https://s3.test/put/${OBJECT_ID}?X-Amz-Signature=abc`,
          download_presigned_url: `https://s3.test/get/${OBJECT_ID}?X-Amz-Signature=def`,
        })),
      });
    }
    if (path === `${base}/documents/batch` && method === "POST") {
      record(route);
      if (options.registerStatus && options.registerStatus >= 400) {
        return json(
          route,
          { detail: "The documents could not be registered." },
          options.registerStatus,
        );
      }
      state.document = "processing";
      return json(route, {
        batch_id: BATCH_ID,
        documents: [
          { object_id: OBJECT_ID, file_name: FILE_NAME, status: "processing" },
        ],
      });
    }
    if (path === `${base}/batch/${BATCH_ID}/status`) {
      record(route);
      return json(route, {
        batch_id: BATCH_ID,
        documents:
          state.document === "none"
            ? []
            : [
                {
                  object_id: OBJECT_ID,
                  file_name: FILE_NAME,
                  status: state.document,
                  error_detail: null,
                },
              ],
      });
    }
    if (
      path === `/api/knowledge/v2/${knowledgeId}/documents` &&
      method === "GET"
    ) {
      record(route);
      return json(
        route,
        documentPage(
          state.document === "none"
            ? []
            : [documentRow(knowledgeId, state.document)],
        ),
      );
    }
    return route.fallback();
  });

  return { state, s3Puts };
}

/**
 * The account's collection list and the new collection's config, so the board
 * is empty before the create screen runs and holds the new collection after.
 */
async function mockEmptyThenCreated(page: Page, recorder: Recorder) {
  const state = { created: false };
  const record = (route: Route) => {
    const request = route.request();
    recorder.urls.push(request.url());
    recorder.calls.push(`${request.method()} ${pathOf(request.url())}`);
  };

  await page.route("**/api/knowledge/**", (route) => {
    const request = route.request();
    const path = pathOf(request.url());
    const method = request.method();

    if (path === "/api/knowledge/v1" && method === "POST") {
      state.created = true;
      return route.fallback();
    }
    if (path === "/api/knowledge/v1" && method === "GET") {
      record(route);
      const ids = state.created ? [NEW_ID] : [];
      return json(route, {
        owner_id: new URL(request.url()).searchParams.get("owner_id"),
        knowledge_services: ids,
        count: ids.length,
      });
    }
    if (path === `/api/knowledge/v1/${NEW_ID}/config` && method === "GET") {
      record(route);
      return json(route, {
        knowledge_id: NEW_ID,
        embedding_model: "paraphrase-multilingual-mpnet-base-v2",
        hybrid_search_enabled: true,
        chunking_strategy: {
          document_level_metadata: true,
          chunk_level_metadata: false,
        },
        collection_name: COLLECTION_NAME,
        service_description: "People applying for the platform team.",
        enrichment_enabled: true,
        use_deterministic_chunking: false,
        created_at: "2026-09-28T09:00:00+00:00",
        updated_at: "2026-09-28T09:00:00+00:00",
      });
    }
    // The answer, cited to the document this journey uploaded rather than to
    // the fixture's own resume.
    if (path === `/api/knowledge/v1/${NEW_ID}/chat` && method === "POST") {
      record(route);
      const question = request.postDataJSON()?.query;
      return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: [
          `data: ${JSON.stringify({ chunk: ANSWER })}\n\n`,
          `data: ${JSON.stringify({
            query: question,
            refined_query: question,
            response: ANSWER,
            session_id: "f1e2d3c4-0000-4000-8000-000000000001",
            chat_id: "f1e2d3c4-0000-4000-8000-000000000002",
            follow_up: [],
            data: {
              knowledge: {
                relevant_sources: {
                  data: [{ id: OBJECT_ID, title: FILE_NAME, context: PASSAGE }],
                  tool_name: "rag",
                },
              },
            },
          })}\n\n`,
        ].join(""),
      });
    }
    return route.fallback();
  });

  return state;
}

/** The Knowledge screen's add-files dialog, with one file chosen and sent. */
async function uploadOneFile(page: Page) {
  await page.getByRole("button", { name: /^Files\b/ }).click();
  const dialog = page.getByRole("dialog", { name: "Upload files" });
  await expect(dialog).toBeVisible();
  await dialog.locator('input[type="file"]').setInputFiles(syntheticPdf);
  await expect(dialog.getByText(FILE_NAME)).toBeVisible();
  await dialog.getByRole("button", { name: "Add", exact: true }).click();
}

/** The one table's row for a document, by its file name. */
const rowFor = (page: Page, name: string) =>
  page.getByRole("row").filter({ hasText: name });

test.describe("A first run", () => {
  test("empty board → create → upload → indexed → answered, knowledge service only", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    // The app's own poll is 35 seconds; the clock is what lets the spec wait
    // for it without waiting 35 seconds.
    await page.clock.install();
    const recorder = await mockKnowledgeService(page, { collections: [] });
    const board = await mockEmptyThenCreated(page, recorder);
    const upload = await mockUpload(page, recorder, NEW_ID);
    await page.setViewportSize({ width: 1400, height: 1000 });

    await test.step("the board is empty", async () => {
      await page.goto("/document-intelligence");
      await expect(
        page.getByText(/No collections yet\. Create one above/),
      ).toBeVisible();
      expect(board.created).toBe(false);
    });

    await test.step("a collection is made on the create screen", async () => {
      await page
        .getByRole("button", { name: /create document collection/i })
        .first()
        .click();
      await expect(page).toHaveURL(/\/document-intelligence\/create\/?$/);
      await expect(
        page.getByRole("heading", { name: "New document collection" }),
      ).toBeVisible();

      await page.getByRole("button", { name: /Resumes \/ CVs/ }).click();
      await page.getByLabel(/collection name/i).fill(COLLECTION_NAME);
      await page
        .getByLabel(/what are these documents about/i)
        .fill("People applying for the platform team.");
      await page.getByRole("button", { name: "Continue" }).click();
      await expect(page.getByLabel("Field name").first()).toHaveValue(
        "candidate_name",
      );
      await page.getByRole("button", { name: /looks right/i }).click();
      await page.getByRole("button", { name: /create it empty/i }).click();

      await expect(
        page.getByRole("heading", { name: `“${COLLECTION_NAME}” is ready` }),
      ).toBeVisible();
      await page.getByRole("button", { name: "Open collection" }).click();
      await expect(page).toHaveURL(
        new RegExp(`/document-intelligence/knowledge/?\\?collection=${NEW_ID}`),
      );
      await expect(
        page.getByText(/No documents yet\. Add some above/),
      ).toBeVisible();
    });

    await test.step("one document is uploaded", async () => {
      await uploadOneFile(page);
      await expect(
        page.getByText("1 document added — they will be ready shortly."),
      ).toBeVisible();
      expect(upload.s3Puts).toEqual([
        `PUT https://s3.test/put/${OBJECT_ID}?X-Amz-Signature=abc`,
      ]);
    });

    await test.step("it is read, and the list says so on its own", async () => {
      const row = rowFor(page, FILE_NAME);
      await expect(row).toBeVisible();
      await expect(row.getByText("Processing")).toBeVisible();
      await expect(row.getByText("Completed")).toHaveCount(0);

      // The service finishes. Nothing is clicked: the screen's own poll, which
      // runs while any document is in flight, is what picks it up.
      upload.state.document = "completed";
      await page.clock.fastForward(36_000);
      await expect(row.getByText("Completed")).toBeVisible();
      await expect(row.getByText("Processing")).toHaveCount(0);
    });

    await test.step("a question is answered from it, with its source", async () => {
      await page
        .getByRole("navigation", { name: "Collection" })
        .getByRole("link", { name: "Conversations" })
        .click();
      await expect(page).toHaveURL(/\/document-intelligence\/conversations/);

      const composer = page.getByPlaceholder("Send a message…");
      await composer.fill("Who has Python experience?");
      await composer.press("Enter");

      await expect(page.getByText(ANSWER).first()).toBeVisible();
      const sources = page.getByRole("tab", { name: /Sources/ });
      await expect(sources).toBeVisible();
      await expect(sources.getByText("1")).toBeVisible();
      await sources.click();
      await expect(page.getByText(PASSAGE).first()).toBeVisible();
      const cited = page.getByRole("listitem").filter({ hasText: PASSAGE });
      await expect(cited.getByText(FILE_NAME)).toBeVisible();
    });

    await test.step("every call went to the knowledge service", async () => {
      expect(recorder.violations).toEqual([]);
      // Nothing aimed at the chat, dashboard or usage services slipped past
      // the fixture unrecorded either.
      expect(
        recorder.urls.filter((url) =>
          /\/api\/(chat|dashboard|usage|agents?)\//.test(url),
        ),
      ).toEqual([]);

      const calls = recorder.calls;
      expect(calls).toContain("POST /api/knowledge/v1");
      expect(calls).toContain(`PUT /api/knowledge/v1/${NEW_ID}/config`);
      expect(calls).toContain(`POST /api/knowledge/v1/${NEW_ID}/enrichment`);
      expect(calls).toContain(
        `POST /api/knowledge/v1/${NEW_ID}/upload-urls/batch`,
      );
      expect(calls).toContain(
        `POST /api/knowledge/v1/${NEW_ID}/documents/batch`,
      );
      expect(calls).toContain(`GET /api/knowledge/v2/${NEW_ID}/documents`);
      expect(calls).toContain(`POST /api/knowledge/v1/${NEW_ID}/chat`);
      // And the auth service's profile read, the one other origin allowed.
      expect(calls).toContain("GET /user/me");
    });
  });
});

test.describe("Saying so when it goes wrong", () => {
  const knowledgeUrl = `/document-intelligence/knowledge?collection=${KNOWLEDGE_ID}`;

  test("a file S3 refuses is named, and nothing is called added", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    const upload = await mockUpload(page, recorder, KNOWLEDGE_ID, {
      putStatus: 500,
    });

    await page.goto(knowledgeUrl);
    await expect(
      page.getByText(/No documents yet\. Add some above/),
    ).toBeVisible();
    await uploadOneFile(page);

    await expect(
      page.getByText(`1 file could not be sent: ${FILE_NAME}`),
    ).toBeVisible();
    await expect(page.getByText(/added — they will be ready/)).toHaveCount(0);
    // It never reached S3, so it was never registered and is not in the list.
    expect(upload.s3Puts).toHaveLength(1);
    expect(recorder.calls).not.toContain(
      `POST /api/knowledge/v1/${KNOWLEDGE_ID}/documents/batch`,
    );
    await expect(rowFor(page, FILE_NAME)).toHaveCount(0);
    expect(recorder.violations).toEqual([]);
  });

  test("a batch the service refuses to register says why", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await mockUpload(page, recorder, KNOWLEDGE_ID, { registerStatus: 500 });

    await page.goto(knowledgeUrl);
    await expect(
      page.getByText(/No documents yet\. Add some above/),
    ).toBeVisible();
    await uploadOneFile(page);

    await expect(
      page.getByText("The documents could not be registered."),
    ).toBeVisible();
    await expect(page.getByText(/added — they will be ready/)).toHaveCount(0);
    // The dialog stays open with the file still chosen, so it can be retried.
    const dialog = page.getByRole("dialog", { name: "Upload files" });
    await expect(dialog.getByText(FILE_NAME)).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "Add", exact: true }),
    ).toBeEnabled();
    expect(recorder.violations).toEqual([]);
  });

  /**
   * `failed` is a document the worker gave up on; `invalid` is one the batch
   * route took but could not read at all. Both are final — nothing polls them —
   * so either one drawn as "Processing" would spin forever.
   */
  for (const status of ["failed", "invalid"]) {
    test(`a document reported \`${status}\` is marked failed, with the service's reason`, async ({
      page,
    }) => {
      const recorder = await mockKnowledgeService(page);
      const reason = "The PDF is password-protected.";
      await page.route(
        `**/api/knowledge/v2/${KNOWLEDGE_ID}/documents`,
        (route) =>
          json(
            route,
            documentPage([
              documentRow(KNOWLEDGE_ID, status, { error_detail: reason }),
            ]),
          ),
      );
      await page.setViewportSize({ width: 1400, height: 1000 });

      await page.goto(knowledgeUrl);
      const row = rowFor(page, FILE_NAME);
      await expect(row.getByText("Failed")).toBeVisible();
      await expect(row.getByText(/Completed|Processing/)).toHaveCount(0);

      // The reason is the service's own words, on the document itself…
      await row.getByText(FILE_NAME).click();
      await expect(page.getByText(reason).first()).toBeVisible();
      await page.keyboard.press("Escape");

      // …and where questions are asked, which is where "why is it not in my
      // answers" comes up.
      await page
        .getByRole("navigation", { name: "Collection" })
        .getByRole("link", { name: "Conversations" })
        .click();
      await page
        .getByRole("button", { name: "Documents", exact: true })
        .click();
      const panel = page.getByRole("complementary", {
        name: "Collection documents",
      });
      await panel.getByText(FILE_NAME).click();
      await expect(panel.getByText(reason)).toBeVisible();
      await expect(panel.getByText(/Still being read/)).toHaveCount(0);
      expect(recorder.violations).toEqual([]);
    });
  }

  test("a document still being read is not called ready", async ({ page }) => {
    const recorder = await mockKnowledgeService(page);
    await page.route(`**/api/knowledge/v2/${KNOWLEDGE_ID}/documents`, (route) =>
      json(route, documentPage([documentRow(KNOWLEDGE_ID, "processing")])),
    );

    await page.goto(knowledgeUrl);
    const row = rowFor(page, FILE_NAME);
    await expect(row.getByText("Processing")).toBeVisible();
    await expect(row.getByText(/Completed|Failed/)).toHaveCount(0);

    // Conversations says the same: answers do not draw on it yet.
    await page
      .getByRole("navigation", { name: "Collection" })
      .getByRole("link", { name: "Conversations" })
      .click();
    await page.getByRole("button", { name: "Documents", exact: true }).click();
    const panel = page.getByRole("complementary", {
      name: "Collection documents",
    });
    await panel.getByText(FILE_NAME).click();
    await expect(
      panel.getByText("Still being read — answers do not draw on it yet."),
    ).toBeVisible();
    expect(recorder.violations).toEqual([]);
  });

  test("a session the auth service refuses goes to sign-in", async ({
    page,
  }) => {
    const recorder = await mockKnowledgeService(page);
    const signIns: string[] = [];
    await page.route("https://auth.futuresmart.ai/agent/login**", (route) => {
      signIns.push(route.request().url());
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<!doctype html><title>Sign in</title><h1>Sign in</h1>",
      });
    });
    await page.route("**/user/me", (route) =>
      json(route, { detail: "Not authenticated" }, 401),
    );

    await page.goto(knowledgeUrl);

    await expect(page).toHaveURL(
      /^https:\/\/auth\.futuresmart\.ai\/agent\/login/,
    );
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    // And back to where it was once signed in.
    const redirect = new URL(signIns[0]).searchParams.get("redirect") ?? "";
    expect(redirect).toContain(`/document-intelligence/knowledge`);
    expect(redirect).toContain(`collection=${KNOWLEDGE_ID}`);
    // Nothing was read without a session to scope it.
    expect(recorder.calls.filter((call) => call.includes("/api/"))).toEqual([]);
    expect(recorder.violations).toEqual([]);
  });
});
