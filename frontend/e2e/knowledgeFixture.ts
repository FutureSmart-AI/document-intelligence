import type { Page } from "@playwright/test";

import { KNOWLEDGE_ID, mockKnowledgeService } from "./fixtures";

/**
 * A collection wide enough to exercise the Knowledge table.
 *
 * The shared fixture holds two documents and one extracted field, which is the
 * right size for the specs that assert *which requests the product makes*. It
 * is the wrong size for the specs that assert what the one table **does**: a
 * facet needs values to count, a distribution needs numbers, a page control
 * needs a second page, and "Fields filled 6/8" needs a field that some
 * documents are missing. So this layers a fuller answer over the same mock,
 * registered afterwards so it wins the two routes it redefines.
 *
 * The shapes are the service's own — the same keys `/v2/{id}/documents` and
 * `/v2/{id}/documents/metadata/all` return in production.
 */

interface Doc {
  file: string;
  objectId: string;
  status: string;
  source: "file" | "url" | "plain_text";
  created: string;
  /** A document still being read, or one that failed, has no extraction. */
  fields?: Record<string, unknown>;
  /** Only a completed file has something to download. */
  links?: boolean;
  /** What processing it took, as GET …/documents returns it (priced). */
  telemetry?: Record<string, unknown>;
}

/** A document whose extraction ran on the fallback model, as the API prices it. */
export const PROCESSING_TELEMETRY = {
  total_ms: 9100,
  parse: { parser: "local_text", mode: "text_based", ms: 420 },
  operations: [
    {
      operation: "extraction",
      provider: "openai",
      model: "gpt-5.4-nano",
      requested_provider: "google",
      requested_model: "gemini-3.1-flash-lite",
      fallback: true,
      fallback_reason: "primary_failed: 402 Payment Required",
      calls: 1,
      input_tokens: 12000,
      cached_input_tokens: 2000,
      output_tokens: 900,
      latency_ms: 7300,
      estimated_cost_usd: 0.003565,
    },
    {
      operation: "embeddings",
      provider: "openai",
      model: "text-embedding-3-small",
      calls: 1,
      input_tokens: 5000,
      output_tokens: 0,
      latency_ms: 600,
      estimated_cost_usd: 0.0001,
    },
  ],
  estimated_cost_usd: 0.003665,
  cost_complete: true,
  unpriced_models: [],
  price_table: "openai-standard-2026-10-26",
};

const DOCS: Doc[] = [
  {
    file: "northwind-logistics-msa.pdf",
    objectId: "11111111-1111-4111-8111-111111111101",
    status: "completed",
    source: "file",
    created: "2026-08-01T09:12:00",
    links: true,
    telemetry: PROCESSING_TELEMETRY,
    fields: {
      vendor: "Northwind Logistics",
      region: "EMEA",
      renewal_date: "2027-03-31",
      notice_period_days: 90,
      contract_value: 240000,
      services: ["freight", "warehousing", "customs", "last-mile delivery"],
      owner: "Priya Sharma",
      summary:
        "Master services agreement covering inbound freight and bonded warehousing across the EMEA region, renewing annually unless either party gives notice.",
    },
  },
  {
    file: "acme-cloud-hosting.pdf",
    objectId: "11111111-1111-4111-8111-111111111102",
    status: "completed",
    source: "file",
    created: "2026-08-02T09:12:00",
    links: true,
    fields: {
      vendor: "Acme Cloud",
      region: "AMER",
      renewal_date: "2026-11-15",
      notice_period_days: 30,
      contract_value: 512000,
      services: ["compute", "object storage"],
      owner: "Priya Sharma",
      summary:
        "Hosting agreement for production compute and object storage, billed monthly against a committed annual spend.",
    },
  },
  {
    file: "acme-cloud-support-addendum.pdf",
    objectId: "11111111-1111-4111-8111-111111111103",
    status: "completed",
    source: "file",
    created: "2026-08-03T09:12:00",
    links: true,
    fields: {
      vendor: "Acme Cloud",
      region: "AMER",
      renewal_date: "2026-11-15",
      notice_period_days: 30,
      contract_value: 64000,
      services: ["support"],
      owner: "Marcus Webb",
      summary:
        "Adds twenty-four hour support coverage to the hosting agreement, coterminous with the parent contract.",
    },
  },
  {
    file: "helios-payroll-services.pdf",
    objectId: "11111111-1111-4111-8111-111111111104",
    status: "completed",
    source: "file",
    created: "2026-08-04T09:12:00",
    links: true,
    fields: {
      vendor: "Helios Payroll",
      region: "APAC",
      renewal_date: "2027-01-01",
      notice_period_days: 60,
      contract_value: 98000,
      services: ["payroll", "tax filing"],
      owner: "Ananya Rao",
      summary:
        "Outsourced payroll and statutory tax filing for the APAC entities, priced per employee per month.",
    },
  },
  {
    file: "helios-payroll-dpa.pdf",
    objectId: "11111111-1111-4111-8111-111111111105",
    status: "completed",
    source: "file",
    created: "2026-08-05T09:12:00",
    links: true,
    fields: {
      vendor: "Helios Payroll",
      region: "APAC",
      renewal_date: "2027-01-01",
      notice_period_days: 60,
      contract_value: 0,
      services: ["data processing"],
      owner: "Ananya Rao",
      summary:
        "Data processing addendum setting out the lawful basis and the sub-processors permitted under the payroll agreement.",
    },
  },
  {
    file: "brightline-marketing-sow.pdf",
    objectId: "11111111-1111-4111-8111-111111111106",
    status: "completed",
    source: "file",
    created: "2026-08-06T09:12:00",
    links: true,
    fields: {
      vendor: "Brightline Studio",
      region: "EMEA",
      renewal_date: "2026-10-01",
      notice_period_days: 14,
      contract_value: 45000,
      services: ["campaign design", "copywriting"],
      owner: "Marcus Webb",
      summary:
        "Statement of work for a two-quarter brand campaign, with fees drawn against a fixed retainer.",
    },
  },
  {
    // Everything but the vendor is missing: this is the row that makes
    // "Fields filled" and the Coverage view say something.
    file: "unsigned-vendor-nda.pdf",
    objectId: "11111111-1111-4111-8111-111111111107",
    status: "completed",
    source: "file",
    created: "2026-08-07T09:12:00",
    links: true,
    fields: {
      vendor: "Brightline Studio",
      region: "",
      renewal_date: "",
      notice_period_days: null,
      contract_value: null,
      services: [],
      owner: "",
      summary: "",
    },
  },
  {
    file: "https://suppliers.example.com/terms",
    objectId: "11111111-1111-4111-8111-111111111108",
    status: "completed",
    source: "url",
    created: "2026-08-08T09:12:00",
    fields: {
      vendor: "Orbital Freight",
      region: "AMER",
      renewal_date: "2027-06-30",
      notice_period_days: 45,
      contract_value: 132000,
      services: ["freight"],
      owner: "Priya Sharma",
      summary:
        "Standard supplier terms published on the vendor's own site, retrieved and read as a document.",
    },
  },
  {
    file: "https://suppliers.example.com/terms/sla",
    objectId: "11111111-1111-4111-8111-111111111109",
    status: "completed",
    source: "url",
    created: "2026-08-09T09:12:00",
    fields: {
      vendor: "Orbital Freight",
      region: "AMER",
      renewal_date: "2027-06-30",
      notice_period_days: 45,
      contract_value: 0,
      services: ["freight", "support"],
      owner: "Marcus Webb",
      summary:
        "Service level schedule attached to the published supplier terms, with credits for missed delivery windows.",
    },
  },
  {
    file: "renewal-notes-q3.txt",
    objectId: "11111111-1111-4111-8111-111111111110",
    status: "completed",
    source: "plain_text",
    created: "2026-08-10T09:12:00",
    fields: {
      vendor: "Northwind Logistics",
      region: "EMEA",
      renewal_date: "2027-03-31",
      notice_period_days: 90,
      contract_value: null,
      services: ["freight"],
      owner: "Ananya Rao",
      summary:
        "Pasted meeting notes on the third quarter renewal cycle, kept alongside the contracts they refer to.",
    },
  },
  {
    file: "vantage-security-audit.pdf",
    objectId: "11111111-1111-4111-8111-111111111111",
    status: "completed",
    source: "file",
    created: "2026-08-11T09:12:00",
    links: true,
    fields: {
      vendor: "Vantage Security",
      region: "EMEA",
      renewal_date: "2026-12-31",
      notice_period_days: 30,
      contract_value: 76000,
      services: ["penetration testing", "audit"],
      owner: "Ananya Rao",
      summary:
        "Annual penetration test and audit engagement, renewing at the end of the calendar year.",
    },
  },
  {
    file: "vantage-security-retainer.pdf",
    objectId: "11111111-1111-4111-8111-111111111112",
    status: "completed",
    source: "file",
    created: "2026-08-12T09:12:00",
    links: true,
    fields: {
      vendor: "Vantage Security",
      region: "EMEA",
      renewal_date: "2026-12-31",
      notice_period_days: 30,
      contract_value: 24000,
      services: ["incident response"],
      owner: "Priya Sharma",
      summary:
        "Standing incident response retainer, drawn down by the hour against the audit engagement.",
    },
  },
  {
    file: "orbital-freight-amendment-3.pdf",
    objectId: "11111111-1111-4111-8111-111111111113",
    status: "processing",
    source: "file",
    created: "2026-08-13T09:12:00",
  },
  {
    file: "scanned-supplier-fax.pdf",
    objectId: "11111111-1111-4111-8111-111111111114",
    status: "failed",
    source: "file",
    created: "2026-08-14T09:12:00",
  },
];

const documentsBody = {
  document: DOCS.map((doc, index) => ({
    id: String(3000 + index),
    object_id: doc.objectId,
    knowledge_id: KNOWLEDGE_ID,
    file_name: doc.file,
    status: doc.status,
    error_detail:
      doc.status === "failed"
        ? "No text layer could be read from this scan."
        : null,
    content_type: doc.source,
    source: doc.source,
    url: doc.source === "url" ? doc.file : null,
    created_time: doc.created,
    updated_time: doc.created,
    source_presigned_url: doc.links
      ? "https://example.invalid/original.pdf"
      : null,
    extracted_s3_url: doc.links ? "https://example.invalid/extracted.md" : null,
    processing_telemetry: doc.telemetry ?? null,
  })),
  total_count: DOCS.length,
  current_page: 1,
  total_pages: 1,
};

/** The extraction schema, in the order the service returns it. */
const column = (name: string, data_type: string, parent_type = "scalar") => ({
  name,
  original_field: name,
  field_path: name,
  subcolumn: null,
  parent_type,
  data_type,
  is_user_selected: true,
});

const metadataBody = {
  columns: [
    column("vendor", "str"),
    column("region", "str"),
    column("renewal_date", "str"),
    column("notice_period_days", "int"),
    column("contract_value", "int"),
    column("services", "List[str]", "list_str"),
    column("owner", "str"),
    // Long and unique per document, so the type guess reads it as prose. It is
    // the column that proves the group-by picker offers every column.
    column("summary", "str"),
  ],
  rows: DOCS.filter((doc) => doc.fields).map((doc) => ({
    object_id: doc.objectId,
    ...doc.fields,
  })),
  total_count: DOCS.filter((doc) => doc.fields).length,
  current_count: DOCS.filter((doc) => doc.fields).length,
  current_page: 1,
  total_pages: 1,
};

/** Documents that have been read, for the assertions that count them. */
export const COMPLETED_COUNT = DOCS.filter(
  (doc) => doc.status === "completed",
).length;
export const PROCESSING_COUNT = DOCS.filter(
  (doc) => doc.status === "processing",
).length;
export const FAILED_COUNT = DOCS.filter(
  (doc) => doc.status === "failed",
).length;
export const DOC_COUNT = DOCS.length;
/** 2 document + 4 status + 8 extracted. */
export const COLUMN_COUNT = 14;
export const FIELD_COUNT = 8;

export async function mockRichKnowledge(page: Page) {
  const recorder = await mockKnowledgeService(page);

  // Registered last, so these win over the shared fixture's thinner answers.
  // Matched by RegExp rather than by glob: the paths differ only by what
  // follows `/documents`, and a glob's `*` would have the list route swallow
  // the metadata one.
  await page.route(/\/documents(\?|$)/, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(documentsBody),
    });
  });
  await page.route(/\/documents\/metadata\/all/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(metadataBody),
    }),
  );

  return recorder;
}
