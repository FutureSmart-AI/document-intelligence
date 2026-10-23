import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";

/**
 * What kind of thing a document is, for the viewer's own chrome.
 *
 * Only two questions are asked of it — what to call it, and whether a browser
 * can render it inline — because those are the only two the viewer acts on. It
 * is deliberately not a MIME table: the service does not report one for every
 * document, and guessing a renderer from a file extension is how a `.doc` ends
 * up in an empty embed frame.
 */
export interface DocumentKind {
  label: string;
  /** True only where an inline embed genuinely works: PDFs, and plain text. */
  embeddable: boolean;
}

export function documentKind(item: KnowledgeItem): DocumentKind {
  const source = String(item.content_type ?? "").toLowerCase();
  if (source === "url") return { label: "Web page", embeddable: false };
  if (source === "plain_text")
    return { label: "Plain text", embeddable: false };

  const name = String(item.file_name ?? "").toLowerCase();
  if (name.endsWith(".pdf")) return { label: "PDF", embeddable: true };
  if (/\.(md|markdown)$/.test(name))
    return { label: "Markdown", embeddable: false };
  if (/\.(txt|csv|json)$/.test(name))
    return { label: "Plain text", embeddable: false };
  if (/\.(png|jpe?g|gif|webp)$/.test(name))
    return { label: "Image", embeddable: false };
  if (/\.docx?$/.test(name)) return { label: "Word", embeddable: false };
  if (/\.xlsx?$/.test(name)) return { label: "Spreadsheet", embeddable: false };
  return { label: "File", embeddable: false };
}
