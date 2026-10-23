from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum

# Worker-specific models - no external dependencies

class ChunkMetadata(BaseModel):
    chunk_text: str = Field(description="The complete text content of the document chunk")
    chunk_keywords: Optional[List[str]] = Field(None, description="List of significant keywords specific to this chunk's content")
    chunk_topic: Optional[str] = Field(None, description="The primary subject or theme addressed in this specific chunk")
    chunk_entities: Optional[List[str]] = Field(None, description="Important named entities (people, products, etc.) mentioned in this chunk")

class ChunkText(BaseModel):
    chunk_text: str = Field(description="The complete text content of the document chunk")

class ChunkWithBreakpoint(BaseModel):
    chunk_idx: int = Field(description="The index of the chunk in the original document")
    start_idx: int = Field(description="The starting character index of the chunk in the original document")
    end_idx: int = Field(description="The ending character index of the chunk in the original document")


# class DocumentMetadata(BaseModel):
#     title: str = Field(description="Clear and concise title that encapsulates the main theme of the document")
#     summary: str = Field(description="Brief summary capturing the document's overall purpose and key details")
#     document_type: str = Field(description="Generic document category (e.g., 'Report', 'Case Study', 'Blog')")
#     document_type_granular: str = Field(description="More specific document category (e.g., 'About Us Page', 'Legal Case Study', 'Technical Documentation')")
#     topic: str = Field(description="Primary subject or theme that the document addresses")
#     keywords: List[str] = Field(description="Significant keywords representative of the document's content")
#     entities: List[str] = Field(description="Key entities such as names of people, products, or other significant items")
#     organizations: List[str] = Field(description="Key organizations mentioned in the document")
#     temporal_context: str = Field(description="Dates or periods mentioned that provide temporal reference")
#     document_outline: List[str] = Field(description="List of headings or section titles forming the document structure")
#     prepended_doc_summary: str = Field(description="Global summary prepended to every chunk for maintaining context")
#     chunks: List[ChunkMetadata] = Field(description="List of document chunks with their associated metadata")

# class DocumentMetadataWithoutChunks(BaseModel):
#     title: str = Field(description="Clear and concise title that encapsulates the main theme of the document")
#     summary: str = Field(description="Brief summary capturing the document's overall purpose and key details")
#     document_type: str = Field(description="Generic document category (e.g., 'Report', 'Case Study', 'Blog')")
#     document_type_granular: str = Field(description="More specific document category (e.g., 'About Us Page', 'Legal Case Study', 'Technical Documentation')")
#     topic: str = Field(description="Primary subject or theme that the document addresses")
#     keywords: List[str] = Field(description="Significant keywords representative of the document's content")
#     entities: List[str] = Field(description="Key entities such as names of people, products, or other significant items")
#     organizations: List[str] = Field(description="Key organizations mentioned in the document")
#     temporal_context: str = Field(description="Dates or periods mentioned that provide temporal reference")
#     document_outline: List[str] = Field(description="List of headings or section titles forming the document structure")
#     prepended_doc_summary: str = Field(description="Global summary prepended to every chunk for maintaining context")

class Breakpoint(BaseModel):
    start: int = Field(description="The starting character index of the chunk")
    end: int = Field(description="The ending character index of the chunk")

class UserPromptType(str, Enum):
    supervisor = "supervisor"
    langgraph_rephrase_answer = "langgraph_rephrase_answer"
    enrichment = "enrichment"
    extract_param = "extract_param"
    follow_up = "follow_up",
    refine_query = "refine_query",
    vanilla_rag_prompt = "vanilla_rag_prompt",
    contextualize_chunk = "contextualize_chunk"

class PromptType(str, Enum):
    supervisor = "supervisor"
    langgraph_rephrase_answer = "langgraph_rephrase_answer"
    enrichment_with_chunks = "enrichment_with_chunks"
    enrichment_without_chunks = "enrichment_without_chunks"
    enrichment_with_deterministic_chunks = "enrichment_with_deterministic_chunks"
    extract_param = "extract_param"
    follow_up = "follow_up",
    refine_query = "refine_query",
    vanilla_rag_prompt_restrict_knowledge = "vanilla_rag_prompt_restrict_knowledge"
    vanilla_rag_prompt_no_restrict_knowledge = "vanilla_rag_prompt_no_restrict_knowledge",
    contextualize_chunk = "contextualize_chunk"
    
# class DocumentMetadataWithBreakpoints(BaseModel):
#     # All fields from DocumentMetadata EXCEPT 'chunks'
#     title: str = Field(description="Clear and concise title...")
#     summary: str = Field(description="Brief summary...")
#     document_type: str = Field(description="Generic document category...")
#     document_type_granular: str = Field(description="More specific document category...")
#     topic: str = Field(description="Primary subject or theme...")
#     keywords: List[str] = Field(description="Significant keywords...")
#     entities: List[str] = Field(description="Key entities...")
#     organizations: List[str] = Field(description="Key organizations...")
#     temporal_context: str = Field(description="Dates or periods...")
#     document_outline: List[str] = Field(description="List of headings...")
#     prepended_doc_summary: str = Field(description="Global summary...")
#     # New field for breakpoints
#     chunk_breakpoints: List[Breakpoint] = Field(description="List of character start/end breakpoints for each chunk")

