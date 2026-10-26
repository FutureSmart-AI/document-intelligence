from typing import Optional, Union
from utils import setup_env
from utils.logger import logger
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import OpenAIEmbeddings
from datetime import datetime, timezone
import os

from utils.db.knowledge_setting_ops import get_knowledge_config
from utils.usage.credentials import get_credential_for_knowledge
from utils.models.embedding_models import EMBEDDING_MODELS


import utils.setup_env as setup_env
USAGE_TRACKING_BASE_URL = setup_env.USAGE_TRACKING_BASE_URL
FASTAPI_SECRET_KEY = setup_env.FASTAPI_SECRET_KEY


def _get_embedding_provider_and_model(embedding_model: str) -> tuple:
    """
    Get provider and model name from embedding model identifier.
    
    Args:
        embedding_model: Embedding model identifier
        
    Returns:
        Tuple of (provider, model_name)
    """
    model_config = EMBEDDING_MODELS.get(embedding_model)
    if not model_config:
        return "huggingface", embedding_model
    
    if model_config["type"] == "openai":
        return "openai", model_config["model_name"]
    elif model_config["type"] == "sentence_transformer":
        return "huggingface", model_config["model_name"]
    else:
        return "huggingface", embedding_model


import tiktoken

enc = tiktoken.get_encoding("cl100k_base")  # For text-embedding-3-* or ada-002

def _estimate_tokens(text: str) -> int:
    """
    Estimate token count for OpenAI embeddings.
    Uses approximation: ~4 characters per token for English text.

    Args:
        text: Text to estimate tokens for

    Returns:
        Estimated token count
    """
    # Single text
    token_count = len(enc.encode(text))
    print("token_count for embedding(using tiktoken): ", token_count)

    return token_count


def calculate_embedding_metrics(
    text: str,
    embedding_model: str,
    embedding_function: Optional[Union[HuggingFaceEmbeddings, OpenAIEmbeddings]] = None
) -> tuple[int, int]:
    """
    Calculate embedding metrics (tokens and char count) without sending events.
    
    Args:
        text: Text that was embedded
        embedding_model: Embedding model identifier
        embedding_function: Optional embedding function instance (for accurate token counting)
        
    Returns:
        Tuple of (embedding_tokens, embedding_char_count)
    """
    try:
        # Calculate metrics
        embedding_char_count = len(text)
        
        # Get provider for token calculation logic
        provider, _ = _get_embedding_provider_and_model(embedding_model)
        
        # Calculate tokens based on model type
        if provider == "huggingface" and embedding_function is not None:
            # For sentence transformer models, use the actual tokenizer
            try:
                # Access the underlying SentenceTransformer model
                if isinstance(embedding_function, HuggingFaceEmbeddings):
                    # HuggingFaceEmbeddings has a 'model' attribute that contains the SentenceTransformer
                    if hasattr(embedding_function, 'model') and hasattr(embedding_function.model, 'tokenizer'):
                        model = embedding_function.model
                    else:
                        # Fallback: create a new SentenceTransformer instance. Imported here
                        # rather than at module level — it's ~1.9s and 1300+ modules on top of
                        # what langchain_huggingface already pulls in, for a branch this rarely
                        # reaches (HuggingFaceEmbeddings normally already exposes .model.tokenizer).
                        model_config = EMBEDDING_MODELS.get(embedding_model)
                        if model_config and model_config["type"] == "sentence_transformer":
                            from sentence_transformers import SentenceTransformer
                            model = SentenceTransformer(model_config["model_name"])
                        else:
                            raise ValueError(f"Cannot access tokenizer for model {embedding_model}")
                    
                    # Calculate tokens using tokenizer (after truncation if needed)
                    encoded = model.tokenizer(text, truncation=True, max_length=model.max_seq_length, return_tensors="pt")
                    embedding_tokens = encoded.input_ids.shape[1]  # Includes [CLS] and [SEP]
                else:
                    # Fallback to estimation if not a HuggingFaceEmbeddings instance
                    embedding_tokens = _estimate_tokens(text)
            except Exception as e:
                logger.warning(f"Error calculating tokens with tokenizer for {embedding_model}: {e}. Falling back to estimation.")
                embedding_tokens = _estimate_tokens(text)
        else:
            # For OpenAI models or when embedding_function is not provided, use tiktoken estimation
            embedding_tokens = _estimate_tokens(text)
        
        return embedding_tokens, embedding_char_count
    except Exception as e:
        logger.error(f"Error calculating embedding metrics: {e}")
        # Return safe defaults
        return 0, len(text)
    

async def get_owner_id_from_service_id(service_id: Optional[str]) -> Optional[str]:
    """
    Get owner_id from service_id by querying the knowledge_config table.

    Args:
        service_id (Optional[str]): The service_id (knowledge_id) to look up

    Returns:
        Optional[str]: The owner_id if found, None otherwise
    """
    if not service_id:
        return None

    try:
        knowledge_config = await get_knowledge_config(service_id)
        if knowledge_config:
            return knowledge_config.get("owner_id")
        return None
    except Exception as e:
        logger.error(f"Error getting owner_id from service_id {service_id}: {e}")
        return None


async def post_processing_event(payload: dict) -> None:
    """Record a usage event in-process (docs/plan/usage-and-limits/).

    Was previously an outbound HTTP POST to the standalone fs-agent-usage-limits
    service's /v1/events; now a direct call into utils/usage/event_recording,
    which lives in this repo. Fire-and-forget: record_single_event already
    logs and swallows its own failures, so a usage-tracking hiccup never
    breaks the embedding/LLM call it's tracking.

    payload is the same {"events": [event_dict]} shape the old HTTP body used,
    with event_dict's "service_id" doubling as knowledge_id (see
    _build_embedding_event / _build_llm_usage_event above).
    """
    from utils.usage.event_recording import record_single_event

    events = payload.get("events") or []
    for event in events:
        metrics = [(m["name"], m["value"]) for m in event.get("metrics") or []]
        await record_single_event(
            knowledge_id=event.get("service_id"),
            service_type=event.get("service_type", "knowledge"),
            operation=event.get("operation"),
            label=event.get("label"),
            owner_id=event.get("owner_id"),
            key_type=event.get("key_type"),
            session_id=event.get("session_id"),
            request_id=event.get("request_id"),
            metadata=event.get("metadata"),
            metrics=metrics,
        )


def _build_embedding_event(
    provider: str,
    model: str,
    embedding_tokens: int,
    char_count: int,
    duration_ms: int,
    owner_id: Optional[str],
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
    key_type: Optional[str] = None
) -> dict:
    """
    Build embedding event payload.

    Args:
        provider (str): Embedding provider ("openai" or "huggingface")
        model (str): Model name (e.g., "text-embedding-3-large", "paraphrase-multilingual-mpnet-base-v2")
        embedding_tokens (int): Total tokens used for embeddings
        char_count (int): Total characters that were embedded
        duration_ms (int): Time taken to process in milliseconds
        owner_id (Optional[str]): Owner ID for the service
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        object_id (Optional[str]): Object ID (request_id)

    Returns:
        dict: Event payload
    """
    return {
        "service_id": knowledge_id,
        "owner_id": owner_id,
        "request_id": object_id,
        "key_type": key_type,
        "service_type": "knowledge",
        "operation": "embedding_generation",
        "label": "query_embedding",
        "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "metadata": {
            "provider": provider,
            "model": model
        },
        "metrics": [
            {"name": "duration_ms", "value": duration_ms},
            {"name": "input_tokens", "value": embedding_tokens},
            {"name": "char_count", "value": char_count}
        ]
    }


async def send_embedding_event(
    provider: str,
    model: str,
    embedding_tokens: int,
    char_count: int,
    duration_ms: int,
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
    key_type: Optional[str] = None
):
    """
    Send embedding generation event to the usage events API.

    Args:
        provider (str): Embedding provider ("openai" or "huggingface")
        model (str): Model name (e.g., "text-embedding-3-large", "paraphrase-multilingual-mpnet-base-v2")
        embedding_tokens (int): Total tokens used for embeddings
        char_count (int): Total characters that were embedded
        duration_ms (int): Time taken to process in milliseconds
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        object_id (Optional[str]): Object ID (request_id)
        key_type (Optional[str]): Pre-resolved key type ("owner-key" or "platform-key"). Resolved automatically if not provided.
    """
    # The chat turn's usage block counts this request too (no-op outside a turn).
    try:
        from chat.run_usage import record_embedding
        record_embedding(provider, model, embedding_tokens, duration_ms)
    except Exception:
        pass
    try:
        owner_id = await get_owner_id_from_service_id(knowledge_id)
        if not key_type:
            _, key_type = await get_credential_for_knowledge(knowledge_id, provider)

        event = _build_embedding_event(
            provider=provider,
            model=model,
            embedding_tokens=embedding_tokens,
            char_count=char_count,
            duration_ms=duration_ms,
            owner_id=owner_id,
            knowledge_id=knowledge_id,
            object_id=object_id,
            key_type=key_type
        )

        payload = {"events": [event]}
        logger.info(f"Sending embedding event: service_id={knowledge_id}, provider={provider}, model={model}, embedding_tokens={embedding_tokens}, key_type={key_type}")
        await post_processing_event(payload)

    except Exception as e:
        logger.error(f"Error sending embedding event: {e}")


def _build_llm_usage_event(
    label: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    owner_id: Optional[str],
    knowledge_id: Optional[str] = None,
    session_id: Optional[str] = None,
    key_type: Optional[str] = None
) -> dict:
    """
    Build an LLM usage event payload (chat/RAG generation calls), mirroring
    ``_build_embedding_event``'s shape.

    Args:
        label (str): Operation label (e.g. "rag_agent", "generate_answer", "refine_query")
        provider (str): LLM provider (e.g. "openai", "anthropic", "groq")
        model (str): Model name (e.g. "gpt-4.1-mini")
        input_tokens (int): Prompt/input tokens consumed
        output_tokens (int): Completion/output tokens produced
        owner_id (Optional[str]): Owner ID for the service (already known by the caller)
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        session_id (Optional[str]): Chat session ID, for tracking
        key_type (Optional[str]): Credential type used ("owner-key" or "platform-key")

    Returns:
        dict: Event payload
    """
    event = {
        "service_id": knowledge_id,
        "owner_id": owner_id,
        "session_id": session_id,
        "key_type": key_type,
        "service_type": "knowledge",
        "operation": "llm_generation",
        "label": label,
        "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "metadata": {
            "provider": provider,
            "model": model
        },
        "metrics": [
            {"name": "input_tokens", "value": input_tokens},
            {"name": "output_tokens", "value": output_tokens},
            {"name": "total_tokens", "value": input_tokens + output_tokens}
        ]
    }
    return event


async def send_llm_usage_event(
    label: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    owner_id: Optional[str],
    knowledge_id: Optional[str] = None,
    session_id: Optional[str] = None,
    key_type: Optional[str] = None
):
    """
    Send an LLM usage (chat/RAG generation) event to the usage events API.

    Unlike ``send_embedding_event``, the caller (the RAG node) already has
    ``owner_id`` and ``key_type`` in hand — both come out of
    ``chat/llm_factory.py::get_llm``'s return tuple — so neither is
    re-derived here via ``get_owner_id_from_service_id``/
    ``get_credential_for_knowledge``.

    Args:
        label (str): Operation label (e.g. "rag_agent", "generate_answer", "refine_query")
        provider (str): LLM provider (e.g. "openai", "anthropic", "groq")
        model (str): Model name (e.g. "gpt-4.1-mini")
        input_tokens (int): Prompt/input tokens consumed
        output_tokens (int): Completion/output tokens produced
        owner_id (Optional[str]): Owner ID for the service
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        session_id (Optional[str]): Chat session ID, for tracking
        key_type (Optional[str]): Credential type used ("owner-key" or "platform-key")
    """
    try:
        event = _build_llm_usage_event(
            label=label,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            owner_id=owner_id,
            knowledge_id=knowledge_id,
            session_id=session_id,
            key_type=key_type
        )

        payload = {"events": [event]}
        logger.info(f"Sending LLM usage event: label={label}, service_id={knowledge_id}, provider={provider}, model={model}, input_tokens={input_tokens}, output_tokens={output_tokens}, key_type={key_type}")
        await post_processing_event(payload)

    except Exception as e:
        logger.error(f"Error sending LLM usage event: {e}")

