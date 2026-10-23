import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import { useCreateCollection, type NewCollection } from "../createCollection";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("../session", () => ({
  useOwnerId: () => "owner@futuresmart.ai",
}));

const mockedPost = vi.mocked(axios.post);
const mockedPut = vi.mocked(axios.put);

const K = "564ce9d2-80be-4d57-a255-7e57a57fde63";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const ENRICHMENT = `/api/knowledge/v1/${K}/enrichment`;
const CONFIG = `/api/knowledge/v1/${K}/config`;

const input: NewCollection = {
  name: "Resumes",
  about: "Candidate profiles from uploaded resumes.",
  structuredExtraction: true,
  fields: [],
};

/** The kind of list the confirm step hands over. */
const fields: NewCollection["fields"] = [
  {
    field_name: "candidate_name",
    description: "Full name.",
    field_type: "str",
  },
  {
    field_name: "work_history",
    description: "One row per job.",
    field_type: "List[dict]",
    schema: { company_name: "str", start_date: "date" },
  },
  // No `field_type`: the documented "auto" case, where the service resolves one
  // from the description rather than the caller guessing. The description is
  // still sent — the route refuses a new field without one.
  { field_name: "notes", description: "Anything else worth keeping." },
  // Chunk-level, which has to be said: without it the field is created at
  // document level and nothing mentions it.
  {
    field_name: "chunk_topic",
    description: "The topic of this section.",
    level: "chunk",
  },
];

async function create(overrides: Partial<NewCollection> = {}) {
  const steps: string[] = [];
  const { result } = renderHook(
    () => useCreateCollection({ onStep: (step) => steps.push(step) }),
    { wrapper },
  );
  const created = await result.current.mutateAsync({ ...input, ...overrides });
  return { created, steps };
}

const enrichmentBody = () =>
  mockedPost.mock.calls.find(([url]) => url === ENRICHMENT)?.[1] as {
    fields: Array<Record<string, unknown>>;
  };

describe("useCreateCollection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mockedPost.mockResolvedValue({
      data: { status: "success", knowledge_id: K },
    });
    mockedPut.mockResolvedValue({ data: { message: "Saved" } });
  });

  /**
   * Two calls when there are no fields, in this order. `POST /v1` mints the
   * knowledge base but leaves its config uninitialized — `GET …/config` answers
   * "Knowledge config exists but is not initialized", and an upload answers
   * "Embedding model not found" — so a create that stopped after the first call
   * would leave a collection the board cannot even read.
   */
  it("creates the knowledge base, then initializes it", async () => {
    const { created, steps } = await create();

    expect(created).toEqual({
      collectionId: K,
      knowledgeId: K,
      createdFields: [],
      fieldsFailed: false,
    });
    expect(steps).toEqual(["collection", "documents"]);

    expect(mockedPost).toHaveBeenCalledTimes(1);
    expect(mockedPost).toHaveBeenCalledWith("/api/knowledge/v1", {
      owner_id: "owner@futuresmart.ai",
      collection_name: "Resumes",
    });
    expect(mockedPut.mock.calls.map(([url]) => url)).toEqual([CONFIG]);
  });

  /**
   * The trap this whole call chain exists to avoid.
   *
   * Without `seed_optional_defaults: false`, `PUT /config` force-creates all
   * nine document-level defaults whatever the person asked for — live-checked
   * against the resume template, which publishes 13 fields and produced a
   * collection holding 22. With the flag, the config call creates only the two
   * still-protected fields and everything else is written once, deliberately,
   * by the enrichment call.
   */
  it("never lets the config call seed fields nobody asked for", async () => {
    await create({ fields });

    const [, body] = mockedPut.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(body.seed_optional_defaults).toBe(false);
  });

  /**
   * `field_template` is never stored (`GET /config` does not return it) and,
   * with the seed flag off, seeds nothing either. A template's fields reach the
   * collection the same way every other field does — on the list below.
   */
  it("does not send a field template", async () => {
    await create({ fields });

    const [, body] = mockedPut.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(body).not.toHaveProperty("field_template");
    expect(body.collection_name).toBe("Resumes");
    expect(body.service_description).toBe(
      "Candidate profiles from uploaded resumes.",
    );
  });

  /**
   * The confirmed list, written verbatim in one call.
   *
   * `is_custom: true` is required on every entry — omitting it is a 422 naming
   * `body.fields.0.is_custom` — and there is no "system field" concept from
   * this screen: a template's field, a proposed field and a hand-typed one are
   * all created the same way.
   */
  it("writes the confirmed fields in one call, every one of them custom", async () => {
    const { created, steps } = await create({ fields });

    expect(steps).toEqual(["collection", "fields", "documents"]);
    expect(
      mockedPost.mock.calls.filter(([url]) => url === ENRICHMENT),
    ).toHaveLength(1);

    const body = enrichmentBody();
    expect(body.fields.every((field) => field.is_custom === true)).toBe(true);
    expect(body.fields.map((field) => field.field_name)).toEqual([
      "candidate_name",
      "work_history",
      "notes",
      "chunk_topic",
    ]);
    expect(created.createdFields).toEqual([
      "candidate_name",
      "work_history",
      "notes",
      "chunk_topic",
    ]);
  });

  /** A table's columns travel with it, or the service creates an empty table. */
  it("sends a table's columns alongside its type", async () => {
    await create({ fields });

    const table = enrichmentBody().fields[1];
    expect(table.field_type).toBe("List[dict]");
    expect(table.schema).toEqual({ company_name: "str", start_date: "date" });
  });

  /**
   * "Auto" is a real choice: a field with no type is one the service resolves
   * from its description. Sending `field_type: undefined` is not the same
   * thing, and is the kind of difference that only shows up in production.
   */
  it("omits the type entirely on a field left on auto", async () => {
    await create({ fields });

    const auto = enrichmentBody().fields[2];
    expect(auto).not.toHaveProperty("field_type");
    expect(auto).not.toHaveProperty("level");
  });

  /**
   * Every field carries a description, and the description is never blank.
   *
   * Live-verified 2026-09-14: the route answers `400` — "'description' is
   * required for new fields" — for an omitted description *and* for `""`, and
   * it applies none of the other fields when it does. One blank box would mean
   * a collection created with nothing on it.
   */
  it("sends a description on every field", async () => {
    await create({ fields });

    for (const field of enrichmentBody().fields) {
      expect(typeof field.description).toBe("string");
      expect(String(field.description).trim()).not.toBe("");
    }
  });

  /** A chunk-level field created without `level` lands at document level. */
  it("says which level a chunk-level field belongs to", async () => {
    await create({ fields });

    const chunk = enrichmentBody().fields[3];
    expect(chunk.field_name).toBe("chunk_topic");
    expect(chunk.level).toBe("chunk");
  });

  /** Extraction off means no fields — not a field call with an empty list. */
  it("writes no fields when structured extraction is off", async () => {
    const { steps } = await create({ fields, structuredExtraction: false });

    expect(mockedPost.mock.calls.some(([url]) => url === ENRICHMENT)).toBe(
      false,
    );
    expect(steps).toEqual(["collection", "documents"]);

    const [, body] = mockedPut.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(body.enrichment_enabled).toBe(false);
  });

  /**
   * Chat works with no chat config at all, and unconfigured is already what
   * this product promises — sources shown, follow-ups on, the service's own
   * default model. Settings is where any of that changes.
   */
  it("never touches the chat config", async () => {
    await create({ fields });

    expect(
      mockedPut.mock.calls.some(([url]) =>
        String(url).includes("/chat/config"),
      ),
    ).toBe(false);
  });

  /**
   * A collection with the wrong fields is recoverable in Settings; a create
   * that throws away a collection the account can already use is not. So the
   * failure is reported and the collection is kept.
   */
  it("keeps the collection when the fields cannot be written", async () => {
    mockedPost.mockImplementation((url: string) =>
      url === ENRICHMENT
        ? Promise.reject(new Error("422"))
        : Promise.resolve({ data: { knowledge_id: K } }),
    );

    const { created } = await create({ fields });

    expect(created.collectionId).toBe(K);
    expect(created.fieldsFailed).toBe(true);
    expect(created.createdFields).toEqual([]);
  });

  /** A knowledge base that was never minted has nothing to configure. */
  it("stops when no knowledge id comes back", async () => {
    mockedPost.mockResolvedValue({ data: { status: "success" } });

    await expect(create()).rejects.toThrow("Collection was not created");
    expect(mockedPut).not.toHaveBeenCalled();
  });
});
