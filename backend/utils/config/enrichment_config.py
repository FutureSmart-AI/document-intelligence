# Protected system fields that cannot be deleted
# These are critical fields required by the system
DEFAULT_ENRICHMENT_PROMPT = (
    "Extract metadata deterministically. Use only information explicitly present in the "
    "document. Do not infer missing facts. Return stable, normalized values that follow "
    "the field definitions exactly."
)

PROTECTED_SYSTEM_FIELDS = {
    "summary",
    "chunk_idx",
    "chunk_text",
    "document_type"
}
# `title` was dropped from this set 2026-09-10: every real consumer
# (chat/rag_agent/answer_node.py, chat/evidence_builder.py) already reads it
# as `.get("title") or .get("file_name")`, so an inactive/removed title
# degrades to showing the filename rather than breaking. It now behaves like
# any other optional default — not force-created by the wizard, and the
# field generator may propose it like any new field — and it can be deleted via
# DELETE /v1/{knowledge_id}/enrichment/{field_id}.

# Default field configurations
DEFAULT_FIELD_CONFIGURATIONS = [
    {
        "field_name": "title",
        "description": "Clear and concise title (max 10 words) that encapsulates the main theme of the document",
        "field_type": "str",
        "is_active": True,
        "is_custom": False, # if false, then it is a system field
        "is_required": True,
        # "is_system_locked": True,  # Cannot be disabled or removed
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": None,
        "level": "document"
    },
    {
        "field_name": "summary",
        "description": "2-3 sentence summary capturing the document's overall purpose, key arguments, and conclusions",
        "field_type": "str",
        "is_active": True,
        "is_custom": False, # if false, then it is a system field
        "is_required": True,
        #"is_system_locked": True,  # Cannot be disabled or removed
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": None,
        "level": "document"
    },
    {
        "field_name": "document_type",
        # This is the primary facet the RAG agent filters on: values must converge, not sprawl.
        # Lowercase + "reuse existing labels" keeps 'Report'/'report'/'Technical Report' from
        # becoming three facet values for the same genre.
        "field_type": "str",
        "description": "Generic document genre as a short, lowercase, reusable label (e.g., 'report', 'case study', 'blog', 'whitepaper', 'documentation', 'tutorial', 'resume', 'invoice', 'contract'). Reuse an existing label whenever one fits; introduce a new label only for a genuinely new genre.",
        "is_active": True,
        "is_custom": False,
        "is_required": True,
        #"is_system_locked": False,  # Can be modified or disabled
        "sample_values": ["report", "case study", "documentation", "resume", "invoice", "contract"],
        "restrict_to_choices": False,
        "max_items": None,
        "level": "document"
    },
    {
        "field_name": "topics",
        "description": "Primary high-level subjects or themes the document addresses (e.g., 'machine learning', 'supply chain', 'climate policy')",
        "field_type": "List[str]",
        "is_active": True,
        "is_custom": False,
        "is_required": False,  # optional: forcing it on corpora without such facts makes the LLM fabricate values
        #"is_system_locked": False,  # Can be modified or disabled
        "sample_values": ["machine learning", "supply chain management", "climate policy"],
        "restrict_to_choices": False,
        "max_items": 3,
        "level": "document"
    },
    {
        "field_name": "keywords",
        "description": "Specific terms or phrases most representative of the document's content (e.g., 'transformer architecture', 'net zero emissions')",
        "field_type": "List[str]",
        "is_active": True,
        "is_custom": False,
        "is_required": False,  # optional: forcing it on corpora without such facts makes the LLM fabricate values
        #"is_system_locked": False,  # Can be modified or disabled
        "sample_values": ["transformer architecture", "net zero emissions", "quarterly revenue"],
        "restrict_to_choices": False,
        "max_items": 5,
        "level": "document"
    },
    {
        "field_name": "entities",
        "description": "Named individuals and products explicitly mentioned in the document (e.g., 'Elon Musk', 'GPT-4'). Exclude organizations and companies — use the 'organizations' field for those.",
        "field_type": "List[str]",
        "is_active": True,
        "is_custom": False,
        "is_required": False,  # optional: forcing it on corpora without such facts makes the LLM fabricate values
        #"is_system_locked": False,  # Can be modified or disabled
        "sample_values": ["Elon Musk", "GPT-4", "iPhone 15"],
        "restrict_to_choices": False,
        "max_items": 5,
        "level": "document"
    },
    {
        "field_name": "organizations",
        "description": "Organizations, companies, institutions, or agencies explicitly mentioned in the document (e.g., 'OpenAI', 'NASA', 'World Bank'). Exclude individuals — use the 'entities' field for those.",
        "field_type": "List[str]",
        "is_active": True,
        "is_custom": False,
        "is_required": False,  # optional: forcing it on corpora without such facts makes the LLM fabricate values
        #"is_system_locked": False,  # Can be modified or disabled
        "sample_values": ["OpenAI", "NASA", "World Bank"],
        "restrict_to_choices": False,
        "max_items": 5,
        "level": "document"
    },
    {
        "field_name": "temporal_context",
        "description": "Time period or date the document covers, as a plain English phrase (e.g., 'Q3 2024', 'early 2020s', 'January to March 2023', 'undated')",
        "field_type": "str",
        "is_active": True,
        "is_custom": False,
        "is_required": False,  # optional: forcing it on corpora without such facts makes the LLM fabricate values
        #"is_system_locked": False,  # Can be modified or disabled
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": None,
        "level": "document"
    },
    {
        "field_name": "document_outline",
        "description": "Main section headings or structural parts of the document as short phrases (e.g., 'Introduction', 'Methodology', 'Results'). Max 8 items.",
        "field_type": "List[str]",
        "is_active": False,  # no retrieval-time consumer found (2026-08-25) — re-enable per-KB via PATCH /enrichment if a UI needs it
        "is_custom": False,
        "is_required": False,
        #"is_system_locked": False,  # Can be modified or disabled
        "sample_values": ["Introduction", "Methodology", "Key Findings", "Conclusion"],
        "restrict_to_choices": False,
        "max_items": 8,
        "level": "document"
    },
    # {
    #     "field_name": "prepended_doc_summary",
    #     "description": "Summary of the document",
    #     "field_type": "str",
    #     "is_active": True,
    #     "is_custom": False,
    #     "is_required": True,
    #     #"is_system_locked": False,  # Can be modified or disabled
    #     "sample_values": None,
    #     "restrict_to_choices": False,
    #     "max_items": None,
    #     "level": "document"
    # },

    # Chunk-level metadata fields 
    {
        "field_name": "chunk_idx",
        "description": "The order number of the chunk in the document. Start from 0.",
        "field_type": "int",
        "is_active": True,
        "is_custom": False,
        "is_required": True,
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": None,
        "level": "chunk"
    },
    {
        "field_name": "chunk_text",
        "description": "The complete text content of the document chunk",
        "field_type": "str",
        "is_active": True,
        "is_custom": False,
        "is_required": True,
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": None,
        "level": "chunk"
    },
    {
        "field_name": "chunk_keywords",
        "description": "Specific terms or phrases most representative of this chunk's content. Extract from this chunk only, not the full document (e.g., 'attention mechanism', 'loss function')",
        "field_type": "List[str]",
        "is_active": True,
        "is_custom": False,
        "is_required": True,
        "sample_values": ["attention mechanism", "loss function", "carbon tax"],
        "restrict_to_choices": False,
        "max_items": 5,
        "level": "chunk"
    },
    {
        "field_name": "chunk_topic",
        "description": "The single primary subject or theme addressed in this chunk (max 5 words). Extract from this chunk only, not the full document.",
        "field_type": "str",
        "is_active": True,
        "is_custom": False,
        "is_required": True,
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": None,
        "level": "chunk"
    },
    {
        "field_name": "chunk_entities",
        "description": "Named individuals and products explicitly mentioned in this chunk (e.g., 'Sam Altman', 'DALL-E 3'). Extract from this chunk only, not the full document. Exclude organizations.",
        "field_type": "List[str]",
        "is_active": True,
        "is_custom": False,
        "is_required": True,
        "sample_values": ["Sam Altman", "DALL-E 3", "Marie Curie"],
        "restrict_to_choices": False,
        "max_items": 5,
        "level": "chunk"
    }
]

# Domain field templates — curated presets seeded ON TOP of
# DEFAULT_FIELD_CONFIGURATIONS when a knowledge base is configured with a
# `field_template` (PUT /v1/{knowledge_id}/config). Keys match
# utils/models/enums.FieldTemplate values ("default" seeds nothing extra).
# Kept out of the global defaults on purpose: a required field the corpus
# can't answer forces the LLM to invent values, which pollutes the facet
# catalog the RAG agent builds metadata_filter from. The retrieval backend
# only supports `$in`/equality filtering, so fields here prefer
# low-cardinality str / List[str] over dates/numbers meant for ranges.
DOMAIN_FIELD_TEMPLATES = {
    "software_docs": [
        # Formerly part of DEFAULT_FIELD_CONFIGURATIONS; moved here 2026-08-25
        # because they are internal software/product-doc specific.
        {
            "field_name": "product_area",
            "description": "Low-cardinality faceted tag identifying the product, service, or domain area the document belongs to. Reusable across many documents (e.g., 'dashboard service', 'ingestion pipeline', 'billing platform', 'ml platform'). Used by the RAG agent as a primary filter. Must be a short noun phrase, lowercase, reusable — NOT a unique sentence-like description.",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": True,
            "sample_values": ["dashboard service", "ingestion pipeline", "ml platform"],
            "restrict_to_choices": False,
            "max_items": 2,
            "level": "document"
        },
        {
            "field_name": "capabilities",
            "description": "Low-to-mid-cardinality faceted tags for functional features or capabilities the document covers. Reusable across documents (e.g., 'widget management', 'api key management', 'session management', 'analytics', 'incident response'). Each tag must be a short noun phrase a sibling document could plausibly share. Avoid one-off descriptive phrases.",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": True,
            "sample_values": ["widget management", "session management", "analytics", "api key management"],
            "restrict_to_choices": False,
            "max_items": 5,
            "level": "document"
        },
        {
            "field_name": "artifact_type",
            "description": "Low-cardinality faceted tag for the technical shape of the document — what kind of artifact it is, independent of subject. Examples: 'rest api', 'runbook', 'design doc', 'architecture note', 'meeting note', 'config reference', 'sql schema'. Distinct from 'document_type' which captures the broad genre.",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": True,
            "sample_values": ["rest api", "runbook", "design doc", "meeting note"],
            "restrict_to_choices": False,
            "max_items": 2,
            "level": "document"
        },
        {
            "field_name": "audience",
            "description": "Intended primary audience of the document, as a single short label. Reusable across documents. Examples: 'developer', 'operator', 'end user', 'data scientist', 'admin'. Use null if the document is not audience-segmented.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["developer", "operator", "end user", "admin"],
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
    ],
    # Resume template design notes:
    # - Flat str/List[str] fields carry the facets (facet value_counts and
    #   restrict_to_choices only work there); List[dict] fields carry structure.
    # - field_schema is the flat {subfield: type} map the workers'
    #   create_nested_model_from_schema supports — ONE level deep, subfield
    #   types str/int/float/bool/List[str] only.
    # - Nested fields are filterable in chat via dot notation
    #   (work_experience.company → work_experience[].company NestedCondition),
    #   including numeric ranges (work_experience.start_year {"gte": 2020}).
    # - max_items is only enforced for List[str] (schema_builder truncator), so
    #   List[dict] caps live in the description text instead.
    "resume": [
        {
            "field_name": "candidate_name",
            "description": "Full name of the candidate as written on the resume.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": True,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "candidate_email",
            "description": "Candidate's email address exactly as written, lowercase. This is the unique identifier for the candidate — use null if absent, never invent one.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "work_experience",
            "description": "Every position in the employment history, most recent first, at most 10. One entry per role held (a promotion at the same company is a separate entry). end_year is null for a current role; summary is one sentence on what the person did there.",
            "field_type": "List[dict]",
            "field_schema": {
                "company": "str",
                "title": "str",
                "start_year": "int",
                "end_year": "int",
                "summary": "str"
            },
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,  # not enforced for List[dict] — the cap lives in the description
            "level": "document"
        },
        {
            "field_name": "education",
            "description": "Every formal education entry, highest/most recent first, at most 5. degree is the credential ('B.Tech', 'MBA'); field_of_study is the subject ('Computer Science').",
            "field_type": "List[dict]",
            "field_schema": {
                "institution": "str",
                "degree": "str",
                "field_of_study": "str",
                "graduation_year": "int"
            },
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,  # not enforced for List[dict] — the cap lives in the description
            "level": "document"
        },
        {
            "field_name": "current_role",
            "description": "The candidate's current or most recent job title, as a short reusable label (e.g., 'Backend Engineer', 'Product Manager'). Use the most recent position if none is marked current.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["Backend Engineer", "Product Manager", "Data Scientist"],
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "seniority_level",
            "description": "Career seniority inferred from titles and years of experience. Must be exactly one of the allowed values.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["intern", "junior", "mid_level", "senior", "lead", "principal", "executive"],
            "restrict_to_choices": True,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "years_of_experience",
            "description": (
                "Total years of paid professional work across the whole career, as a whole number, "
                "computed from the dated jobs in the work history. Start at the start year of the "
                "earliest job that is not an internship (jobs in an earlier career count) and end at "
                "the latest end year, where 'Present' or an open end means the reference date's year. "
                "Subtract the years of any break between jobs (study, a bootcamp, a career break). "
                "A figure the resume states for only part of the career (for example 'seven years of "
                "teaching') is not the total. Use 0 when the only work is internships."
            ),
            "field_type": "int",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "skills",
            "description": "Technical and professional skills explicitly listed or clearly demonstrated (e.g., 'python', 'kubernetes', 'financial modeling'). Lowercase, short reusable labels.",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": True,
            "sample_values": ["python", "kubernetes", "financial modeling"],
            "restrict_to_choices": False,
            "max_items": 10,
            "level": "document"
        },
        {
            "field_name": "education_level",
            "description": "Highest completed education level. Must be exactly one of the allowed values.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["high_school", "diploma", "bachelors", "masters", "phd", "other"],
            "restrict_to_choices": True,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "certifications",
            "description": "Professional certifications explicitly listed (e.g., 'AWS Solutions Architect', 'PMP'). Empty list if none.",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["AWS Solutions Architect", "PMP"],
            "restrict_to_choices": False,
            "max_items": 5,
            "level": "document"
        },
        {
            "field_name": "locations",
            "description": "Locations associated with the candidate — current city and any stated preferred/willing-to-relocate locations (e.g., 'Bengaluru', 'Remote').",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["Bengaluru", "Pune", "Remote"],
            "restrict_to_choices": False,
            "max_items": 3,
            "level": "document"
        },
        {
            "field_name": "industries",
            "description": "Industries the candidate has worked in, as short reusable labels (e.g., 'fintech', 'healthcare', 'e-commerce').",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["fintech", "healthcare", "e-commerce"],
            "restrict_to_choices": False,
            "max_items": 3,
            "level": "document"
        },
        {
            "field_name": "languages",
            "description": "Human languages the candidate lists (e.g., 'english', 'hindi', 'german'), lowercase. Not programming languages — those belong in skills.",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["english", "hindi", "german"],
            "restrict_to_choices": False,
            "max_items": 5,
            "level": "document"
        },
    ],
    "invoice_contract": [
        {
            "field_name": "counterparty_vendor",
            "description": "The issuing party — vendor, seller, supplier, or service provider named on the document.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "counterparty_client",
            "description": "The receiving party — client, buyer, or customer named on the document.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "document_number",
            "description": "Invoice number, contract number, or PO number exactly as printed (e.g., 'INV-2024-0042'). Use null if absent.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "total_amount",
            "description": "Total monetary value of the invoice or contract as a number, without currency symbol or thousands separators.",
            "field_type": "float",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "currency",
            "description": "ISO 4217 currency code of the amounts (e.g., 'USD', 'EUR', 'INR').",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["USD", "EUR", "INR"],
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "issue_date",
            "description": "Date the invoice was issued or the contract signed, formatted YYYY-MM-DD. Use 'undated' if not present.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "end_or_due_date",
            "description": "Payment due date (invoices) or expiry/end date (contracts), formatted YYYY-MM-DD. Use 'undated' if not present.",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "payment_terms",
            "description": "Stated payment terms as a short label (e.g., 'net 30', 'due on receipt', 'milestone-based').",
            "field_type": "str",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["net 30", "due on receipt", "milestone-based"],
            "restrict_to_choices": False,
            "max_items": None,
            "level": "document"
        },
        {
            "field_name": "line_item_categories",
            "description": "Categories of goods/services billed or contracted, as short reusable labels (e.g., 'consulting', 'software licenses', 'cloud infrastructure').",
            "field_type": "List[str]",
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": ["consulting", "software licenses", "cloud infrastructure"],
            "restrict_to_choices": False,
            "max_items": 5,
            "level": "document"
        },
        {
            "field_name": "line_items",
            "description": "The individual billed lines, in document order, at most 15. amount is the line total in the document's currency; quantity and unit_price are null when the document shows only a lump sum.",
            "field_type": "List[dict]",
            "field_schema": {
                "description": "str",
                "quantity": "float",
                "unit_price": "float",
                "amount": "float"
            },
            "is_active": True,
            "is_custom": False,
            "is_required": False,
            "sample_values": None,
            "restrict_to_choices": False,
            "max_items": None,  # not enforced for List[dict] — the cap lives in the description
            "level": "document"
        },
    ],
    # invoice / contract split — CC2/Q4 (docs/plan/collection-creation-flow). The
    # combined "invoice_contract" above is kept, unmodified, for existing callers;
    # these two are what new callers (the redesigned create-collection wizard) get.
    # Fields below are NOT copy-pasted from invoice_contract — an invoice's line
    # items and a contract's renewal terms are genuinely different shapes, which is
    # the actual argument for splitting rather than relabeling (see gap-analysis.md).
    "invoice": [
        {
            "field_name": "vendor_name",
            "description": "The issuing party — vendor, seller, or supplier named on the invoice.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "customer_name",
            "description": "The receiving party — client, buyer, or customer named on the invoice.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "invoice_number",
            "description": "Invoice number exactly as printed (e.g., 'INV-2024-0042'). Use null if absent.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "total_amount",
            "description": "Total invoiced amount as a number, without currency symbol or thousands separators.",
            "field_type": "float", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "currency",
            "description": "ISO 4217 currency code of the amounts (e.g., 'USD', 'EUR', 'INR').",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["USD", "EUR", "INR"], "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "issue_date",
            "description": "Date the invoice was issued, formatted YYYY-MM-DD.",
            "field_type": "date", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "due_date",
            "description": "Payment due date, formatted YYYY-MM-DD.",
            "field_type": "date", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "payment_terms",
            "description": "Stated payment terms as a short label (e.g., 'net 30', 'due on receipt').",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["net 30", "due on receipt", "milestone-based"], "restrict_to_choices": False,
            "max_items": None, "level": "document"
        },
        {
            "field_name": "line_item_categories",
            "description": "Categories of goods/services billed, as short reusable labels (e.g., 'consulting', 'software licenses').",
            "field_type": "List[str]", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["consulting", "software licenses", "cloud infrastructure"],
            "restrict_to_choices": False, "max_items": 5, "level": "document"
        },
        {
            "field_name": "line_items",
            "description": "The individual billed lines, in document order, at most 15. amount is the line total; quantity and unit_price are null when the invoice shows only a lump sum.",
            "field_type": "List[dict]",
            "field_schema": {"description": "str", "quantity": "float", "unit_price": "float", "amount": "float"},
            "is_active": True, "is_custom": False, "is_required": False, "sample_values": None,
            "restrict_to_choices": False, "max_items": None, "level": "document"
        },
    ],
    "contract": [
        {
            "field_name": "party_a",
            "description": "The first named party to the contract (often the service provider or disclosing party).",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "party_b",
            "description": "The second named party to the contract (often the client or receiving party).",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "contract_number",
            "description": "Contract, agreement, or reference number exactly as printed. Use null if absent.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "contract_type",
            "description": "The kind of agreement this document is. Must be exactly one of the allowed values.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["MSA", "NDA", "SOW", "employment", "lease", "licensing", "other"],
            "restrict_to_choices": True, "max_items": None, "level": "document"
        },
        {
            "field_name": "contract_value",
            "description": "Total monetary value of the contract as a number, if stated. Null for contracts with no fixed value (e.g. an NDA).",
            "field_type": "float", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "currency",
            "description": "ISO 4217 currency code of contract_value, when set.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["USD", "EUR", "INR"], "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "effective_date",
            "description": "Date the contract takes effect or was signed, formatted YYYY-MM-DD.",
            "field_type": "date", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "end_date",
            "description": "Contract expiry, term end, or renewal-decision date, formatted YYYY-MM-DD. Null for an agreement with no fixed end (e.g. a perpetual license).",
            "field_type": "date", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "auto_renews",
            "description": "Whether the contract renews automatically unless cancelled. Null if the document does not say.",
            "field_type": "bool", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "key_obligations",
            "description": "The main obligations or deliverables each party commits to, as short reusable phrases, at most 8 (e.g., 'monthly service uptime 99.9%', 'quarterly security audit').",
            "field_type": "List[str]", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": 8, "level": "document"
        },
    ],
    "support_ticket": [
        {
            "field_name": "customer_name",
            "description": "The customer or reporter's name, as stated in the ticket, email, or chat log.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "issue_category",
            "description": "Low-cardinality faceted tag for the type of problem reported (e.g., 'billing', 'login', 'bug', 'feature request', 'how-to'). Reusable across many tickets.",
            "field_type": "List[str]", "is_active": True, "is_custom": False, "is_required": True,
            "sample_values": ["billing", "login", "bug", "feature request", "how-to"],
            "restrict_to_choices": False, "max_items": 3, "level": "document"
        },
        {
            "field_name": "priority",
            "description": "Reported or inferred urgency. Must be exactly one of the allowed values.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["low", "medium", "high", "urgent"], "restrict_to_choices": True,
            "max_items": None, "level": "document"
        },
        {
            "field_name": "status",
            "description": "The ticket's resolution state as stated in the document. Must be exactly one of the allowed values.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["open", "in_progress", "resolved", "closed", "unknown"],
            "restrict_to_choices": True, "max_items": None, "level": "document"
        },
        {
            "field_name": "channel",
            "description": "How the ticket arrived. Must be exactly one of the allowed values, or 'unknown' if not stated.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["email", "chat", "phone", "portal", "social", "unknown"],
            "restrict_to_choices": True, "max_items": None, "level": "document"
        },
        {
            "field_name": "summary",
            "description": "One or two sentences summarizing the customer's issue, in your own words.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "first_reported_date",
            "description": "Date the issue was first reported, formatted YYYY-MM-DD. Use null if not stated.",
            "field_type": "date", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": None, "restrict_to_choices": False, "max_items": None, "level": "document"
        },
        {
            "field_name": "sentiment",
            "description": "The reporter's overall tone. Must be exactly one of the allowed values.",
            "field_type": "str", "is_active": True, "is_custom": False, "is_required": False,
            "sample_values": ["positive", "neutral", "frustrated", "angry"], "restrict_to_choices": True,
            "max_items": None, "level": "document"
        },
    ],
}

# Display metadata for the templates above, keyed the same way. Not every
# FieldTemplate enum member needs an entry with "visible": True — software_docs
# exists and is used internally but is deliberately not offered in the
# create-collection wizard's picker (CC1/1.4, docs/plan/collection-creation-flow).
# GET /v1/field-templates (routers/knowledge_config_new.py) serves this.
FIELD_TEMPLATE_METADATA = {
    "default": {
        "display_name": "Something else",
        "description": "No preset — describe what you want pulled out yourself.",
        "visible": True,
    },
    "resume": {
        "display_name": "Resumes / CVs",
        "description": "People applying for a role.",
        "visible": True,
        "default_collection_name": "Candidate Resumes",
        "default_service_description": "Resumes and CVs submitted by candidates applying for a role.",
        "default_extraction_instructions": (
            "Pull out each candidate's name and email, their current job title and seniority, "
            "total years of experience, and their highest level of education. List their skills, "
            "certifications, industries and locations, and the languages they speak. For work "
            "history, capture every past role — company, title, start and end year, and a "
            "one-line summary — and for education, every degree with institution, field of study "
            "and graduation year."
        ),
    },
    "invoice": {
        "display_name": "Invoices",
        "description": "Bills, receipts, purchase orders.",
        "visible": True,
        "default_collection_name": "Vendor Invoices",
        "default_service_description": "Invoices received from vendors and suppliers.",
        "default_extraction_instructions": (
            "Pull out the vendor and customer names, the invoice number, the total amount and "
            "currency, and the issue and due dates. Note the payment terms, and break out each "
            "line item with its description, quantity, unit price and amount."
        ),
    },
    "contract": {
        "display_name": "Contracts",
        "description": "Agreements, MSAs, NDAs.",
        "visible": True,
        "default_collection_name": "Contracts & Agreements",
        "default_service_description": "Signed contracts and agreements with vendors, clients or partners.",
        "default_extraction_instructions": (
            "Pull out both named parties, the contract number and type, its total value and "
            "currency if stated, and the effective and end dates. Note whether it renews "
            "automatically, and list each party's key obligations or deliverables."
        ),
    },
    "support_ticket": {
        "display_name": "Support tickets",
        "description": "Emails, chat logs, complaints.",
        "visible": True,
        "default_collection_name": "Support Tickets",
        "default_service_description": "Customer support tickets, emails and chat logs.",
        "default_extraction_instructions": (
            "Pull out the customer's name, the category and priority of the issue, its current "
            "status and how it came in. Summarize what they're reporting in your own words, note "
            "when it was first reported, and read their overall tone."
        ),
    },
    "invoice_contract": {
        "display_name": "Invoices & contracts (legacy combined)",
        "description": "Superseded by the separate 'invoice' and 'contract' templates above. Kept for existing callers; not offered as a new choice.",
        "visible": False,
    },
    "software_docs": {
        "display_name": "Software / product docs",
        "description": "Internal engineering documentation.",
        "visible": False,
    },
}


def get_field_template_catalog(include_hidden: bool = False) -> list:
    """The field-template picker's data, for GET /v1/field-templates.

    Not knowledge-scoped — identical for everyone — mirrors
    chat/model_catalog.py::get_chat_model_catalog in that respect (CC1,
    docs/plan/collection-creation-flow/gap-analysis.md). "default" is
    included with an empty field list: it seeds nothing extra on top of
    DEFAULT_FIELD_CONFIGURATIONS, which is the whole point of that key.

    Returns a list of dicts:
        {key, display_name, description, visible, default_collection_name,
         default_service_description, default_extraction_instructions,
         fields: [{field_name, description, field_type, field_type_label,
         schema?}, ...]}

    ``default_collection_name``/``default_service_description``/
    ``default_extraction_instructions`` are None for a template that doesn't
    define them (``default`` has no fields to summarize, so none are set) —
    written by hand per template, not mechanically derived from the fields'
    own ``description`` text (that read as a wall of raw field descriptions,
    not something a person would type). ``schema`` is present only on a field
    whose ``field_type`` is ``dict``/``List[dict]`` — same convention as
    ``POST /v1/enrichment/generate``'s response, which omits the key rather
    than sending it ``null`` for a field that isn't nested.
    """
    from utils.models.enums import label_for_field_type

    catalog = []
    for key, meta in FIELD_TEMPLATE_METADATA.items():
        if not include_hidden and not meta.get("visible", False):
            continue
        raw_fields = DOMAIN_FIELD_TEMPLATES.get(key, [])
        fields = []
        for f in raw_fields:
            field_entry = {
                "field_name": f["field_name"],
                "description": f["description"],
                "field_type": f["field_type"],
                "field_type_label": label_for_field_type(f["field_type"]),
            }
            if f.get("field_schema"):
                field_entry["schema"] = f["field_schema"]
            fields.append(field_entry)
        catalog.append({
            "key": key,
            "display_name": meta["display_name"],
            "description": meta["description"],
            "visible": meta.get("visible", False),
            "default_collection_name": meta.get("default_collection_name"),
            "default_service_description": meta.get("default_service_description"),
            "default_extraction_instructions": meta.get("default_extraction_instructions"),
            "fields": fields,
        })
    return catalog
