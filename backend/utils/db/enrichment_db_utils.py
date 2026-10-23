from sqlmodel import select, and_, delete
from typing import List, Dict, Any, Optional
from datetime import datetime

from utils.logger import logger
from utils.db.session import async_session as get_async_session
from utils.db.retry_utils import retry_db_connection

from utils.config.enrichment_config import DEFAULT_FIELD_CONFIGURATIONS, PROTECTED_SYSTEM_FIELDS
from utils.models.enums import CALLER_FIELD_TYPES, label_for_field_type
from utils.pydantic_models_enrichment import FieldConfiguration  # , ExtractedMetadata
from utils.pydantic_models_knowledge import KnowledgeConfig  # ChatbotDetails removed
from qdrant_client.http import models
from time import perf_counter

from utils.tracing import track_time


# SQLModel-based Database Operations
@retry_db_connection(max_retries=3)
@track_time("DB: Initialize Default Field Configurations")
async def initialize_default_field_configurations_sqlmodel(knowledge_id: str):
    """Initialize default field configurations using SQLModel."""
    try:
        start_time = perf_counter()
        async with get_async_session() as session:
            for field_data in DEFAULT_FIELD_CONFIGURATIONS:
                # Check if field already exists (including deleted ones)
                statement = select(FieldConfiguration).where(
                    and_(
                        FieldConfiguration.knowledge_id == knowledge_id,
                        FieldConfiguration.field_name == field_data["field_name"]
                    )
                )
                result = await session.execute(statement)
                existing_field = result.scalars().first()
                
                if not existing_field:
                    # Create new field configuration
                    field_config = FieldConfiguration(
                        knowledge_id=knowledge_id,
                        **field_data
                    )
                    session.add(field_config)
                elif existing_field.deleted:
                    # Restore deleted default field
                    existing_field.deleted = False
                    existing_field.deleted_at = None
                    existing_field.updated_at = datetime.utcnow()
                    session.add(existing_field)
            
            await session.commit()
            logger.info(f"Initialized default field configurations for knowledge {knowledge_id}, time taken: {perf_counter() - start_time} seconds  ")
    except Exception as e:
        logger.info(f"Initialized default field configurations for knowledge {knowledge_id}, time taken: {perf_counter() - start_time} seconds")
        logger.error(f"Error initializing default field configurations(time taken: {perf_counter() - start_time} seconds): {str(e)}")
        raise


# async def get_field_configurations_sqlmodel(knowledge_id: str) -> List[Dict[str, Any]]:
#     """Retrieve all field configurations using SQLModel."""
#     try:
#         async with get_async_session() as session:
#             # Filter out deleted fields
#             statement = select(FieldConfiguration).where(
#                 and_(
#                     FieldConfiguration.knowledge_id == knowledge_id,
#                     FieldConfiguration.deleted == False
#                 )
#             ).order_by(FieldConfiguration.is_custom, FieldConfiguration.field_name)
            
#             result = await session.execute(statement)
#             configurations = result.scalars().all()
            
#             # Convert to dictionaries
#             config_list = []
#             for config in configurations:
#                 config_dict = {
#                     "id": config.id,
#                     "knowledge_id": config.knowledge_id,
#                     "field_name": config.field_name,
#                     "description": config.description,
#                     "field_type": config.field_type,
#                     "is_enabled": config.is_enabled,
#                     "is_custom": config.is_custom,
#                     "sample_values": config.sample_values,
#                     "restrict_to_choices": config.restrict_to_choices,
#                     "max_items": config.max_items
#                 }
#                 config_list.append(config_dict)
            
#             logger.info(f"Retrieved {len(config_list)} field configurations for knowledge {knowledge_id}")
#             return config_list
#     except Exception as e:
#         logger.error(f"Error retrieving field configurations: {str(e)}")
#         raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Field Configurations V2")
async def get_field_configurations_sqlmodel_v2(knowledge_id: str) -> List[Dict[str, Any]]:
    """Retrieve all field configurations including v2 fields (level, is_active, deprecated_at)."""
    try:
        start_time = perf_counter()
        async with get_async_session() as session:
            # All field configurations ordered by is_custom and field_name. 
            # is_custom is False first, then True. field_name is alphabetically sorted.
            # Filter out deleted fields
            statement = select(FieldConfiguration).where(
                and_(
                    FieldConfiguration.knowledge_id == knowledge_id,
                    FieldConfiguration.deleted == False,
                    FieldConfiguration.field_name != "chunk_idx" # iNTERNAL FIELD, exclude from API
                )
            ).order_by(FieldConfiguration.is_custom, FieldConfiguration.field_name)

            result = await session.execute(statement)
            configurations = result.scalars().all()

            config_list: List[Dict[str, Any]] = []
            for config in configurations:
                config_dict = {
                    "id": config.id,
                    "knowledge_id": config.knowledge_id,
                    "field_name": config.field_name,
                    "description": config.description,
                    "field_type": config.field_type,
                    # NULL for every row predating migration 052; the response
                    # model falls back to the default label for those.
                    "field_type_label": config.field_type_label,
                    "level": config.level,
                    "is_custom": config.is_custom,
                    "is_required": config.is_required,
                    "is_active": config.is_active if config.is_active is not None else bool(config.is_enabled),
                    "sample_values": config.sample_values,
                    "restrict_to_choices": config.restrict_to_choices,
                    "max_items": config.max_items,
                    "schema": config.field_schema,
                    "created_at": config.created_at,
                    "updated_at": config.updated_at,
                    "deprecated_at": config.deprecated_at
                }
                config_list.append(config_dict)

            logger.info(f"Retrieved {len(config_list)} v2 field configurations for knowledge {knowledge_id}, time taken: {perf_counter() - start_time} seconds")
            return config_list
    except Exception as e:
        logger.error(f"Error retrieving v2 field configurations(time taken: {perf_counter() - start_time} seconds): {str(e)}")
        raise


# Keys that are reserved system/payload keys and must never be registered as
# user-defined fields. Mirrors the reserved set enforced at document ingestion
# (routers/document_management.py) and in the worker payload builder (indexer.py).
_MANIFEST_RESERVED_KEYS = {"object_id", "knowledge_id", "created_at", "chunk_text"}


@retry_db_connection(max_retries=3)
@track_time("DB: Upsert Field Manifest")
async def upsert_field_manifest(knowledge_id: str, extra_metadata: Dict[str, Any]) -> Dict[str, int]:
    """Idempotently register user-defined (passthrough) field names for a knowledge_id.

    Persists the union of extra_metadata keys into user_defined_field_manifest so the
    enrichment endpoint can return the complete field set deterministically instead of
    sampling a few documents. Safe to call on every ingestion: it only inserts NEW keys
    and bumps last_seen_at for ones already present (race-safe via ON CONFLICT).

    Excluded keys: None values, reserved/system keys, and any name that already exists in
    field_configurations (to avoid double-listing an enrichment field as user-defined).
    field_type/sample_value are frozen at first insert and never overwritten.

    Returns {"inserted": n, "touched": m}.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from utils.db.retrieval_ops import _infer_metadata_field_type
    from utils.pydantic_models_knowledge import UserDefinedFieldManifest

    if not isinstance(extra_metadata, dict) or not extra_metadata:
        return {"inserted": 0, "touched": 0}

    # Candidate keys: non-null, non-reserved
    candidates: Dict[str, Any] = {
        k: v for k, v in extra_metadata.items()
        if v is not None and k not in _MANIFEST_RESERVED_KEYS
    }
    if not candidates:
        return {"inserted": 0, "touched": 0}

    async with get_async_session() as session:
        # Exclude names that are already enrichment fields (any deleted state) so we never
        # treat a known field_configurations field as user-defined.
        fc_stmt = select(FieldConfiguration.field_name).where(
            FieldConfiguration.knowledge_id == knowledge_id
        )
        fc_result = await session.execute(fc_stmt)
        field_config_names = {row for row in fc_result.scalars().all()}

        keys = [k for k in candidates if k not in field_config_names]
        if not keys:
            return {"inserted": 0, "touched": 0}

        # Existing manifest names (for accurate inserted/touched counts)
        existing_stmt = select(UserDefinedFieldManifest.field_name).where(
            and_(
                UserDefinedFieldManifest.knowledge_id == knowledge_id,
                UserDefinedFieldManifest.field_name.in_(keys),
            )
        )
        existing_result = await session.execute(existing_stmt)
        existing_names = {row for row in existing_result.scalars().all()}

        now = datetime.utcnow()
        rows = []
        for k in keys:
            rows.append({
                "knowledge_id": knowledge_id,
                "field_name": k,
                "field_type": _infer_metadata_field_type([candidates[k]]),
                "sample_value": candidates[k],
                "is_active": True,
                "first_seen_at": now,
                "last_seen_at": now,
                "created_at": now,
                "updated_at": now,
            })

        stmt = pg_insert(UserDefinedFieldManifest).values(rows)
        # On conflict: keep field_type/sample_value frozen; only refresh last_seen_at.
        stmt = stmt.on_conflict_do_update(
            index_elements=["knowledge_id", "field_name"],
            set_={"last_seen_at": now, "updated_at": now},
        )
        await session.execute(stmt)
        await session.commit()

    inserted = len([k for k in keys if k not in existing_names])
    touched = len(keys) - inserted
    logger.info(
        f"Field manifest upsert for knowledge {knowledge_id}: inserted={inserted}, touched={touched}"
    )
    return {"inserted": inserted, "touched": touched}


@retry_db_connection(max_retries=3)
@track_time("DB: Get Field Manifest")
async def get_field_manifest(knowledge_id: str, active_only: bool = True) -> List[Dict[str, Any]]:
    """Return the persisted user-defined field manifest for a knowledge_id.

    Each item: {field_name, field_type, sample_value, is_active}. This is the deterministic
    source of truth for user-defined field NAMES used by GET /enrichment.
    """
    from utils.pydantic_models_knowledge import UserDefinedFieldManifest

    async with get_async_session() as session:
        conditions = [UserDefinedFieldManifest.knowledge_id == knowledge_id]
        if active_only:
            conditions.append(UserDefinedFieldManifest.is_active == True)  # noqa: E712
        statement = select(UserDefinedFieldManifest).where(and_(*conditions)).order_by(
            UserDefinedFieldManifest.field_name
        )
        result = await session.execute(statement)
        rows = result.scalars().all()

    return [
        {
            "field_name": row.field_name,
            "field_type": row.field_type,
            "sample_value": row.sample_value,
            "is_active": row.is_active,
        }
        for row in rows
    ]


@retry_db_connection(max_retries=3)
@track_time("DB: Sync Field Configurations")
async def sync_field_configurations_sqlmodel(knowledge_id: str, fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Synchronize field configurations using SQLModel."""
    try:
        start_time = perf_counter()
        async with get_async_session() as session:
            # Get current configurations for this knowledge (filter out deleted)
            current_statement = select(FieldConfiguration).where(
                and_(
                    FieldConfiguration.knowledge_id == knowledge_id,
                    FieldConfiguration.deleted == False
                )
            )
            current_result = await session.execute(current_statement)
            current_configs_list = current_result.scalars().all()
            
            # Create lookup dictionaries
            current_configs_by_id = {config.id: config for config in current_configs_list}
            
            # Track operations
            updated_count = 0
            created_count = 0
            deleted_count = 0
            warnings = []
            
            # Process incoming fields
            processed_config_ids = set()
            processed_field_names = set()
            
            # Flag to track if any fields with IDs were processed
            has_valid_fields_with_ids = False
            
            # First pass: validate and clean the input fields (filter out invalid fields)
            valid_fields = []
            for field_data in fields:
                field_id = field_data.get('id')
                field_name = field_data.get('field_name')
                
                # Skip if this field name has already been processed
                if field_name in processed_field_names:
                    warning_msg = f"Duplicate field name '{field_name}' found in request. Using first occurrence only."
                    logger.warning(warning_msg)
                    warnings.append(warning_msg)
                    continue
                
                # For fields with IDs, verify they belong to this knowledge and are not deleted
                if field_id is not None:
                    field_check_statement = select(FieldConfiguration).where(
                        and_(
                            FieldConfiguration.id == field_id,
                            FieldConfiguration.deleted == False
                        )
                    )
                    field_check_result = await session.execute(field_check_statement)
                    field_to_check = field_check_result.scalar_one_or_none()
                    
                    if field_to_check is None:
                        warning_msg = f"Field ID {field_id} not found or is deleted. Skipping."
                        logger.warning(warning_msg)
                        warnings.append(warning_msg)
                        continue
                    
                    if field_to_check.knowledge_id != knowledge_id:
                        warning_msg = f"Field ID {field_id} does not belong to current knowledge. Skipping."
                        logger.warning(warning_msg)
                        warnings.append(warning_msg)
                        continue
                    
                    # Track that we have at least one valid field with ID
                    has_valid_fields_with_ids = True
                
                # Add to valid fields if it passes validation
                valid_fields.append(field_data)
                if field_name:
                    processed_field_names.add(field_name)
            
            # Second pass: process valid fields
            for field_data in valid_fields:
                field_id = field_data.get('id')
                field_name = field_data.get('field_name')
                
                if field_id is not None:
                    # We already verified this field belongs to this chatbot
                    existing_config = current_configs_by_id.get(field_id)
                    
                    # Double check we have the config (shouldn't be needed but safety first)
                    if not existing_config:
                        continue
                    
                    # Check if field_name is being changed and if the new name conflicts with existing fields
                    new_field_name = field_data.get('field_name')
                    if new_field_name and new_field_name != existing_config.field_name:
                        # Check if the new field name already exists (excluding the current field and deleted fields)
                        name_conflict_statement = select(FieldConfiguration).where(
                            and_(
                                FieldConfiguration.knowledge_id == knowledge_id,
                                FieldConfiguration.field_name == new_field_name,
                                FieldConfiguration.id != field_id,
                                FieldConfiguration.deleted == False
                            )
                        )
                        name_conflict_result = await session.execute(name_conflict_statement)
                        name_conflict = name_conflict_result.scalar_one_or_none()
                        
                        if name_conflict is not None:
                            warning_msg = f"Cannot rename field '{existing_config.field_name}' to '{new_field_name}' - name already exists"
                            logger.warning(warning_msg)
                            warnings.append(warning_msg)
                            continue
                    
                    # Update existing field
                    processed_config_ids.add(existing_config.id)
                    
                    # Handle system-locked fields
                    if existing_config.is_system_locked:
                        # For system-locked fields, allow updating description, sample_values, and field_type
                        if 'description' in field_data and field_data['description'] is not None:
                            existing_config.description = field_data['description']
                        if 'field_type' in field_data and field_data['field_type'] is not None:
                            existing_config.field_type = field_data['field_type']
                        existing_config.sample_values = field_data.get('sample_values', existing_config.sample_values)
                        existing_config.is_enabled = True
                        # Allow field_name updates for system-locked fields
                        if 'field_name' in field_data and field_data['field_name'] is not None:
                            existing_config.field_name = field_data['field_name']
                    else:
                        # For non-system-locked fields, allow updating all properties
                        if 'field_name' in field_data and field_data['field_name'] is not None:
                            existing_config.field_name = field_data['field_name']
                        if 'description' in field_data and field_data['description'] is not None:
                            existing_config.description = field_data['description']
                        if 'field_type' in field_data and field_data['field_type'] is not None:
                            existing_config.field_type = field_data['field_type']
                        existing_config.is_enabled = field_data.get('is_enabled', existing_config.is_enabled)
                        existing_config.sample_values = field_data.get('sample_values', existing_config.sample_values)
                        existing_config.restrict_to_choices = field_data.get('restrict_to_choices', existing_config.restrict_to_choices)
                        existing_config.max_items = field_data.get('max_items', existing_config.max_items)
                    
                    existing_config.updated_at = datetime.utcnow()
                    session.add(existing_config)
                    updated_count += 1
                else:
                    # Create new field (only when no ID is provided)
                    if field_name:
                        # Check if field name already exists in database - prevent duplicates (excluding deleted fields)
                        existing_field_statement = select(FieldConfiguration).where(
                            and_(
                                FieldConfiguration.knowledge_id == knowledge_id,
                                FieldConfiguration.field_name == field_name,
                                FieldConfiguration.deleted == False
                            )
                        )
                        existing_field_result = await session.execute(existing_field_statement)
                        existing_field = existing_field_result.scalar_one_or_none()
                        
                        if existing_field is not None:
                            # Field with this name already exists, skip it
                            warning_msg = f"Field name '{field_name}' already exists in database. Cannot create duplicate."
                            logger.warning(warning_msg)
                            warnings.append(warning_msg)
                            continue
                        
                        new_field = FieldConfiguration(
                            knowledge_id=knowledge_id,
                            field_name=field_name,
                            description=field_data.get('description', ''),
                            field_type=field_data.get('field_type', 'str'),
                            is_enabled=field_data.get('is_enabled', True),
                            is_custom=True,
                            is_system_locked=False,
                            sample_values=field_data.get('sample_values'),
                            restrict_to_choices=field_data.get('restrict_to_choices', False),
                            max_items=field_data.get('max_items')
                        )
                        session.add(new_field)
                        created_count += 1
            
            # Check if there were any valid operations performed
            valid_operations_performed = (updated_count > 0 or created_count > 0)
            
            # Perform deletions only if there were valid fields in the request - use soft delete
            if len(valid_fields) > 0:
                # Soft delete non-processed, non-system-locked fields
                for config in current_configs_list:
                    if (config.id not in processed_config_ids and 
                        config.field_name not in processed_field_names and 
                        not config.is_system_locked):
                        
                        # REMOVED: extracted_metadata SQL delete — table being phased out.
                        # Field payload removal from Qdrant is handled by delete_payload in the
                        # enrichment config delete endpoint (delete_metadata=true path).
                        # delete_metadata_statement = delete(ExtractedMetadata).where(
                        #     ExtractedMetadata.field_configuration_id == config.id
                        # )
                        # await session.execute(delete_metadata_statement)

                        # Soft delete instead of hard delete
                        config.deleted = True
                        config.deleted_at = datetime.utcnow()
                        config.updated_at = datetime.utcnow()
                        session.add(config)
                        deleted_count += 1
            
            await session.commit()
            
            logger.info(f"Synchronized field configurations for knowledge {knowledge_id}(time taken: {perf_counter() - start_time} seconds): "
                       f"created={created_count}, updated={updated_count}, deleted={deleted_count}")
            
            return {
                "created": created_count,
                "updated": updated_count,
                "deleted": deleted_count,
                "warnings": warnings
            }
    except Exception as e:
        logger.error(f"Error synchronizing field configurations(time taken: {perf_counter() - start_time} seconds): {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Sync Field Configurations V2")
async def sync_field_configurations_sqlmodel_v2(knowledge_id: str, fields: List[Dict[str, Any]], delete_missing: bool = True) -> Dict[str, Any]:
    """Synchronize field configurations with v2 semantics (immutability and new fields).
    
    Args:
        knowledge_id: The knowledge ID
        fields: List of field configurations to sync
        delete_missing: If True, delete fields not in the request (PUT behavior). 
                       If False, only update/create provided fields (PATCH behavior).
    """
    try:
        start_time = perf_counter()
        async with get_async_session() as session:
            # Filter out deleted fields
            current_statement = select(FieldConfiguration).where(
                and_(
                    FieldConfiguration.knowledge_id == knowledge_id,
                    FieldConfiguration.deleted == False
                )
            )
            current_result = await session.execute(current_statement)
            current_configs_list = current_result.scalars().all()

            current_configs_by_id = {config.id: config for config in current_configs_list}
            current_names = {config.field_name.lower(): config for config in current_configs_list}

            updated_count = 0
            unchanged_count = 0
            created_count = 0
            deleted_count = 0
            skipped_count = 0
            warnings: List[str] = []

            processed_config_ids = set()
            processed_field_names = set()

            valid_fields: List[Dict[str, Any]] = []
            for field_data in fields:
                field_name = (field_data.get('field_name') or '').strip()
                field_id: Optional[int] = field_data.get('id')

                # if not field_name:
                #     warnings.append("Skipping field with empty field_name")
                #     continue

                if field_name in processed_field_names:
                    msg = f"Duplicate field name '{field_name}' found in request. Using first occurrence only."
                    warnings.append(msg)
                    logger.warning(f"skip_duplicate | knowledge={knowledge_id} id={field_id} name={field_name} | {msg}")
                    skipped_count += 1
                    continue

                # Basic validation of allowed field_type
                # ft = field_data.get('field_type')
                # if ft is not None and ft not in ['str', 'List[str]', 'int', 'float', 'bool']:
                #     warnings.append(f"Invalid field_type '{ft}' for field '{field_name}'. Skipping.")
                #     continue

                # lvl = field_data.get('level') or 'document'
                # if lvl not in ['document', 'chunk']:
                #     warnings.append(f"Invalid level '{lvl}' for field '{field_name}'. Skipping.")
                #     continue

                if field_id is not None:
                    existing = current_configs_by_id.get(field_id)
                    if existing is None or existing.knowledge_id != knowledge_id:
                        msg = f"Field ID {field_id} not found for this knowledge. Skipping."
                        warnings.append(msg)
                        logger.warning(f"skip_invalid_id | knowledge={knowledge_id} id={field_id} name={field_name} | {msg}")
                        skipped_count += 1
                        continue
                    # Immutability checks
                    if field_name != existing.field_name:
                        msg = f"Cannot change field_name for id {field_id} from '{existing.field_name}' to '{field_name}'."
                        warnings.append(msg)
                        logger.warning(f"skip_immutable_name | knowledge={knowledge_id} id={field_id} | {msg}")
                        skipped_count += 1
                        continue
                    if field_data.get('field_type') is not None and field_data.get('field_type') != existing.field_type:
                        # A type change is permitted ONLY when the caller has
                        # established that no document carries a value for this
                        # field yet (routers/enrichment_configuration_new.py
                        # ::_retype_relabelled_fields, via
                        # retrieval_ops.count_documents_with_field). Re-typing a
                        # field that already has extracted values does not
                        # re-extract them: the old-shaped values stay and then
                        # fail Tier 1 type_conformance until a re-ingest.
                        #
                        # The marker is set by that caller, never by a request —
                        # it is stripped from the request model, so a client
                        # cannot send it to bypass this.
                        if not field_data.get('_allow_field_type_change'):
                            msg = f"Cannot change field_type for '{field_name}'."
                            warnings.append(msg)
                            logger.warning(f"skip_immutable_type | knowledge={knowledge_id} id={field_id} name={field_name} | {msg}")
                            skipped_count += 1
                            continue
                        logger.info(
                            f"retype_allowed | knowledge={knowledge_id} id={field_id} name={field_name} "
                            f"{existing.field_type} -> {field_data['field_type']} (no extracted values)"
                        )
                    if field_data.get('level') is not None and field_data.get('level') != existing.level:
                        msg = f"Cannot change level for '{field_name}'."
                        warnings.append(msg)
                        logger.warning(f"skip_immutable_level | knowledge={knowledge_id} id={field_id} name={field_name} | {msg}")
                        skipped_count += 1
                        continue
                    if (
                        existing.field_schema is not None
                        and 'schema' in field_data
                        and field_data.get('schema') != existing.field_schema
                        # Same exemption as field_type above: with nothing
                        # extracted yet there is no stored data for a schema
                        # change to invalidate. Without this the re-type below
                        # would be skipped here first, for a table whose
                        # sub-fields legitimately changed with its type.
                        and not field_data.get('_allow_field_type_change')
                    ):
                        msg = f"Cannot change schema for '{field_name}'."
                        warnings.append(msg)
                        logger.warning(f"skip_immutable_schema | knowledge={knowledge_id} id={field_id} name={field_name} | {msg}")
                        skipped_count += 1
                        continue

                valid_fields.append(field_data)
                processed_field_names.add(field_name)

            # Apply changes
            for field_data in valid_fields:
                field_id = field_data.get('id')
                field_name = field_data.get('field_name')
                if field_id is not None:
                    existing = current_configs_by_id.get(field_id)
                    if not existing:
                        continue
                    # Track whether any mutable attribute value actually differs from DB
                    mutated = False
                    if 'description' in field_data and field_data['description'] is not None:
                        if field_data['description'] != existing.description:
                            existing.description = field_data['description']
                            mutated = True
                    if 'sample_values' in field_data:
                        if field_data.get('sample_values') != existing.sample_values:
                            existing.sample_values = field_data.get('sample_values')
                            mutated = True
                    if 'restrict_to_choices' in field_data and field_data['restrict_to_choices'] is not None:
                        incoming = bool(field_data['restrict_to_choices'])
                        if incoming != existing.restrict_to_choices:
                            existing.restrict_to_choices = incoming
                            mutated = True
                    if 'max_items' in field_data and field_data['max_items'] is not None:
                        if field_data['max_items'] != existing.max_items:
                            existing.max_items = field_data['max_items']
                            mutated = True
                    if 'is_required' in field_data and field_data['is_required'] is not None:
                        incoming = bool(field_data['is_required'])
                        if incoming != existing.is_required:
                            existing.is_required = incoming
                            mutated = True
                    # field_type_label is MUTABLE, unlike field_type above. It is
                    # display text a user may reword; changing it re-labels the
                    # field without touching what the worker extracts. Saved
                    # exactly as sent — never normalized back to the canonical
                    # label, or the user's edit would be silently undone.
                    if 'field_type_label' in field_data and field_data['field_type_label'] is not None:
                        if field_data['field_type_label'] != existing.field_type_label:
                            existing.field_type_label = field_data['field_type_label']
                            mutated = True
                    # Only reachable when the immutability guard above allowed it,
                    # i.e. nothing has been extracted for this field yet.
                    if field_data.get('_allow_field_type_change') and field_data.get('field_type'):
                        if field_data['field_type'] != existing.field_type:
                            existing.field_type = field_data['field_type']
                            # A re-type usually changes the shape too: a str
                            # becoming a List[dict] needs its sub-fields, and one
                            # going the other way must not keep a stale schema.
                            if 'schema' in field_data:
                                existing.field_schema = field_data.get('schema')
                            elif field_data['field_type'] not in ('dict', 'List[dict]'):
                                existing.field_schema = None
                            mutated = True
                    if 'is_active' in field_data and field_data['is_active'] is not None:
                        incoming = bool(field_data['is_active'])
                        if incoming != existing.is_active:
                            existing.is_active = incoming
                            if existing.is_active:
                                existing.deprecated_at = None
                            else:
                                existing.deprecated_at = datetime.utcnow()
                            mutated = True
                    # Schema can only be set if it's currently None (immutable once set) and field_type is dict/List[dict]
                    if 'schema' in field_data and existing.field_schema is None:
                        if existing.field_type in ['dict', 'List[dict]']:
                            existing.field_schema = field_data.get('schema')
                            mutated = True
                        else:
                            msg = f"Cannot set 'schema' for field '{field_name}' with field_type '{existing.field_type}'. Schema is only allowed for 'dict' and 'List[dict]' types."
                            warnings.append(msg)
                            logger.warning(f"skip_immutable_schema | knowledge={knowledge_id} id={field_id} name={field_name} | {msg}")

                    if mutated:
                        existing.updated_at = datetime.utcnow()
                        session.add(existing)
                        processed_config_ids.add(existing.id)
                        updated_count += 1
                        logger.info(f"update | knowledge={knowledge_id} id={field_id} name={field_name}")
                    else:
                        processed_config_ids.add(existing.id)
                        unchanged_count += 1
                        logger.info(f"unchanged | knowledge={knowledge_id} id={field_id} name={field_name}")
                else:
                    # Create new
                    if field_name:
                        # Prevent duplicates by name (current_names already filtered to non-deleted)
                        if field_name.lower() in current_names and current_names[field_name.lower()].knowledge_id == knowledge_id:
                            msg = f"Field name '{field_name}' already exists. Skipping create."
                            warnings.append(msg)
                            logger.warning(f"skip_duplicate_existing_name | knowledge={knowledge_id} name={field_name} | {msg}")
                            skipped_count += 1
                            continue
                        # Validate schema is only set for dict/List[dict] types
                        field_type = field_data.get('field_type')
                        schema_value = field_data.get('schema')
                        if schema_value is not None and field_type not in ['dict', 'List[dict]']:
                            msg = f"Cannot set 'schema' for field '{field_name}' with field_type '{field_type}'. Schema is only allowed for 'dict' and 'List[dict]' types."
                            warnings.append(msg)
                            logger.warning(f"skip_immutable_schema | knowledge={knowledge_id} name={field_name} | {msg}")
                            schema_value = None

                        new_field = FieldConfiguration(
                            knowledge_id=knowledge_id,
                            field_name=field_name,
                            description=field_data.get('description'),
                            field_type=field_type,
                            # Stored so no read has to map anything. Falls back to
                            # the canonical label when the caller sent none — a
                            # field created through a path that predates this
                            # (or a direct call) still gets a usable label rather
                            # than a NULL the response layer has to paper over.
                            field_type_label=(
                                field_data.get('field_type_label')
                                or label_for_field_type(field_type)
                            ),
                            is_required=bool(field_data.get('is_required')),
                            # Default TRUE, and the default has to live here.
                            # The POST/PATCH routers build `field_data` with
                            # `model_dump(exclude_unset=True)`, which DROPS any key the
                            # caller omitted — including the request model's own
                            # `is_active: bool = True`. So a bare `.get('is_active')`
                            # returns None for every caller that did not send the field,
                            # and `bool(None)` is False: the field was created INACTIVE
                            # while the API replied `{"created": N, "warnings": []}` and
                            # nothing ever extracted it.
                            #
                            # Both other declarations of this column already say True
                            # (FieldConfigurationRequestV2 and the FieldConfiguration
                            # table); this line was the only one disagreeing. Note the
                            # update path above guards with `'is_active' in field_data`
                            # for the same reason — absent must mean "unchanged"/"default",
                            # never False.
                            is_active=bool(field_data.get('is_active', True)),
                            is_custom=True,
                            sample_values=field_data.get('sample_values'),
                            restrict_to_choices=field_data.get('restrict_to_choices'),
                            max_items=field_data.get('max_items'),
                            field_schema=schema_value,
                            level=field_data.get('level'),
                            created_at=datetime.utcnow(),
                            # updated_at=datetime.utcnow()
                        )
                        session.add(new_field)
                        created_count += 1
                        logger.info(f"create | knowledge={knowledge_id} name={field_name} type={field_type} level={field_data.get('level')}")

            # Deletions: only if delete_missing is True (PUT behavior) - use soft delete
            if delete_missing and len(valid_fields) > 0:
                for config in current_configs_list:
                    if (config.id not in processed_config_ids and 
                        config.field_name not in processed_field_names and 
                        config.is_custom):
                        # REMOVED: extracted_metadata SQL delete — table being phased out.
                        # delete_metadata_statement = delete(ExtractedMetadata).where(
                        #     ExtractedMetadata.field_configuration_id == config.id
                        # )
                        # await session.execute(delete_metadata_statement)
                        # Soft delete instead of hard delete
                        config.deleted = True
                        config.deleted_at = datetime.utcnow()
                        config.updated_at = datetime.utcnow()
                        session.add(config)
                        deleted_count += 1

            await session.commit()

            elapsed = perf_counter() - start_time
            logger.info(
                f"Synchronized v2 field configurations for knowledge {knowledge_id} "
                f"(time taken: {elapsed:.3f}s) | "
                f"received={len(fields)} created={created_count} updated={updated_count} "
                f"unchanged={unchanged_count} deleted={deleted_count} skipped={skipped_count} "
                f"warnings={len(warnings)}"
            )
            return {
                "created": created_count,
                "updated": updated_count,
                "unchanged": unchanged_count,
                "deleted": deleted_count,
                "skipped": skipped_count,
                "warnings": warnings
            }
    except Exception as e:
        logger.error(f"Error synchronizing v2 field configurations(time taken: {perf_counter() - start_time:.3f}s): {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Check Knowledge Existence")
async def knowledge_exists_check(knowledge_id: str) -> bool:
    """Check if a knowledge exists using SQLModel."""
    try: 
        start_time = perf_counter()
        async with get_async_session() as session:
            statement = select(KnowledgeConfig).where(
                and_(
                    KnowledgeConfig.knowledge_id == knowledge_id,
                    KnowledgeConfig.deleted == False
                )
            )
            result = await session.execute(statement)
            knowledge = result.scalar_one_or_none()
            
            exists = knowledge is not None
            logger.info(f"Knowledge {knowledge_id} exists: {exists}, time taken: {perf_counter() - start_time} seconds")
            return exists
    except Exception as e:
        logger.error(f"Error checking knowledge existence(time taken: {perf_counter() - start_time} seconds): {str(e)}")
        return False


# def validate_field_configuration_sqlmodel(field: Dict[str, Any], is_update: bool = False) -> List[str]:
#     """Validate a field configuration and return list of errors."""
#     errors = []
    
#     if is_update:
#         # For updates, an ID is required
#         if not field.get('id'):
#             errors.append("'id' is required for updating fields. Updates by field name are not allowed.")
#     else:
#         # Required fields for creation
#         if not field.get('field_name'):
#             errors.append("'field_name' is required for new fields.")
#         elif not field.get('field_name').replace('_', '').isalnum():
#             errors.append("'field_name' must contain only alphanumeric characters and underscores.")
            
#         if not field.get('description'):
#             errors.append("'description' is required for new fields.")
            
#         if not field.get('field_type'):
#             errors.append("'field_type' is required for new fields.")
#         elif field.get('field_type') not in ['str', 'List[str]', 'int', 'float', 'bool', 'dict', 'List[dict]']:
#             errors.append("'field_type' must be one of: str, List[str], int, float, bool, dict, List[dict].")
    
#     # Validate field_name format for both create and update
#     if field.get('field_name'):
#         if not field.get('field_name').replace('_', '').isalnum():
#             errors.append("'field_name' must contain only alphanumeric characters and underscores.")
    
#     # Validate field_type for both create and update
#     if field.get('field_type') and field.get('field_type') not in ['str', 'List[str]', 'int', 'float', 'bool', 'dict', 'List[dict]']:
#         errors.append("'field_type' must be one of: str, List[str], int, float, bool, dict, List[dict].")
    
#     # Validate sample_values if restrict_to_choices is True
#     if field.get('restrict_to_choices') and not field.get('sample_values'):
#         errors.append("'sample_values' is required when 'restrict_to_choices' is true.")
    
#     # Validate max_items for List types
#     if field.get('field_type') in ['List[str]', 'List[dict]'] and field.get('max_items') is not None:
#         if not isinstance(field.get('max_items'), int) or field.get('max_items') < 1:
#             errors.append(f"'max_items' must be a positive integer for {field.get('field_type')} fields.")
    
#     # Validate schema if provided
#     schema = field.get('schema')
#     if schema is not None:
#         if not isinstance(schema, dict):
#             errors.append("'schema' must be a dictionary.")
#         else:
#             valid_schema_types = ['str', 'List[str]', 'int', 'float', 'bool', 'dict', 'List[dict]']
#             for key, value in schema.items():
#                 if not isinstance(key, str):
#                     errors.append("'schema' keys must be strings.")
#                 if not isinstance(value, str):
#                     errors.append(f"'schema' value for '{key}' must be a string representing a field type.")
#                 elif value not in valid_schema_types:
#                     errors.append(f"'schema' value for '{key}' must be one of: {', '.join(valid_schema_types)}.")
    
#     return errors 


def validate_field_configuration_sqlmodel_v2(field: Dict[str, Any], is_update: bool = False) -> List[str]:
    """Validate v2 field including level/is_active and allowed types. Immutability is enforced in sync step."""
    errors: List[str] = []

    if is_update and not field.get('id'):
        errors.append("'id' is required for updating fields.")

    # Required on create
    if not is_update:
        if not field.get('field_name'):
            errors.append("'field_name' is required for new fields.")
        if not field.get('field_type'):
            errors.append("'field_type' is required for new fields.")
        if not field.get('description'):
            errors.append("'description' is required for new fields.")
        lvl = field.get('level') or 'document'
        if lvl not in ['document', 'chunk']:
            errors.append("'level' must be 'document' or 'chunk'.")

    # Common validations
    if field.get('field_name') and not field.get('field_name').replace('_', '').isalnum():
        errors.append("'field_name' must contain only alphanumeric characters and underscores.")
    # Caller-facing rejection. The internal 'chunks' type is deliberately NOT
    # accepted here — the worker writes it directly and never round-trips it
    # through this API, so allowing it would only let a caller create a field
    # that shadows the chunk model. 'text' is rejected too, though it is now a
    # retired value nothing writes anymore — it simply falls outside
    # CALLER_FIELD_TYPES like any other unsupported string.
    if field.get('field_type') and field.get('field_type') not in CALLER_FIELD_TYPES:
        errors.append(
            f"'field_type' must be one of: {', '.join(sorted(CALLER_FIELD_TYPES))}."
        )
    # Caught here rather than at the DB: field_type_label is VARCHAR(64)
    # (migration 052), so a longer label would surface as a 500 from asyncpg
    # instead of a 400 naming the field.
    label = field.get('field_type_label')
    if label is not None:
        if not isinstance(label, str):
            errors.append("'field_type_label' must be a string.")
        elif len(label) > 64:
            errors.append("'field_type_label' must be 64 characters or fewer.")
    if field.get('restrict_to_choices') and not field.get('sample_values'):
        errors.append("'sample_values' is required when 'restrict_to_choices' is true.")
    if field.get('field_type') in ['List[str]', 'List[dict]'] and field.get('max_items') is not None:
        if not isinstance(field.get('max_items'), int) or field.get('max_items') < 1:
            errors.append(f"'max_items' must be a positive integer for {field.get('field_type')} fields.")
    if field.get('level') and field.get('level') not in ['document', 'chunk']:
        errors.append("'level' must be 'document' or 'chunk'.")
    
    # Validate schema if provided
    schema = field.get('schema')
    field_type = field.get('field_type')
    if schema is not None:
        # Schema is only allowed for dict and List[dict] field types
        if field_type not in ['dict', 'List[dict]']:
            errors.append(f"'schema' is only allowed for 'dict' and 'List[dict]' field types, not for '{field_type}'.")
        elif not isinstance(schema, dict):
            errors.append("'schema' must be a dictionary.")
        else:
            # 'date' belongs here as much as at the top level: a transactions
            # table's per-row Date column is the common case, and it is where an
            # unspecified date format does the most damage (a row reading
            # "16 Jun 19" cannot be range-filtered alongside one reading
            # "2019-06-16"). Mirrors basic_type_map in fs-agent-workers
            # celery_utils/enrichment/schema_builder.py.
            valid_schema_value_types = ['str', 'int', 'float', 'bool', 'List[str]', 'date']
            for key, value in schema.items():
                if not isinstance(key, str):
                    errors.append("'schema' keys must be strings.")
                if not isinstance(value, str):
                    errors.append(f"'schema' value for '{key}' must be a string representing a field type.")
                elif value not in valid_schema_value_types:
                    errors.append(f"'schema' value for '{key}' must be one of {valid_schema_value_types}, not '{value}'.")
    
    return errors
    

@retry_db_connection(max_retries=3)
@track_time("DB: Delete Field Configurations V2")
async def delete_field_configurations_sqlmodel_v2(knowledge_id: str, field_ids: List[int], delete_metadata: bool = False) -> Dict[str, Any]:
    """
    Delete field configurations by IDs.
    
    Args:
        knowledge_id: The knowledge ID
        field_ids: List of field configuration IDs to delete
        delete_metadata: If True, delete associated ExtractedMetadata records and remove values from vector DB.
                        If False, delete only field configuration (keep existing metadata in vector DB).
        
    Returns:
        Dictionary with deletion summary
    """
    try:
        async with get_async_session() as session:
            if not field_ids:
                return {
                    "deleted": 0,
                    "errors": ["field_ids list cannot be empty"]
                }
            
            # Get all fields to delete (including already deleted ones for validation)
            statement = select(FieldConfiguration).where(
                and_(
                    FieldConfiguration.knowledge_id == knowledge_id,
                    FieldConfiguration.id.in_(field_ids)
                )
            )
            result = await session.execute(statement)
            fields_to_delete = result.scalars().all()
            
            # Check if all requested fields exist
            found_ids = {field.id for field in fields_to_delete}
            missing_ids = set(field_ids) - found_ids
            if missing_ids:
                return {
                    "deleted": 0,
                    "errors": [f"Field IDs not found: {sorted(missing_ids)}"]
                }
            
            # Check if any fields are already deleted
            already_deleted = [field for field in fields_to_delete if field.deleted]
            if already_deleted:
                already_deleted_ids = [field.id for field in already_deleted]
                return {
                    "deleted": 0,
                    "errors": [f"Field IDs already deleted: {sorted(already_deleted_ids)}"]
                }

            # Check if any fields are protected system fields
            protected_fields = [field for field in fields_to_delete if field.field_name in PROTECTED_SYSTEM_FIELDS]
            if protected_fields:
                protected_field_names = [f"{field.field_name} (ID: {field.id})" for field in protected_fields]
                return {
                    "deleted": 0,
                    "errors": [f"Cannot delete protected system fields: {', '.join(protected_field_names)}"]
                }

            # Collect field names for vector DB cleanup
            field_names_to_remove = [field.field_name for field in fields_to_delete]
            
            # Remove metadata fields from vector DB if delete_metadata is True.
            # REMOVED: extracted_metadata SQL delete — table being phased out.
            # Qdrant delete_payload (below) is now the sole cleanup path.
            if delete_metadata:
                try:
                    # REMOVED: SQL delete of ExtractedMetadata rows per field config
                    # for field in fields_to_delete:
                    #     delete_metadata_statement = delete(ExtractedMetadata).where(
                    #         ExtractedMetadata.field_configuration_id == field.id
                    #     )
                    #     await session.execute(delete_metadata_statement)
                    # await session.commit()
                    await session.commit()
                    
                    # Remove metadata fields from vector DB (Qdrant)
                    try:
                        from utils.db.retrieval_ops import get_hybrid_search_value
                        from utils.files.retrieval_utils import _get_document_indexer, _get_chunk_indexer
                        
                        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
                        document_indexer = await _get_document_indexer(embedding_model)
                        chunk_indexer = await _get_chunk_indexer(embedding_model)
                        
                        # Remove fields from document collection
                        try:
                            # Scroll through all points with this knowledge_id
                            offset = None
                            while True:
                                response = await document_indexer.client_async.scroll(
                                    collection_name=document_indexer.collection_name,
                                    scroll_filter=models.Filter(
                                        must=[models.FieldCondition(
                                            key="knowledge_id",
                                            match=models.MatchValue(value=knowledge_id)
                                        )]
                                    ),
                                    limit=100,
                                    offset=offset,
                                    with_payload=True
                                )
                                
                                # Handle response format - either tuple (points, next_offset) or object
                                if isinstance(response, tuple) and len(response) > 0:
                                    points = response[0]
                                    next_offset = response[1] if len(response) > 1 else None
                                elif hasattr(response, 'points'):
                                    points = response.points
                                    next_offset = getattr(response, 'next_page_offset', None)
                                else:
                                    points = []
                                    next_offset = None
                                
                                if not points:
                                    break
                                
                                # Delete payload keys for each point
                                for point in points:
                                    if hasattr(point, 'payload') and point.payload:
                                        await document_indexer.client_async.delete_payload(
                                            collection_name=document_indexer.collection_name,
                                            keys=field_names_to_remove,
                                            points=[point.id]
                                        )
                                
                                offset = next_offset
                                if offset is None:
                                    break
                        except Exception as e:
                            logger.warning(f"Error removing metadata from document collection: {str(e)}")
                        
                        # Remove fields from chunk collection
                        try:
                            offset = None
                            while True:
                                response = await chunk_indexer.client_async.scroll(
                                    collection_name=chunk_indexer.collection_name,
                                    scroll_filter=models.Filter(
                                        must=[models.FieldCondition(
                                            key="knowledge_id",
                                            match=models.MatchValue(value=knowledge_id)
                                        )]
                                    ),
                                    limit=100,
                                    offset=offset,
                                    with_payload=True
                                )
                                
                                # Handle response format - either tuple (points, next_offset) or object
                                if isinstance(response, tuple) and len(response) > 0:
                                    points = response[0]
                                    next_offset = response[1] if len(response) > 1 else None
                                elif hasattr(response, 'points'):
                                    points = response.points
                                    next_offset = getattr(response, 'next_page_offset', None)
                                else:
                                    points = []
                                    next_offset = None
                                
                                if not points:
                                    break
                                
                                # Delete payload keys for each point
                                for point in points:
                                    if hasattr(point, 'payload') and point.payload:
                                        await chunk_indexer.client_async.delete_payload(
                                            collection_name=chunk_indexer.collection_name,
                                            keys=field_names_to_remove,
                                            points=[point.id]
                                        )
                                
                                offset = next_offset
                                if offset is None:
                                    break
                        except Exception as e:
                            logger.warning(f"Error removing metadata from chunk collection: {str(e)}")
                            
                    except Exception as e:
                        logger.warning(f"Error removing metadata from vector DB: {str(e)}")
                        # Continue with field deletion even if vector DB cleanup fails
                        
                except Exception as e:
                    logger.error(f"Error deleting associated ExtractedMetadata: {str(e)}")
                    raise
            
            # Soft delete: set deleted=True and deleted_at for provided fields in FieldConfiguration table
            for field in fields_to_delete:
                field.deleted = True
                field.deleted_at = datetime.utcnow()
                field.updated_at = datetime.utcnow()
                session.add(field)
            
            await session.commit()
            
            logger.info(f"Soft deleted {len(found_ids)} field configurations for knowledge {knowledge_id} (delete_metadata={delete_metadata})")
            return {
                "deleted": len(found_ids),
                "errors": []
            }
    except Exception as e:
        logger.error(f"Error deleting field configurations: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
async def create_enrichment_tables():
    """Create enrichment configuration tables."""
    # Note: Tables are managed by the shared SQLModel definitions
    # This function is kept for compatibility but tables are handled by the main app
    logger.info("Enrichment configuration tables are managed by shared SQLModel definitions")
