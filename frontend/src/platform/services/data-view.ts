import type {
  DataViewByObjectIdsRequest,
  DataViewCursorRequest,
  DataViewCursorResponse,
  DataViewRequest,
  DataViewResponse,
} from "@/platform/types/data-view";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

const APIS = {
  GET_DATA_VIEW_TABULAR:
    "/api/knowledge/v2/{knowledge_id}/documents/metadata/all",
  GET_DATA_VIEW_TABULAR_CURSOR:
    "/api/knowledge/v1/{knowledge_id}/documents/metadata/all",
  LIST_DOCUMENTS: "/api/knowledge/v2/{knowledge_id}/documents",
} as const;

/** Document-level fields the metadata routes do not all carry. */
export interface KnowledgeDocumentSummary {
  object_id: string;
  file_name?: string;
  url?: string;
  status?: string;
  created_time?: string;
  updated_time?: string;
}

/**
 * Document list used to fill document-level columns the v1 cursor route drops
 * (today: `file_name`, which the v2 table shows but the export route omits).
 */
export const getKnowledgeDocuments = async (
  knowledgeId: string,
): Promise<KnowledgeDocumentSummary[]> => {
  const url = buildApiUrl(APIS.LIST_DOCUMENTS, { knowledge_id: knowledgeId });
  const response = await axios.get(url);
  const payload = response.data;
  const documents = Array.isArray(payload)
    ? payload
    : (payload?.documents ?? payload?.document);

  return Array.isArray(documents) ? documents : [];
};

/**
 * Get tabular data view with flattened document metadata
 *
 * @param knowledgeId - The knowledge base ID
 * @param request - Request parameters for filtering, sorting, pagination
 * @returns Tabular data response with fixed schema
 */
export const getDataViewTabular = async (
  knowledgeId: string,
  request: DataViewRequest = {},
): Promise<DataViewResponse> => {
  const url = buildApiUrl(APIS.GET_DATA_VIEW_TABULAR, {
    knowledge_id: knowledgeId,
  });

  try {
    const response = await axios.post<DataViewResponse>(url, {
      page_size: request.page_size ?? 20,
      page: request.page ?? 1,
      columns: request.columns,
      filters: request.filters,
      sort: request.sort ?? [],
    });

    return response.data;
  } catch (error: any) {
    throw error;
  }
};

/**
 * Tabular metadata for a known set of documents (`/v2/…/metadata/all`).
 *
 * The v2 route carries the full column schema (connector fields such as
 * `file_name`, `source`, `last_modified` and every `clickup_*` key) that the v1
 * export route projects away, but its `page` pagination returns overlapping
 * windows, so it cannot be walked. Filtering by `object_id in [...]` sidesteps
 * pagination entirely: one page holds the whole batch, so the export reads the
 * v1 cursor walk for the row set and then re-reads those ids here for columns.
 *
 * Keep `objectIds` small enough that the matching rows fit in one page — a
 * document can own several metadata points, so rows can outnumber ids.
 */
export const getDataViewTabularByObjectIds = async (
  knowledgeId: string,
  request: DataViewByObjectIdsRequest,
): Promise<DataViewResponse> => {
  const url = buildApiUrl(APIS.GET_DATA_VIEW_TABULAR, {
    knowledge_id: knowledgeId,
  });

  const response = await axios.post<DataViewResponse>(url, {
    page: 1,
    page_size: request.page_size ?? 100,
    columns: request.columns,
    filters: {
      ...request.filters,
      object_id: { op: "in", value: request.objectIds },
    },
    sort: request.sort ?? [],
  });

  return response.data;
};

/**
 * Cursor-paginated tabular metadata (`/v1/…/metadata/all`).
 *
 * The v2 endpoint above cannot read past its first page: `page` is validated
 * and echoed back as `current_page` but never applied, and `page_size` is capped
 * at 100. This v1 route paginates with a Qdrant cursor instead — pass the
 * previous response's `next_page_offset` and keep going while `has_more`.
 * Used by the CSV export; the table itself still uses v2.
 */
export const getDataViewTabularCursor = async (
  knowledgeId: string,
  request: DataViewCursorRequest = {},
): Promise<DataViewCursorResponse> => {
  const url = buildApiUrl(APIS.GET_DATA_VIEW_TABULAR_CURSOR, {
    knowledge_id: knowledgeId,
  });

  const response = await axios.post<DataViewCursorResponse>(url, {
    limit: request.limit ?? 100,
    ...(request.page_offset ? { page_offset: request.page_offset } : {}),
    columns: request.columns,
    filters: request.filters,
    sort: request.sort ?? [],
  });

  return response.data;
};
