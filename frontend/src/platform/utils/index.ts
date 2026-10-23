/**
 * Converts an ArrayBuffer to a base64 string
 */
export function arrayBufferToBase64(buffer: ArrayBuffer): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const base64String = reader.result?.toString().split(",")[1];
      resolve(base64String ?? "");
    };
    reader.onerror = (error) => {
      reject(error);
    };
    const blob = new Blob([buffer]);
    reader.readAsDataURL(blob);
  });
}

// Extract parameter names from URL template
type ExtractParams<T extends string> =
  T extends `${infer _Start}{${infer Param}}${infer Rest}`
    ? Param | ExtractParams<Rest>
    : never;

// Convert union of keys to a Record type
type ParamsRecord<T extends string> =
  ExtractParams<T> extends never
    ? Record<string, never> // No params needed
    : Record<ExtractParams<T>, string>;

/**
 * Build API URL from template with parameter substitution
 * Uses native string replacement instead of lodash template for smaller bundle
 */
export const buildApiUrl = <T extends string>(
  templateUrl: T,
  params: ParamsRecord<T>,
): string => {
  try {
    // Replace {param} patterns with actual values from params object
    return templateUrl.replace(/\{(\w+)\}/g, (_, key) => {
      const value = (params as Record<string, string>)[key];
      return value !== undefined ? value : `{${key}}`;
    });
  } catch (error) {
    console.error("Error building API URL:", error);
    return templateUrl;
  }
};

/**
 * Utility function to conditionally join class names
 * Filters out falsy values and joins with space
 */
export const cn = (
  ...classes: (string | undefined | null | false)[]
): string => {
  return classes.filter(Boolean).join(" ");
};

/**
 * Utility function for formatting numbers
 * Returns K, M, B suffixes for large numbers
 */
export const formatNumber = (num: number | string): string => {
  if (typeof num === "string") return num;
  if (num >= 1_000_000_000) return `${(num / 1_000_000_000).toFixed(1)}B`;
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}k`;
  return num?.toLocaleString() || "0";
};
