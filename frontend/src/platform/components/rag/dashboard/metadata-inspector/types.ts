export interface MetadataSectionConfig {
  title: string;
  description?: string;
  emptyMessage?: string;
  loadingMessage?: string;
  rawJsonLabel?: string;
}

export interface ChunkSummary {
  id: string;
  text?: string;
  [key: string]: any;
}

export interface ChunkDetailPayload {
  metadata: Record<string, any>;
  raw: any;
}

export interface MetadataInspectorProps {
  open: boolean;
  onClose: () => void;
  targetId: string | null;
  dialogTitle?: string;
  fetchDocumentMetadata?: (id: string) => Promise<Record<string, any>>;
  fetchChunkList?: (id: string) => Promise<ChunkSummary[]>;
  fetchChunkMetadata?: (chunkId: string) => Promise<any>;
  documentConfig?: MetadataSectionConfig;
  chunkConfig?: MetadataSectionConfig;
}
