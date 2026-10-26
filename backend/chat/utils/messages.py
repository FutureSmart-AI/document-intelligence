"""
Shared message conversion utilities for the agent graph.

Consolidates _build_history_messages, _format_user_memories, and
_convert_messages which were duplicated across rephrase.py, refiner.py,
router.py, and run.py.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage


def build_history_messages(
    past_messages: Optional[List[Dict]] = None,
    limit: Optional[int] = None,
    skip_empty: bool = False,
) -> list:
    """Convert raw message dicts into LangChain message objects.

    Args:
        past_messages: List of {role, content} dicts.
        limit: If set, only use the last *limit* messages.
        skip_empty: If True, skip messages with empty/whitespace content.
    """
    msgs: list = []
    items = past_messages or []
    if limit is not None:
        items = items[-limit:]
    for m in items:
        content = m.get("content", "")
        if skip_empty and (not content or not str(content).strip()):
            continue
        role = m.get("role", "user")
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    return msgs


def format_user_memories(memories: Optional[List[str]]) -> str:
    """Format user memories as a bulleted list for inclusion in prompts."""
    if not memories:
        return "None"
    return "\n".join(f"- {m}" for m in memories)
