"""OpenAI `reasoning_effort` gating, ported from fs-agent-chat/chat_utils/openai_reasoning.py.

Use "low" for every call to a reasoning-capable model. Non-reasoning models
(e.g. gpt-4o-mini) must not receive this parameter.
"""

from __future__ import annotations

OPENAI_REASONING_EFFORT_LOW = "low"
OPENAI_REASONING_EFFORT_NONE = "none"


def openai_reasoning_effort_for_model(
    model: str | None,
    *,
    for_function_calling: bool = False,
) -> str | None:
    """Return the ``reasoning_effort`` value to send for ``model``, or ``None`` to omit it.

    OpenAI returns 400 when ``reasoning_effort`` is set together with
    function tools for GPT-5 models on ``/v1/chat/completions`` (the
    Responses API is required for that combo) — UNLESS ``reasoning_effort``
    is explicitly ``"none"``. Simply omitting the parameter is NOT
    equivalent to ``"none"`` for every GPT-5 snapshot: some (e.g.
    gpt-5.4-mini) tolerate omission, but newer ones (e.g. gpt-5.6-luna)
    default to active reasoning unless told otherwise and 400 on a
    tool-bound call with the parameter absent — the error message names the
    exact fix: "...or set reasoning_effort to 'none'". Sending ``"none"``
    explicitly is safe for every GPT-5 snapshot, so tool-calling call sites
    (the RAG ReAct loop, the query refiner) always get the explicit value,
    never an omission, by passing ``for_function_calling=True``.
    """
    if not model:
        return None
    m = model.lower().strip()
    if "gpt-5" in m:
        return OPENAI_REASONING_EFFORT_NONE if for_function_calling else OPENAI_REASONING_EFFORT_LOW
    # o-series reasoning models — no known 400 on the tools+reasoning_effort
    # combo for this family, so always send "low" regardless of tool use.
    if m.startswith(("o1", "o3", "o4")):
        return OPENAI_REASONING_EFFORT_LOW
    return None
