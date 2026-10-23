import {
  defaultTo,
  filter,
  find,
  first,
  get,
  groupBy,
  isArray,
  isPlainObject,
  isString,
  map,
  omit,
} from "lodash-es";

/**
 * Chunk-only fields that some backends mistakenly attach to a source's
 * document-level `metadata` (instead of only on `chunks[].metadata`). Strip
 * these everywhere a source's `metadata` is finalized so chunk data can never
 * masquerade as Document metadata, regardless of which extraction level
 * produced it.
 */
const CHUNK_ONLY_METADATA_KEYS = [
  "chunk_topic",
  "chunk_keywords",
  "chunk_entities",
  "id",
];

const sanitizeDocumentMetadata = (metadata: any): any => {
  if (!isPlainObject(metadata)) return metadata;
  const cleaned = omit(metadata, CHUNK_ONLY_METADATA_KEYS);
  return Object.keys(cleaned).length > 0 ? cleaned : undefined;
};

/**
 * Processes sources by replacing context with summary chunk text when available,
 * and filtering out summary chunks from the chunks array. Preserves all other
 * fields via spread.
 */
export const processSources = (sources: any[]) => {
  if (!sources || !Array.isArray(sources)) return sources;

  return map(sources, (source) => {
    const chunks = get(source, "chunks");
    if (!chunks || !Array.isArray(chunks)) return { ...source };

    const summaryChunk = find(chunks, { type: "summary" });
    if (summaryChunk) {
      return {
        ...source,
        context: summaryChunk.text,
        summary: defaultTo(get(source, "summary"), summaryChunk.text),
        // For `chunks_only` sources (e.g. connected_agent) the document-level
        // metadata rides on the summary chunk. Promote it so the UI can render
        // Document metadata — otherwise it's dropped with the filtered summary
        // chunk. Any existing source-level metadata wins.
        metadata: firstNonEmpty(
          get(source, "metadata"),
          get(summaryChunk, "metadata"),
        ),
        chunks: filter(chunks, (c) => c?.type !== "summary"),
      };
    }
    return { ...source };
  });
};

/** First defined, non-empty value (treats null/undefined/"" as empty). */
const firstNonEmpty = (...values: any[]) =>
  find(values, (v) => v !== null && v !== undefined && v !== "");

/**
 * Maps a raw chunk to the SourceChunk shape, preserving rich fields.
 */
const mapChunk = (chunk: any) => ({
  chunk_id: get(chunk, "chunk_id"),
  topic: defaultTo(get(chunk, "topic"), "Section"),
  text: defaultTo(get(chunk, "text"), ""),
  score: get(chunk, "score"),
  position: get(chunk, "position"),
  metadata: get(chunk, "metadata"),
});

/**
 * Locates the summary entry that belongs to a chunk group. `summaries` is keyed
 * by the true document id, but some backends set a chunk's `document_id` to the
 * chunk's own id (so the key lookup misses). We therefore match by document_id
 * first, then fall back to `file_name` — the only field reliably shared by both
 * collections. This mirrors the document-identity precedence used when merging
 * sources, so both code paths agree on what "the same document" means.
 */
const findSummary = (summaries: any, documentId: any, fileName: any): any => {
  if (!isPlainObject(summaries)) return undefined;
  if (documentId && summaries[documentId]) return summaries[documentId];
  if (fileName && fileName !== "Unknown") {
    return find(summaries, (s: any) => get(s, "file_name") === fileName);
  }
  return undefined;
};

/**
 * Groups chunks by document_id (or file_name as fallback) and transforms them
 * into the sources format expected by the UI, preserving document- and
 * chunk-level metadata.
 */
export const processChunks = (chunks: any[], summaries: any = {}): any[] => {
  if (!chunks || !isArray(chunks) || chunks.length === 0) return [];

  const chunksByDoc = groupBy(chunks, (chunk: any) => {
    const docId = get(chunk, "document_id");
    return docId || defaultTo(get(chunk, "file_name"), "Unknown");
  });

  return map(chunksByDoc, (chunksGroup) => {
    const head = first(chunksGroup);
    let context = "";
    let summary = "";
    let finalChunks = chunksGroup;
    let fileName = defaultTo(get(head, "file_name"), "Unknown");
    const documentId = get(head, "document_id");

    // Document-level metadata ONLY. Never fall back to the chunk's own
    // `metadata` (chunk_topic/chunk_keywords/chunk_entities/…): that is
    // chunk-level data already surfaced per-chunk via mapChunk, and leaking
    // it here makes chunk metadata masquerade as Document metadata.
    let documentMetadata = get(head, "document_metadata");

    const summaryData = findSummary(summaries, documentId, fileName);
    if (summaryData) {
      summary = defaultTo(get(summaryData, "summary"), "");
      context = summary;
      fileName = defaultTo(get(summaryData, "file_name"), fileName);
      // The summary entry is where the backend actually carries document-level
      // metadata (document_outline/entities/topics/title/…); prefer it over any
      // chunk-level document_metadata.
      documentMetadata = firstNonEmpty(
        get(summaryData, "metadata"),
        documentMetadata,
      );
      finalChunks = filter(chunksGroup, (c) => get(c, "type") !== "summary");
    } else {
      const summaryChunk = find(chunksGroup, { type: "summary" });
      if (summaryChunk) {
        summary = defaultTo(get(summaryChunk, "text"), "");
        context = summary;
        finalChunks = filter(chunksGroup, (c) => get(c, "type") !== "summary");
      }
    }

    return {
      file_name: fileName,
      context,
      summary,
      document_id: documentId,
      source_type: get(head, "source_type"),
      retrieval_mode: get(head, "retrieval_mode"),
      // When a document has no document metadata this stays undefined so the UI
      // can show an explicit "no document metadata" empty state.
      metadata: documentMetadata,
      chunks: map(finalChunks, mapChunk),
    };
  });
};

/**
 * Stable identity for a source so the same document is never split across
 * cards. Prefer a real file_name — it's the identity users actually see, and
 * some backends assign a fresh document_id per retrieved chunk/citation
 * rather than one stable id per document, which would otherwise defeat
 * id-based merging and fragment one file into many cards. Fall back to
 * document_id when file_name is missing/"Unknown". Returns null when there
 * is no reliable identity (so unrelated unnamed sources never merge).
 */
const sourceIdentity = (source: any): string | null => {
  const fileName = get(source, "file_name");
  if (fileName && fileName !== "Unknown") return `name:${fileName}`;
  const docId = get(source, "document_id");
  if (docId) return `id:${docId}`;
  return null;
};

/** Union two chunk arrays, de-duplicating by chunk_id when present. */
const unionChunks = (a: any[] = [], b: any[] = []): any[] => {
  const result: any[] = [];
  const seen = new Set<string>();
  for (const chunk of [...(a || []), ...(b || [])]) {
    const id = get(chunk, "chunk_id");
    if (id) {
      if (seen.has(id)) continue;
      seen.add(id);
    }
    result.push(chunk);
  }
  return result;
};

/** Merge two document-metadata objects, the first source's keys winning. */
const mergeMetadata = (a: any, b: any): any => {
  if (isPlainObject(a) && isPlainObject(b)) return { ...b, ...a };
  return firstNonEmpty(a, b);
};

/**
 * Collapses sources that refer to the same document into one. Some backends
 * emit a document's summary and its retrieved chunks as separate entries; left
 * as-is they render as multiple cards. Merging by document identity keeps every
 * document in a single card, combining summary, metadata and chunks. First-seen
 * order and the first entry's scalar values are preserved.
 */
export const mergeSourcesByDocument = (sources: any[]): any[] => {
  if (!isArray(sources) || sources.length === 0) return sources;

  const merged: any[] = [];
  const indexByKey = new Map<string, number>();

  for (const source of sources) {
    const key = sourceIdentity(source);
    if (key === null) {
      merged.push(source);
      continue;
    }

    const existingIndex = indexByKey.get(key);
    if (existingIndex === undefined) {
      indexByKey.set(key, merged.length);
      merged.push({ ...source });
      continue;
    }

    const existing = merged[existingIndex];
    merged[existingIndex] = {
      ...source,
      ...existing,
      file_name: firstNonEmpty(existing.file_name, source.file_name),
      context: firstNonEmpty(existing.context, source.context),
      summary: firstNonEmpty(existing.summary, source.summary),
      document_id: firstNonEmpty(existing.document_id, source.document_id),
      source_type: firstNonEmpty(existing.source_type, source.source_type),
      retrieval_mode: firstNonEmpty(
        existing.retrieval_mode,
        source.retrieval_mode,
      ),
      metadata: mergeMetadata(existing.metadata, source.metadata),
      chunks: unionChunks(existing.chunks, source.chunks),
    };
  }

  return map(merged, (source) => ({
    ...source,
    metadata: sanitizeDocumentMetadata(source.metadata),
  }));
};

/**
 * Extracts sources from a message object using a 4-level fallback, then merges
 * any entries that share a document identity so each document renders as one
 * card. All fields are carried through (spread) so document/chunk metadata
 * survives.
 *   1. data.knowledge.sources        — new unified API format
 *   2. data.knowledge.chunks         — grouped & enriched with summaries
 *   2.5 data.connected_agent[].sources — connected-agent replies (debug-independent)
 *   3. debug_info.sources            — legacy debug format (processSources applied)
 *   4. item.sources                  — raw legacy array
 */
export const extractSources = (messageItem: any): any[] =>
  mergeSourcesByDocument(selectRawSources(messageItem));

const selectRawSources = (messageItem: any): any[] => {
  // Level 1 — data.knowledge.sources
  const knowledgeSources = get(messageItem, "data.knowledge.sources");
  if (
    knowledgeSources &&
    isArray(knowledgeSources) &&
    knowledgeSources.length > 0
  ) {
    return map(knowledgeSources, (source: any) => ({
      ...source,
      file_name: defaultTo(get(source, "file_name"), "Unknown"),
      context: defaultTo(get(source, "context"), ""),
    }));
  }

  // Level 2 — data.knowledge.chunks
  const knowledgeChunks = get(messageItem, "data.knowledge.chunks");
  const knowledgeSummaries = get(messageItem, "data.knowledge.summaries");
  if (
    knowledgeChunks &&
    isArray(knowledgeChunks) &&
    knowledgeChunks.length > 0
  ) {
    return processChunks(knowledgeChunks, knowledgeSummaries || {});
  }

  // Level 2.5 — data.connected_agent[].sources
  // Connected-agent replies carry their (and their downstream knowledge/
  // integration) sources here, not under data.knowledge. Read them directly so
  // they render even when debug is off (debug_info absent). Same `chunks_only`
  // shape as debug sources, so processSources lifts summary text + metadata.
  const connectedAgents = get(messageItem, "data.connected_agent");
  if (isArray(connectedAgents) && connectedAgents.length > 0) {
    const caSources = connectedAgents.flatMap((agent: any) => {
      const sources = get(agent, "sources");
      return isArray(sources) ? sources : [];
    });
    if (caSources.length > 0) return processSources(caSources);
  }

  // Level 3 — debug_info.sources (may be JSON string)
  let debugInfo = get(messageItem, "debug_info");
  if (isString(debugInfo)) {
    try {
      debugInfo = JSON.parse(debugInfo);
    } catch {
      debugInfo = null;
    }
  }
  const debugSources = get(debugInfo, "sources");
  if (debugSources && isArray(debugSources) && debugSources.length > 0) {
    return processSources(debugSources);
  }

  // Level 4 — item.sources
  const itemSources = get(messageItem, "sources");
  if (itemSources && isArray(itemSources) && itemSources.length > 0) {
    return map(itemSources, (s: any) => ({ ...s }));
  }

  return [];
};
