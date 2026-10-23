from fastapi import Depends, HTTPException, APIRouter
from pydantic import BaseModel, Field
from typing import Dict, Optional
import traceback

from utils.auth.dependencies import get_api_key_or_session, require_permission
from utils.logger import logger
# from utils.db.knowledge_ops import (
#     create_knowledge_service,
#     get_knowledge_services_by_owner,
#     get_knowledge_service
# )
# from utils.common_utils import generate_object_id as generate_knowledge_id
# from utils.pydantic_models_knowledge import (
#     GenerateKnowledgeRequest, GenerateKnowledgeResponse
# )
from utils.pydantic_models_knowledge_settings import (
    KnowledgeConfigResponse,
    GetKnowledgeConfigResponse,
    ChunkingStrategyConfig,
    EmbeddingModel,
    FieldTemplate
)
from utils.config import knowledge_defaults
from utils.db.knowledge_setting_ops import (
    initialize_field_configurations_by_template,
    save_knowledge_config,
    get_knowledge_config,
    get_enrichment_default_config,
)
from chat.model_catalog import get_chat_model_catalog
from utils.config.enrichment_config import get_field_template_catalog
from utils.models.enums import get_field_type_catalog


class ChatModelEntry(BaseModel):
    """One selectable model. An object rather than a bare string so per-model
    tags (recommended / faster / better reasoning — G8) can be added later
    without breaking callers already reading model_name."""
    model_name: str


class ChatProviderEntry(BaseModel):
    provider_name: str
    models: list[ChatModelEntry]
    default_model: str


class ChatModelCatalogResponse(BaseModel):
    providers: list[ChatProviderEntry]


class FieldTemplateFieldEntry(BaseModel):
    field_name: str
    description: str
    field_type: str
    field_type_label: str
    # Present only on a dict/List[dict] field — the sub-field name -> type map
    # a "Table"/"Group of fields" field needs to render its columns without
    # asking the user to redefine them from scratch. Same convention as
    # POST /v1/enrichment/generate's response: omitted, not null, when the
    # field isn't nested. Named field_schema, not schema, because "schema" is
    # a reserved BaseModel attribute in Pydantic v2 (its own .schema()
    # classmethod) — aliased so the wire key is still "schema", matching
    # get_field_template_catalog()'s dict and field_generation.py's response.
    field_schema: Optional[Dict[str, str]] = Field(default=None, alias="schema")


class FieldTemplateEntry(BaseModel):
    key: str
    display_name: str
    description: str
    visible: bool
    # Hand-written per template (None for "default", which has no fields to
    # summarize) — Step 1's starting collection_name/service_description/
    # extraction_instructions, so picking a template pre-fills those the same
    # way the mock does, without mechanically dumping every field's own
    # description into the instructions box.
    default_collection_name: Optional[str] = None
    default_service_description: Optional[str] = None
    default_extraction_instructions: Optional[str] = None
    fields: list[FieldTemplateFieldEntry]


class FieldTemplateCatalogResponse(BaseModel):
    templates: list[FieldTemplateEntry]


class FieldTypeEntry(BaseModel):
    field_type: str
    field_type_label: str
    # True only for "dict" / "List[dict]" — the two types that take a
    # caller-defined field_schema (sub-fields with their own types). Lets the
    # wizard's type dropdown know when to show the schema builder without
    # hardcoding that pair on the frontend. See
    # utils.models.enums.SCHEMA_BEARING_FIELD_TYPES for where this is derived.
    requires_schema: bool


class FieldTypeCatalogResponse(BaseModel):
    field_types: list[FieldTypeEntry]



router = APIRouter()


# New API request model for PUT /knowledge/{knowledge_id}/config
class UpdateKnowledgeConfigBody(BaseModel):
    embedding_model: Optional[EmbeddingModel] = None
    collection_name: Optional[str] = None
    service_description: Optional[str] = None
    enrichment_provider: Optional[str] = None
    enrichment_model: Optional[str] = None
    enrichment_temperature: Optional[float] = None
    enrichment_prompt: Optional[str] = None
    enrichment_enabled: Optional[bool] = None
    # Everything below this line is backend-defaulted: the dashboard sends none of it, and the
    # backend resolves a platform default when it is omitted. They stay *accepted* so API-level
    # experimentation is still possible — same posture as embedding_model and the enrichment_*
    # fields above, not the "removed from the contract" posture an earlier revision used.
    #
    # chunking_strategy's default is derived from enrichment_enabled rather than being a fixed
    # constant. It used to be independent, which let a caller ask for enrichment_enabled=True with
    # both metadata levels off — "extraction is on" with nothing configured to extract. Sending it
    # explicitly still wins, so the inconsistency is only ruled out for callers (like the UI) that
    # leave it alone.
    chunking_strategy: Optional[ChunkingStrategyConfig] = None
    # Domain preset seeded on top of the default fields (see DOMAIN_FIELD_TEMPLATES).
    # Additive and idempotent: omitting it on a later PUT never removes fields.
    field_template: Optional[FieldTemplate] = None
    # False for the create-collection wizard (CC1): only the still-protected document defaults
    # (summary, document_type) get force-created here; title/topics/keywords/entities/
    # organizations/temporal_context/document_outline are left for Step 4's own field list, built
    # from Step 1's proposed_fields (Step 1 no longer returns default-field verdicts,
    # 2026-09-22). Omitted (None) or True keeps every prior caller's
    # behavior — all universal defaults are seeded as before.
    seed_optional_defaults: Optional[bool] = None
    use_deterministic_chunking: Optional[bool] = None
    # Defaults to knowledge_defaults.HYBRID_SEARCH_ENABLED. Note the default here is None, not True:
    # a literal True would be indistinguishable from a caller explicitly asking for hybrid search,
    # so the platform default could never be changed in one place.
    hybrid_search_enabled: Optional[bool] = None

# New APIs (keep old endpoints as-is)

@router.get("/v1/{knowledge_id}/config", response_model=GetKnowledgeConfigResponse)
async def get_knowledge_configuration_v2(
    knowledge_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        if not knowledge_id:
            raise HTTPException(status_code=400, detail="knowledge_id must be provided")

        config_data = await get_knowledge_config(knowledge_id)

        if not config_data:
            logger.info(f"Knowledge {knowledge_id} does not have a knowledge_config row")
            raise HTTPException(status_code=404, detail="Configuration not found for the provided knowledge_id")

        # Still 404s on an uninitialized row. `PUT /config` is the single place a collection is
        # set up, so "no embedding model yet" genuinely means "this collection has not been
        # configured" — reporting invented defaults here would describe a state that does not exist
        # in the database and that ingestion will refuse to run against.
        embedding_model = config_data.get("embedding_model")
        if embedding_model is None:
            logger.info(f"Knowledge {knowledge_id} exists but config is not initialized: embedding_model is null")
            raise HTTPException(
                status_code=404,
                detail="Knowledge config exists but is not initialized. Set it first with PUT /api/knowledge/v1/{knowledge_id}/config."
            )

        hybrid_search_enabled = config_data.get("hybrid_search_enabled")
        if hybrid_search_enabled is None:
            hybrid_search_enabled = knowledge_defaults.HYBRID_SEARCH_ENABLED

        # `.get(key, fallback)` is not enough here: these keys are present and hold None on a row
        # written before these columns existed, so the fallback never fires and the response would
        # claim document_level_metadata=None — which the worker reads as "off" and silently
        # downgrades to its weakest enrichment strategy.
        document_level_metadata = config_data.get("document_level_metadata")
        if document_level_metadata is None:
            document_level_metadata = knowledge_defaults.DOCUMENT_LEVEL_METADATA

        chunk_level_metadata = config_data.get("chunk_level_metadata")
        if chunk_level_metadata is None:
            chunk_level_metadata = knowledge_defaults.CHUNK_LEVEL_METADATA

        chunking_strategy = ChunkingStrategyConfig(
            document_level_metadata=document_level_metadata,
            chunk_level_metadata=chunk_level_metadata,
        )

        # Resolve enrichment fields: per-knowledge → global default → nothing
        per_knowledge_model = config_data.get("enrichment_model")
        per_knowledge_provider = config_data.get("enrichment_provider")
        per_knowledge_temperature = config_data.get("enrichment_temperature")

        if per_knowledge_model:
            # Per-knowledge override is set — use it as-is
            enrichment_model = per_knowledge_model
            enrichment_provider = per_knowledge_provider
            enrichment_temperature = per_knowledge_temperature
            enrichment_source = "knowledge"
        else:
            # Fall back to global default
            global_config = await get_enrichment_default_config()
            if global_config:
                enrichment_model = global_config.get("enrichment_model")
                enrichment_provider = global_config.get("enrichment_provider")
                enrichment_temperature = global_config.get("enrichment_temperature")
                enrichment_source = "global"
            else:
                enrichment_model = None
                enrichment_provider = None
                enrichment_temperature = None
                enrichment_source = "default"

        return GetKnowledgeConfigResponse(
            knowledge_id=knowledge_id,
            embedding_model=embedding_model,
            hybrid_search_enabled=hybrid_search_enabled,
            chunking_strategy=chunking_strategy,
            collection_name=config_data.get("collection_name"),
            service_description=config_data.get("service_description"),
            enrichment_provider=enrichment_provider,
            enrichment_model=enrichment_model,
            enrichment_temperature=enrichment_temperature,
            enrichment_prompt=config_data.get("enrichment_prompt"),
            enrichment_source=enrichment_source,
            enrichment_enabled=config_data.get("enrichment_enabled", True),
            use_deterministic_chunking=config_data.get("use_deterministic_chunking")
            if config_data.get("use_deterministic_chunking") is not None
            else knowledge_defaults.USE_DETERMINISTIC_CHUNKING,
            created_at=config_data.get("created_at"),
            updated_at=config_data.get("updated_at"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving knowledge configuration: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to retrieve configuration")


@router.put("/v1/{knowledge_id}/config", response_model=KnowledgeConfigResponse)
async def update_knowledge_configuration_v2(
    knowledge_id: str,
    body: UpdateKnowledgeConfigBody,
    _: Optional[str] = Depends(require_permission("write")),
):
    try:
        if not knowledge_id:
            raise HTTPException(status_code=400, detail="knowledge_id is required")

        # Fetched at most once and reused below — both the enrichment_enabled resolution and the
        # embedding_model resolution need "whatever is already stored", not two separate reads.
        existing = None
        existing_fetched = False

        async def _existing():
            nonlocal existing, existing_fetched
            if not existing_fetched:
                existing = await get_knowledge_config(knowledge_id)
                existing_fetched = True
            return existing

        # The metadata levels are DERIVED from enrichment_enabled when the caller omits
        # chunking_strategy, which is what the dashboard does: "extraction off" must mean nothing is
        # configured to extract, and "extraction on" gets the platform's own metadata-level defaults.
        # A caller that does send chunking_strategy still wins — the field stays open for API-level
        # experimentation, so this rules the mismatch out for the UI without freezing the contract.
        if body.chunking_strategy:
            document_level_metadata = body.chunking_strategy.document_level_metadata
            chunk_level_metadata = body.chunking_strategy.chunk_level_metadata
        else:
            # Resolve the effective enrichment_enabled the same way as any other omitted field —
            # caller value, else whatever is already stored, else the platform default (True).
            effective_enrichment_enabled = body.enrichment_enabled
            if effective_enrichment_enabled is None:
                effective_enrichment_enabled = ((await _existing()) or {}).get("enrichment_enabled")
            if effective_enrichment_enabled is None:
                effective_enrichment_enabled = True

            if effective_enrichment_enabled:
                document_level_metadata = knowledge_defaults.DOCUMENT_LEVEL_METADATA
                chunk_level_metadata = knowledge_defaults.CHUNK_LEVEL_METADATA
            else:
                document_level_metadata = False
                chunk_level_metadata = False

        # This endpoint is the ONE place a collection gets initialized, deliberately. An earlier
        # revision also initialized lazily during ingestion, which meant the same defaulting logic
        # lived in two places and no single call could be pointed at as "where the collection was
        # set up". Keeping it here means the sequence is always POST /v1 -> PUT /config, and a
        # collection that skipped the PUT fails loudly at upload instead of quietly configuring
        # itself.
        #
        # `embedding_model` is what the UI is not expected to choose, so it is filled in here when
        # omitted. Only when nothing is stored yet: the column is write-once, so passing a default
        # over an existing value would trip save_knowledge_config's own 400.
        embedding_model = body.embedding_model
        if embedding_model is None:
            existing = await _existing()
            if not existing or existing.get("embedding_model") is None:
                embedding_model = knowledge_defaults.EMBEDDING_MODEL
                logger.info(
                    f"No embedding_model supplied for knowledge {knowledge_id}; "
                    f"applying platform default {embedding_model}"
                )

        # hybrid_search_enabled and use_deterministic_chunking follow the same three-step shape:
        # caller value wins, else keep whatever is already stored (so a later PUT that omits the
        # field is not a silent reset), else fall back to the platform default. Both are omitted by
        # the dashboard, so in practice the default is what applies.
        hybrid_search_enabled = body.hybrid_search_enabled
        if hybrid_search_enabled is None:
            hybrid_search_enabled = ((await _existing()) or {}).get("hybrid_search_enabled")
        if hybrid_search_enabled is None:
            hybrid_search_enabled = knowledge_defaults.HYBRID_SEARCH_ENABLED

        use_deterministic_chunking = body.use_deterministic_chunking
        if use_deterministic_chunking is None:
            use_deterministic_chunking = ((await _existing()) or {}).get("use_deterministic_chunking")
        if use_deterministic_chunking is None:
            use_deterministic_chunking = knowledge_defaults.USE_DETERMINISTIC_CHUNKING

        # Only create field configs matching the enabled metadata levels
        await initialize_field_configurations_by_template(
            knowledge_id,
            template=body.field_template,
            document_level_metadata=document_level_metadata,
            chunk_level_metadata=chunk_level_metadata,
            seed_optional_defaults=body.seed_optional_defaults if body.seed_optional_defaults is not None else True,
        )

        await save_knowledge_config(
            knowledge_id=knowledge_id,
            embedding_model=embedding_model,
            hybrid_search_enabled=hybrid_search_enabled,
            document_level_metadata=document_level_metadata,
            chunk_level_metadata=chunk_level_metadata,
            collection_name=body.collection_name,
            service_description=body.service_description,
            enrichment_provider=body.enrichment_provider,
            enrichment_model=body.enrichment_model,
            enrichment_temperature=body.enrichment_temperature,
            enrichment_prompt=body.enrichment_prompt,
            use_deterministic_chunking=use_deterministic_chunking,
            enrichment_enabled=body.enrichment_enabled,
        )

        return KnowledgeConfigResponse(
            knowledge_id=knowledge_id,
            message="Configuration saved successfully"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving knowledge configuration: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to save configuration")


@router.get("/v1/embedding-models")
async def list_embedding_models_v2(auth: dict = Depends(get_api_key_or_session)):
    try:
        models = [model.value for model in EmbeddingModel]
        return {"embedding_models": models}
    except Exception as e:
        logger.exception("An unexpected error occurred")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while fetching embedding models {e}")


@router.get("/v1/chat-models", response_model=ChatModelCatalogResponse)
async def list_chat_models(auth: dict = Depends(get_api_key_or_session)):
    """Chat providers and their models, for the dashboard's model pickers.

    Not knowledge-scoped — the catalog is the same for every collection, so
    the dashboard fetches it once and reuses it for all four model pickers on
    the Settings screen (the extraction LLM plus the three per-node cards).

    Distinct from GET /v1/embedding-models, which lists *embedding* models:
    those are write-once per collection and not interchangeable with these.
    """
    try:
        catalog = get_chat_model_catalog()
        logger.info(
            f"Returning chat model catalog: {len(catalog)} providers, "
            f"{sum(len(p['models']) for p in catalog)} models"
        )
        return ChatModelCatalogResponse(providers=catalog)
    except Exception as e:
        logger.exception("Failed to build the chat model catalog")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while fetching chat models {e}")


@router.get(
    "/v1/field-templates",
    response_model=FieldTemplateCatalogResponse,
    # Every Optional field this response carries (default_collection_name and
    # friends, a field's schema) is meant to be ABSENT when unset, not sent as
    # a literal null — same "omitted, not null" convention as
    # POST /v1/enrichment/generate. No currently-shipped field on this
    # response can ever be None, so this can't silently drop anything a
    # caller already depends on.
    response_model_exclude_none=True,
)
async def list_field_templates(auth: dict = Depends(get_api_key_or_session)):
    """Document-type presets for the create-collection wizard's step 1 picker
    (CC1, docs/plan/collection-creation-flow/gap-analysis.md).

    Not knowledge-scoped — the catalog is the same for every caller, so the
    frontend fetches it once, same as GET /v1/chat-models above. Picking a
    template here does not create anything; the wizard sends the chosen
    template's key back on PUT /v1/{knowledge_id}/config as `field_template`
    once the collection is actually created.

    Hidden templates (invoice_contract, software_docs) are excluded — they
    stay valid values on PUT /config for existing callers, they are just not
    offered as a new choice in this picker.
    """
    try:
        templates = get_field_template_catalog()
        return FieldTemplateCatalogResponse(templates=templates)
    except Exception as e:
        logger.exception("Failed to build the field template catalog")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while fetching field templates {e}")


@router.get("/v1/field-types", response_model=FieldTypeCatalogResponse)
async def list_field_types(auth: dict = Depends(get_api_key_or_session)):
    """The create-collection wizard's step 2 type dropdown (per the mock) —
    every field_type a caller may choose explicitly, with its default label.

    Not knowledge-scoped, same reasoning as GET /v1/field-templates above: one
    static catalog, fetched once.

    This does NOT bring back a required type picker. `field_type` is still
    optional everywhere it always was — leave it unset and
    utils.config.field_type_resolver resolves one from the field's description,
    same as today. This catalog is for a caller who wants to choose explicitly:
    an explicit field_type still wins over resolution ("deterministic first"),
    it previously just had nowhere to read valid choices from. See
    docs/plan/field-and-retrieval-precision/field-type-resolution.md.

    `int` and `float` both carry the label "Number" (by design — see
    utils.models.enums.FIELD_TYPE_LABELS) — two entries, same label, different
    field_type. Whether to merge them into one dropdown row is a UI call.

    Each entry also carries `requires_schema`: true for `dict`/`List[dict]`,
    the only two types the write path lets a caller attach a `field_schema`
    to — see utils.models.enums.SCHEMA_BEARING_FIELD_TYPES.
    """
    try:
        field_types = get_field_type_catalog()
        return FieldTypeCatalogResponse(field_types=field_types)
    except Exception as e:
        logger.exception("Failed to build the field type catalog")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while fetching field types {e}")

