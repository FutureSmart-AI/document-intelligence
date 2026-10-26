from fastapi import Depends, HTTPException, APIRouter, Path, Query, UploadFile, File, Form
from pydantic import BaseModel
from typing import List, Optional, Dict, Union
import traceback
import json

from utils.auth.dependencies import get_api_key_or_session, require_permission
from utils.auth.auth_utils import extract_owner_id_from_session
from utils.logger import logger

from utils.pydantic_models_enrichment import (
    EnrichmentConfigurationValidationErrorResponse,
    FieldValidationError,
    EnrichmentConfigurationRequestV2,
    EnrichmentConfigurationResponseV2,
    FieldConfigurationResponseV2
)

from utils.db.enrichment_db_utils import (
    initialize_default_field_configurations_sqlmodel,
    knowledge_exists_check,
    get_field_configurations_sqlmodel_v2,
    validate_field_configuration_sqlmodel_v2,
    sync_field_configurations_sqlmodel_v2,
    delete_field_configurations_sqlmodel_v2,
    get_field_manifest,
)
from utils.db.retrieval_ops import (
    count_documents_with_field,
    get_metadata_from_qdrant_object_ids,
    get_metadata_values_for_field,
    get_user_defined_metadata_from_qdrant_object_ids,
)
from utils.db.knowledge_setting_ops import get_knowledge_config, save_knowledge_config
from utils.files.parsing_utils import peek_document_text, get_file_text
from utils.files.processing_modes import derive_use_llama_parse, resolve_document_processing_mode
from utils.files.url_utils import extract_filename_and_content_type
from utils.files.file_utils import generate_s3_presigned_urls, sanitize_filename
from utils.common_utils import generate_object_id
from utils.validation.validation_utils import validate_file_content_type
from utils.models.enums import DocumentProcessingMode
from utils.enrichment.preview_run import (
    MAX_PREVIEW_TEXT_CHARS,
    bad_preview_request,
    parse_fields_json,
    run_preview,
)
from utils.enrichment.preview_samples import available_sample_keys, get_sample
from utils.config.field_generation import (
    generate_field_schema,
    deactivate_chunk_fields_if_disabled,
    run_generate_preview,
    MAX_PEEKED_FILES,
)
from utils.config.field_type_resolver import needs_type_resolution, resolve_field_types
from utils.config.enrichment_config import PROTECTED_SYSTEM_FIELDS
from utils.pydantic_models_knowledge import MetadataResponse
from utils.tracing import tracer

router = APIRouter()


class DeleteEnrichmentFieldsRequest(BaseModel):
    """Request model for deleting enrichment configuration fields."""
    field_ids: List[int]


def _field_dicts(fields) -> List[Dict]:
    """Flatten request field models into the dicts the validator/sync expect.

    Keeps the field_type unwrapping that both write endpoints did inline, in
    one place, so the type-resolution step below sees identical input on the
    POST and PATCH paths.
    """
    out = []
    for field in fields:
        field_dict = field.model_dump(exclude_unset=True)
        if field.field_type is not None:
            field_dict['field_type'] = (
                field.field_type.value if hasattr(field.field_type, 'value') else field.field_type
            )
        out.append(field_dict)
    return out


async def _resolve_missing_field_types(
    knowledge_id: str,
    field_dicts: List[Dict],
) -> tuple:
    """Fill in field_type for any field sent without one, via the LLM resolver.

    This is what lets a caller describe a field instead of naming a Python
    type: `field_type` is optional on both write endpoints, and when it is
    absent utils.config.field_type_resolver picks the code-level type from the
    field's own description. An explicitly sent type is passed through
    untouched, so every existing integration behaves exactly as before.

    Returns (field_dicts, resolutions, added_fields). A resolver failure is
    surfaced as a 502 rather than swallowed: silently defaulting the whole
    batch to `str` is indistinguishable from a successful resolution, and it is
    precisely the silent coercion this feature removes.
    """
    if not any(needs_type_resolution(f) for f in field_dicts):
        return field_dicts, [], []

    config = await get_knowledge_config(knowledge_id) or {}
    existing = await get_field_configurations_sqlmodel_v2(knowledge_id)
    existing_names = [
        f.get("field_name") for f in (existing or []) if isinstance(f, dict)
    ]

    try:
        return await resolve_field_types(
            field_dicts,
            existing_field_names=existing_names,
            collection_name=config.get("collection_name"),
            service_description=config.get("service_description"),
            owner_id=config.get("owner_id"),
            knowledge_id=knowledge_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            f"Field type resolution failed | knowledge={knowledge_id} | "
            f"fields={[f.get('field_name') for f in field_dicts if needs_type_resolution(f)]} | {exc}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not resolve the type of one or more fields. Retry, or send "
                "an explicit 'field_type' for each field to skip resolution."
            ),
        )


async def _retype_relabelled_fields(
    knowledge_id: str,
    field_dicts: List[Dict],
) -> tuple:
    """Handle an EXISTING field whose type label the user reworded.

    This is the settings-screen flow: the user does not pick a type, they edit
    the label. A reworded label is the only signal that they want a different
    type, so it is resolved through the same LLM as a new field — and then the
    resolved type is applied ONLY if nothing has been extracted for that field
    yet.

    That last condition is the whole reason this is not simply "re-resolve and
    save". Re-typing a field does not re-extract the documents already processed
    under the old type: their stored values keep the old shape and then fail
    Tier 1 type_conformance indefinitely. So with values present the label
    changes and the type stays, and the caller is told why rather than left to
    discover the mismatch.

    Returns (field_dicts, resolutions, warnings).
    """
    candidates = []
    for f in field_dicts:
        if f.get('id') is None or not (f.get('field_type_label') or '').strip():
            continue
        candidates.append(f)
    if not candidates:
        return field_dicts, [], []

    existing_rows = await get_field_configurations_sqlmodel_v2(knowledge_id) or []
    by_id = {r.get('id'): r for r in existing_rows if isinstance(r, dict)}

    relabelled = []
    for f in candidates:
        row = by_id.get(f['id'])
        if not row:
            continue
        if (f.get('field_type_label') or '').strip() != (row.get('field_type_label') or ''):
            relabelled.append((f, row))

    if not relabelled:
        return field_dicts, [], []

    logger.info(
        f"Label changed on {len(relabelled)} existing field(s) | knowledge={knowledge_id} | "
        f"{[f.get('field_name') for f, _ in relabelled]} — re-resolving type"
    )

    config = await get_knowledge_config(knowledge_id) or {}
    # Ask the resolver as if these were untyped: the label is the signal, and a
    # stale field_type would short-circuit resolution via deterministic-first.
    # Seed from the STORED row first, then overlay what the caller actually
    # sent: a settings-screen relabel typically resends only id/field_name/
    # is_custom/field_type_label, never the field's own description or
    # sample_values. Probing with the sent dict alone (as this used to do)
    # handed the resolver "(no description given)" for every relabelled
    # field regardless of how rich its real description was, starving it of
    # exactly the context that decides a hard case ("Group of fields" alone,
    # with nothing else, reads as too vague to place — TYPE_RULES_BLOCK rule
    # 4 — so it silently kept the existing type with no visible sign anything
    # was wrong). The caller's own field_type_label must still win over the
    # stored one, so it is applied last.
    probe = [{**row, **f, 'field_type': None} for f, row in relabelled]
    try:
        resolved, resolutions, _ = await resolve_field_types(
            probe,
            existing_field_names=[r.get('field_name') for r in existing_rows],
            collection_name=config.get('collection_name'),
            service_description=config.get('service_description'),
            owner_id=config.get('owner_id'),
            knowledge_id=knowledge_id,
        )
    except Exception as exc:
        logger.error(
            f"Re-resolution after a label change failed | knowledge={knowledge_id} | {exc}",
            exc_info=True,
        )
        # Not fatal: the label edit itself is still valid and worth saving.
        return field_dicts, [], [
            "Could not re-check the field type after the label change; the label "
            "was saved and the type left unchanged."
        ]

    warnings: List[str] = []
    for (original, row), candidate in zip(relabelled, resolved):
        new_type = candidate.get('field_type')
        if not new_type or new_type == row.get('field_type'):
            continue
        used = await count_documents_with_field(knowledge_id, original['field_name'])
        if used == 0:
            original['field_type'] = new_type
            if candidate.get('schema'):
                original['schema'] = candidate['schema']
            original['_allow_field_type_change'] = True
            logger.info(
                f"retype | knowledge={knowledge_id} field={original['field_name']} "
                f"{row.get('field_type')} -> {new_type} (0 documents affected)"
            )
        else:
            # Unknown (None) lands here too, deliberately — see
            # count_documents_with_field: an unverifiable count must not permit
            # a destructive change.
            original.pop('field_type', None)
            where = f"{used} document(s) already have" if used else "documents may already have"
            warnings.append(
                f"'{original['field_name']}': the label \"{original['field_type_label']}\" "
                f"implies {new_type}, but {where} values extracted as "
                f"{row.get('field_type')}. The label was saved; the type is unchanged. "
                f"Deactivate and recreate the field to change its type."
            )
            logger.info(
                f"retype_refused | knowledge={knowledge_id} field={original['field_name']} "
                f"{row.get('field_type')} -> {new_type} | documents_with_values={used}"
            )

    return field_dicts, resolutions, warnings


@router.get("/v1/{knowledge_id}/enrichment", response_model_exclude_none=True)
async def get_enrichment(
    knowledge_id: str = Path(..., description="The unique identifier of the knowledge"),
    include_metadata: bool = Query(default=False, description="Whether to include metadata in the response"),
    field_name: Optional[str] = Query(default=None, description="Return values only for this specific field."),
    filters: Optional[str] = Query(default=None, description="JSON-encoded dict for filtering documents (e.g., '{\"document_type\": \"case_study\"}')."),
    limit: Optional[int] = Query(default=None, description="Limit number of results."),
    _: Optional[str] = Depends(require_permission("read")),
):
    """
    Get enrichment configuration v2 with metadata (unique values) per field.
    
    When field_name is provided, returns possible values for that specific metadata field with optional filtering.
    When field_name is not provided, returns all field configurations (backward compatible behavior).
    """
    try:
        with tracer.start_as_current_span("Node: Resolve Enrichment Query"):
            query_limit = limit if limit is not None else 10

        with tracer.start_as_current_span("Node: Check Knowledge Exists"):
            if not await knowledge_exists_check(knowledge_id):
                raise HTTPException(status_code=404, detail=f"Knowledge with ID '{knowledge_id}' not found.")

        parsed_filters: Optional[Dict[str, Union[str, List[str]]]] = None
        if filters:
            with tracer.start_as_current_span("Node: Parse Enrichment Filters"):
                try:
                    parsed_filters = json.loads(filters)
                    if not isinstance(parsed_filters, dict):
                        raise HTTPException(
                            status_code=400,
                            detail="filters parameter must be a valid JSON object/dict"
                        )
                except json.JSONDecodeError as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid JSON in filters parameter: {str(e)}"
                    )

        # If field_name is provided, return values for that specific field
        if field_name:
            try:
                with tracer.start_as_current_span("Node: Load Field Metadata Values"):
                    metadata_field = await get_metadata_values_for_field(
                        knowledge_id=knowledge_id,
                        field_name=field_name,
                        filters=parsed_filters,
                        limit=query_limit
                    )
                with tracer.start_as_current_span("Node: Build Field Metadata Response"):
                    return MetadataResponse(fields=[metadata_field])
            except ValueError:
                with tracer.start_as_current_span("Node: Load User Defined Metadata Values"):
                    metadata_response = await get_user_defined_metadata_from_qdrant_object_ids(
                        knowledge_id=knowledge_id,
                        limit=query_limit,
                        filters=parsed_filters,
                        field_name=field_name,
                    )
                if metadata_response.fields:
                    return metadata_response
                raise HTTPException(
                    status_code=404,
                    detail=f"Field '{field_name}' not found in active enrichment configuration or user-defined metadata for knowledge_id: {knowledge_id}",
                )
            except Exception as e:
                logger.error(f"Error retrieving metadata values for field {field_name} in knowledge {knowledge_id}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise HTTPException(status_code=500, detail=f"Failed to get metadata values for field '{field_name}'.")

        # Original behavior: return all field configurations
        # Ensure defaults exist (uses v1 initializer; v2 fields default set in model)
        with tracer.start_as_current_span("Node: Load Field Configurations"):
            configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)
            # if not configurations:
            #     await initialize_default_field_configurations_sqlmodel(knowledge_id)
            #     configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)

        # Fetch unique metadata values and map by field_name from Qdrant
        field_to_values = {}
        user_defined_metadata = None
        if include_metadata:
            with tracer.start_as_current_span("Node: Load Enrichment Metadata"):
                metadata_response = await get_metadata_from_qdrant_object_ids(
                    knowledge_id,
                    limit=query_limit,
                    filters=parsed_filters
                )
                try:
                    for f in getattr(metadata_response, 'fields', []) or []:
                        fname = getattr(f, 'field_name', None)
                        vals = getattr(f, 'values', []) or []
                        if fname:
                            field_to_values[fname] = vals
                except Exception:
                    field_to_values = {}

        with tracer.start_as_current_span("Node: Load User Defined Metadata"):
            # The manifest is the deterministic source of truth for user-defined field
            # names. Pass it in so every registered field appears even when it isn't in
            # the sampled documents (values are still sampled). Degrade gracefully: if the
            # manifest is unavailable (e.g. migration not yet applied in this environment),
            # fall back to the prior sampling-only behavior instead of failing the endpoint.
            try:
                manifest = await get_field_manifest(knowledge_id, active_only=True)
            except Exception as e:
                logger.warning(
                    f"Field manifest unavailable for {knowledge_id}; falling back to sampling: {e}"
                )
                manifest = []
            manifest_names = [m["field_name"] for m in manifest]
            manifest_types = {m["field_name"]: m["field_type"] for m in manifest}

            metadata_response = await get_user_defined_metadata_from_qdrant_object_ids(
                knowledge_id=knowledge_id,
                limit=query_limit,
                filters=parsed_filters,
                field_names=manifest_names or None,
                field_types=manifest_types or None,
            )
            if metadata_response.fields:
                # De-dup: never list a field that is already an enrichment field in
                # `fields[]` (avoids double-listing if a key collides with a config field).
                configured_names = {c.get('field_name') for c in configurations}
                user_defined_metadata = [
                    f for f in metadata_response.fields
                    if f.field_name not in configured_names
                ] or None

        with tracer.start_as_current_span("Node: Build Enrichment Response"):
            fields_v2 = []
            for cfg in configurations:
                metadata_vals = field_to_values.get(cfg.get('field_name'), [])
                fields_v2.append(
                    FieldConfigurationResponseV2(
                        id=cfg.get('id'),
                        knowledge_id=cfg.get('knowledge_id'),
                        field_name=cfg.get('field_name'),
                        description=cfg.get('description'),
                        field_type=cfg.get('field_type'),
                        field_type_label=cfg.get('field_type_label'),
                        sample_values=cfg.get('sample_values'),
                        restrict_to_choices=cfg.get('restrict_to_choices', False),
                        max_items=cfg.get('max_items'),
                        field_schema=cfg.get('schema'),
                        level=cfg.get('level') or 'document',
                        is_custom=cfg.get('is_custom', False),
                        is_required=cfg.get('is_required', False),
                        is_active=cfg.get('is_active', True),
                        created_at=cfg.get('created_at'),
                        updated_at=cfg.get('updated_at'),
                        deprecated_at=cfg.get('deprecated_at'),
                        metadata=metadata_vals
                    )
                )
            return EnrichmentConfigurationResponseV2(
                fields=fields_v2,
                user_defined_metadata=user_defined_metadata,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving v2 enrichment configuration for knowledge {knowledge_id}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to get enrichment configuration.")


@router.post("/v1/{knowledge_id}/enrichment/generate")
async def generate_enrichment_fields(
    knowledge_id: str = Path(..., description="The unique identifier of the knowledge"),
    extraction_instructions: Optional[str] = Form(
        None,
        description="What the user wants to filter/search by, e.g. 'filter by vendor, contract value, renewal date'",
    ),
    files: List[UploadFile] = File(default=[]),
    persist: bool = Query(
        default=True,
        description="False = propose only (CC5): run the same generation, apply the same "
                    "validation, but write nothing — extraction_instructions is not saved and no "
                    "field_configurations row is created or changed.",
    ),
    _: Optional[str] = Depends(require_permission("write")),
):
    """LLM-generate an enrichment field schema for a new (or existing) collection.

    Called from the Document Intelligence "Create document collection" modal,
    after `POST /v1` and `PUT /v1/{knowledge_id}/config` (which must run first —
    it creates the config row and seeds DEFAULT_FIELD_CONFIGURATIONS), before
    the real document upload. Reads `collection_name` / `service_description`
    from that already-saved config, takes `extraction_instructions` and up to
    a handful of attached files here, and:

      1. Persists `extraction_instructions` onto knowledge_config (migration 051).
      2. Creates whatever new custom fields the LLM proposes, after independent
         validation (utils.config.field_generation._validate_and_filter) — a
         hallucinated or unsupported field, a name this collection already
         has, or anything past the 20-field cap is dropped and reported in
         rejected_fields, never silently coerced or discarded. Existing
         default fields are left exactly as they are: the LLM no longer
         judges them (removed 2026-09-22).

    Both steps are writes; ``persist=false`` (CC5) skips both and returns the
    same proposal shape the write path would have applied, so a caller can show
    it for review before anything is saved.

    Attached files are peeked locally (pdfminer/python-docx/plain-text — see
    utils.files.parsing_utils.peek_document_text) for signal only. This never
    calls LlamaParse or any paid parser — the real document_processing_mode
    choice and the real extraction happen later, at `POST /documents`.
    """
    try:
        with tracer.start_as_current_span("Node: Check Knowledge Exists"):
            if not await knowledge_exists_check(knowledge_id):
                raise HTTPException(status_code=404, detail=f"Knowledge with ID '{knowledge_id}' not found.")

        with tracer.start_as_current_span("Node: Load Knowledge Config"):
            config = await get_knowledge_config(knowledge_id)
            if not config or not config.get("embedding_model"):
                raise HTTPException(
                    status_code=400,
                    detail="Knowledge config is not initialized. Call PUT /v1/{knowledge_id}/config first.",
                )
            owner_id = config.get("owner_id")
            collection_name = config.get("collection_name")
            service_description = config.get("service_description")

        with tracer.start_as_current_span("Node: Ensure Default Fields Exist"):
            # Idempotent — a caller that hits this before PUT /config's own
            # seeding (or out of the expected order) still gets the floor.
            # Skipped when persist=false: this is itself a write (it creates
            # any missing DEFAULT_FIELD_CONFIGURATIONS row), and a propose-only
            # call must not create rows either. In the normal call order
            # PUT /config has already seeded these, so existing_fields below
            # is populated either way.
            if persist:
                await initialize_default_field_configurations_sqlmodel(knowledge_id)

        with tracer.start_as_current_span("Node: Peek Attached Documents"):
            doc_previews = []
            skipped_files = []
            for upload in (files or [])[:MAX_PEEKED_FILES]:
                try:
                    raw = await upload.read()
                finally:
                    await upload.close()
                text = peek_document_text(upload.content_type, upload.filename or "unnamed", raw)
                if text:
                    doc_previews.append((upload.filename or "unnamed", text))
                else:
                    skipped_files.append(upload.filename or "unnamed")
            if skipped_files:
                logger.info(f"generate_enrichment_fields: could not peek {skipped_files} for knowledge {knowledge_id}")

        with tracer.start_as_current_span("Node: Load Existing Fields"):
            # Loaded BEFORE generation so a proposal reusing a name this
            # collection already has is rejected — writing it would overwrite
            # that field's definition.
            existing_fields = await get_field_configurations_sqlmodel_v2(knowledge_id)

        with tracer.start_as_current_span("Node: Generate Field Schema"):
            result = await generate_field_schema(
                owner_id=owner_id,
                knowledge_id=knowledge_id,
                collection_name=collection_name,
                service_description=service_description,
                extraction_instructions=extraction_instructions,
                doc_previews=doc_previews,
                existing_field_names=[f["field_name"] for f in existing_fields],
            )

        with tracer.start_as_current_span("Node: Persist Extraction Instructions"):
            if persist and extraction_instructions is not None:
                await save_knowledge_config(knowledge_id=knowledge_id, extraction_instructions=extraction_instructions)

        with tracer.start_as_current_span("Node: Enforce Chunk-Level Metadata Setting"):
            # This is the one place after the LLM (and the buggy seeding step above) has
            # run where the knowledge base's own chunk_level_metadata setting still wins —
            # see deactivate_chunk_fields_if_disabled's own docstring for why it's needed.
            chunk_metadata_on = bool(config.get("chunk_level_metadata"))
            chunk_updates, chunk_fields_deactivated = deactivate_chunk_fields_if_disabled(
                existing_fields, [], chunk_metadata_on
            )

        with tracer.start_as_current_span("Node: Sync Generated Fields"):
            sync_payload = list(chunk_updates) + list(result.accepted_fields)
            summary = {"created": 0, "updated": 0}
            if persist and sync_payload:
                summary = await sync_field_configurations_sqlmodel_v2(knowledge_id, sync_payload, delete_missing=False)

        return {
            # Key name kept as "created_fields" even when persist=false, so an
            # existing caller's response shape never changes based on a param
            # it doesn't send — run.persisted is the one source of truth for
            # whether these were actually written.
            "message": (
                f"Generated enrichment fields for knowledge_id: {knowledge_id}" if persist
                else f"Proposed enrichment fields for knowledge_id: {knowledge_id} — nothing saved"
            ),
            "created_fields": [f["field_name"] for f in result.accepted_fields],
            "rejected_fields": result.rejected_fields,
            "chunk_level_metadata_enabled": chunk_metadata_on,
            "chunk_fields_deactivated": chunk_fields_deactivated,
            "protected_fields_untouched": sorted(PROTECTED_SYSTEM_FIELDS),
            "documents_used": [name for name, _ in doc_previews],
            "documents_skipped": skipped_files,
            "generation": {"provider": result.provider, "model": result.model, "key_type": result.key_type},
            "sync_summary": summary,
            "run": {"persisted": persist},
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating enrichment fields for knowledge {knowledge_id}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to generate enrichment fields.")


@router.patch("/v1/{knowledge_id}/enrichment")
async def patch_enrichment(
    request: EnrichmentConfigurationRequestV2,
    knowledge_id: str = Path(..., description="The unique identifier of the knowledge"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """
    Patch enrichment configuration v2 (PATCH behavior):
    - Update fields that are sent with IDs (if ID exists)
    - Create new fields that are sent without IDs
    - Do NOT delete fields that are missing from the request
    
    To keep existing fields, send them with their IDs. To create new fields, send them without IDs.
    """
    try:
        with tracer.start_as_current_span("Node: Check Knowledge Exists"):
            if not await knowledge_exists_check(knowledge_id):
                raise HTTPException(status_code=404, detail=f"Knowledge with ID '{knowledge_id}' not found.")

        with tracer.start_as_current_span("Node: Resolve Missing Field Types"):
            fields_data = _field_dicts(request.fields)

            # Resolution applies to NEW fields only. On an existing field an
            # absent field_type means "leave it as it is" — resolving there
            # would silently change the stored type of a field the caller only
            # meant to rename or re-describe, and a type change is the one edit
            # that invalidates every value already extracted for it.
            new_idx = [i for i, f in enumerate(fields_data) if f.get('id') is None]
            to_resolve = [fields_data[i] for i in new_idx]
            resolved, type_resolutions, added_fields = await _resolve_missing_field_types(
                knowledge_id, to_resolve
            )
            for i, resolved_field in zip(new_idx, resolved):
                fields_data[i] = resolved_field
            fields_data.extend(added_fields)

        with tracer.start_as_current_span("Node: Re-resolve Relabelled Field Types"):
            # The settings flow: an existing field whose label the user reworded.
            # Mutates fields_data in place; the type is only re-applied where
            # nothing has been extracted yet.
            fields_data, relabel_resolutions, relabel_warnings = await _retype_relabelled_fields(
                knowledge_id, fields_data
            )
            type_resolutions = list(type_resolutions) + list(relabel_resolutions)

        with tracer.start_as_current_span("Node: Validate Enrichment Fields"):
            validation_errors = []
            for field_dict in fields_data:
                is_update = field_dict.get('id') is not None
                schema_val = field_dict.get('schema')
                logger.info(
                    f"PATCH field request | knowledge={knowledge_id} | "
                    f"id={field_dict.get('id')} name={field_dict.get('field_name')} "
                    f"type={field_dict.get('field_type')} level={field_dict.get('level')} "
                    f"is_custom={field_dict.get('is_custom')} is_required={field_dict.get('is_required')} "
                    f"is_active={field_dict.get('is_active')} restrict_to_choices={field_dict.get('restrict_to_choices')} "
                    f"max_items={field_dict.get('max_items')} has_schema={schema_val is not None} "
                    f"schema_keys={list(schema_val.keys()) if isinstance(schema_val, dict) else None} "
                    f"sample_values_count={len(field_dict['sample_values']) if field_dict.get('sample_values') else 0}"
                )
                errors = validate_field_configuration_sqlmodel_v2(field_dict, is_update)
                if errors:
                    logger.warning(
                        f"PATCH field validation failed | knowledge={knowledge_id} | "
                        f"id={field_dict.get('id')} name={field_dict.get('field_name')} "
                        f"errors={errors}"
                    )
                    validation_errors.append(
                        FieldValidationError(
                            field_name=field_dict.get('field_name', 'unknown'),
                            error='; '.join(errors)
                        )
                    )

            if validation_errors:
                raise HTTPException(
                    status_code=400,
                    detail=EnrichmentConfigurationValidationErrorResponse(details=validation_errors).model_dump()
                )

        with tracer.start_as_current_span("Node: Sync Enrichment Fields"):
            summary = await sync_field_configurations_sqlmodel_v2(knowledge_id, fields_data, delete_missing=False)

        logger.info(
            f"PATCH sync complete | knowledge={knowledge_id} | "
            f"received={len(fields_data)} created={summary.get('created')} updated={summary.get('updated')} "
            f"unchanged={summary.get('unchanged')} deleted={summary.get('deleted')} "
            f"skipped={summary.get('skipped')} warnings={len(summary.get('warnings', []))}"
        )
        message = f"Enrichment configuration synchronized successfully for knowledge_id: {knowledge_id}"
        response = {"message": message, "summary": summary}
        # Returned only when resolution actually ran, so an unchanged caller
        # sees an unchanged response. Every resolved type is reported with its
        # reason — a resolution the caller cannot see is one they cannot correct.
        if type_resolutions:
            response["type_resolutions"] = type_resolutions
        if added_fields:
            response["added_fields"] = [f["field_name"] for f in added_fields]
        if relabel_warnings:
            # A saved label that now disagrees with its stored type is exactly
            # what a caller must be told, rather than discover later from a
            # validation failure.
            if isinstance(response.get("summary"), dict):
                response["summary"].setdefault("warnings", []).extend(relabel_warnings)
            else:
                response["warnings"] = relabel_warnings
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error synchronizing v2 enrichment configuration for knowledge {knowledge_id}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to set enrichment configuration.")


@router.post("/v1/{knowledge_id}/enrichment")
async def post_enrichment(
    request: EnrichmentConfigurationRequestV2,
    knowledge_id: str = Path(..., description="The unique identifier of the knowledge"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """
    Post enrichment configuration v2 (POST behavior):
    - Create new fields that are sent without IDs
    - Do NOT update existing fields (fields with IDs are ignored)
    - Do NOT delete fields that are missing from the request
    
    Only fields without IDs will be processed. Fields with IDs will be filtered out.
    """
    try:
        with tracer.start_as_current_span("Node: Check Knowledge Exists"):
            if not await knowledge_exists_check(knowledge_id):
                raise HTTPException(status_code=404, detail=f"Knowledge with ID '{knowledge_id}' not found.")

        with tracer.start_as_current_span("Node: Filter New Enrichment Fields"):
            new_fields = [field for field in request.fields if field.id is None]
            
            if not new_fields:
                raise HTTPException(
                    status_code=400,
                    detail="No new fields to create. POST endpoint only creates new fields (fields without IDs)."
                )

        with tracer.start_as_current_span("Node: Resolve Missing Field Types"):
            # Every field here is new by construction, so any of them may carry
            # a description instead of a field_type.
            fields_data, type_resolutions, added_fields = await _resolve_missing_field_types(
                knowledge_id, _field_dicts(new_fields)
            )
            fields_data.extend(added_fields)

        with tracer.start_as_current_span("Node: Validate Enrichment Fields"):
            validation_errors = []
            for field_dict in fields_data:
                errors = validate_field_configuration_sqlmodel_v2(field_dict, is_update=False)
                if errors:
                    validation_errors.append(
                        FieldValidationError(
                            field_name=field_dict.get('field_name', 'unknown'),
                            error='; '.join(errors)
                        )
                    )

            if validation_errors:
                raise HTTPException(
                    status_code=400,
                    detail=EnrichmentConfigurationValidationErrorResponse(details=validation_errors).model_dump()
                )

        with tracer.start_as_current_span("Node: Sync Enrichment Fields"):
            summary = await sync_field_configurations_sqlmodel_v2(knowledge_id, fields_data, delete_missing=False)

        message = f"Enrichment configuration created successfully for knowledge_id: {knowledge_id}"
        logger.info(message)
        response = {"message": message, "summary": summary}
        if type_resolutions:
            response["type_resolutions"] = type_resolutions
        if added_fields:
            response["added_fields"] = [f["field_name"] for f in added_fields]
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating v2 enrichment configuration for knowledge {knowledge_id}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to create enrichment configuration.")


@router.delete("/v1/{knowledge_id}/enrichment/fields")
async def delete_enrichment(
    request: DeleteEnrichmentFieldsRequest,
    knowledge_id: str = Path(..., description="The unique identifier of the knowledge"),
    delete_metadata: bool = Query(default=False, description="If true, delete associated ExtractedMetadata records and remove values from vector DB. If false, delete only field configuration (keep existing metadata in vector DB)."),
    _: Optional[str] = Depends(require_permission("delete")),
):
    """
    Delete enrichment configuration fields (DELETE behavior):
    - Soft deletes fields specified by field_ids (sets deleted=true in database)
    - Can delete both system fields (is_custom=False) and custom fields (is_custom=True)
    - Protected system fields cannot be deleted: summary, chunk_idx, chunk_text, document_type
    - If delete_metadata=true: Deletes associated ExtractedMetadata records and removes values from vector DB
    - If delete_metadata=false (default): Deletes only field configuration (keeps existing metadata in vector DB)
    - Returns summary of deleted fields
    """
    try:
        with tracer.start_as_current_span("Node: Check Knowledge Exists"):
            if not await knowledge_exists_check(knowledge_id):
                raise HTTPException(status_code=404, detail=f"Knowledge with ID '{knowledge_id}' not found.")

        with tracer.start_as_current_span("Node: Validate Delete Request"):
            if not request.field_ids:
                raise HTTPException(
                    status_code=400,
                    detail="field_ids list cannot be empty"
                )

        with tracer.start_as_current_span("Node: Delete Enrichment Fields"):
            summary = await delete_field_configurations_sqlmodel_v2(knowledge_id, request.field_ids, delete_metadata=delete_metadata)

        with tracer.start_as_current_span("Node: Validate Delete Result"):
            if summary.get("errors"):
                error_messages = "; ".join(summary["errors"])
                raise HTTPException(
                    status_code=400,
                    detail=error_messages
                )

        message = f"Enrichment configuration fields deleted successfully for knowledge_id: {knowledge_id}"
        logger.info(message)
        return {"message": message, "summary": summary}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting enrichment configuration fields for knowledge {knowledge_id}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to delete enrichment configuration fields.")


# ---------------------------------------------------------------------------
# CC6 + CC9 — the single-document, nothing-saved preview run
#
# Design:    docs/plan/collection-creation-flow/preview-run.md
# Decisions: docs/plan/collection-creation-flow/decisions.md ("Decided 2026-09-08")
#
# This is the wizard's step 3 ("Try a sample"). It is the one endpoint in this
# router that is NOT knowledge-scoped, and that is not an oversight: collection
# creation is deferred to step 4 (Q2), so at step 3 there is no knowledge_id, no
# stored field schema and no knowledge-scoped credential to read. Everything comes
# from the request or from the authenticated owner's account-level defaults.
# ---------------------------------------------------------------------------

# A preview reads the file in-process, before any ingestion size check has run.
# The real upload cap lives in utils/validation/file_validation_utils.py; this is
# a separate ceiling on what we are willing to parse for a free-form preview, so a
# huge attachment cannot make one wizard click slow.
MAX_PREVIEW_FILE_BYTES = 15 * 1024 * 1024

# Below this, treat the read as having failed rather than as an empty document —
# a scanned PDF with no text layer produces a handful of stray characters, and
# running an extraction over them wastes the user's tokens on nothing.
MIN_PREVIEW_TEXT_CHARS = 20


def _require_session_owner_id(auth: dict) -> str:
    """Session-only, exactly like routers/api_key_management.py::_require_session_user_id.

    Same reason, too: there is no knowledge base here for a master API key to be
    scoped to, and this call spends real LLM tokens that must be charged to a
    specific account's monthly limit. A key with no owning user has no account to
    charge, so it is rejected rather than allowed to spend anonymously.
    """
    if auth.get("auth_type") != "session":
        raise HTTPException(status_code=401, detail="A logged-in session is required to try a sample extraction")
    owner_id = extract_owner_id_from_session(auth["session_data"])
    if not owner_id:
        raise HTTPException(status_code=401, detail="Could not resolve user identity")
    return owner_id


async def _read_preview_document(
    file: Optional[UploadFile],
    file_url: Optional[str],
    sample_key: Optional[str],
    mode: DocumentProcessingMode,
) -> tuple:
    """Resolve exactly one document source down to (file_name, text).

    Returns the text as read, untruncated — the caller applies the cap so it can
    report ``text_truncated`` honestly.
    """
    provided = [name for name, value in
                (("file", file), ("file_url", file_url), ("sample_key", sample_key)) if value]
    if len(provided) != 1:
        raise bad_preview_request(
            "Exactly one of 'file', 'file_url' or 'sample_key' must be provided"
            + (f" (received: {', '.join(provided)})" if provided else "")
        )

    # A bundled sample skips document reading entirely — the text is already here,
    # so there is no parser call and no parse credit to spend.
    if sample_key:
        sample = get_sample(sample_key)
        if not sample:
            raise bad_preview_request(
                f"Unknown sample_key '{sample_key}'. Available: {', '.join(available_sample_keys())}"
            )
        return sample["file_name"], sample["text"]

    use_llama_parse = derive_use_llama_parse(mode)

    if file is not None:
        try:
            raw = await file.read()
        finally:
            await file.close()
        if len(raw) > MAX_PREVIEW_FILE_BYTES:
            raise bad_preview_request(
                f"File is {len(raw)} bytes; the preview cap is {MAX_PREVIEW_FILE_BYTES} bytes"
            )
        file_name = file.filename or "uploaded_document"
        content_type = file.content_type or "application/octet-stream"
    else:
        # extract_filename_and_content_type raises 400 on an unsupported type,
        # which is the behaviour we want here too.
        file_name, content_type, _object_id = await extract_filename_and_content_type(file_url)
        import httpx

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("GET", file_url, follow_redirects=True) as response:
                    response.raise_for_status()
                    raw = await response.aread()
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"preview: could not fetch file_url: {exc}")
            raise HTTPException(status_code=400, detail=f"Could not fetch 'file_url': {exc}")
        if len(raw) > MAX_PREVIEW_FILE_BYTES:
            raise bad_preview_request(
                f"File is {len(raw)} bytes; the preview cap is {MAX_PREVIEW_FILE_BYTES} bytes"
            )

    # get_file_text is synchronous and can take seconds (pdfminer, python-docx, or
    # a LlamaParse round trip). Run it off the event loop so one preview cannot
    # stall every other request this worker is serving.
    from fastapi.concurrency import run_in_threadpool

    try:
        result = await run_in_threadpool(get_file_text, raw, content_type, file_name, use_llama_parse, None, None)
    except ValueError as exc:
        # get_file_text's own "Unsupported file type." / ".doc not supported".
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"preview: could not read {file_name!r} ({content_type}): {exc}")
        raise HTTPException(status_code=400, detail=f"Could not read '{file_name}': {exc}")

    text = (result or {}).get("content") or ""
    return file_name, text


@router.post("/v1/enrichment/preview")
async def preview_enrichment_extraction(
    file: Optional[UploadFile] = File(default=None, description="The one document to try"),
    file_url: Optional[str] = Form(default=None, description="Alternative to 'file' - a presigned URL"),
    sample_key: Optional[str] = Form(default=None, description="Alternative to both - a bundled example document"),
    fields: str = Form(..., description="JSON array of {field_name, description, field_type} - required"),
    document_processing_mode: Optional[DocumentProcessingMode] = Form(
        default=None, description="What the document is like; defaults to 'auto'"
    ),
    auth: dict = Depends(get_api_key_or_session),
):
    """Run one document against a field list and show what comes out. Saves nothing.

    Step 3 of the create-collection wizard. Two jobs in one call (CC9 folded into
    CC6 — see utils/enrichment/preview_run.py for why one call, not two):

      * ``extracted`` — the value of every field the caller asked for, with
        ``fields_run[].status = "not_found"`` where the document simply does not
        answer one. That is a distinct, useful outcome, not a failure.
      * ``suggested_fields`` — fields this document plainly contains that nobody
        asked for (an invoice's PO reference, its tax amount). Accepting one later
        is an ordinary ``POST /v1/{knowledge_id}/enrichment`` call once the
        collection exists at step 4; no new write endpoint is needed.

    Nothing is persisted: no S3 object, no Qdrant point, no Document row, no
    field_configurations row. ``run.persisted`` says so in the payload rather than
    leaving the UI to assert it. The one row that IS written is the usage event —
    a preview costs about one real extraction, counts against the owner's
    ``max_llm_tokens_per_month``, and is refused with 429 before it spends anything
    if that budget is already gone (Q1).

    ``fields[].field_type`` is OPTIONAL, exactly as on the write endpoints: a
    field sent with only a name and a description has its type resolved from that
    description, and the choice is reported back in ``type_resolutions``. Sending
    an explicit type skips resolution entirely.

    Errors: 400 (not exactly one source / unreadable or unsupported file / bad
    ``fields``), 401 (not a logged-in session), 422 (the document produced no
    readable text), 429 (over the monthly LLM budget), 502 (the provider failed,
    or a field's type could not be resolved — never a silently empty
    ``extracted``, and never a silent fallback to ``str``).
    """
    owner_id = _require_session_owner_id(auth)

    with tracer.start_as_current_span("Node: Validate Preview Fields"):
        parsed_fields = parse_fields_json(fields)

    mode = resolve_document_processing_mode(document_processing_mode, None)

    with tracer.start_as_current_span("Node: Read Preview Document"):
        file_name, text = await _read_preview_document(file, file_url, sample_key, mode)

    if len(text.strip()) < MIN_PREVIEW_TEXT_CHARS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{file_name}' produced no readable text. If it is a scan or an image-heavy "
                f"document, try document_processing_mode='scanned' or 'layout_aware'."
            ),
        )

    text_truncated = len(text) > MAX_PREVIEW_TEXT_CHARS
    if text_truncated:
        logger.info(f"preview: truncating {file_name!r} from {len(text)} to {MAX_PREVIEW_TEXT_CHARS} chars")
        text = text[:MAX_PREVIEW_TEXT_CHARS]

    # Resolve the type of any field the user described but did not type — the same
    # step the write path runs (_resolve_missing_field_types). Deliberately AFTER
    # the document read: an unreadable file must fail for free, not after an LLM
    # call. There is no knowledge_id to pass (deferred creation, Q2), so the
    # resolver works from the field's own description alone.
    type_resolutions: List[Dict] = []
    added_fields: List[Dict] = []
    if any(needs_type_resolution(f) for f in parsed_fields):
        with tracer.start_as_current_span("Node: Resolve Preview Field Types"):
            try:
                parsed_fields, type_resolutions, added_fields = await resolve_field_types(
                    parsed_fields,
                    owner_id=owner_id,
                )
            except HTTPException:
                raise
            except Exception as exc:
                logger.error(
                    f"preview: field type resolution failed for owner {owner_id} | "
                    f"fields={[f.get('field_name') for f in parsed_fields if needs_type_resolution(f)]} | {exc}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Could not resolve the type of one or more fields. Retry, or send "
                        "an explicit 'field_type' for each field to skip resolution."
                    ),
                )
            # A resolved monetary field brings a document-level `currency` field with
            # it. Run it here too: the preview must show what the real collection
            # would extract, and that includes the field the resolver adds.
            parsed_fields = list(parsed_fields) + list(added_fields)

    with tracer.start_as_current_span("Node: Run Preview Extraction"):
        response = await run_preview(
            owner_id=owner_id,
            document_text=text,
            file_name=file_name,
            fields=parsed_fields,
            document_processing_mode=mode.value,
            text_truncated=text_truncated,
        )

    # Same keys the write endpoints use, so the wizard reads one shape throughout.
    # Never silent: a type this service chose on the user's behalf is reported, not
    # quietly applied (utils/config/field_type_resolver.py, "THE FAILURE MODE THIS
    # GUARDS").
    if type_resolutions:
        response["type_resolutions"] = type_resolutions
    if added_fields:
        response["added_fields"] = [f["field_name"] for f in added_fields]
    return response


# Same ceiling as the Step 3 preview's own file_url fetch (MAX_PREVIEW_FILE_BYTES) — a
# peek reads at most 3000 chars out of this, but the download itself must still be bounded.
MAX_GENERATE_PEEK_FILE_BYTES = 15 * 1024 * 1024


@router.post("/v1/enrichment/generate-upload-url")
async def get_generate_preview_upload_url(
    file_name: str = Form(...),
    content_type: str = Form(...),
    auth: dict = Depends(get_api_key_or_session),
):
    """Step 1's attach-a-file path: a presigned URL to upload one document to, before
    any collection exists. Owner-scoped, mirrors
    `POST /v1/{knowledge_id}/upload-url` (routers/document_management.py::get_upload_url)
    minus the knowledge_id — that endpoint never actually used it for anything but its
    write-permission check; the S3 key it builds doesn't reference it either.

    Upload the file to `presigned_url` with a `PUT` whose `Content-Type` matches what
    was sent here exactly, then pass `download_presigned_url` back as `file_url` to
    `POST /v1/enrichment/generate`.

    NOT named `/v1/enrichment/upload-url` — that 3-segment shape is silently shadowed
    by `POST /v1/{knowledge_id}/upload-url` (document_management_router is included in
    main.py before this router, and Starlette matches routes in registration order, not
    by literal-vs-param specificity, so `{knowledge_id}` would bind to the literal string
    "enrichment"). Confirmed live 2026-09-09: that name returned a 200 through the OTHER
    endpoint, master key and all. Don't rename this back without also reordering
    main.py's router registration and re-verifying live.
    """
    owner_id = _require_session_owner_id(auth)

    if not file_name:
        raise HTTPException(status_code=400, detail="file_name is required")
    if not content_type:
        raise HTTPException(status_code=400, detail="content_type is required")
    validate_file_content_type(content_type)

    sanitized_file_name = sanitize_filename(file_name)
    object_id = await generate_object_id()
    object_key = f"rag_demo_dev/generate_preview_uploads/{object_id}_{sanitized_file_name}"

    try:
        upload_presigned_url, download_presigned_url = await generate_s3_presigned_urls(object_key, content_type)
    except Exception as e:
        logger.error(f"Error generating upload URL for owner {owner_id}: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while generating upload URL: {e}")

    return {
        "object_id": object_id,
        "presigned_url": upload_presigned_url,
        "download_presigned_url": download_presigned_url,
    }


@router.post("/v1/enrichment/generate")
async def generate_enrichment_fields_preview(
    collection_name: Optional[str] = Form(default=None, description="Working name for the collection, if chosen yet"),
    service_description: Optional[str] = Form(
        default=None, description="What these documents are about — Step 1's 'what are these documents about?' box"
    ),
    extraction_instructions: Optional[str] = Form(
        default=None,
        description="What the user wants to filter/search by, e.g. 'filter by vendor, contract value, renewal date'",
    ),
    file_url: Optional[str] = Form(
        default=None,
        description="One document to peek for signal — a presigned URL from POST /v1/enrichment/generate-upload-url",
    ),
    auth: dict = Depends(get_api_key_or_session),
):
    """Step 1 of the create-collection wizard: propose an enrichment field schema
    from free text, before any collection exists. Saves nothing.

    The owner-scoped counterpart to `POST /v1/{knowledge_id}/enrichment/generate`
    above — same generation logic (utils.config.field_generation.generate_field_schema),
    same new-field validation and 20-field cap (no default-field verdicts
    since 2026-09-22). The difference is entirely about *when*: collection creation is
    deferred to step 4 (Q2, docs/plan/collection-creation-flow/decisions.md), so at
    step 1 there is no knowledge_id, no stored collection_name/service_description
    to read back, and nothing to write to — every input comes from the request, and
    the response is a proposal the wizard applies to its own in-browser field list.
    Accepting it into a real collection is an ordinary `POST /v1/{knowledge_id}/enrichment`
    call once step 4 creates one, exactly like accepting a Step 3 suggested field.

    This is CC5's "propose-without-persisting" outcome for the pre-creation case,
    achieved by construction rather than a flag — see the sibling `persist=false`
    query param on the knowledge-scoped endpoint for the equivalent once a
    collection already exists.

    At most one attached document — sent as `file_url`, not a raw multipart upload,
    so a large file never rides inline on this call. Get the URL from
    `POST /v1/enrichment/generate-upload-url` first, `PUT` the file there, then pass the
    resulting `download_presigned_url` here. Peeked locally for signal only (no
    LlamaParse spend, same as the knowledge-scoped endpoint's own peek).

    Metered like Step 3's preview (Q1): counts against the owner's
    `max_llm_tokens_per_month` and is refused with 429 before it spends anything
    if that budget is already gone.

    Errors: 400 (`file_url` set but unreadable/unsupported/too large), 401 (not a
    logged-in session — same reason as Step 3: there is no account-less way to charge
    this call's LLM spend), 429 (over the monthly LLM budget), 500 (a genuine
    LLM/provider failure).
    """
    owner_id = _require_session_owner_id(auth)

    doc_previews = []
    if file_url:
        with tracer.start_as_current_span("Node: Fetch And Peek Attached Document"):
            file_name, content_type, _object_id = await extract_filename_and_content_type(file_url)
            import httpx

            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream("GET", file_url, follow_redirects=True) as response:
                        response.raise_for_status()
                        raw = await response.aread()
            except HTTPException:
                raise
            except Exception as exc:
                logger.error(f"generate preview: could not fetch file_url: {exc}")
                raise HTTPException(status_code=400, detail=f"Could not fetch 'file_url': {exc}")

            if len(raw) > MAX_GENERATE_PEEK_FILE_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"File is {len(raw)} bytes; the peek cap is {MAX_GENERATE_PEEK_FILE_BYTES} bytes",
                )

            text = peek_document_text(content_type, file_name, raw)
            if text:
                doc_previews.append((file_name, text))

    with tracer.start_as_current_span("Node: Generate Field Schema Preview"):
        try:
            return await run_generate_preview(
                owner_id=owner_id,
                collection_name=collection_name,
                service_description=service_description,
                extraction_instructions=extraction_instructions,
                doc_previews=doc_previews,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error generating enrichment field preview for owner {owner_id}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail="Failed to generate an enrichment field schema.")
