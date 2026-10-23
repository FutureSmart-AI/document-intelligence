/**
 * NL2SQL result export. Thin wrapper over the shared CSV util so query results,
 * Data View and the Knowledge list all produce identically escaped files.
 */
import { downloadRowsAsCsv, rowsToCsv } from "@/platform/utils/csv";

export { rowsToCsv };

export const downloadCsv = (
  rows: Record<string, unknown>[],
  filename: string,
): void => downloadRowsAsCsv(rows, filename);
