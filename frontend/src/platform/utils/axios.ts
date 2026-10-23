import { buildApiUrl } from ".";
import axios from "axios";

import { browserStorageUrl } from "@/lib/storageUrl";
import { baseUrl } from "@/platform/config";

import { handleSessionExpiry } from "./session-expiry";

// Create axios instance with default config
const api = axios.create({
  baseURL: baseUrl,
  // CHANGE FROM MAIN: Increased default timeout from 10s to 180s (3 minutes)
  // Operations like file deletion with enrichment, metadata fetching, and large file uploads
  // can take longer than 10 seconds. Setting a reasonable default prevents premature timeouts.
  // Individual endpoints can override this if needed.
  timeout: 180 * 1000,
  headers: {
    "Content-Type": "application/json",
  },
});

// The browser reaches the development S3 emulator through its own UI origin.
// Backend/worker file URLs retain the container hostname for internal downloads.
api.interceptors.request.use((config) => {
  if (config.url) config.url = browserStorageUrl(config.url);
  return config;
});

api.interceptors.response.use(
  (response) => {
    if (response.data?.detail === "Not authenticated") {
      if (process.env.NEXT_PUBLIC_AUTH_MODE !== "standalone")
        handleSessionExpiry();
    }
    return response;
  },
  (error) => {
    if (error.response && error.response.status === 401) {
      // If the proxy refreshed cookies behind the scenes, retry the request once
      if (
        error.response.headers["x-cookies-refreshed"] === "true" &&
        !error.config._retried
      ) {
        error.config._retried = true;
        return api.request(error.config);
      }

      // Any other 401 means the session is unrecoverable — log out.
      if (process.env.NEXT_PUBLIC_AUTH_MODE !== "standalone")
        handleSessionExpiry();
    }
    return Promise.reject(error);
  },
);

// CHANGE FROM MAIN: Added TypeScript interfaces for document and chunk metadata
// These interfaces define the shape of metadata returned from the enrichment API
// Document metadata interface based on API documentation - includes fields like
// summary, topics, keywords, entities, and other document-level information
export interface DocumentMetadata {
  document_outline: string[];
  document_type: string;
  entities: string[];
  keywords: string[];
  organizations: string[];
  prepended_doc_summary: string;
  summary: string;
  temporal_context: string;
  title: string;
  topics: string[];
  seo_title: string;
  seo_description: string;
  url: string;
  source_knowledge_id: string;
}

export interface GetDocumentMetadataResponse {
  object_id: string;
  metadata: DocumentMetadata;
}

export interface ChunkMetadata {
  chunk_keywords?: string[];
  chunk_topic?: string;
  chunk_entities?: string[];
  [key: string]: any;
}

export interface ChunkData {
  id: string;
  text: string;
}

export interface GetDocumentChunksResponse {
  document_id: string;
  chunks: ChunkData[];
  total_chunks: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface GetDocumentChunkMetadataResponse {
  chunk_id: string;
  object_id: string;
  metadata: {
    chunk_keywords: string[] | null;
    chunk_topic: string | null;
    chunk_entities: string[] | null;
  } | null;
}
/**
 * CHANGE FROM MAIN: Added new API function to fetch document-level metadata
 * Get Document Metadata
 * Endpoint: GET /api/knowledge/v1/documents/{document_id}
 * Retrieve metadata for a specific document including title, summary, topics,
 * keywords, entities, and other enrichment data generated during processing
 */

// api/knowledge/75eb28d5-73b2-4c02-b5e5-cfaa8f5a9116/documents/237e4c75-058f-4f8c-ab0a-c851afc7e503/metadata

const API = {
  GET_DOCUMENT_METADATA:
    "/api/knowledge/v1/{knowledge_id}/documents/{object_id}/metadata",
  GET_DOCUMENT_CHUNKS:
    "/api/knowledge/v1/{knowledge_id}/documents/{object_id}/chunks",
  GET_DOCUMENT_CHUNK_METADATA:
    "/api/knowledge/v1/{knowledge_id}/chunks/{chunk_id}/metadata",
} as const;

export const getDocumentMetadata = async (params: {
  object_id: string;
  knowledgeId: string;
}): Promise<GetDocumentMetadataResponse> => {
  const { object_id, knowledgeId } = params;

  try {
    const url = buildApiUrl(API.GET_DOCUMENT_METADATA, {
      knowledge_id: knowledgeId,
      object_id,
    });
    const response = await api.get<GetDocumentMetadataResponse>(url, {
      // CHANGE FROM MAIN: 60 seconds timeout for metadata fetching
      // Metadata retrieval involves querying the database for enrichment data
      timeout: 60000,
    });

    return response.data;
  } catch (error: any) {
    throw error;
  }
};

export const getDocumentChunks = async (params: {
  object_id: string;
  knowledgeId: string;
}): Promise<GetDocumentChunksResponse> => {
  const { object_id, knowledgeId } = params;

  try {
    const url = buildApiUrl(API.GET_DOCUMENT_CHUNKS, {
      knowledge_id: knowledgeId,
      object_id: object_id,
    });

    const response = await api.get<GetDocumentChunksResponse>(url, {
      // CHANGE FROM MAIN: 60 seconds timeout for chunk fetching
      // Can return large amounts of data when fetching many chunks with metadata
      timeout: 60000,
    });

    return response.data;
  } catch (error: any) {
    throw error;
  }
};

export const getDocumentChunkMetadata = async (params: {
  chunk_id: string;
  knowledgeId: string;
}): Promise<GetDocumentChunkMetadataResponse> => {
  const { chunk_id, knowledgeId } = params;

  try {
    const url = buildApiUrl(API.GET_DOCUMENT_CHUNK_METADATA, {
      knowledge_id: knowledgeId,
      chunk_id: chunk_id,
    });

    const response = await api.get<GetDocumentChunkMetadataResponse>(url);
    return response.data;
  } catch (error: any) {
    throw error;
  }
};

export default api;
