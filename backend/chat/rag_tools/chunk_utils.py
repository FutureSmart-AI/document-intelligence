"""
Chunk metadata utility.

Inlined from fs-agent-chat/chat_utils/response_builder/chunk_utils.py — only
``normalized_chunk_metadata`` (and its ``_CHUNK_ROOT_NON_METADATA_KEYS``
constant dependency) is needed by renderers.py; the rest of that donor
module (score-threshold helpers, file-name extraction, service dedup) has no
call site in the ported rag_tools surface, so the whole response_builder
package was not ported.
"""
from typing import Dict, Any


# Root keys that are chunk envelope / transport, not document or chunk metadata.
_CHUNK_ROOT_NON_METADATA_KEYS = frozenset({
    "text",
    "chunk_text",
    "document_id",
    "object_id",
    "file_name",
    "filename",
    "source",
    "relevance_score",
    "score",
    "metadata",
})


def normalized_chunk_metadata(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return all chunk metadata for API clients: full nested ``metadata`` when present,
    plus any other top-level attributes on the chunk (e.g. search hits) except
    envelope fields (text, ids, scores, filenames).
    """
    raw = chunk.get("metadata")
    out: Dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    for key, value in chunk.items():
        if key in _CHUNK_ROOT_NON_METADATA_KEYS or value is None:
            continue
        if key not in out:
            out[key] = value
    return out
