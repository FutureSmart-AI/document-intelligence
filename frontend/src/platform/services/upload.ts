import { defaultTo, endsWith, get, join, lowerCase } from "lodash-es";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

interface UploadUrlResponse {
  presigned_url: string;
  download_presigned_url: string;
  knowledge_id: string;
}

interface UploadOptions {
  file: File;
  chunkSize: number;
  useLlamaParse: boolean;
  enableChunking: boolean;
  knowledgeId: string;
  onProgress?: (percent: number) => void;
  showMessage?: (
    type: "success" | "error" | "warning",
    content: string,
  ) => void;
}

const API = {
  GET_UPLOAD_URL: "/api/knowledge/v1/{knowledge_id}/upload-url",
  ADD_DOCUMENT: "/api/knowledge/v1/{knowledge_id}/documents",
  BATCH_UPLOAD_URLS: "/api/knowledge/v1/{knowledge_id}/upload-urls/batch",
  BATCH_PROCESS_DOCUMENTS: "/api/knowledge/v1/{knowledge_id}/documents/batch",
  BATCH_STATUS: "/api/knowledge/v1/{knowledge_id}/batch/{batch_id}/status",
} as const;

type BatchUploadRequestFile = { file_name: string; content_type: string };

interface BatchUploadUrlsResponse {
  batch_id: string;
  uploads: Array<{
    object_id: string;
    file_name: string;
    presigned_url: string;
    download_presigned_url: string;
  }>;
}

/**
 * What the uploaded documents' content is like — the knowledge service's own
 * `document_processing_mode` enum, sent verbatim: the string is passed on to the
 * worker over SQS, so these values are the cross-service contract rather than a
 * local vocabulary.
 *
 * It replaces `use_llama_parse`, which the service marks deprecated ("use
 * document_processing_mode"), which is why the batch call below no longer sends
 * the flag at all — the service keeps "not sent" distinguishable from an
 * explicit false, and sending false would pin the legacy behaviour.
 */
export type DocumentProcessingMode =
  "text_based" | "scanned" | "layout_aware" | "visual" | "mixed";

interface ProcessBatchOptions {
  knowledgeId: string;
  file_urls: string[];
  enableChunking?: boolean;
  documentProcessingMode?: DocumentProcessingMode;
  chunkSize?: number;
  contextualiseChunks?: boolean;
  transcriptProvider?: string;
  userId?: string;
}

export const getContentType = (file: File): string => {
  if (endsWith(lowerCase(file?.name), ".md")) {
    return "text/markdown";
  }
  return file.type || "application/octet-stream";
};

export const uploadService = {
  getPresignedUrl: async (
    fileName: string,
    contentType: string,
    knowledgeId: string,
  ): Promise<UploadUrlResponse> => {
    const url = buildApiUrl(API.GET_UPLOAD_URL, {
      knowledge_id: knowledgeId,
    });
    const formData = new FormData();
    formData.append("file_name", fileName);
    formData.append("content_type", contentType);
    const response = await axios.post(url, formData, {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    });
    return get(response, "data");
  },

  getBatchPresignedUrls: async (options: {
    knowledgeId: string;
    files: BatchUploadRequestFile[];
    userId?: string;
  }): Promise<BatchUploadUrlsResponse> => {
    const url = buildApiUrl(API.BATCH_UPLOAD_URLS, {
      knowledge_id: options.knowledgeId,
    });

    const response = await axios.post(url, {
      files: options.files,
      user_id: options.userId,
    });

    return get(response, "data");
  },

  processBatchDocuments: async ({
    knowledgeId,
    file_urls,
    enableChunking = true,
    documentProcessingMode,
    chunkSize = 2000,
    contextualiseChunks = false,
    transcriptProvider,
  }: ProcessBatchOptions) => {
    const url = buildApiUrl(API.BATCH_PROCESS_DOCUMENTS, {
      knowledge_id: knowledgeId,
    });

    const formData = new FormData();
    formData.append("file_urls", join(file_urls, ","));
    formData.append("enable_chunking", enableChunking.toString());
    formData.append("chunk_size", chunkSize.toString());
    formData.append("contextualise_chunks", contextualiseChunks.toString());
    // Both are strict enums on the service, so a field only goes out when there
    // is a real value for it — an absent one falls back to the service default.
    if (documentProcessingMode) {
      formData.append("document_processing_mode", documentProcessingMode);
    }
    if (transcriptProvider) {
      formData.append("transcript_provider", transcriptProvider);
    }

    return axios.post(url, formData, {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    });
  },

  getBatchStatus: async (knowledgeId: string, batchId: string) => {
    const url = buildApiUrl(API.BATCH_STATUS, {
      knowledge_id: knowledgeId,
      batch_id: batchId,
    });
    const response = await axios.get(url);
    return get(response, "data");
  },

  uploadToS3: async (
    presignedUrl: string,
    file: File,
    onProgress?: (percent: number) => void,
  ) => {
    // CHANGE: Set timeout to 0 (unlimited) for S3 uploads
    // Upload to S3 can take longer than the default API timeout (10s). Override
    // timeout for the presigned S3 PUT so large files don't trigger a client-side
    // timeout. Setting timeout: 0 disables the timeout in axios for this request.
    // Large files (up to 50MB) may take several minutes to upload on slower connections.
    await axios.put(presignedUrl, file, {
      timeout: 0,
      headers: {
        credentials: true,
        "Content-Type": getContentType(file),
      },
      onUploadProgress: (progressEvent) => {
        const percent = Math.round(
          (progressEvent.loaded * 100) / progressEvent.total,
        );
        onProgress?.(percent);
      },
    });
  },

  addKnowledge: async (options: {
    knowledgeId: string;
    fileUrl: string;
    chunkSize: number;
    useLlamaParse: boolean;
    enableChunking: boolean;
    showMessage?: (
      type: "success" | "error" | "warning",
      content: string,
    ) => void;
  }) => {
    const formData = new FormData();
    formData.append("knowledge_id", options.knowledgeId);
    formData.append("file_url", options.fileUrl);
    formData.append("scrape_using", "Newspaper");
    formData.append("transcript_provider", "openai");
    formData.append("chunk_size", options.chunkSize.toString());
    formData.append("use_llama_parse", options.useLlamaParse.toString());
    formData.append("enable_chunking", options.enableChunking.toString());

    const url = buildApiUrl(API.ADD_DOCUMENT, {
      knowledge_id: options.knowledgeId,
    });

    return axios
      .post(url, formData, {
        timeout: 120000,
        headers: {
          "Content-Type": "multipart/form-data",
        },
      })
      .then((res) => {
        options.showMessage?.(
          "success",
          defaultTo(get(res, "data.message"), "Knowledge added successfully"),
        );
        return get(res, "data");
      })
      .catch((err) => {
        options.showMessage?.(
          "error",
          defaultTo(
            get(err, "response.data.detail"),
            "Failed to add knowledge",
          ),
        );
        throw err;
      });
  },

  handleFileUpload: async (options: UploadOptions) => {
    const {
      file,
      chunkSize,
      useLlamaParse,
      enableChunking,
      knowledgeId,
      onProgress,
      showMessage,
    } = options;

    // Step 1: Get presigned URL with proper content type
    const contentType = getContentType(file);
    const urlResponse = await uploadService.getPresignedUrl(
      file.name,
      contentType,
      knowledgeId,
    );
    const presigned_url = get(urlResponse, "presigned_url");
    const download_presigned_url = get(urlResponse, "download_presigned_url");

    // Step 2: Upload to S3
    await uploadService.uploadToS3(presigned_url, file, onProgress);

    // Step 3: Add knowledge
    return uploadService.addKnowledge({
      knowledgeId,
      fileUrl: download_presigned_url,
      chunkSize,
      useLlamaParse,
      enableChunking,
      showMessage,
    });
  },
};
