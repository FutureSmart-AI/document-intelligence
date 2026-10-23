export interface SourceChunk {
  chunk_id?: string;
  topic?: string;
  text?: string;
  score?: number;
  position?: { index: number; total: number };
  metadata?: Record<string, unknown>;
}

export interface Source {
  file_name: string;
  context: string;
  document_id?: string;
  source_type?: string;
  retrieval_mode?: string;
  summary?: string;
  metadata?: Record<string, unknown>;
  chunks?: SourceChunk[];
}

export interface DebugInfo {
  sources?: Source[];
  user_info_used?: boolean | null;
}
