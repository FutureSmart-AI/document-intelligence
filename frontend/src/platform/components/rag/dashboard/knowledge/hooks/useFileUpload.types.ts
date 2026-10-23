import type { DocumentProcessingMode } from "@/platform/services/upload";

export interface BatchUploadError {
  file_name: string;
  error_detail: string;
}

export interface UseFileUploadOptions {
  onSuccess?: () => void;
  onBatchErrors?: (errors: BatchUploadError[]) => void;
}

export type TranscriptProvider = "openai" | "groq";

export interface FileUploadState {
  uploadProgress: number;
  isUploading: boolean;
  /**
   * Sent as `document_processing_mode`; replaces the deprecated
   * `use_llama_parse` toggle this form used to show.
   */
  documentProcessingMode: DocumentProcessingMode;
  chunkSize: number;
  enableChunking: boolean;
  selectedFiles: globalThis.File[];
  transcriptProvider: TranscriptProvider;
}

export type FileUploadAction =
  | { type: "SET_UPLOAD_PROGRESS"; payload: number }
  | { type: "SET_IS_UPLOADING"; payload: boolean }
  | { type: "SET_DOCUMENT_PROCESSING_MODE"; payload: DocumentProcessingMode }
  | { type: "SET_TRANSCRIPT_PROVIDER"; payload: TranscriptProvider }
  | { type: "SET_CHUNK_SIZE"; payload: number }
  | { type: "SET_ENABLE_CHUNKING"; payload: boolean }
  | { type: "ADD_FILES"; payload: globalThis.File[] }
  | { type: "REMOVE_FILE"; payload: number }
  | { type: "CLEAR_FILES" }
  | { type: "RESET_UPLOAD_STATE" };
