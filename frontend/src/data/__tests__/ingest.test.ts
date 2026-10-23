import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import { ingestFiles, ingestPlainText, ingestWebsite } from "../ingest";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const getBatchPresignedUrls = vi.fn();
const uploadToS3 = vi.fn();
vi.mock("@/platform/services/upload", () => ({
  getContentType: (file: File) => file.type || "application/octet-stream",
  uploadService: {
    getBatchPresignedUrls: (...args: unknown[]) =>
      getBatchPresignedUrls(...args),
    uploadToS3: (...args: unknown[]) => uploadToS3(...args),
  },
}));

const addBatchToTracking = vi.fn();
vi.mock("@/platform/hooks/useBatchTracking", () => ({
  addBatchToTracking: (...args: unknown[]) => addBatchToTracking(...args),
}));

const mockedPost = vi.mocked(axios.post);

const K = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";

/**
 * The fields the service defaults for itself. A client that sends any of them
 * freezes today's value into a released UI, and none of them is a choice this
 * product asks anyone to make — so every request is checked for their absence,
 * not just for what it does carry.
 */
const BACKEND_OWNED = [
  "enable_chunking",
  "chunk_size",
  "transcript_provider",
  "use_llama_parse",
  "extra_metadata",
  // The service detects how a document has to be read — including re-reading an
  // image-only PDF — and defaults the mode to auto, so sending a guess made in
  // a dialog would override a decision made with the document in hand.
  "document_processing_mode",
];

function lastCall() {
  const [url, body, config] = mockedPost.mock.calls.at(-1) as [
    string,
    FormData,
    { headers?: Record<string, string> },
  ];
  return { url, body, config };
}

function sentFields(body: FormData): string[] {
  return Array.from(body.keys()).sort();
}

describe("ingestPlainText", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedPost.mockResolvedValue({ data: { status: "processing" } });
  });

  /** Pasted text is already text: there is nothing to parse and nothing to fetch. */
  it("sends the text and nothing else", async () => {
    await ingestPlainText(K, "some notes");
    const { url, body, config } = lastCall();

    expect(url).toBe(`/api/knowledge/v1/${K}/documents`);
    // The shared axios instance defaults to JSON, and axios serialises FormData
    // to JSON when that is the declared content type — the route then takes form
    // fields only and stores nothing.
    expect(config.headers?.["Content-Type"]).toBe("multipart/form-data");
    expect(sentFields(body)).toEqual(["text"]);
    expect(body.get("text")).toBe("some notes");
  });

  /** The knowledge id is in the path; the form field for it does not exist. */
  it("does not repeat the knowledge id as a form field", async () => {
    await ingestPlainText(K, "some notes");
    expect(lastCall().body.get("knowledge_id")).toBeNull();
  });
});

describe("ingestWebsite", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedPost.mockResolvedValue({ data: { status: "processing" } });
  });

  it("sends the link, the method and the depth — and only those", async () => {
    await ingestWebsite(K, "https://example.com", {
      method: "crawl",
      maxDepth: 2,
    });
    const { url, body } = lastCall();

    expect(url).toBe(`/api/knowledge/v1/${K}/documents`);
    expect(sentFields(body)).toEqual([
      "max_depth",
      "scraping_method",
      "weblink",
    ]);
    expect(body.get("weblink")).toBe("https://example.com");
    expect(body.get("scraping_method")).toBe("crawl");
    expect(body.get("max_depth")).toBe("2");
  });

  /**
   * `scraping_method` is lowercase on the wire even where the control reads
   * "Scrape". Pinned because the wrong case is a 422 at upload time, not at
   * build time.
   */
  it("sends the method in the casing the service accepts", async () => {
    await ingestWebsite(K, "https://example.com");
    expect(lastCall().body.get("scraping_method")).toBe("scrape");
  });

  /**
   * A scrape reads the one page it was given, so a depth beside it means
   * nothing — leaving it out keeps the service's own default.
   */
  it("sends no depth for a scrape", async () => {
    await ingestWebsite(K, "https://example.com", {
      method: "scrape",
      maxDepth: 2,
    });
    const { body } = lastCall();

    expect(body.get("scraping_method")).toBe("scrape");
    expect(body.get("max_depth")).toBeNull();
  });

  /** Omitting the depth on a crawl would take whatever the service defaults to. */
  it("falls back to depth 1 when a crawl does not name one", async () => {
    await ingestWebsite(K, "https://example.com", { method: "crawl" });
    expect(lastCall().body.get("max_depth")).toBe("1");
  });

  /**
   * `scrape_using` is deprecated and has no effect, and the processing mode is
   * the service's own call now. Pinned so neither creeps back in.
   */
  it("never sends the deprecated scraper field or a processing mode", async () => {
    await ingestWebsite(K, "https://example.com", { method: "crawl" });
    expect(lastCall().body.get("scrape_using")).toBeNull();
    expect(lastCall().body.get("document_processing_mode")).toBeNull();
  });

  it("leaves the backend's own fields out", async () => {
    await ingestWebsite(K, "https://example.com");
    for (const field of BACKEND_OWNED) {
      expect(lastCall().body.get(field)).toBeNull();
    }
  });
});

describe("ingestFiles", () => {
  const file = (name: string) =>
    new File(["hello"], name, { type: "text/plain" });

  const presigned = (...names: string[]) => ({
    batch_id: "batch-1",
    uploads: names.map((name) => ({
      object_id: `o-${name}`,
      file_name: name,
      presigned_url: `https://s3/put/${name}?sig=abc`,
      download_presigned_url: `https://s3/get/${name}?sig=abc`,
    })),
  });

  beforeEach(() => {
    vi.clearAllMocks();
    getBatchPresignedUrls.mockResolvedValue(presigned("a.pdf", "b.pdf"));
    uploadToS3.mockResolvedValue(undefined);
    mockedPost.mockResolvedValue({
      data: { documents: [{ file_name: "a.pdf" }, { file_name: "b.pdf" }] },
    });
  });

  /**
   * The batch pair, always. The single-file presign and direct-multipart routes
   * exist on the service but this product never calls them, so one file and
   * fifty answer the same shape.
   */
  it("registers the batch with the file addresses as its only field", async () => {
    const result = await ingestFiles(K, [file("a.pdf"), file("b.pdf")]);

    expect(getBatchPresignedUrls).toHaveBeenCalledWith({
      knowledgeId: K,
      files: [
        { file_name: "a.pdf", content_type: "text/plain" },
        { file_name: "b.pdf", content_type: "text/plain" },
      ],
    });
    expect(uploadToS3).toHaveBeenCalledTimes(2);

    const { url, body, config } = lastCall();
    expect(url).toBe(`/api/knowledge/v1/${K}/documents/batch`);
    expect(config.headers?.["Content-Type"]).toBe("multipart/form-data");
    expect(sentFields(body)).toEqual(["file_urls"]);
    expect(result).toEqual({ batchId: "batch-1", failed: [], invalid: [] });
  });

  /** A presigned URL's signature is not part of the document's address. */
  it("registers the download URLs without their signatures", async () => {
    await ingestFiles(K, [file("a.pdf"), file("b.pdf")]);

    expect(lastCall().body.get("file_urls")).toBe(
      "https://s3/get/a.pdf,https://s3/get/b.pdf",
    );
  });

  /** Omitting the mode is the same as asking for Text-based. */
  it("omits the mode when none was chosen", async () => {
    await ingestFiles(K, [file("a.pdf"), file("b.pdf")]);

    expect(sentFields(lastCall().body)).toEqual(["file_urls"]);
    for (const field of BACKEND_OWNED) {
      expect(lastCall().body.get(field)).toBeNull();
    }
  });

  /** The list shows the documents before the first one has finished being read. */
  it("tracks the batch so the list can watch it", async () => {
    getBatchPresignedUrls.mockResolvedValue(presigned("a.pdf"));
    await ingestFiles(K, [file("a.pdf")]);
    expect(addBatchToTracking).toHaveBeenCalledWith(K, "batch-1");
  });

  /** One unreadable file must not cost the rest of the folder. */
  it("registers the files that reached S3 and names the ones that did not", async () => {
    uploadToS3.mockImplementation((presignedUrl: string) =>
      presignedUrl.includes("b.pdf")
        ? Promise.reject({ response: { data: { detail: "Too large" } } })
        : Promise.resolve(undefined),
    );

    const result = await ingestFiles(K, [file("a.pdf"), file("b.pdf")]);

    expect(result.failed).toEqual([{ fileName: "b.pdf", error: "Too large" }]);
    expect(lastCall().body.get("file_urls")).toBe("https://s3/get/a.pdf");
  });

  /** Every file failing means there is nothing to register. */
  it("does not register anything when no file reached S3", async () => {
    getBatchPresignedUrls.mockResolvedValue(presigned("a.pdf"));
    uploadToS3.mockRejectedValue(new Error("network down"));

    const result = await ingestFiles(K, [file("a.pdf")]);

    expect(mockedPost).not.toHaveBeenCalled();
    expect(result.failed).toEqual([
      { fileName: "a.pdf", error: "network down" },
    ]);
  });

  /** A file the service took but could not read carries its own reason. */
  it("reports the documents the service could not read", async () => {
    mockedPost.mockResolvedValue({
      data: {
        documents: [
          { file_name: "a.pdf", status: "processing" },
          {
            file_name: "b.pdf",
            status: "invalid",
            error_detail: "Password protected",
          },
        ],
      },
    });

    const result = await ingestFiles(K, [file("a.pdf"), file("b.pdf")]);

    expect(result.invalid).toEqual([
      { fileName: "b.pdf", errorDetail: "Password protected" },
    ]);
  });

  it("reports progress across the whole batch", async () => {
    uploadToS3.mockImplementation(
      (_url: string, _file: File, onProgress: (percent: number) => void) => {
        onProgress(100);
        return Promise.resolve(undefined);
      },
    );
    const seen: number[] = [];

    await ingestFiles(K, [file("a.pdf"), file("b.pdf")], {
      onProgress: (percent) => seen.push(percent),
    });

    expect(seen.at(-1)).toBe(100);
  });

  it("does nothing at all when there are no files", async () => {
    const result = await ingestFiles(K, []);

    expect(result).toEqual({ batchId: null, failed: [], invalid: [] });
    expect(getBatchPresignedUrls).not.toHaveBeenCalled();
  });
});
