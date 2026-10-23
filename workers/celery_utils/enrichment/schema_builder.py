from httpx._transports import default
import pydantic
from pydantic import BaseModel, Field, create_model, conlist, confloat, conint
from pydantic.functional_validators import BeforeValidator

from datetime import date
from typing import Any, List, Optional, Type, Literal, get_origin, get_args, Dict
from typing_extensions import Annotated
from sqlmodel import select

from services.pydantic_models_db import FieldConfiguration, ExtractedMetadata
from celery_utils.db.db_utils import get_sync_session
from services.celery_logger import logger
from celery_utils.shared.tracing import track_time

from services.pydantic_models import ChunkMetadata# , ChunkText, ChunkWithBreakpoint


def list_to_string_validator(value: Any) -> str:
    """
    Validator function that converts lists to strings by joining elements.
    Handles cases where LLMs return lists instead of strings for string fields.
    
    Args:
        value: Input value that may be a list, string, or None
        
    Returns:
        str: String representation of the input
        - If input is a list, joins all elements with ", " separator
        - If input is already a string, returns it as-is
        - If input is None, returns None (for optional fields)
        - If input is empty list, returns empty string
    """
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return ""
        # Convert each element to string and join with ", "
        return ", ".join(str(item) for item in value)
    # If already a string or other type, convert to string
    return str(value) if value is not None else None


@track_time("DB: Get Field Configurations")
def get_field_configurations_sqlmodel(knowledge_id: str) -> List[FieldConfiguration]:
    """
    Get all enabled field configurations for a knowledge base.
    
    Args:
        knowledge_id (str): The knowledge base ID
        
    Returns:
        List[FieldConfiguration]: List of enabled field configurations
    """
    try:
        with get_sync_session() as session:
            statement = select(FieldConfiguration).where(
                FieldConfiguration.knowledge_id == knowledge_id,
                FieldConfiguration.is_active == True,
                FieldConfiguration.deleted == False,
                FieldConfiguration.level == "document"
            ).order_by(FieldConfiguration.created_at)
            
            result = session.execute(statement)
            configurations = result.scalars().all()
            logger.info(f"Retrieved {len(configurations)} enabled field configurations for knowledge base {knowledge_id}")
            return configurations
            
    except Exception as e:
        logger.error(f"Error retrieving enabled field configurations: {str(e)}")
        raise


@track_time("DB: Get Chunk Field Configurations")
def get_chunk_field_configurations_sqlmodel(knowledge_id: str) -> List[FieldConfiguration]:
    """
    Get all active chunk-level field configurations for a knowledge base.
    
    Args:
        knowledge_id (str): The knowledge base ID
        
    Returns:
        List[FieldConfiguration]: List of active chunk-level field configurations
    """
    try:
        with get_sync_session() as session:
            statement = select(FieldConfiguration).where(
                FieldConfiguration.knowledge_id == knowledge_id,
                FieldConfiguration.is_active == True,
                FieldConfiguration.deleted == False,
                FieldConfiguration.level == "chunk"
            ).order_by(FieldConfiguration.created_at)
            
            result = session.execute(statement)
            configurations = result.scalars().all()
            
            logger.info(f"Retrieved {len(configurations)} active chunk-level field configurations for knowledge base {knowledge_id}")
            return configurations
            
    except Exception as e:
        logger.error(f"Error retrieving chunk-level field configurations: {str(e)}")
        raise


@track_time("Node: Create Nested Model From Schema")
def create_nested_model_from_schema(schema: Dict[str, str], model_name: str) -> Type[pydantic.BaseModel]:
    """
    Create a Pydantic model from a schema dictionary.
    
    Args:
        schema: Dictionary mapping field names to field types (e.g., {"name": "str", "age": "int"})
        model_name: Name for the created Pydantic model class
        
    Returns:
        A Pydantic BaseModel class with fields defined by the schema
        
    Note:
        Supports str, int, float, bool, and List[str]. Does NOT support nested dict or List[dict].
    """ 
    logger.debug(f"Creating nested model named {model_name} from schema: {schema}")
    fields_definitions = {}
    basic_type_map = {
        'str': str,
        'int': int,
        'float': float,
        'bool': bool,
        'List[str]': list[str],
        # A real date, not a string. This is the point of the type: pydantic puts
        # `format: date` in the JSON schema handed to the extraction model, which
        # is what forces ONE format out of it. A bank statement's transactions
        # table printing "16 Jun 19" per row is the case this fixes — those rows
        # are unfilterable until they normalize to 2019-06-16.
        'date': date,
    }

    for field_name, field_type_str in schema.items():
        field_type: Any = None

        if field_type_str == "List[str]":
            field_type = basic_type_map[field_type_str]
            fields_definitions[field_name] = (Optional[field_type], pydantic.Field(default=None, description="A list of strings like '[value1, value2, value3]'"))

        elif field_type_str == "str":
            # BeforeValidator coerces LLM list responses into a single string
            field_type = Annotated[str, BeforeValidator(list_to_string_validator)]
            fields_definitions[field_name] = (Optional[field_type], pydantic.Field(default=None, description="A single string like 'value1'"))

        elif field_type_str in basic_type_map:
            field_type = basic_type_map[field_type_str]
            fields_definitions[field_name] = (Optional[field_type], pydantic.Field(default=None))

        else:
            logger.info(f"Unsupported field type '{field_type_str}' for nested field '{field_name}'. Skipping.")
            continue
        
    # Create the model dynamically
    NestedModel = pydantic.create_model(model_name, **fields_definitions)
    return NestedModel


@track_time("Node: Create Dynamic Pydantic Model")
def create_dynamic_pydantic_model(model_name: str, chunking_params: Dict[str, Any], configs: List[FieldConfiguration], chunk_type: Optional[BaseModel] = None) -> Type[pydantic.BaseModel]:
    """
    Dynamically creates a Pydantic model from a list of FieldConfiguration objects.

    Args:
        model_name: The name for the new Pydantic model class.
        chunking_params: Dictionary containing chunking parameters like enable_chunking and chunk_size.
        configs: A list of FieldConfiguration objects.
        chunk_type: Optional chunk model type to use for chunks field.

    Returns:
        A new Pydantic BaseModel class.
    """
    try :    
        fields_definitions = {}
        type_map = {
            'str': str,
            'int': int,
            'float': float,
            'bool': bool,
            'List[str]': list[str],
            'date': date,  # see basic_type_map above
            'dict': dict[str, Any],  # Fallback if no schema provided
            'List[dict]': list[dict[str, Any]],  # Fallback if no schema provided
            'chunks': list[chunk_type]
        }
        for config in configs:
            # We only add active fields to the model
            if not config.is_active:
                continue

            field_type_str = config.field_type
            field_name = config.field_name
            description = config.description or ""  # DB field is nullable; guard all downstream += concatenations
            example_values = list(config.sample_values) if config.sample_values else None  # copy — never mutate the SQLModel instance
            logger.debug(f"Processing field: {field_name} with type: {field_type_str}")
            
            # Handle dict type
            if config.field_type in ['dict', 'List[dict]']:
                if config.schema:
                    # Create nested model from schema
                    nested_model_name = f"{field_name}_Nested"
                    nested_model = create_nested_model_from_schema(config.schema, nested_model_name)
                    if config.field_type == 'List[dict]':
                        field_type = list[nested_model]
                    else:
                        field_type = nested_model
                    logger.debug(f"Created nested model for field {field_name}, field_type: {nested_model.model_json_schema()}")
                else:
                    # Use generic Dict[str, Any] if no schema provided
                    logger.debug(f"Using generic Dict[str, Any] for field {field_name}")
                    field_type = type_map[field_type_str]
            
            elif config.field_type == 'List[str]':
                item_type: Any = str

                # Purpose: For List[str] fields, add a description of the format of the strings and sample values
                description += "| Format: List of strings like '[value1, value2, value3]'" 
                if not example_values:
                    example_values = ["value1", "value2", "value3"]

                # Check if the list items themselves should be restricted
                if config.restrict_to_choices and config.sample_values:
                    item_type = Literal[tuple(config.sample_values)]

                # If max_items is specified, create a constrained list type that truncates
                if config.max_items is not None:
                    def truncator_factory(max_len: int):
                        def truncate(v) -> list:
                            if isinstance(v, list):
                                return v[:max_len]
                            return v
                        return truncate
                    
                    field_type = Annotated[list[item_type], BeforeValidator(truncator_factory(config.max_items))]
                else:
                    # Otherwise, it's a regular list of the item type
                    field_type = list[item_type]
            
            # Apply validator to string fields to handle cases where lists are provided instead of strings
            elif config.field_type == 'str':
                
                if field_name == 'chunk_text':
                    # For chunk_text field, add a description of the chunk size
                    description += f". No metadata, tags, or explanations — pure text only. Target length exactly {chunking_params.get('chunk_size')} characters. Can't change the original content. format the text on your own."
            
                if config.max_items:
                    description += f"| Max items: {config.max_items}"
                            
                # If restrict_to_choices is set, apply it after the validator
                if config.restrict_to_choices and config.sample_values:
                    literal_values = tuple(config.sample_values)
                    if literal_values:
                        # The validator converts lists to strings, then Literal validates the string value
                        field_type = Annotated[Literal[literal_values], BeforeValidator(list_to_string_validator)]
                    else:
                        field_type = Annotated[str, BeforeValidator(list_to_string_validator)]
                else:
                    field_type = Annotated[str, BeforeValidator(list_to_string_validator)]
            
            elif config.field_type == 'date':
                # Tell the model what to do with what documents actually print —
                # "16 Jun 19", "May 2024", "05/24" — rather than leaving it to
                # guess. pydantic enforces the shape; this decides the two cases
                # pydantic cannot: an absent date and a partial one.
                description += (
                    ". Return a single calendar date in ISO-8601 format (YYYY-MM-DD). "
                    "Convert whatever format the document uses. If the document gives "
                    "only a month and year, use the first day of that month. If the "
                    "document does not state this date, return null — never infer or "
                    "calculate it from other dates."
                )
                if not example_values:
                    example_values = ["2024-05-01"]
                # restrict_to_choices is deliberately NOT applied: sample_values are
                # strings, and a Literal of strings against a `date` type rejects
                # every value the model returns. A closed set of dates is also not a
                # thing anyone means — for dates, the range filter is the constraint.
                if config.restrict_to_choices:
                    logger.info(
                        f"Ignoring restrict_to_choices on date field '{field_name}' — "
                        f"not applicable to dates; use a range filter at query time."
                    )
                field_type = date

            # Determine the Python type for other field types
            else:
                field_type: Any = type_map.get(field_type_str)
                if field_type is None:
                    logger.info(f"Warning: Unsupported field type '{field_type_str}' for field '{field_name}'. Skipping.")
                    continue
                
                # Handle constraints for other non-list types (bool, int, float)
                if config.restrict_to_choices and config.sample_values:
                    # field_type: Any = type_map.get(field_type_str)
                    if config.field_type == 'bool':
                        literal_values = tuple(s.lower() == 'true' for s in config.sample_values)
                    else:
                        literal_values = tuple(config.sample_values)
                    if literal_values:
                        field_type = Literal[literal_values]

            # Make fields optional or required based on is_required flag
            # Use is_required from database configuration
            field_dict = {}
            field_dict["description"] = description
            if config.is_required:
                default_value = None
                default_factory = None
                max_items = None
                min_items = None
                    
                
                if config.is_custom:
                    # Nullable defaults — missing LLM output stays detectably None,
                    # not a fake placeholder. The prompt still marks fields as required;
                    # the schema accepts None so retry logic can detect truly missing data.
                    field_dict["default"] = None
                    field_type = Optional[field_type]

                    if example_values:
                        field_dict["examples"] = example_values

                    pydantic_field = pydantic.Field(**field_dict)
                else:
                    # For system fields the fields are required by default
                    if not chunking_params.get('enable_chunking') and config.field_type == 'chunks':
                        max_items = 1
                        min_items = 1
                        field_dict["max_items"] = max_items
                        field_dict["min_items"] = min_items
                        if example_values:
                            field_dict["examples"] = example_values

                    pydantic_field = pydantic.Field(**field_dict)

                logger.debug(f"Required field type: {field_type}")
            else:
                # Optional fields with appropriate defaults
                if config.field_type == 'List[str]':
                    default_value = []
                    field_dict["default"] = default_value
                elif config.field_type == 'List[dict]':
                    default_value = []
                    field_dict["default"] = default_value
                elif config.field_type == 'dict':
                    default_value = None  # Use None for optional dict
                    field_dict["default"] = default_value
                elif config.field_type == 'bool':
                    default_value = None  # None, not False — False is a meaningful LLM output
                    field_dict["default"] = default_value
                elif config.field_type in ['int', 'float']:
                    default_value = None
                    field_dict["default"] = default_value
                else:  # str and other types
                    default_value = None
                    field_dict["default"] = default_value
                # Make the field type optional
                field_type = Optional[field_type]
                pydantic_field = pydantic.Field(**field_dict)
                logger.debug(f"Optional field type: {field_type}")
            
            fields_definitions[field_name] = (field_type, pydantic_field)
        
        # Use pydantic.create_model to build the class dynamically
        DynamicModel = pydantic.create_model(model_name, **fields_definitions)
        
        return DynamicModel

    except Exception as e:
        logger.error(f"Error creating dynamic Pydantic model: {str(e)}")
        raise

@track_time("Node: Create Deterministic Chunking Pydantic Model")
def create_deterministic_chunking_pydantic_model(
    knowledge_id: str,
    doc_configs: List[FieldConfiguration],
    chunk_configs: List[FieldConfiguration],
) -> Type[pydantic.BaseModel]:
    """
    Build the output schema for Strategy 2.

    The LLM receives pre-split chunks and must return:
      - All document-level fields (same as Strategy 6 doc model)
      - chunks_metadata: List[ChunkMetaEntry] where each entry has
          chunk_idx: int  (for alignment only — canonical index comes from enumerate)
          + all active chunk-level fields (NO chunk_text)

    chunk_text is deliberately excluded from the chunk meta entry schema so the LLM
    never rewrites splitter output.

    Args:
        knowledge_id: Used only for logging context.
        doc_configs: Active document-level FieldConfiguration objects.
        chunk_configs: Active chunk-level FieldConfiguration objects (chunk_text excluded inside).

    Returns:
        Dynamic Pydantic model class with doc fields + chunks_metadata list.
    """
    try:
        # Build doc-level fields (reuse existing builder with empty chunking_params)
        doc_model = create_dynamic_pydantic_model(
            "DeterministicDocModel",
            {"enable_chunking": True, "chunk_size": None},
            doc_configs,
        )
        doc_fields: dict = dict(doc_model.model_fields)

        # Build chunk metadata entry: chunk_idx + chunk fields (skip chunk_text)
        chunk_entry_fields: dict = {
            "chunk_idx": (
                int,
                pydantic.Field(description="Index of this chunk matching the input chunk_idx. Used for alignment only."),
            )
        }

        # Filter out chunk_text — LLM must never produce it in Strategy 2
        filtered_chunk_configs = [c for c in chunk_configs if c.field_name != "chunk_text"]

        if filtered_chunk_configs:
            chunk_entry_model_base = create_dynamic_pydantic_model(
                "DeterministicChunkEntryBase",
                {"enable_chunking": True, "chunk_size": None},
                filtered_chunk_configs,
            )
            for fname, finfo in chunk_entry_model_base.model_fields.items():
                # Use FieldInfo.annotation (Pydantic v2) — reliable for Annotated types
                chunk_entry_fields[fname] = (finfo.annotation, finfo)

        ChunkMetaEntry = pydantic.create_model("ChunkMetaEntry", **chunk_entry_fields)
        logger.debug(f"ChunkMetaEntry schema for knowledge_id={knowledge_id}: {ChunkMetaEntry.model_json_schema()}")

        # Combine: doc fields + chunks_metadata list
        combined_fields: dict = {k: (v.annotation, v) for k, v in doc_fields.items()}
        combined_fields["chunks_metadata"] = (
            List[ChunkMetaEntry],
            pydantic.Field(
                description=(
                    "One metadata entry per input chunk. "
                    "Count MUST equal the number of input chunks. "
                    "Do NOT include chunk_text."
                )
            ),
        )

        DeterministicOutputModel = pydantic.create_model("DeterministicOutputModel", **combined_fields)
        logger.info(
            f"Created DeterministicOutputModel for knowledge_id={knowledge_id}: "
            f"{len(doc_fields)} doc fields + {len(chunk_entry_fields)} chunk meta fields"
        )
        return DeterministicOutputModel

    except Exception as e:
        logger.error(f"Error creating deterministic chunking Pydantic model for knowledge_id={knowledge_id}: {e}")
        raise


# REMOVED: save_enriched_metadata_to_db — extracted_metadata table being phased out.
# All enriched field values are already written to Qdrant payload at index time;
# this SQL write was redundant for Qdrant-native reads and is now a no-op.
# Call sites in enrichment_utils.py:584 and enrichment_utils.py:902 are also commented out.
#
# @track_time("DB: Save Enriched Metadata")
# def save_enriched_metadata_to_db(
#     object_id: str,
#     knowledge_id: str,
#     field_configurations: List[FieldConfiguration],
#     enriched_metadata: BaseModel
# ) -> bool:
#     try:
#         if enriched_metadata is None:
#             logger.info(f"enriched_metadata is None for object_id: {object_id}. Skipping save.")
#             return False
#         with get_sync_session() as session:
#             field_name_to_id = {config.field_name: config.id for config in field_configurations}
#             for field_name in enriched_metadata.model_fields:
#                 if field_name == 'chunks':
#                     continue
#                 if field_name in field_name_to_id:
#                     extracted_value = getattr(enriched_metadata, field_name)
#                     if extracted_value is None:
#                         continue
#                     if isinstance(extracted_value, list) and not extracted_value:
#                         continue
#                     if isinstance(extracted_value, dict) and not extracted_value:
#                         continue
#                     if isinstance(extracted_value, BaseModel):
#                         extracted_value = extracted_value.model_dump()
#                     elif isinstance(extracted_value, list):
#                         serialized_list = []
#                         for item in extracted_value:
#                             serialized_list.append(item.model_dump() if isinstance(item, BaseModel) else item)
#                         extracted_value = serialized_list
#                     session.add(ExtractedMetadata(
#                         object_id=object_id,
#                         knowledge_id=knowledge_id,
#                         field_configuration_id=field_name_to_id[field_name],
#                         extracted_content=extracted_value
#                     ))
#             session.commit()
#             logger.info(f"Successfully saved enriched metadata for document {object_id} to database")
#             return True
#     except Exception as e:
#         logger.error(f"Error saving enriched metadata to database: {str(e)}")
#         raise

# # Enhanced function that handles both document metadata and chunks
# def index_in_vectordb(
#     document_id: str,
#     chatbot_id: str,
#     user_id: str,
#     enriched_metadata: Union[BaseModel, Dict[str, Any]],
#     embedding_model: str = "paraphrase-multilingual-mpnet-base-v2"
# ) -> bool:
#     """
#     Index enriched metadata and chunks in vector database.
    
#     Args:
#         document_id (str): Unique identifier for the document
#         chatbot_id (str): The chatbot ID
#         user_id (str): The user ID
#         enriched_metadata (Union[BaseModel, Dict[str, Any]]): The enriched metadata from LLM containing document metadata and chunks
#         embedding_model (str): The embedding model to use for indexing
        
#     Returns:
#         bool: True if successfully indexed
#     """
#     try:
#         logger.info(f"Starting vector indexing for enriched metadata and chunks of document {document_id}")
        
#         # Initialize the indexers
#         document_indexer = MultiQdrantDocumentIndexer(embedding_model)
#         document_indexer.initialize_qdrant()
        
#         chunk_indexer = MultiQdrantChunkIndexer(embedding_model)
#         chunk_indexer.initialize_qdrant()
        
#         # Handle both BaseModel and dictionary inputs
#         if isinstance(enriched_metadata, BaseModel):
#             logger.info(f"Enriched metadata is a Pydantic model")
#             summary = getattr(enriched_metadata, 'summary', '')
#             metadata_dict = enriched_metadata.model_dump()
#         else:
#             logger.info(f"Enriched metadata is a dictionary")
#             summary = enriched_metadata.get('summary', '')
#             metadata_dict = enriched_metadata
        
#         logger.info(f"Using summary for vector indexing: {str(summary)[:200]}...")
        
#         # Prepare document payload by combining system fields with metadata
#         document_payload = {
#             "document_id": document_id,
#             "chatbot_id": chatbot_id,
#             "user_id": user_id,
#             "created_at": time.time(),
#             **{k: v for k, v in metadata_dict.items() if k != 'chunks' and v is not None}  # Exclude chunks and None values from document payload
#         }
        
#         logger.info(f"Document payload: {document_payload}")
#         # Index document in vector database
#         document_indexer.index_document_in_qdrantdb(
#             knowledge_id=document_id,
#             document_payload=document_payload,
#             summary=str(summary)
#         )
        
#         logger.info(f"Successfully indexed enriched document metadata for document {document_id} in vector database")
        
#         # Index chunks if they exist
#         chunks = metadata_dict.get('chunks', [])
#         if chunks:
#             logger.info(f"Indexing {len(chunks)} chunks for document {document_id}")
            
#             chunk_records = []
#             prepended_doc_summary = metadata_dict.get('prepended_doc_summary', '')
            
#             for idx, chunk in enumerate(chunks):
#                 chunk_id = str(uuid.uuid4())
                
#                 # Get chunk text based on chunk type (handle both dict and object)
#                 if isinstance(chunk, dict):
#                     chunk_text = chunk.get('chunk_text', '')
#                 else:
#                     chunk_text = getattr(chunk, 'chunk_text', '')
                
#                 if not chunk_text:
#                     logger.warning(f"Chunk {idx} for document {document_id} missing chunk_text field")
#                     continue
                
#                 # Prepare chunk text for embedding (with prepended summary if available)
#                 chunk_text_for_embedding = f"{prepended_doc_summary}{chunk_text}" if prepended_doc_summary else chunk_text
                
#                 # Build base chunk payload
#                 chunk_payload = {
#                     "document_id": document_id,
#                     "chunk_text": chunk_text,
#                     "chatbot_id": chatbot_id,
#                     "user_id": user_id,
#                 }
                
#                 # Add chunk-specific metadata only if it exists in the chunk
#                 # For ChunkMetadata type (Strategy 1)
#                 if isinstance(chunk, dict):
#                     if 'chunk_keywords' in chunk:
#                         chunk_payload["chunk_keywords"] = chunk.get('chunk_keywords', [])
#                     if 'chunk_topic' in chunk:
#                         chunk_payload["chunk_topic"] = chunk.get('chunk_topic', "Unknown")
#                     if 'chunk_entities' in chunk:
#                         chunk_payload["chunk_entities"] = chunk.get('chunk_entities', [])
#                     if 'chunk_idx' in chunk:
#                         chunk_payload["chunk_idx"] = chunk.get('chunk_idx', idx)
#                     if 'start_idx' in chunk:
#                         chunk_payload["start_idx"] = chunk.get('start_idx')
#                     if 'end_idx' in chunk:
#                         chunk_payload["end_idx"] = chunk.get('end_idx')
                    
                        
#                 else:
#                     if hasattr(chunk, 'chunk_keywords'):
#                         chunk_payload["chunk_keywords"] = getattr(chunk, 'chunk_keywords', [])
#                     if hasattr(chunk, 'chunk_topic'):
#                         chunk_payload["chunk_topic"] = getattr(chunk, 'chunk_topic', "Unknown")
#                     if hasattr(chunk, 'chunk_entities'):
#                         chunk_payload["chunk_entities"] = getattr(chunk, 'chunk_entities', [])
#                     if hasattr(chunk, 'chunk_idx'):
#                         chunk_payload["chunk_idx"] = getattr(chunk, 'chunk_idx', idx)
#                     if hasattr(chunk, 'start_idx'):
#                         chunk_payload["start_idx"] = getattr(chunk, 'start_idx')
#                     if hasattr(chunk, 'end_idx'):
#                         chunk_payload["end_idx"] = getattr(chunk, 'end_idx')
                    
                
#                 # Add file_name to chunk payload if it exists in document metadata
#                 if 'file_name' in metadata_dict:
#                     chunk_payload["file_name"] = metadata_dict['file_name']
                
#                 # Add prepended doc summary if available
#                 if prepended_doc_summary:
#                     chunk_payload["prepended_doc_summary"] = prepended_doc_summary
                
#                 chunk_records.append({
#                     "id": chunk_id,
#                     "embedded_text": chunk_text_for_embedding,
#                     "payload": chunk_payload
#                 })
            
#             # Index all chunks
#             if chunk_records:
#                 chunk_indexer.index_chunk_in_qdrantdb(document_id, chunk_records)
#                 logger.info(f"Successfully indexed {len(chunk_records)} chunks for document {document_id} in vector database")
#             else:
#                 logger.warning(f"No valid chunks to index for document {document_id}")
#         else:
#             logger.info(f"No chunks found for document {document_id}, skipping chunk indexing")
        
#         logger.info(f"Successfully completed indexing for document {document_id} with both metadata and chunks")
#         return True
        
#     except Exception as e:
#         logger.error(f"Error indexing enriched metadata and chunks in vector database: {str(e)}")
#         raise


# async def enrich_document_dynamic(document_id: str, chatbot_id: str, document_text: str) -> BaseModel:
#     """
#     Enrich a document with metadata using dynamically configured fields and save to database.
    
#     Args:
#         document_id (str): Unique identifier for the document
#         chatbot_id (str): The chatbot ID to get field configurations for
#         document_text (str): The text content of the document to enrich
        
#     Returns:
#         BaseModel: Pydantic model containing the extracted metadata
#     """
#     try:
#         logger.info(f"Starting dynamic document enrichment for document {document_id}, chatbot {chatbot_id}")
        
#         # Get enabled field configurations
#         field_configurations = get_field_configurations_sqlmodel(chatbot_id)
#         logger.info(f"Field configurations: {field_configurations}")
        
#         # Create dynamic output model and prompt
#         DynamicOutputModel = create_dynamic_pydantic_model("DynamicOutputModel", field_configurations)
#         enrichment_prompt = create_enrichment_prompt()
        
#         # Create structured LLM output based on dynamic model
#         structured_llm = model_enrichment.with_structured_output(DynamicOutputModel)
        
#         # Extract metadata
#         logger.info(f"Calling LLM for metadata extraction with {len(field_configurations)} fields")
#         response = await structured_llm.ainvoke([
#             {"role": "system", "content": enrichment_prompt},
#             {"role": "user", "content": f"Document to analyze:\n\n{document_text}"}
#         ])
        
#         # Save enriched metadata to database
#         logger.info(f"Saving enriched metadata to database for document {document_id}")
#         save_enriched_metadata_to_db(document_id, chatbot_id, field_configurations, response)
        
#         logger.info(f"Successfully enriched document {document_id} with dynamic metadata and saved to database")
#         return response
        
#     except Exception as e:
#         logger.error(f"Error in dynamic document enrichment: {str(e)}")
#         raise



# Field type mapping removed - using exact values from database

# def create_enrichment_prompt() -> str:
#     """
#     Create a dynamic enrichment prompt using database field configurations.
#     Formats each field with name, description, type, restrictions, and constraints.
#     """
#     # Map FieldType enum values to readable types for the prompt
    
#     # Base prompt structure
#     base_prompt = """
#     You are an expert in document processing and metadata extraction."""
#     # Add extraction guidelines
#     extraction_guidelines = """

#     **Document Metadata Extraction Guidelines:**
#     1. Extract the most relevant information from the document for each field
#     2. Follow any specified restrictions on values or limits
#     3. For fields with provided examples, use similar formatting and style
#     4. For fields with restricted choices, only use the specified options
#     5. Keep the output concise and focused on the document's content
#     6. If a field specifies a maxItems constraint, generate no more than that number of values for that field.
#     7. If information for a field is not available in the document, provide a reasonable default or leave it appropriately blank
    
#     **Output Format:**
#     Return the extracted metadata in a structured format that matches the field definitions and types provided.
#     """
    
#     return base_prompt + extraction_guidelines


# def create_dynamic_output_model(field_configurations: List[FieldConfiguration]) -> Type[BaseModel]:
#     """
#     Create a dynamic Pydantic model based on field configurations.
    
#     Args:
#         field_configurations (List[FieldConfiguration]): List of field configurations
        
#     Returns:
#         Type[BaseModel]: Dynamically created Pydantic model for metadata extraction
#     """
#     model_fields = {}
#     for config in field_configurations:
#         # Convert string type to actual Python type
#         if config.field_type == "str":
#             field_type = str
#         elif config.field_type == "List[str]":
#             from typing import List
#             field_type = List[str]
#         elif config.field_type == "int":
#             field_type = int
#         elif config.field_type == "float":
#             field_type = float
#         elif config.field_type == "bool":
#             field_type = bool
#         else:
#             field_type = str  # fallback
        
#         # Build Field kwargs with database values
#         field_kwargs = {"description": config.description} 

        
#         # Only add list-specific constraints if the field type is a list
#         if config.field_type == "List[str]" and config.max_items is not None:
#             field_kwargs["max_items"] = config.max_items
            
#         # Add other constraints if they exist
#         if config.sample_values is not None:
#             field_kwargs["sample_values"] = config.sample_values
#         if config.restrict_to_choices is not None:
#             field_kwargs["restrict_to_choices"] = config.restrict_to_choices
        
#         # Create field definition based on system lock status
#         if config.is_system_locked:
#             model_fields[config.field_name] = (field_type, Field(**field_kwargs))
#         else:
#             model_fields[config.field_name] = (Optional[field_type], Field(default=None, **field_kwargs))
    
#     return create_model('DynamicDocumentMetadata', **model_fields)