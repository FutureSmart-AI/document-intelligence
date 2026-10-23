import axios from "@/platform/utils/axios";

/** MIME types whose conventional extension is not just the subtype. */
const MIME_EXTENSIONS: Record<string, string> = {
  "text/plain": "txt",
  "text/markdown": "md",
  "text/html": "html",
  "text/csv": "csv",
  "application/pdf": "pdf",
  "application/json": "json",
  "application/msword": "doc",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
    "docx",
  "application/vnd.ms-excel": "xls",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "application/vnd.ms-powerpoint": "ppt",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation":
    "pptx",
  "application/zip": "zip",
};

/** MIME types that say nothing about the format, so cannot name the file. */
const OPAQUE_MIME = new Set([
  "application/octet-stream",
  "binary/octet-stream",
  "application/binary",
]);

const extensionFromUrl = (url: string): string | null => {
  let path: string;
  try {
    path = new URL(url, "http://localhost").pathname;
  } catch {
    return null;
  }
  const segment = decodeURIComponent(path.split("/").pop() ?? "");
  const dot = segment.lastIndexOf(".");
  if (dot <= 0 || dot === segment.length - 1) return null;
  const ext = segment.slice(dot + 1);
  // Real extensions are short and alphanumeric; anything else is part of a name.
  return /^[a-z0-9]{1,8}$/i.test(ext) ? ext : null;
};

const extensionFromMime = (mimeType: string): string | null => {
  const type = mimeType.split(";")[0].trim().toLowerCase();
  if (!type || OPAQUE_MIME.has(type)) return null;
  if (MIME_EXTENSIONS[type]) return MIME_EXTENSIONS[type];
  const subtype = type.split("/")[1];
  if (!subtype) return null;
  return subtype.replace(/^vnd\./, "").replace(/\+.*$/, "") || null;
};

/**
 * Guarantees the download has a file extension.
 *
 * Knowledge items are named by `file_name`, which is a bare title for anything
 * that was not uploaded as a file (crawled URLs, ClickUp docs, pasted text). The
 * browser saves that name verbatim, so the file lands extensionless and the OS
 * cannot open it. Falls back to the extension in the URL path, then to the
 * response's content type; when neither identifies the format the name is left
 * as-is rather than guessed wrong.
 */
export const ensureFileExtension = (
  filename: string,
  { url, mimeType }: { url?: string; mimeType?: string },
): string => {
  const base = filename?.trim() || "document";
  const dot = base.lastIndexOf(".");
  const existing = dot > 0 && dot < base.length - 1 ? base.slice(dot + 1) : "";
  // A dotted stem ("2026.07.30 notes") is not an extension.
  if (/^[a-z0-9]{1,8}$/i.test(existing) && !/\s/.test(existing)) return base;

  const stem = base.replace(/\.$/, "");
  const ext =
    (url ? extensionFromUrl(url) : null) ??
    (mimeType ? extensionFromMime(mimeType) : null);
  return ext ? `${stem}.${ext}` : stem;
};

export const downloadFile = async (url: string, filename: string) => {
  try {
    const response = await axios.get(url, {
      responseType: "blob",
    });

    const blob = new Blob([response.data], { type: "text/plain" });
    const downloadUrl = window.URL.createObjectURL(blob);

    // Force .md extension while preserving original filename
    const mdFilename = filename?.includes(".")
      ? `${filename.substring(0, filename.lastIndexOf("."))}.md`
      : `${filename}.md`;

    const link = document.createElement("a");
    link.href = downloadUrl;
    link.setAttribute("download", mdFilename);
    link.style.display = "none";

    document.body.appendChild(link);
    link.click();

    window.URL.revokeObjectURL(downloadUrl);
    document.body.removeChild(link);
  } catch (error) {
    console.error("Download failed:", error);
    throw error;
  }
};

export const downloadExternalFile = async (url: string, filename: string) => {
  try {
    const response = await axios.get(url, { responseType: "blob" });
    const downloadUrl = window.URL.createObjectURL(response.data);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = ensureFileExtension(filename, {
      url,
      mimeType:
        (response.data as Blob)?.type ||
        (response.headers?.["content-type"] as string | undefined),
    });
    document.body.appendChild(link);
    link.click();
    window.URL.revokeObjectURL(downloadUrl);
    document.body.removeChild(link);
  } catch (error) {
    console.error("Download failed:", error);
    window.open(url, "_blank");
  }
};
