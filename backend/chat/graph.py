"""LangGraph wiring for the Knowledge Service's own chat graph.

Ported design, not code — there is no single donor file. Assembles the two
already-ported nodes (chat/query_refiner.py, chat/rag_node.py) into the
target graph from claude-logs/plan/knowledge-owned-chat.md's "Target design"
section:

    START -> query_refiner -> rag -> END
            (rag internally: ReAct retrieval loop -> generate_rag_answer,
             streaming)

Deliberately trimmed from the donor's fs-agent-chat/agents/state.py
AgentState, which is the state for the Orchestrator's full multi-service
planner graph. Dropped entirely (per the plan's confirmed Phase-4 design,
step 17):
  - Planner fields: next_agent, routing_reasoning, selected_custom_action_id,
    selected_integration_id, selected_connected_link_id,
    selected_connected_agent_id, plan, dispatch_plan, dispatch_size,
    needs_followup, dispatch_rounds, tool_outputs, tool_attempts,
    produced_count, outputs_seen. None of this applies — this graph has
    exactly one capability (RAG) and no router/fan-out.
  - HITL fields: needs_clarification, clarification_rounds. Option B scope
    excludes HITL clarification entirely (see plan's "No LangGraph
    checkpointer" section) — there is no ask_user node to pause for.
  - Other-service response fields: nl2sql_response, integration_response,
    custom_action_response, action_response, connected_agent_responses.
    Single-capability graph; nothing else ever writes these.
  - usage_metrics (the donor's whole-turn token accumulator dict) — neither
    query_refiner_node nor rag_node reads or writes it; both report their own
    per-call spend via usage_by_model/service_calls instead. Not carried over
    since nothing in this graph would populate or consume it.

Kept, because the two ported nodes' REAL signatures (not the donor's
state+config dict convention) actually produce/consume them:
  - refined_query, summaries, detailed_chunks, rag_context, knowledge_id,
    relevant_context, relevant_sources, is_direct_response, usage_by_model,
    service_calls — all appear verbatim in query_refiner_node's or
    rag_node's return dict (see chat/query_refiner.py, chat/rag_node.py).
  - response, is_answered, error, services_used — rag_node returns these too
    (is_answered/services_used/error are in the donor's AgentState already;
    response and error are part of rag_node's actual return shape).
  - past_messages, query, user_id, session_id, owner_id — plain inputs both
    node functions take as parameters; threaded through state so the graph's
    entry point can set them once and both nodes read them back out.

NOT kept from the plan's suggested list: ``messages`` (the donor's
``Annotated[Sequence[BaseMessage], add_messages]`` accumulator). Neither
query_refiner_node nor rag_node reads or writes a LangChain-message-typed
``messages`` field — both take ``past_messages: List[Dict[str, Any]]``
(plain role/content dicts) as a plain parameter instead, and query_refiner
converts that to LangChain messages internally via
``chat.utils.messages.build_history_messages`` without ever touching graph
state. Adding an unused ``messages`` field (and the ``add_messages``
reducer import that comes with it) would be donor-fidelity for its own sake,
not something this graph's actual nodes need — dropped per the task's
explicit instruction to check real usage rather than copy the field
blindly.
"""
from __future__ import annotations

import operator
import time
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from chat.chart_node import chart_node
from chat.follow_up import follow_up_node
from chat.query_refiner import query_refiner_node
from chat.rag_node import rag_node
from chat.run_usage import build_usage_block, collect_run_usage, usage_operation
from utils.logger import logger


class KnowledgeChatState(TypedDict, total=False):
    # ── Query ────────────────────────────────────────────────────────────
    query: str
    refined_query: str
    past_messages: List[Dict[str, Any]]

    # ── Identity (plain inputs, set once at entry, read by both nodes) ──
    knowledge_id: str
    user_id: Optional[str]
    session_id: Optional[str]
    owner_id: Optional[str]

    # ── Response ─────────────────────────────────────────────────────────
    response: str
    is_answered: str
    is_direct_response: bool
    relevant_context: str
    relevant_sources: Dict[str, Any]
    error: Optional[str]
    services_used: List[str]

    # ── Per-turn quality self-report (rag_node, from agent_output) ────────
    # RagAgentOutput.confidence / .stop_reason (chat/rag_agent/agent_output.py),
    # produced by the retrieval sub-agent on every turn and previously consumed
    # only inside rag_agent (verifier branching, answer-node prompt) — the outer
    # graph never saw them, so knowledge_health_events.confidence/stop_reason
    # were hardcoded NULL and the Analytics screen had no per-turn quality
    # signal beyond the binary is_answered. Surfaced here so the health event
    # can record what the agent already judged, at zero extra LLM cost.
    # None when the agent emitted no parseable structured output for the turn
    # (_parse_agent_output returned None) or on the error path.
    confidence: Optional[str]
    stop_reason: Optional[str]

    # ── RAG eval Tier 0 (migration 048) ──────────────────────────────────
    # The free structural metrics for this turn, or {} when the turn's owner is
    # outside RAG_EVAL_OWNER_IDS (the default for everyone). Computed in
    # rag_node, written by chat/db/health_events.py. A dict rather than nine
    # state keys so adding a tenth signal does not touch this schema.
    rag_eval_tier0: Optional[Dict[str, Any]]

    # ── RAG-specific context ─────────────────────────────────────────────
    summaries: Optional[Dict[str, Any]]
    detailed_chunks: Optional[Dict[str, List[dict]]]
    rag_context: Optional[Dict[str, Any]]

    # ── Chart output (G16) ───────────────────────────────────────────────
    # Populated by chart_node, which runs after rag. None when include_charts
    # is off, there were no chunks to chart, or generation failed.
    chart_output: Optional[Dict[str, Any]]

    # ── Follow-up questions ──────────────────────────────────────────────
    # Populated by follow_up_node, the last node in the graph. None when the
    # knowledge's follow_up flag is off, the turn went unanswered, or
    # generation failed/timed out. Lives in the graph (rather than in
    # routers/chat.py, where it used to run after the graph returned) so the
    # LLM call it makes is recorded inside the turn's LangSmith trace instead
    # of as a separate root trace.
    follow_up: Optional[List[str]]

    # ── Token / cost tracking ─────────────────────────────────────────────
    # Both nodes APPEND their own entries (query_refiner_node's LLM call,
    # rag_node's aggregate sub-agent call) rather than overwrite — operator.add
    # reducer so the graph's final state carries both nodes' entries, matching
    # the donor's AgentState convention for the same two fields.
    usage_by_model: Annotated[List[Dict[str, Any]], operator.add]
    service_calls: Annotated[List[Dict[str, Any]], operator.add]

    # ── Streaming handle (per-request, not turn data) ───────────────────
    # Must be declared here even though no node writes it — LangGraph's
    # StateGraph filters the initial state to keys present in the schema
    # before invoking the entry node, so an undeclared key is silently
    # dropped and never reaches _rag_graph_node's state.get("_token_queue")
    # lookup. Bug found live (2026-08-06 smoke test): SSE streaming never
    # emitted any {"chunk": ...} frames — token_queue was always None inside
    # rag_node despite being passed into run_knowledge_chat_graph, because
    # this field was missing here. See chat/rag_node.py's docstring for the
    # streaming-gate logic this key feeds.
    _token_queue: Optional[Any]

    # ── Level-1 config override (per-request, not turn data) ────────────
    # Same "must be declared or it's silently dropped" reason as
    # _token_queue above.
    #
    # Shape: {node_name: {"provider", "model", "temperature",
    # "system_prompt"}} — keyed by node, because this service's three LLM
    # roles can each carry a different provider/model/prompt (see
    # routers/chat.py's ChatRequest.node_configs). routers/chat.py has
    # already merged the flat request fields under each node's own entry, so
    # every consumer here just reads its own key and needs no knowledge of
    # the flat-vs-per-node precedence rules.
    _request_chat_config: Optional[Dict[str, Dict[str, Any]]]

    # ── Resolved chat config (per-request, not turn data) ───────────────
    # The already-resolved chat/config.py::ResolvedChatConfig for this turn,
    # passed in whole so follow_up_node can read its follow_up gate and its
    # answer-model choice. Same "must be declared or it's silently dropped"
    # reason as _token_queue above. None for callers that resolve no chat
    # config (chat/report_execution.py) — follow_up_node then no-ops.
    _chat_config: Optional[Any]


async def _query_refiner_graph_node(state: KnowledgeChatState) -> Dict[str, Any]:
    """Thin LangGraph wrapper: unpack state -> query_refiner_node's plain
    parameters, repack its return dict as a partial state update.

    query_refiner_node is a plain-parameter async function (chat/query_refiner.py),
    not the donor's (state, config) node signature — this wrapper is the
    adaptation point, per the task's design. Must itself be async def and
    await the call: query_refiner_node is `async def` (it awaits get_llm and
    the LLM chain internally), so calling it without awaiting would hand
    LangGraph an un-awaited coroutine as the "state update" instead of a dict.
    """
    with usage_operation("query_refinement"):
        return await _query_refiner_call(state)


async def _query_refiner_call(state: KnowledgeChatState) -> Dict[str, Any]:
    return await query_refiner_node(
        query=state["query"],
        past_messages=state.get("past_messages"),
        owner_id=state.get("owner_id"),
        knowledge_id=state.get("knowledge_id"),
        session_id=state.get("session_id"),
        # This node only ever reads its own slice — the rewriter's model is
        # independent of the retrieval loop's and the answer writer's.
        request_chat_config=(state.get("_request_chat_config") or {}).get("refined_query_node"),
    )


async def _rag_graph_node(state: KnowledgeChatState) -> Dict[str, Any]:
    """Thin LangGraph wrapper: unpack state -> rag_node's plain parameters,
    repack its return dict as a partial state update.

    Uses refined_query (query_refiner_node's output) as the retrieval query
    when present, falling back to the original query when refinement was
    skipped (no history) or failed (query_refiner_node's own fallback already
    returns the original query in refined_query in that case, so this `or`
    is a belt-and-suspenders guard, not the primary fallback path).

    token_queue is not threaded through state — it is a per-request streaming
    handle (e.g. an asyncio.Queue set up by the future chat endpoint per
    connection), not turn data. run_knowledge_chat_graph accepts it directly
    and passes it here via closure, never via state, so it never round-trips
    through LangGraph's state-diffing/serialization. Must itself be async
    def and await the call, same reason as _query_refiner_graph_node above:
    rag_node is `async def`.
    """
    return await rag_node(
        query=state.get("refined_query") or state["query"],
        knowledge_id=state["knowledge_id"],
        user_id=state.get("user_id"),
        session_id=state.get("session_id"),
        owner_id=state.get("owner_id"),
        past_messages=state.get("past_messages"),
        token_queue=state.get("_token_queue"),
        # Whole dict, not a single slice: this node drives TWO roles — the
        # retrieval ReAct loop (rag_agent) and the answer writer
        # (generate_answer) — and resolves each separately.
        request_chat_config=state.get("_request_chat_config"),
    )


async def _chart_graph_node(state: KnowledgeChatState) -> Dict[str, Any]:
    """Thin LangGraph wrapper: unpack state -> chart_node's plain parameters,
    repack its return dict as a partial state update. Runs after rag so it
    has rag's document population (summaries / rag_context) and
    detailed_chunks to work with; never raises (chart_node swallows its
    own failures)."""
    with usage_operation("chart_render"):
        return await _chart_call(state)


async def _chart_call(state: KnowledgeChatState) -> Dict[str, Any]:
    return await chart_node(
        query=state.get("refined_query") or state["query"],
        knowledge_id=state["knowledge_id"],
        owner_id=state.get("owner_id"),
        detailed_chunks=state.get("detailed_chunks"),
        session_id=state.get("session_id"),
        summaries=state.get("summaries"),
        rag_context=state.get("rag_context"),
    )


async def _follow_up_graph_node(state: KnowledgeChatState) -> Dict[str, Any]:
    """Thin LangGraph wrapper: unpack state -> follow_up_node's plain
    parameters. Runs last, so the answer it suggests questions from is final.

    Uses the ORIGINAL query, not refined_query: the follow-ups are shown to
    the user, so they should follow from what the user actually asked.
    Never raises (follow_up_node swallows its own failures)."""
    with usage_operation("follow_up"):
        return await _follow_up_call(state)


async def _follow_up_call(state: KnowledgeChatState) -> Dict[str, Any]:
    return await follow_up_node(
        query=state["query"],
        response=state.get("response") or "",
        is_answered=state.get("is_answered"),
        owner_id=state.get("owner_id"),
        past_messages=state.get("past_messages"),
        chat_config=state.get("_chat_config"),
        knowledge_id=state.get("knowledge_id"),
        session_id=state.get("session_id"),
    )


def build_knowledge_chat_graph():
    """Build and compile the graph: START -> query_refiner -> rag -> chart ->
    follow_up -> END.

    Linear, no conditional routing — there is no planner, no branching (per
    the plan: this service has exactly one capability). No checkpointer —
    HITL pause/resume doesn't apply here (see plan's "No LangGraph
    checkpointer" section); Postgres (chat/db/) is the only history store.
    chart runs unconditionally after rag; it internally no-ops when
    include_charts is off or there's nothing to chart (chat/chart_node.py).
    follow_up runs last and no-ops the same way when the flag is off or the
    turn went unanswered (chat/follow_up.py).
    """
    graph = StateGraph(KnowledgeChatState)
    graph.add_node("query_refiner", _query_refiner_graph_node)
    graph.add_node("rag", _rag_graph_node)
    graph.add_node("chart", _chart_graph_node)
    graph.add_node("follow_up", _follow_up_graph_node)

    graph.add_edge(START, "query_refiner")
    graph.add_edge("query_refiner", "rag")
    graph.add_edge("rag", "chart")
    graph.add_edge("chart", "follow_up")
    graph.add_edge("follow_up", END)

    return graph.compile()


# Built once at import time — a compiled StateGraph is reusable/stateless
# across invocations (no checkpointer, nothing per-run cached on it), same
# convention as a module-level FastAPI router or a module-level LLM client
# elsewhere in this codebase.
_COMPILED_GRAPH = build_knowledge_chat_graph()


# Root LangSmith run for one turn. Everything the graph does nests under this,
# so its rolled-up cost IS the turn's cost — which is the whole point: it gives
# the cost finalizer (chat/db/health_events.py -> utils/knowledge_health/
# cost_finalizer.py) a single run to read the turn's real priced cost back from,
# instead of trusting chat/pricing.py's hardcoded price map.
#
# Named "KnowledgeChat" because chat/langsmith_cost.py's metadata query filters
# on `like(name, "KnowledgeChat%")` — the prefix, so the "- <query>" suffix added
# below for trace-list scannability still matches. Renaming this run means
# updating that filter too.
@traceable(name="KnowledgeChat")
async def _traced_graph_invoke(initial_state: "KnowledgeChatState") -> Dict[str, Any]:
    """Invoke the compiled graph inside a root LangSmith run, returning the
    final state plus that run's id under `langsmith_run_id`.

    The run id is read from the ambient run tree rather than generated here:
    `traceable` has already created the run by the time the body executes, and
    minting our own id would store one that LangSmith has never heard of.

    Tracing is best-effort — with LANGCHAIN_TRACING_V2 unset, or the SDK
    unhappy for any reason, `get_current_run_tree()` returns None and this
    degrades to a plain graph invocation with `langsmith_run_id: None`. A row
    with no run id is simply skipped by the run-id backfill; it must never cost
    us the turn.
    """
    run_id = None
    try:
        rt = get_current_run_tree()
        if rt is not None:
            run_id = str(rt.id)
            # Suffix the query onto the run name so the LangSmith trace list is
            # scannable. Truncated: run names are display strings, and a long
            # question makes the list unreadable.
            q = (initial_state.get("query") or "").replace("\n", " ").strip()
            if q:
                rt.name = f"KnowledgeChat - {q[:80]}"
    except Exception as e:  # tracing must never break a turn
        logger.debug(f"[graph] could not read LangSmith run tree: {e}")

    final_state = await _COMPILED_GRAPH.ainvoke(initial_state)
    out = dict(final_state)
    out["langsmith_run_id"] = run_id
    return out


async def run_knowledge_chat_graph(
    query: str,
    knowledge_id: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    past_messages: Optional[List[Dict[str, Any]]] = None,
    token_queue: Optional[Any] = None,
    request_chat_config: Optional[Dict[str, Dict[str, Any]]] = None,
    chat_config: Optional[Any] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Entry point a future chat endpoint calls: build the initial state,
    invoke the compiled graph, return the final state.

    Parameter list is the union of what query_refiner_node and rag_node
    actually need (chat/query_refiner.py, chat/rag_node.py):
      - query, knowledge_id: required. knowledge_id has NO default, per the
        plan's tenancy section — every retrieval call downstream must carry
        it, and it must never be optional at this boundary.
      - user_id, session_id, owner_id, past_messages: optional identity/
        history inputs both nodes accept as optional parameters.
      - token_queue: optional live-streaming handle for rag_node's answer
        token stream (e.g. an asyncio.Queue the endpoint drains into an SSE
        response). Passed straight through to rag_node, never stored in
        graph state (see _rag_graph_node's docstring).
      - request_chat_config: optional PER-NODE level-1 override map for
        chat/config.py::resolve_chat_config, sourced from the caller's
        request body. Shape {node_name: {"provider", "model", "temperature",
        "system_prompt"}}, where node_name is one of routers/chat.py's
        SUPPORTED_NODES and each inner dict holds any subset of the four
        fields. routers/chat.py has already folded the flat request-level
        fields into each node's entry, so no consumer downstream re-applies
        that precedence. None means no override anywhere.
      - chat_config: the resolved ResolvedChatConfig for this turn
        (chat/config.py), used by the follow_up node for its gate and its
        model. Optional: a caller that resolves no chat config (batch
        reports) simply gets no follow-up questions.
      - chat_id: the turn's id, PRE-generated by the caller (routers/chat.py)
        rather than defaulted by the chat_logs insert, so it can be tagged onto
        the root LangSmith run's metadata here — the run is created before the
        row exists, and a run tagged after the fact could not be found by the
        metadata-strategy cost backfill. Optional: without it the run is still
        tagged with knowledge_id and is still reachable by its own run id, only
        the metadata strategy loses this turn.

    Returns the graph's final state plus `langsmith_run_id` — the root run's id,
    or None when tracing is inactive.
    """
    logger.info(f"[graph] Running knowledge chat graph for knowledge_id={knowledge_id}")

    initial_state: KnowledgeChatState = {
        "query": query,
        "knowledge_id": knowledge_id,
        "user_id": user_id,
        "session_id": session_id,
        "owner_id": owner_id,
        "past_messages": past_messages or [],
        "_token_queue": token_queue,
        "_request_chat_config": request_chat_config,
        "_chat_config": chat_config,
    }

    # knowledge_id/chat_id go on the ROOT run's metadata so
    # chat/langsmith_cost.py::fetch_knowledge_costs can find this turn's run by
    # a server-side filter and key it back to the right health-event row.
    # Passed via langsmith_extra (the SDK's documented per-call hook) rather
    # than mutated onto the run tree, so it is set before the run is first
    # posted instead of only on the closing patch.
    md: Dict[str, Any] = {"knowledge_id": knowledge_id}
    if chat_id:
        md["chat_id"] = str(chat_id)
    if session_id:
        md["session_id"] = session_id
    # Every model call the turn makes — whichever node, sub-agent, fallback or
    # helper makes it — is counted by one run-level collector, and the turn's
    # `usage` block is built from that count (chat/run_usage.py). The block is
    # what the API returns, streams, persists with the turn and what the health
    # event's tokens/cost come from, so all four agree by construction.
    started = time.perf_counter()
    with collect_run_usage() as collector:
        final_state = await _traced_graph_invoke(initial_state, langsmith_extra={"metadata": md})
    latency_ms = int((time.perf_counter() - started) * 1000)
    final_state["usage"] = build_usage_block(
        collector, latency_ms, final_state.get("service_calls") or []
    )
    return final_state
