import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import { KNOWLEDGE_CONTRACT } from "@/generated/knowledgeContract";

import { APIS } from "../api";

/**
 * Document Intelligence talks to the knowledge service and nothing else.
 *
 * Two things are checked here, both against the service's own published
 * contract (`src/generated/knowledgeContract.ts`, regenerated with
 * `bun run gen:knowledge`):
 *
 * 1. every endpoint in `APIS` exists on that service, with the verbs this app
 *    uses and the query parameters it sends — a renamed route or a dropped
 *    parameter fails here rather than at runtime;
 * 2. no file in this app names an endpoint of any other service. That is the
 *    product rule, and it is the kind of rule that only holds if something
 *    checks it: a single chat-service path typed into a component is invisible
 *    in review and works fine in dev against a signed-in session.
 */

const routes = new Set(Object.keys(KNOWLEDGE_CONTRACT));

const has = (method: string, path: string) => routes.has(`${method} ${path}`);

const queryParams = (method: string, path: string) =>
  (KNOWLEDGE_CONTRACT as Record<string, readonly string[]>)[
    `${method} ${path}`
  ] ?? [];

/** Every verb this app uses on each endpoint it names. */
const USED: Array<[keyof typeof APIS, string[]]> = [
  ["KNOWLEDGE", ["GET", "POST"]],
  ["KNOWLEDGE_ITEM", ["DELETE"]],
  ["KNOWLEDGE_VALIDATE", ["GET"]],
  ["KNOWLEDGE_CONFIG", ["GET", "PUT"]],
  ["ENRICHMENT", ["GET", "POST"]],
  ["FIELD_TEMPLATES", ["GET"]],
  ["FIELD_TYPES", ["GET"]],
  ["SCHEMA_UPLOAD_URL", ["POST"]],
  ["SCHEMA_GENERATE", ["POST"]],
  ["SCHEMA_PREVIEW", ["POST"]],
  ["DOCUMENTS", ["GET"]],
  ["ADD_DOCUMENT", ["POST"]],
  ["ADD_DOCUMENTS_BATCH", ["POST"]],
  ["CHAT", ["POST"]],
  ["CHAT_CONFIG", ["GET", "PUT"]],
  ["CHAT_MODELS", ["GET"]],
  ["SESSIONS", ["GET"]],
  ["SESSION", ["GET", "DELETE"]],
  ["MESSAGE_FEEDBACK", ["PATCH"]],
  ["USAGE_LIMITS", ["GET"]],
  ["USAGE_ANALYTICS", ["POST"]],
  ["USAGE_SERVICE_OPTIONS", ["GET"]],
  ["CREDENTIALS", ["GET", "POST"]],
  ["CREDENTIAL", ["DELETE"]],
  ["CREDENTIAL_PROVIDERS", ["GET"]],
  ["REPORTS", ["GET", "POST"]],
  ["REPORT_SETTINGS", ["GET", "PUT"]],
  ["REPORT", ["PUT", "DELETE"]],
  ["REPORT_RUNS", ["GET", "POST"]],
  ["REPORT_RUN", ["GET"]],
  ["REPORT_RUN_STATUS", ["GET"]],
  ["REPORT_RUN_EXPORT", ["GET"]],
  ["REPORT_RUN_CANCEL", ["POST"]],
  ["INVITATIONS", ["GET", "POST"]],
  ["INVITATION", ["PATCH", "DELETE"]],
  ["WIDGET_CONFIG", ["GET", "POST", "DELETE"]],
  ["WIDGET_ICONS", ["GET"]],
  ["WIDGET_AVATARS", ["GET"]],
  ["API_KEYS", ["GET", "POST"]],
  ["API_KEY_REVOKE", ["POST"]],
  ["HEALTH_SCORECARD", ["GET"]],
  ["HEALTH_TRENDS", ["GET"]],
  ["HEALTH_QUERIES", ["GET"]],
  ["HEALTH_SYNC_COSTS", ["POST"]],
  ["HEALTH_ENRICHMENT_DOCUMENTS", ["GET"]],
];

describe("every endpoint this app calls is on the knowledge service", () => {
  it("names only knowledge routes", () => {
    for (const url of Object.values(APIS)) {
      expect(url.startsWith("/api/knowledge/")).toBe(true);
    }
  });

  it.each(USED)("%s exists with the verbs used", (key, methods) => {
    for (const method of methods) {
      expect(
        has(method, APIS[key]),
        `${method} ${APIS[key]} is not in the knowledge contract`,
      ).toBe(true);
    }
  });

  it("declares the query parameters the collection board sends", () => {
    expect(queryParams("GET", APIS.KNOWLEDGE)).toContain("owner_id");
  });

  /**
   * "Shared with me" is built on validate answering ownership, so both
   * parameters have to be ones the service actually reads — an ignored
   * `owner_id` would make every collection look owned and empty the tab.
   */
  it("declares the parameters the ownership probe sends", () => {
    const params = queryParams("GET", APIS.KNOWLEDGE_VALIDATE);
    for (const name of ["owner_id", "knowledge_id"]) {
      expect(params).toContain(name);
    }
  });

  /**
   * The Knowledge table reads which schema each document ran under from this
   * log; an ignored `include_field_data` would leave every row on today's
   * schema, and `period` only knows week and month, so the range is explicit.
   */
  it("declares the parameters the extraction-log walk sends", () => {
    const params = queryParams("GET", APIS.HEALTH_ENRICHMENT_DOCUMENTS);
    for (const name of [
      "include_field_data",
      "start_date",
      "end_date",
      "limit",
      "offset",
    ]) {
      expect(params).toContain(name);
    }
  });

  it("declares the query parameters the conversations list sends", () => {
    const params = queryParams("GET", APIS.SESSIONS);
    for (const name of ["page", "page_size", "q"]) {
      expect(params).toContain(name);
    }
  });

  /**
   * The scope of the usage surface, pinned.
   *
   * These routes used to be mounted per knowledge base and were moved under
   * the owner; the app called the old paths for long enough to ship two dead
   * screens, because the committed contract was stale and agreed with them.
   * Asserting the shape here means a move back — or a half-updated constant —
   * fails in the suite rather than as a 404 in the browser.
   */
  it("reads usage and credentials against the owner, not a collection", () => {
    const ownerScoped = [
      APIS.USAGE_LIMITS,
      APIS.USAGE_ANALYTICS,
      APIS.USAGE_SERVICE_OPTIONS,
      APIS.CREDENTIALS,
      APIS.CREDENTIAL,
      APIS.CREDENTIAL_PROVIDERS,
    ];
    for (const url of ownerScoped) {
      expect(url).toContain("/owners/{owner_id}/");
      expect(url).not.toContain("{knowledge_id}");
    }
  });

  /**
   * The analytics screen renders the agent dashboard's page, whose hooks send a
   * fixed set of parameters. Each of those has to exist on the route it is sent
   * to — an unknown query parameter here is not an error, it is **ignored**, so
   * a renamed one would quietly return an unfiltered page rather than fail.
   */
  it("declares the analytics parameters the shared page sends", () => {
    for (const name of ["period", "top_n", "start_date", "end_date"]) {
      expect(queryParams("GET", APIS.HEALTH_SCORECARD)).toContain(name);
    }
    for (const name of ["bucket", "buckets", "metrics"]) {
      expect(queryParams("GET", APIS.HEALTH_TRENDS)).toContain(name);
    }
    for (const name of [
      "sort",
      "order",
      "status",
      "feedback",
      "chat_id",
      "limit",
      "offset",
      "include_node_data",
    ]) {
      expect(queryParams("GET", APIS.HEALTH_QUERIES)).toContain(name);
    }
  });

  /**
   * `/health/trends` is the one analytics route with no `period`: its window is
   * `bucket` × `buckets`. The scope overrides the window builder for it, and
   * this is what makes that override provably necessary rather than folklore.
   */
  it("pins that trends takes no period", () => {
    expect(queryParams("GET", APIS.HEALTH_TRENDS)).not.toContain("period");
  });

  it("declares the parameters Refresh costs sends", () => {
    const params = queryParams("POST", APIS.HEALTH_SYNC_COSTS);
    for (const name of ["period", "start_date", "end_date", "strategy"]) {
      expect(params).toContain(name);
    }
  });

  it("declares the export format the run viewer sends", () => {
    expect(queryParams("GET", APIS.REPORT_RUN_EXPORT)).toContain("format");
  });

  /**
   * API keys are account-scoped: the collection a key is limited to travels in
   * the body. A path with `{knowledge_id}` in it would be a different feature.
   */
  it("reads api keys against the account, not a collection", () => {
    for (const url of [APIS.API_KEYS, APIS.API_KEY_REVOKE]) {
      expect(url).not.toContain("{knowledge_id}");
    }
  });

  it("declares the document list's pagination", () => {
    const params = queryParams("GET", APIS.DOCUMENTS);
    for (const name of ["page", "page_size"]) {
      expect(params).toContain(name);
    }
  });
});

const APP_SRC = join(__dirname, "../..");

/**
 * The two exceptions, stated once.
 *
 * - The auth service. The knowledge service publishes no session or identity
 *   route, so the profile read and the SSO redirects cannot move onto it — they
 *   go through `@/platform/config`'s `authServiceUrl` / `authLoginUrlFor`,
 *   never a path typed here.
 * - The voice gateway (`sttUrl`), for the optional mic and read-aloud. It is
 *   not part of the core journey; with no gateway key configured both controls
 *   are hidden (see `.env.example`).
 */
const ALLOWED = [
  /authServiceUrl/,
  /authLoginUrlFor/,
  /export const sttUrl = /,
  // A test asserting a path is *absent* names it in order to forbid it, which is
  // this rule being enforced rather than broken. Only the negated matchers are
  // exempt, so a test that genuinely calls another service still fails.
  /\.not\.(toContain|toMatch|toEqual|toBe)\b/,
];

/**
 * Any path on a service that is not the knowledge one.
 *
 * Deliberately not anchored to a quote. The rule this replaced only matched a
 * path typed directly after `"`, `'` or a backtick, so every way this codebase
 * actually builds a route slipped past it — `` `${baseUrl}/api/chat/…` ``, a
 * `const API_PREFIX = "/api/usage"` joined later, a template continued on the
 * next line — and it scanned only the product's own files, never the shared
 * code under `src/platform/` that it imports. It was green while the app still
 * carried Chat, Dashboard and Usage calls.
 */
const OTHER_SERVICE = /\/api\/(?!knowledge\b)[a-z][a-z0-9-]*|\/nl2sql\/api\b/;

/**
 * Comments may name another service — explaining why a route is *not* called
 * is most of what the comments near these calls do. Strips block comments and
 * whole-line `//` comments, keeping line numbers intact.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(/^\s*\/\/.*$/gm, "");
}

function sourceFiles(dir = APP_SRC): string[] {
  // A directory walk, not `git ls-files`: an unstaged file is exactly the one a
  // reviewer has not seen yet, and the rule must not depend on git being there.
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [relative(APP_SRC, path)] : [];
  });
}

const FILES = sourceFiles();

describe("no other service is called from this app", () => {
  /**
   * A scan over nothing passes. Pin that it actually reads the product's own
   * code *and* the shared code it runs on, so a moved `src/` or a broken walk
   * turns this red instead of vacuously green.
   */
  it("scans the whole source tree, shared code included", () => {
    expect(FILES.length).toBeGreaterThan(300);
    expect(FILES).toContain(join("data", "api.ts"));
    expect(FILES).toContain(join("platform", "hooks", "useChatStream.tsx"));
  });

  it("catches a route however it is written", () => {
    for (const line of [
      "const API_PREFIX = " + JSON.stringify("/api" + "/usage") + ";",
      "url: `${baseUrl}" + "/api" + "/chat/v1/chat_completion`,",
      "axios.get(`" + "/nl2sql/ap" + "i/v1/agents/${id}`)",
    ]) {
      expect(OTHER_SERVICE.test(line), line).toBe(true);
    }
    expect(OTHER_SERVICE.test("`/api/knowledge/v1/${id}/chat`")).toBe(false);
  });

  it.each(FILES)("%s", (file) => {
    const source = stripComments(readFileSync(join(APP_SRC, file), "utf8"));
    const offending = source
      .split("\n")
      .map((line, index) => `${index + 1}: ${line.trim()}`)
      .filter(
        (line) =>
          OTHER_SERVICE.test(line) &&
          !ALLOWED.some((allowed) => allowed.test(line)),
      );
    expect(offending).toEqual([]);
  });

  /**
   * The create screen's four calls, in the order it makes them, pinned as a
   * shape rather than as four separate route checks.
   *
   * Two of them are deliberately *not* scoped to a collection: the schema is
   * worked out and a sample is run before anything exists to scope them to,
   * which is the whole reason the screen can be abandoned without leaving an
   * empty collection behind. A knowledge-scoped variant of `enrichment/generate`
   * also exists and is not what this app calls — using it would mean creating
   * the collection first, which is the behaviour this replaced.
   */
  it("works a schema out before any collection exists", () => {
    for (const url of [
      APIS.FIELD_TEMPLATES,
      APIS.FIELD_TYPES,
      APIS.SCHEMA_UPLOAD_URL,
      APIS.SCHEMA_GENERATE,
      APIS.SCHEMA_PREVIEW,
    ]) {
      expect(url).not.toContain("{knowledge_id}");
    }
  });
});
