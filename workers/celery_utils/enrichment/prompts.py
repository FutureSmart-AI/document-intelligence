from typing import Dict, List, Optional
from services.pydantic_models import PromptType
from celery_utils.db.prompt_db_utils import get_admin_prompts_sync
from services import celery_setup_env

def create_enrichment_prompt(
    existing_metadata=None,
    include_chunks=True,
    knowledge_prompt: Optional[str] = None,
    include_deterministic_chunks: bool = False,
    existing_field_values: Optional[Dict[str, List]] = None,
):
    """
    Create enrichment prompt by combining base admin prompt with optional knowledge-specific prompt.

    Args:
        existing_metadata: Existing metadata context (currently unused)
        include_chunks: Whether to include chunk-level extraction (Strategy 1 path)
        knowledge_prompt: Optional custom prompt from KnowledgeConfig to append to base prompt
        include_deterministic_chunks: When True, fetches the Strategy 2 deterministic pre-split prompt instead
        existing_field_values: Dict of field_name → [values] fetched from previously indexed documents.
                               When provided and non-empty, appends a Consistency Guidelines section.

    Returns:
        Combined enrichment prompt string
    """
    if include_deterministic_chunks:
        # LOCAL TESTING OVERRIDE — bypass DB for deterministic prompt only.
        # Paste prompt between the triple quotes. Set to "" to fall back to DB.
        DETERMINISTIC_PROMPT_OVERRIDE = """You are an expert in document processing and metadata extraction.

            ## PURPOSE
            The metadata you produce is consumed by a RAG agent that filters documents BEFORE retrieval using these fields as a decision tree. Generic-but-true values ("api", "documentation", "information") are FAILURES — they collapse the filter and force the agent to guess. Hyper-unique values (a full sentence describing one document) are also failures — nothing else in the knowledge base will share them, so they cannot be used as a filter. Your job is to produce reusable, low-cardinality faceted tags that partition the knowledge base cleanly, plus a high-cardinality keywords layer for precision.

            ## INPUT
            The document has already been split into chunks. Each chunk is supplied as a JSON array:
            [{"chunk_idx": 0, "chunk_text": "..."}, {"chunk_idx": 1, "chunk_text": "..."}, ...]

            ## TASK
            1. Extract document-level metadata fields from the full set of chunks.
            2. For EACH chunk, extract chunk-level metadata only. Do NOT rewrite, merge, split, or reorder chunks.
            3. Return one chunks_metadata entry per input chunk, each containing chunk_idx and the requested chunk fields.

            Before filling any fields, perform a HIGH-SIGNAL NOUN scan: internally list the proper product/service names, API paths, table names, env var names, feature flags, metric names, and vendor/platform names that appear in the document. These feed `keywords`, `entities`, and `organizations`.

            ## FACETED FIELDS
            The new faceted fields form a decision tree the RAG agent uses to filter:
            - Product/service/domain facet (List[str], 1–2 items): low-cardinality reusable tags identifying the product, service, or domain area (e.g., "dashboard service", "ingestion pipeline", "ml platform").
            - Functional feature facet (List[str], 2–5 items): reusable feature tags (e.g., "widget management", "session management", "analytics", "api key management").
            - Technical-shape facet (List[str], 1–2 items): the technical shape of the document, independent of subject (e.g., "rest api", "runbook", "design doc", "meeting note", "architecture note", "config reference").
            - Intended-reader facet (str or null): single short label for the intended reader (e.g., "developer", "operator", "end user", "admin"). Null if the document is not reader-segmented.

            Uniqueness comes from the COMBINATION of facet values, NOT from any single tag being unique. Each tag in isolation must be reusable.

            Field relationships:
            - document_type = broad genre (Report, Documentation, Tutorial, Blog).
            - The technical-shape facet = technical shape (rest api, runbook, design doc).
            - topics = subject themes the document is about.
            - keywords = high-cardinality, specific, possibly-unique identifiers.

            ## REUSABILITY RULE
            If a value in any reusable facet, document_type, or topics is so specific that fewer than 3 sibling documents in this knowledge base could plausibly share it, it is too specific. Break it into smaller faceted tags or replace with a more general reusable term.

            ## EXAMPLES
            - Domain facet BAD: "FutureSmart dashboard widget customization API". GOOD: "dashboard service".
            - Feature facet BAD: "API endpoints", "documentation". GOOD: "widget management", "session management".
            - Technical-shape facet BAD: "API documentation for the dashboard". GOOD: "rest api".

            ## DOCUMENT-LEVEL RULES
            - The document may be of any type or industry (report, case study, blog post, technical document, policy, runbook, architecture note, meeting note, etc.).
            - Extract only information explicitly supported by the document content.
            - Respect all field restrictions, allowed values, formatting examples, and maxItems limits.
            - Keep outputs concise, specific, and useful for search and retrieval.
            - Normalize casing and formatting consistently within the output.
            - Deduplicate near-duplicate values. When a list field has overlapping values, prefer the more specific one for keywords/entities and the more reusable one for facets.
            - Preserve proper nouns, API paths, service names, table names, file names, and product names exactly as they appear in the document.
            - Do not invent relationships, ownership, roles, dates, or associations not directly stated or strongly evidenced.
            - Do not output generic filler values such as "information", "data", "primary", "general", "miscellaneous", or similar low-signal tags.
            - Do not mix categories (e.g., do not place technologies in organization-style fields unless explicitly identified as such).
            - If a document-level field is not derivable from the content, return an appropriate default or null.

            keywords is the high-cardinality precision layer. It SHOULD include specific identifiers — API paths, feature flag names, env var names, metric names, table names, class names, CLI commands. These CAN be unique to one document. That is their job.

            PROPER-NOUN INCLUSION RULE: if the document names a specific vendor, platform, or product (AWS, S3, Cognito, OpenAI, Postgres, Stripe, Datadog, etc.), it MUST appear in `organizations` or `entities` as appropriate. Do not return [] for `organizations` on docs that mention them.

            GENRE-TOKEN CAP: do not repeat genre words ("documentation", "guide", "reference", "api", "manual", "overview") inside `topics` or the functional feature facet. Those slots are for subject matter and functional features, not genre labels.

            ## CHUNK-LEVEL RULES
            - chunk_text is NOT an output field. Never include it in your response.
            - The number of entries in chunks_metadata MUST equal the number of input chunks.
            - Each entry must include chunk_idx matching the input.
            - Extract only what is explicitly supported by the chunk content.
            - If a field is not present in a chunk, return an appropriate default or null.

            ## SELF-CHECK
            Before returning, internally simulate 3 realistic user queries that should retrieve this document. For each query, verify the document would be selected via filtering on the domain facet, feature facet, technical-shape facet, document_type, or topics. If any query would not match, revise the values to be more reusable or add a missing facet value.
"""
        if DETERMINISTIC_PROMPT_OVERRIDE.strip():
            prompts = [{"prompt_content": DETERMINISTIC_PROMPT_OVERRIDE}]
        else:
            prompts = get_admin_prompts_sync(prompt_type=PromptType.enrichment_with_deterministic_chunks)
    elif include_chunks is True:
        prompts = get_admin_prompts_sync(prompt_type=PromptType.enrichment_with_chunks)
    else:
        prompts = get_admin_prompts_sync(prompt_type=PromptType.enrichment_without_chunks)

    if not prompts:
        raise ValueError("No enrichment prompt found in database")

    enrichment_prompt = prompts[0]["prompt_content"]

    if knowledge_prompt:
        enrichment_prompt = f"{enrichment_prompt}\n\nAdditional Instructions:\n{knowledge_prompt}"

    # Append consistency guidelines from previously indexed documents
    _FIELD_CHAR_CAP = 5000  # ~1,250 tokens per field; interim cap pending dynamic budget
    if existing_field_values:
        guidelines_lines = []
        for field_name, values in existing_field_values.items():
            if not values:
                continue
            joined = ", ".join(str(v) for v in values)
            if len(joined) > _FIELD_CHAR_CAP:
                # Count how many values fit within the cap
                budget = 0
                kept = 0
                for v in values:
                    segment = (", " if kept else "") + str(v)
                    if budget + len(segment) > _FIELD_CHAR_CAP:
                        break
                    budget += len(segment)
                    kept += 1
                remaining = len(values) - kept
                joined = ", ".join(str(v) for v in values[:kept]) + f"… (+{remaining} more)"
            guidelines_lines.append(f"- {field_name}: {joined}")
        if guidelines_lines:
            consistency_section = (
                "\n\n## Consistency Guidelines\n"
                "Previously extracted values for this knowledge base:\n"
                + "\n".join(guidelines_lines)
                + "\nPrefer matching existing values over introducing new ones to ensure consistency across documents."
            )
            enrichment_prompt = f"{enrichment_prompt}{consistency_section}"

    return enrichment_prompt

