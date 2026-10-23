import { isEmpty, lowerCase, some } from "lodash-es";

export const MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024; // 50MB
export const ACCEPTED_TYPES_LABEL = "pdf, docx, txt, mp3, md";

// Standardized message shown for every single-upload workflow
// (Plain Text, File, Web URL) to keep the experience consistent.
export const UPLOAD_INITIATED_MESSAGE = "Document upload has been initiated";

export function getStatusBadgeClass(status: string): string {
  switch (status) {
    case "completed":
      return "bg-green-100 text-green-800";
    case "processing":
    case "enriching":
    case "pending":
    case "queued":
    case "indexing":
      return "bg-blue-100 text-blue-800";
    default:
      return "bg-red-100 text-red-800";
  }
}

const PROCESSING_STATUSES = [
  "processing",
  "enriching",
  "pending",
  "queued",
  "indexing",
  "chunking",
  "deleting",
];

export function isFileBeingProcessed(status: string): boolean {
  return PROCESSING_STATUSES.includes(lowerCase(status));
}

const FINAL_STATUSES = ["completed", "failed", "invalid"];
/**
 * Checks if an item has a final status (completed or failed)
 */
export function isStatusFinal(status: string): boolean {
  return FINAL_STATUSES.includes(lowerCase(status || ""));
}

/**
 * Checks if any items in the array have in-progress status
 */
export function hasInProgressItems<
  T extends { status?: string; sub_urls?: Array<{ status?: string }> },
>(items: T[] | undefined | null): boolean {
  if (!items || isEmpty(items)) {
    return false;
  }
  return some(items, (item) => {
    const hasItemInProgress = item.status && !isStatusFinal(item.status);
    const hasSubUrlInProgress =
      Array.isArray(item.sub_urls) &&
      some(item.sub_urls, (sub) => sub.status && !isStatusFinal(sub.status));
    return hasItemInProgress || hasSubUrlInProgress;
  });
}

/**
 * Returns the effective display status of an item, accounting for sub_urls.
 * If the parent is "completed" but any sub_url is still in progress, returns that sub_url's status.
 */
export function getEffectiveStatus(
  status: string,
  subUrls?: Array<{ status?: string }> | null,
): string {
  if (!subUrls || subUrls.length === 0) return status;
  const inProgress = subUrls.find(
    (sub) => sub.status && !isStatusFinal(sub.status),
  );
  if (inProgress && inProgress.status) return inProgress.status;
  return status;
}

/**
 * Counts the number of pages in a PDF file by searching for /Type /Page patterns.
 * This is a lightweight estimation method that avoids heavy dependencies.
 */
export async function countPdfPages(file: File): Promise<number> {
  if (file.type !== "application/pdf") return 0;

  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const content = reader.result as string;
      // PDF page objects are usually defined with '/Type /Page'
      // We search for this pattern to count pages.
      const matches = content.match(/\/Type\s*\/Page\b/g);
      resolve(matches ? matches.length : 0);
    };
    reader.onerror = () => resolve(0);
    // Read only the first 5MB or so if it's huge, but for page counting
    // we might need the whole thing if the structure is complex.
    // However, binary search is usually fast.
    reader.readAsBinaryString(file);
  });
}
