"""A turn's result set, carried into the follow-up that refers to it.

"Which candidates know Python and AWS and have 5+ years?" answered 8 people;
"chart their years" must chart exactly those 8. Before this module nothing
recorded WHICH documents an answer presented: the persisted cross-turn
evidence is the retrieval trail (an exhaustive group is stored whole, so a
retrieval whose filter was looser than the question — ``skills $in
[python, aws]``, 14 matches — came back as 14), and the follow-up re-derived
"their" from that trail or from fresh retrieval. Measured on the model
evaluation (run 40299739518, case ``fu_python_aws_years_chart``): the charts
covered 14 people with gpt-5.4-mini and gpt-5.4-nano as the chat models.

The design keeps each semantic decision with an LLM and makes the carrying
mechanical:
  * the ANSWER node, which writes the answer, says which documents it
    presented (``result_documents``: the ``[Doc N]`` numbers of its context,
    mapped back to object_ids here — numbers, so nothing is mis-copied);
  * that set is persisted with the turn's evidence as one group with
    ``source = "answer_result_set"`` (chat/evidence_builder.py) and shown to
    the next turn's agent as "the result of the previous answer";
  * the next turn's retrieval AGENT says whether the new question is about
    exactly that set (``RagAgentOutput.about_previous_result``);
  * when it is, the set is the turn's population as is
    (``carried_population``): the chart (chat/chart_generator.build_population)
    and the answer's grounding (chat/rag_agent/answer_node) use those
    documents, whatever the turn retrieved again.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

ANSWER_RESULT_SOURCE = "answer_result_set"


def is_answer_result_group(group: Any) -> bool:
    return isinstance(group, dict) and group.get("source") == ANSWER_RESULT_SOURCE


def previous_result_group(prior_evidence: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """The newest answer result set among the loaded prior-evidence groups
    (chat/db/chat_logs.get_recent_evidence returns turns newest-first and each
    turn stores its answer group first)."""
    for group in prior_evidence or []:
        if is_answer_result_group(group) and group.get("documents"):
            return group
    return None


def carried_population(
    prior_evidence: Optional[List[Dict[str, Any]]],
    agent_output: Optional[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """The previous answer's documents when the agent said this question is
    about exactly them; None otherwise."""
    if not isinstance(agent_output, dict) or agent_output.get("about_previous_result") is not True:
        return None
    group = previous_result_group(prior_evidence)
    if group is None:
        return None
    docs = [d for d in group.get("documents") or [] if isinstance(d, dict) and d.get("object_id")]
    return docs or None


def result_ids_from_handles(handles: List[str], numbers: Any) -> List[str]:
    """Map the answer node's ``[Doc N]`` numbers (1-based) to object_ids, in
    order, dropping numbers outside the context and duplicates."""
    ids: List[str] = []
    for n in numbers or []:
        try:
            i = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(handles) and handles[i - 1] not in ids:
            ids.append(handles[i - 1])
    return ids
