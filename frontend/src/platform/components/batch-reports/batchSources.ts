import type { Source } from "@/platform/chat-core/types";
import { RetrievedChunk, SourceUsed } from "@/platform/types/batch-reports";

export const safeUrl = (u?: string): string | undefined => {
  if (!u) return undefined;
  try {
    const { protocol } = new URL(u);
    return protocol === "http:" || protocol === "https:" ? u : undefined;
  } catch {
    return undefined;
  }
};

export const sourceName = (s: SourceUsed): string => {
  // Backend sometimes sends sources as plain strings
  if (typeof (s as unknown) === "string")
    return (s as unknown as string) || "—";
  const r = s as Record<string, unknown>;
  return (
    (r.name as string) ||
    (r.title as string) ||
    (r.source as string) ||
    (r.document_name as string) ||
    (r.file_name as string) ||
    (r.filename as string) ||
    (r.doc_name as string) ||
    (r.label as string) ||
    safeUrl(r.url as string) ||
    safeUrl(r.link as string) ||
    (r.doc_id as string) ||
    (r.chunk_id as string) ||
    (r.id as string) ||
    "—"
  );
};

const chunkText = (c: RetrievedChunk): string =>
  (c.content ?? c.text ?? (c.chunk as string) ?? "") as string;

/**
 * Map the batch report's loose `sources_used` + `retrieved_chunks` into the
 * chat page's `Source[]` shape so the report reuses the same `SourcesPanel`
 * UI as the chat page. Chunks are linked to their parent
 * document by id/name when possible, otherwise attached to the lone source or
 * bucketed under a synthetic "Retrieved Chunks" entry.
 */
export const toChatSources = (
  sources: SourceUsed[],
  chunks: RetrievedChunk[],
): Source[] => {
  const byKey = new Map<string, Source>();
  const order: string[] = [];

  const ensure = (key: string, seed: () => Source): Source => {
    let s = byKey.get(key);
    if (!s) {
      s = seed();
      byKey.set(key, s);
      order.push(key);
    }
    return s;
  };

  sources.forEach((s) => {
    const rec =
      typeof (s as unknown) === "string"
        ? ({} as Record<string, unknown>)
        : (s as Record<string, unknown>);
    const name = sourceName(s);
    const docId = (rec.document_id ?? rec.doc_id ?? rec.id) as
      string | undefined;
    const key = docId ?? name;
    ensure(key, () => ({
      file_name: name,
      context: (rec.context ??
        rec.content ??
        rec.text ??
        rec.summary ??
        "") as string,
      document_id: docId,
      source_type: (rec.type ?? rec.source_type ?? rec.kind) as
        string | undefined,
      summary: rec.summary as string | undefined,
      metadata: rec,
      chunks: [],
    }));
  });

  // If exactly one document is present, unlinked chunks belong to it.
  const singleKey = order.length === 1 ? order[0] : null;

  chunks.forEach((c, i) => {
    const rec = c as Record<string, unknown>;
    const docId = (rec.doc_id ?? rec.document_id) as string | undefined;
    const docName = (rec.document_name ?? rec.file_name ?? rec.doc_name) as
      string | undefined;
    const linkKey =
      (docId && byKey.has(docId) && docId) ||
      (docName && byKey.has(docName) && docName) ||
      singleKey ||
      "__chunks__";
    const target = ensure(linkKey, () => ({
      file_name: docName ?? "Retrieved Chunks",
      context: "",
      document_id: docId,
      chunks: [],
    }));
    target.chunks!.push({
      chunk_id: c.chunk_id ?? c.doc_id ?? `Chunk ${i + 1}`,
      text: chunkText(c),
      score: c.score,
      metadata: rec,
    });
  });

  return order.map((k) => byKey.get(k)!);
};
