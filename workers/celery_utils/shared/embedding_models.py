"""Canonical registry of supported embedding models.

Single source of truth for provider type, vector dimension, and underlying
model name. Must stay in sync with the knowledge service's copy.
"""

EMBEDDING_MODELS = {
    "paraphrase-multilingual-mpnet-base-v2": {
        "type": "sentence_transformer",
        "dimension": 768,
        "model_name": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        "price_per_1m_tokens": 0.0,  # local model, no API cost
    },
    "openai-text-embedding-3-large": {
        "type": "openai",
        "dimension": 3072,
        "model_name": "text-embedding-3-large",
        "price_per_1m_tokens": 0.13,
    },
    "openai-text-embedding-3-small": {
        "type": "openai",
        "dimension": 1536,
        "model_name": "text-embedding-3-small",
        "price_per_1m_tokens": 0.02,
    }
}


def embedding_cost_usd(embedding_model: str, tokens: int) -> float:
    """Cost in USD to embed `tokens` with `embedding_model` (0.0 for local models)."""
    cfg = EMBEDDING_MODELS.get(embedding_model) or {}
    return (tokens / 1_000_000) * cfg.get("price_per_1m_tokens", 0.0)
