import { expect, request, test } from "@playwright/test";

/**
 * The same contract, against a real knowledge service and its ingestion worker.
 *
 * Opt-in, because it creates and deletes real knowledge bases and needs a
 * signed-in session:
 *
 *   DI_LIVE=1 DI_LIVE_OWNER=you@futuresmart.ai DI_LIVE_COOKIE='…' \
 *     DI_LIVE_BASE=https://<staging host> bun run test:e2e:live
 *
 * It calls the service directly rather than through the UI, so nothing depends
 * on the UI being up:
 *
 * - `DI_LIVE_BASE`   the host that serves `/api/knowledge/*` (default production,
 *                    `https://app.futuresmart.ai` — prefer staging);
 * - `DI_LIVE_COOKIE` the `Cookie` header of a signed-in browser on that host
 *                    (DevTools → any `/api/knowledge` request → Request Headers).
 *                    It is a live session: keep it out of shell history and CI logs;
 * - `DI_LIVE_OWNER`  the email that session belongs to — `GET /v1?owner_id=`
 *                    answers 403 for anyone else's id.
 *
 * What it proves is what the stubbed specs cannot: the shapes the product reads
 * are the shapes the service still sends, the two write semantics the data layer
 * is built around still hold (a partial knowledge-config `PUT` re-applies
 * defaults, hence `mergeKnowledgeConfig`; a partial chat-config `PUT` does not),
 * and — the product's real success condition — a document added through the
 * service is picked up by the worker, indexed, and cited in an answer.
 */

const LIVE = process.env.DI_LIVE === "1";
const BASE = (process.env.DI_LIVE_BASE ?? "https://app.futuresmart.ai").replace(
  /\/+$/,
  "",
);
const OWNER = process.env.DI_LIVE_OWNER;
const COOKIE = process.env.DI_LIVE_COOKIE;
const API = `${BASE}/api/knowledge`;
/** How long the worker may take to index one short document. */
const INDEX_TIMEOUT_MS = Number(
  process.env.DI_LIVE_INDEX_TIMEOUT_MS ?? 600_000,
);

const liveContext = () =>
  request.newContext({
    extraHTTPHeaders: COOKIE ? { cookie: COOKIE } : {},
  });

test.describe("live knowledge service", () => {
  test.skip(!LIVE, "set DI_LIVE=1 to run against the real service");
  test.skip(
    !OWNER || !COOKIE,
    "set DI_LIVE_OWNER and DI_LIVE_COOKIE to a signed-in session",
  );
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  test("a collection can be created, configured, read and deleted", async () => {
    const api = await liveContext();
    let knowledgeId: string | undefined;

    try {
      // 1. Create.
      const created = await api.post(`${API}/v1`, {
        data: { owner_id: OWNER, collection_name: "zz-e2e-live-probe" },
      });
      expect(created.ok()).toBe(true);
      knowledgeId = (await created.json()).knowledge_id as string;
      expect(knowledgeId).toBeTruthy();

      // 2. A new knowledge base has no config yet — the board's own filter
      //    depends on this, since an unconfigured one cannot be read.
      const beforeConfig = await api.get(`${API}/v1/${knowledgeId}/config`);
      expect(beforeConfig.ok()).toBe(false);

      // 3. Configure it the way the create dialog does.
      const configured = await api.put(`${API}/v1/${knowledgeId}/config`, {
        data: {
          collection_name: "zz-e2e-live-probe",
          service_description: "created by the Document Intelligence e2e suite",
        },
      });
      expect(configured.ok()).toBe(true);

      // 4. The fields the board and the About screen read.
      const config = await (
        await api.get(`${API}/v1/${knowledgeId}/config`)
      ).json();
      expect(config).toMatchObject({
        knowledge_id: knowledgeId,
        collection_name: "zz-e2e-live-probe",
      });
      expect(config.updated_at).toBeTruthy();

      // 5. What the extraction toggle now relies on: `enrichment_enabled` is
      //    the switch, and the service derives both metadata levels from it —
      //    which is why the toggle sends nothing else.
      await api.put(`${API}/v1/${knowledgeId}/config`, {
        data: { enrichment_enabled: false },
      });
      const afterToggle = await (
        await api.get(`${API}/v1/${knowledgeId}/config`)
      ).json();
      expect(afterToggle.enrichment_enabled).toBe(false);
      expect(
        afterToggle.chunking_strategy,
        "extraction off has to take both metadata levels with it — the toggle sends no levels of its own",
      ).toMatchObject({
        document_level_metadata: false,
        chunk_level_metadata: false,
      });

      // 6. And the description is still the About screen's alone to change,
      //    without carrying the extraction flag back on.
      await api.put(`${API}/v1/${knowledgeId}/config`, {
        data: { service_description: "changed", enrichment_enabled: false },
      });
      const afterPartial = await (
        await api.get(`${API}/v1/${knowledgeId}/config`)
      ).json();
      expect(afterPartial.service_description).toBe("changed");
      expect(afterPartial.enrichment_enabled).toBe(false);

      // 7. The chat config: a partial PUT leaves the rest alone.
      await api.put(`${API}/v1/${knowledgeId}/chat/config`, {
        data: {
          show_sources: true,
          follow_up: true,
          llm_provider: "openai",
          llm_model: "gpt-5.6-luna",
          suggested_questions: ["What is this about?"],
        },
      });
      await api.put(`${API}/v1/${knowledgeId}/chat/config`, {
        data: { follow_up: false },
      });
      const chatConfig = await (
        await api.get(`${API}/v1/${knowledgeId}/chat/config`)
      ).json();
      expect(chatConfig).toMatchObject({
        configured: true,
        follow_up: false,
        show_sources: true,
        llm_model: "gpt-5.6-luna",
        suggested_questions: ["What is this about?"],
      });

      // 8. The routes the rest of the product reads, on an empty collection.
      const documents = await api.get(
        `${API}/v2/${knowledgeId}/documents?page=1&page_size=20`,
      );
      expect(documents.ok()).toBe(true);
      const sessions = await (
        await api.get(`${API}/v1/${knowledgeId}/sessions?page=1&page_size=20`)
      ).json();
      expect(sessions).toMatchObject({ sessions: [], total_count: 0 });
      expect((await api.get(`${API}/v1/${knowledgeId}/enrichment`)).ok()).toBe(
        true,
      );
      const models = await (await api.get(`${API}/v1/chat-models`)).json();
      expect(models.providers?.length).toBeGreaterThan(0);

      // 9. And it is listed as one of this owner's knowledge bases.
      const list = await (
        await api.get(
          `${API}/v1?owner_id=${encodeURIComponent(OWNER as string)}`,
        )
      ).json();
      expect(list.knowledge_services).toContain(knowledgeId);
    } finally {
      if (knowledgeId) {
        const deleted = await api.delete(`${API}/v1/${knowledgeId}`);
        expect(deleted.ok()).toBe(true);
      }
      await api.dispose();
    }
  });
  /**
   * The first-run journey end to end on the real worker: a synthetic document
   * with one fact nobody else could know goes in, the worker indexes it, and a
   * question about that fact is answered with the document as its source.
   */
  test("a document is indexed by the worker and cited in an answer", async () => {
    test.setTimeout(INDEX_TIMEOUT_MS + 120_000);
    const api = await liveContext();
    const marker = `zz-e2e-${Date.now().toString(36)}`;
    const fact = `The ${marker} project codename is Heliotrope Lantern.`;
    let knowledgeId: string | undefined;

    try {
      const created = await api.post(`${API}/v1`, {
        data: { owner_id: OWNER, collection_name: marker },
      });
      expect(created.ok()).toBe(true);
      knowledgeId = (await created.json()).knowledge_id as string;
      const configured = await api.put(`${API}/v1/${knowledgeId}/config`, {
        data: {
          collection_name: marker,
          service_description: "created by the Document Intelligence e2e suite",
        },
      });
      expect(configured.ok()).toBe(true);

      // Plain text is one multipart field and nothing else, as the UI sends it.
      const added = await api.post(`${API}/v1/${knowledgeId}/documents`, {
        multipart: { text: `${fact}\nThis note exists only to test indexing.` },
      });
      expect(added.ok(), await added.text()).toBe(true);

      // The worker's half: processing → completed, or a reason it failed.
      let status = "";
      await expect
        .poll(
          async () => {
            const page = await (
              await api.get(
                `${API}/v2/${knowledgeId}/documents?page=1&page_size=20`,
              )
            ).json();
            const doc = (page.document ?? [])[0];
            status = doc?.status ?? "missing";
            if (status === "failed") {
              throw new Error(
                `ingestion failed: ${doc?.error_detail ?? "no detail"}`,
              );
            }
            return status;
          },
          { timeout: INDEX_TIMEOUT_MS, intervals: [5_000, 10_000, 15_000] },
        )
        .toBe("completed");

      // An answer from it, with the document as evidence.
      const answer = await api.post(`${API}/v1/${knowledgeId}/chat`, {
        data: {
          query: `What is the ${marker} project codename?`,
          stream: true,
        },
        timeout: 120_000,
      });
      expect(answer.ok()).toBe(true);
      const events = (await answer.text())
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => {
          try {
            return JSON.parse(line.slice(5).trim());
          } catch {
            return null;
          }
        })
        .filter(Boolean);
      const final = events.findLast((event) => event.session_id);
      expect(final, "the stream ended without a final event").toBeTruthy();
      expect(String(final.response)).toMatch(/Heliotrope Lantern/i);
      const sources = final.data?.knowledge?.relevant_sources?.data ?? [];
      expect(sources.length, "the answer cited no source").toBeGreaterThan(0);
    } finally {
      if (knowledgeId) await api.delete(`${API}/v1/${knowledgeId}`);
      await api.dispose();
    }
  });
});
