"""Structured-handoff contract between the RAG sub-agent and its downstream
consumers (retrieval_verifier short-circuit, rag_answer_node, the outer
graph's direct-answer surfacing).

The ReAct sub-agent is created with ``response_format=ToolStrategy(RagAgentOutput)``:
the schema is bound as a tool the model calls to end its final turn — no extra
end-of-turn coercion LLM call (the cost that got the old plain
``response_format=RagAgentOutput`` binding removed in 110915c). Downstream code
reads ``result["structured_response"]`` first and falls back to parsing the
final AIMessage as JSON (``_parse_agent_output``) when the model emitted the
blob as text instead of calling the tool.
"""
from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class StopReason(str, Enum):
    answered = "answered"                       # confident answer produced
    not_found = "not_found"                     # search exhausted, attribute genuinely absent
    budget_exhausted = "budget_exhausted"       # call budget hit before answer
    needs_clarification = "needs_clarification" # query underspecified
    refused = "refused"                         # out-of-scope / policy refusal


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Granularity(str, Enum):
    """The index layer this query is answerable from (Core Principle #3).

    - ``doc``  — a TOPIC-level query ("find docs about X"). The answer is the
      set of relevant documents; their summaries/metadata suffice.
    - ``chunk`` — a SPECIFIC-DETAIL query (a value, config, link/owner/date, or
      "details about" a NAMED entity). The literal answer lives in document
      body text, not the summary — it needs the chunk layer.

    GUIDED by the metadata catalog field names, not guessed from query wording
    (Core Principle #3, content shapes only). Two-step test: (i) if the asked-for
    attribute IS a catalog field (e.g. ``skills`` / ``education`` / ``owner``
    exists), it's ``doc`` — answerable from metadata/summary; (ii) if it is NOT a
    field and the retrieved summaries plainly can't carry it either, it's
    ``chunk`` — the value lives in the body. Set this as the OUTCOME of that test.

    Gates whether the escalation ladder's chunk rungs (incl. the Rung-3 global
    call) are even in play: a ``doc`` query answered at medium confidence is a
    legitimate terminal state and is NOT forced to descend to chunks.
    """
    doc = "doc"
    chunk = "chunk"


class RelevantDoc(BaseModel):
    """A document the agent actually used to ground its answer."""
    doc_id: str = Field(
        description="The supporting doc's object_id. Must be a doc you saw in "
                    "this turn's retrieval — do not invent ids."
    )
    why: str = Field(
        description="One short line on what this doc contributed to the answer."
    )
    chunk_ids: List[str] = Field(
        default_factory=list,
        description="Specific chunk ids within this doc that grounded the answer. "
                    "Empty when the answer came from the doc's summary/metadata "
                    "alone; populated when chunk text was the actual source. Must "
                    "be chunk ids you saw in retrieve_chunks output this turn — "
                    "do not invent ids."
    )


class RagAgentOutput(BaseModel):
    """Structured final output of the RAG sub-agent.

    Consumed by:
    - retrieval_verifier: branches on stop_reason / confidence / open_questions
    - answer_node: formats the user-facing response from answer + relevant_docs
    - debug surfaces: previous_context_used, cited doc/chunk ids
    """
    stop_reason: StopReason
    confidence: Confidence
    granularity: Granularity = Field(
        description="The index layer this query needs (Core Principle #3). "
                    "GUIDED by the metadata catalog field names, not guessed: "
                    "'doc' = the asked-for attribute IS a catalog field "
                    "(answerable from summaries/metadata); 'chunk' = it is NOT a "
                    "field and the retrieved summaries can't carry it either, so "
                    "the literal answer lives in document body text. This is the "
                    "outcome of the field-name + summary test, not an up-front label."
    )
    previous_context_used: bool = Field(
        description="True if this turn relied on context from EARLIER turns in "
                    "the same session — e.g. resolved 'tell me more about that' "
                    "against the prior turn's doc, scoped retrieval to the "
                    "prior-evidence block injected at the start of this thread, "
                    "or used a name introduced in an earlier user message. "
                    "False when the turn was answered from fresh retrieval "
                    "against THIS query alone. You are the right place to "
                    "report this because you can see the prior-evidence block "
                    "and decide whether to consume it."
    )
    about_previous_result: bool = Field(
        default=False,
        description="True when the question is about EXACTLY the documents a "
                    "previous answer presented — the whole set, unchanged — e.g. "
                    "'chart their years', 'compare those candidates', 'sort them "
                    "by experience'. The prior-evidence block marks that set as "
                    "'result of the previous answer'; when this is true, that set "
                    "is used as this turn's documents as is. False when the "
                    "question narrows the set ('which of them know Go'), widens "
                    "it ('and all other candidates?'), or is about anything else "
                    "— then cite the documents you used as usual."
    )
    answer: str = Field(
        description="The user-facing answer. Plain Markdown. Empty only when "
                    "stop_reason in {needs_clarification, refused}."
    )
    relevant_docs: List[RelevantDoc] = Field(
        default_factory=list,
        description="LOAD-BEARING — this is the ONLY evidence the answer step "
                    "will see. List EVERY doc that contributed to your answer, "
                    "each with a one-line reason and the specific chunk_ids that "
                    "carried the value (when the answer came from chunk body "
                    "text). The answer node grounds solely on the docs+chunks "
                    "you cite here; anything you used but omit will NOT be "
                    "available to it, so be complete — under-citing drops real "
                    "evidence from the final answer. Leave empty ONLY when the "
                    "doc SET itself is the answer (a `lookup`/`aggregate`/"
                    "`metadata_lookup` enumeration) or for stop_reason in "
                    "{needs_clarification, refused}; an empty list tells the "
                    "answer node to fall back to all retrieved evidence."
    )
    open_questions: List[str] = Field(
        default_factory=list,
        description="Gaps the agent identified but could not resolve. MUST be "
                    "non-empty when stop_reason in {not_found, budget_exhausted}."
    )


RESULT_DOCUMENTS_DESCRIPTION = (
    "The [Doc N] numbers (just N) of the documents this answer presents as its "
    "result: every document it lists, ranks, compares, counts or charts — the set "
    "a follow-up saying 'their', 'those' or 'them' would mean. Only documents the "
    "answer actually includes: a document the evidence shows but the answer "
    "leaves out (it fails the question's condition) is not listed. Empty when the "
    "answer is not about a set of documents (one fact, not found, a refusal)."
)


class RagAnswerOutput(BaseModel):
    """Structured output of the RAG answer-generation node.

    The answer node has the full retrieved evidence + the user's question in
    front of it — the right place to self-evaluate whether the answer it just
    produced actually resolves the question against the evidence.

    Consumed by:
    - rag_node / direct-answer mode: surfaces `answer` to the user
    - debug surfaces: surfaces `is_answered` alongside the answer
    - downstream verifiers / retries: a False signal can trigger a retry path
    """
    answer: str = Field(
        description="The user-facing answer. Plain Markdown. Grounded ONLY in "
                    "the retrieved evidence shown to you — never invent facts "
                    "outside what the evidence supports."
    )
    is_answered: bool = Field(
        description="True ONLY if the `answer` above directly resolves the user's "
                    "question using the retrieved evidence. Set to False when: "
                    "the answer is empty, partial, generic, says 'not found' / "
                    "'no info available', hedges without a concrete answer, or "
                    "you couldn't ground it in the retrieved evidence. Honest "
                    "self-evaluation — do NOT set True just because you produced "
                    "text. Used downstream for debug surfaces and retry logic."
    )
    result_documents: List[int] = Field(
        default_factory=list,
        description=RESULT_DOCUMENTS_DESCRIPTION,
    )


class IsAnsweredVerdict(BaseModel):
    """Standalone is_answered self-eval, used ONLY on the answer-node's
    streaming path (chat/rag_agent/answer_node.py). Streaming already
    produced the answer text as plain prose (structured output can't stream
    cleanly), so this is a second, minimal structured-output call — same
    question/evidence, plus the answer just streamed — asking the SAME
    answer LLM to self-judge it. Keeps the verdict authored by the answer
    LLM rather than borrowed from the retrieval agent's stop_reason."""
    is_answered: bool = Field(
        description="True ONLY if the given answer directly resolves the user's "
                    "question using the retrieved evidence. Set to False when: "
                    "the answer is empty, partial, generic, says 'not found' / "
                    "'no info available', hedges without a concrete answer, or "
                    "isn't grounded in the retrieved evidence. Honest "
                    "self-evaluation — do NOT set True just because the answer "
                    "contains text."
    )
    result_documents: List[int] = Field(
        default_factory=list,
        description=RESULT_DOCUMENTS_DESCRIPTION,
    )
