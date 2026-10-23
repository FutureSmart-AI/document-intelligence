"""
Usage Utilities
Helper functions to extract usage metadata from LLM responses across different providers.
"""

from typing import Dict, Any, Optional, Tuple
from utils.logger import logger


def extract_cache_tokens(usage_metadata: Any) -> Tuple[int, int]:
    """(cached_input_tokens, cache_write_tokens) from an LLM usage payload.

    These are the cache-READ and cache-CREATION portions of the prompt, expressed
    as **subsets** of ``input_tokens`` (never added on top). LangChain normalizes
    every provider's cache split into ``input_token_details.cache_read`` /
    ``cache_creation``; we also read the raw provider shapes as a fallback:

    - OpenAI: ``prompt_tokens_details.cached_tokens`` (read; no separate write)
    - Anthropic: ``cache_read_input_tokens`` / ``cache_creation_input_tokens``
    - Google: ``cached_content_token_count`` (read)

    Returns ``(0, 0)`` when nothing cacheable is reported.
    """
    if not isinstance(usage_metadata, dict):
        try:
            usage_metadata = dict(usage_metadata) if usage_metadata else {}
        except Exception:
            return 0, 0

    # LangChain-normalized shape (preferred — uniform across providers).
    details = usage_metadata.get("input_token_details")
    if isinstance(details, dict):
        cache_read = details.get("cache_read", 0) or 0
        cache_creation = details.get("cache_creation", 0) or 0
        if cache_read or cache_creation:
            return int(cache_read), int(cache_creation)

    # Raw provider fallbacks.
    ptd = usage_metadata.get("prompt_tokens_details")
    if isinstance(ptd, dict) and ptd.get("cached_tokens"):
        return int(ptd["cached_tokens"] or 0), 0
    cache_read = usage_metadata.get("cache_read_input_tokens", 0) or 0
    cache_creation = usage_metadata.get("cache_creation_input_tokens", 0) or 0
    if cache_read or cache_creation:
        return int(cache_read), int(cache_creation)
    cached_content = usage_metadata.get("cached_content_token_count", 0) or 0
    if cached_content:
        return int(cached_content), 0
    return 0, 0


def extract_usage_metadata(response: Any, provider: Optional[str] = None) -> Dict[str, int]:
    """
    Extract usage metadata from LLM response across different providers.

    Args:
        response: LLM response object (varies by provider)
        provider: Optional provider name to help with extraction

    Returns:
        Dictionary with normalized token counts:
        {
            "input_tokens": int,
            "output_tokens": int,
            "total_tokens": int
        }
    """
    usage_metadata = {}

    try:
        # Standardized way to get usage_metadata (OpenAI, Groq usually have this)
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage_metadata = response.usage_metadata
            if not isinstance(usage_metadata, dict):
                usage_metadata = dict(usage_metadata) if hasattr(usage_metadata, '__dict__') else {}

        # Anthropic specific — cache tokens are reported SEPARATELY from
        # input_tokens, so fold them in to keep the "cache is a subset of input"
        # contract that pricing.estimate_cost relies on.
        elif provider == "anthropic" and hasattr(response, 'usage') and response.usage:
            usage_data = response.usage
            base_in = getattr(usage_data, 'input_tokens', 0) or 0
            cache_read = getattr(usage_data, 'cache_read_input_tokens', 0) or 0
            cache_creation = getattr(usage_data, 'cache_creation_input_tokens', 0) or 0
            usage_metadata = {
                "input_tokens": base_in + cache_read + cache_creation,
                "output_tokens": getattr(usage_data, 'output_tokens', 0) or 0,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            }

        # Google specific
        elif provider == "google" and hasattr(response, 'response_metadata') and response.response_metadata.get('usage_metadata'):
            google_usage = response.response_metadata['usage_metadata']
            usage_metadata = {
                "input_tokens": google_usage.get("prompt_token_count", 0) or 0,
                "output_tokens": google_usage.get("candidates_token_count", 0) or 0,
                "total_tokens": google_usage.get("total_token_count", 0) or 0,
            }

        # Fallback for OpenAI if stream_usage=True put it in response_metadata
        elif provider == "openai" and hasattr(response, 'response_metadata') and response.response_metadata.get('token_usage'):
            openai_token_usage = response.response_metadata['token_usage']
            usage_metadata = {
                "input_tokens": openai_token_usage.get("prompt_tokens", 0) or 0,
                "output_tokens": openai_token_usage.get("completion_tokens", 0) or 0,
                "total_tokens": openai_token_usage.get("total_tokens", 0) or 0,
            }

        # Try to extract from dict format if response is a dict
        elif isinstance(response, dict):
            if "usage_metadata" in response:
                usage_metadata = response["usage_metadata"]
            elif "usage" in response:
                usage_data = response["usage"]
                if isinstance(usage_data, dict):
                    usage_metadata = usage_data
                else:
                    usage_metadata = {
                        "input_tokens": getattr(usage_data, 'input_tokens', 0) or 0,
                        "output_tokens": getattr(usage_data, 'output_tokens', 0) or 0,
                    }

    except Exception as e:
        logger.debug(f"Error extracting usage metadata: {str(e)}")
        usage_metadata = {}

    # Normalize token counts
    normalized = {
        "input_tokens": usage_metadata.get("input_tokens", usage_metadata.get("prompt_tokens", 0)) or 0,
        "output_tokens": usage_metadata.get("output_tokens", usage_metadata.get("completion_tokens", 0)) or 0,
        "total_tokens": usage_metadata.get("total_tokens", 0) or 0
    }

    # Calculate total_tokens if not provided
    if normalized["total_tokens"] == 0:
        normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]

    # Cache split (subsets of input_tokens) for cost pricing; 0 when not reported.
    cached_in, cache_write = extract_cache_tokens(usage_metadata)
    normalized["cached_input_tokens"] = cached_in
    normalized["cache_write_tokens"] = cache_write

    return normalized


def normalize_model_name(model_name: str, provider: Optional[str] = None) -> str:
    """
    Normalize model name by removing provider-specific prefixes.

    Args:
        model_name: Raw model name from LLM instance
        provider: Optional provider name to help with normalization

    Returns:
        Normalized model name without provider prefixes

    Examples:
        - "models/gemini-2.0-flash" -> "gemini-2.0-flash"
        - "openai/gpt-oss-120b" -> "openai/gpt-oss-120b" (keep this prefix as it's part of model name)
    """
    if not model_name:
        return model_name

    # Remove Google's "models/" prefix
    if model_name.startswith("models/"):
        return model_name[7:]  # Remove "models/" prefix

    # Add other provider-specific normalizations here if needed

    return model_name


def extract_model_name(llm_instance: Any, response: Any = None, provider: Optional[str] = None) -> Optional[str]:
    """
    Extract model name from LLM instance or response and normalize it.

    Args:
        llm_instance: LLM instance object
        response: Optional response object to extract model from
        provider: Optional provider name

    Returns:
        Normalized model name string or None if not available
    """
    try:
        model_name = None

        # Try to get from LLM instance
        if hasattr(llm_instance, 'model_name'):
            model_name = llm_instance.model_name
        elif hasattr(llm_instance, 'model'):
            model_name = llm_instance.model

        # Try to get from response metadata if not found in instance
        if not model_name and response:
            if hasattr(response, 'response_metadata'):
                metadata = response.response_metadata
                if isinstance(metadata, dict):
                    model_name = metadata.get('model') or metadata.get('model_name')

        # Normalize the model name to remove provider-specific prefixes
        if model_name:
            return normalize_model_name(model_name, provider)

        # Fallback to provider name
        if provider:
            return provider

    except Exception as e:
        logger.debug(f"Error extracting model name: {str(e)}")

    return None


def normalize_usage_metrics(usage_metrics: Dict[str, int]) -> Dict[str, int]:
    """
    Normalize usage metrics to ensure consistent format.

    Args:
        usage_metrics: Dictionary with token metrics (may have various key names)

    Returns:
        Normalized dictionary with input_tokens, output_tokens, total_tokens
    """
    normalized = {
        "input_tokens": usage_metrics.get("input_tokens", usage_metrics.get("prompt_tokens", 0)) or 0,
        "output_tokens": usage_metrics.get("output_tokens", usage_metrics.get("completion_tokens", 0)) or 0,
        "total_tokens": usage_metrics.get("total_tokens", 0) or 0
    }

    # Calculate total_tokens if not provided
    if normalized["total_tokens"] == 0:
        normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]

    return normalized
