"""Resolve the retrieval agent's cited document ids to the documents it saw.

The agent cites a document by copying its 36-character object_id into
``RagAgentOutput.relevant_docs[].doc_id``. Every consumer of those citations
(the answer node's grounding gate, the chart population, the sources panel,
the persisted cross-turn evidence) matched them by exact string equality, so
one mis-copied character silently removed that document from the answer and
the chart. Measured on the model-evaluation follow-up case
(``fu_python_aws_years_chart``, run 40299739518): turn 2 cited all 8
candidates of turn 1, but gpt-5.6-luna wrote ``b4307e75-657e-49ac-0f052d54a1206a``
for Rahul Verma and gpt-5.4-mini wrote ``8f633108-6ac9-4382-aa8d-a8c2f0ca28c3``
for ``...-aa8d-aac2f0ca28c3`` (Arjun Malhotra); both charts showed 7 of 8.

This is id resolution, not a judgement about meaning: the agent already
decided WHICH document it means; this only recovers the id it meant from the
ids it was shown. A cited id resolves when it equals a known id (ignoring
case and surrounding whitespace/brackets), or when exactly one known id shares
its first UUID group and none shares its last group with another candidate —
copy errors observed so far keep both ends intact. An id that matches nothing
is left as cited and logged; it never resolves to a guess.

``canonicalize_citations`` rewrites ``relevant_docs`` in place, once, right
after the agent's output is parsed (chat/rag_agent/agent_execution.py), so
every downstream consumer sees canonical ids without changing.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from utils.logger import logger


def _norm(value: Any) -> str:
    return str(value or "").strip().strip("[]{}()<>\"'` ").lower()


def known_doc_ids(retrieved_context: Dict[str, Any]) -> List[str]:
    """Every document id the agent was shown this turn: retrieved summaries,
    the parents of retrieved chunks, every document a tool returned, and the
    earlier turns' documents injected as prior evidence."""
    seen: Dict[str, None] = {}

    def _add(oid: Any) -> None:
        if oid:
            seen.setdefault(str(oid), None)

    for oid in (retrieved_context.get("summaries") or {}):
        _add(oid)
    for chunk in retrieved_context.get("chunks") or []:
        if isinstance(chunk, dict):
            _add(chunk.get("document_id") or chunk.get("object_id"))
    for response in retrieved_context.get("tool_responses") or []:
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict):
            for doc in result.get("documents") or []:
                if isinstance(doc, dict):
                    _add(doc.get("object_id"))
    for doc in retrieved_context.get("prior_turn_docs") or []:
        if isinstance(doc, dict):
            _add(doc.get("object_id"))
    for doc in retrieved_context.get("carried_population") or []:
        if isinstance(doc, dict):
            _add(doc.get("object_id"))
    return list(seen)


def resolve_doc_id(cited: Any, known: Iterable[str]) -> Optional[str]:
    """The known id ``cited`` refers to, or None when it cannot be told."""
    want = _norm(cited)
    if not want:
        return None
    known = list(known)
    by_norm = {_norm(k): k for k in known}
    if want in by_norm:
        return by_norm[want]
    groups = want.split("-")
    head, tail = groups[0], groups[-1]
    if len(head) < 8:
        return None
    head_matches = [k for k in known if _norm(k).split("-")[0] == head]
    if len(head_matches) == 1:
        tail_matches = [k for k in known if len(tail) >= 8 and _norm(k).split("-")[-1] == tail]
        # A tail that points at a DIFFERENT document makes the citation ambiguous.
        if not tail_matches or tail_matches == head_matches:
            return head_matches[0]
        return None
    if not head_matches and len(tail) >= 12:
        tail_matches = [k for k in known if _norm(k).split("-")[-1] == tail]
        if len(tail_matches) == 1:
            return tail_matches[0]
    return None


def canonicalize_citations(agent_output: Optional[Dict[str, Any]], known: Iterable[str]) -> Dict[str, int]:
    """Rewrite each ``relevant_docs[].doc_id`` to the known id it refers to.

    A repaired entry keeps what the agent wrote under ``cited_as``. Returns
    ``{"repaired": n, "unresolved": m}`` for the caller's log line."""
    stats = {"repaired": 0, "unresolved": 0}
    if not isinstance(agent_output, dict):
        return stats
    known = list(known)
    known_set = set(known)
    for entry in agent_output.get("relevant_docs") or []:
        if not isinstance(entry, dict) or not entry.get("doc_id"):
            continue
        cited = str(entry["doc_id"])
        if cited in known_set:
            continue
        resolved = resolve_doc_id(cited, known)
        if resolved is None:
            stats["unresolved"] += 1
            logger.warning(f"[citations] cited doc_id {cited!r} matches no document shown this turn")
            continue
        entry["cited_as"] = cited
        entry["doc_id"] = resolved
        stats["repaired"] += 1
        logger.info(f"[citations] cited doc_id {cited!r} resolved to {resolved!r}")
    return stats
