"""Chat endpoint for the Knowledge Service's own chat graph (Phase 5 — see
claude-logs/plan/knowledge-owned-chat.md's "Target design" section and
claude-logs/plan/knowledge-owned-chat-progress.md).

POST /v1/{knowledge_id}/chat — service-to-service capability. Callers are
trusted services (the Orchestrator today) presenting the master x-api-key;
require_permission("use") already returns None for that path and the RBAC
branch is never taken in practice, but the dependency stays wired so a
future session-authenticated caller (Phase B, not this round) is a config
change rather than a rewrite.

Applied ONCE, as a handler parameter — NOT also as a router-level
dependency. routers/search.py double-applies require_permission (router-
level dependency AND handler parameter), which runs the DB-backed check
twice per request; the plan explicitly calls that out as something not to
copy here.
"""
from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from pydantic import BaseModel, Field, field_validator, model_validator

from chat.byok_fallback import ByokKeyRejected, drain_warnings, reset_warnings
from chat.config import resolve_chat_config
from chat.db.chat_logs import get_past_messages, get_session_transcript, insert_chat_log, set_message_feedback
from chat.db.config_ops import (
    WRITABLE_NODE_FIELDS,
    delete_config_row,
    delete_node_config_row,
    get_config_row,
    list_node_config_rows,
    upsert_config_row,
    upsert_node_config_row,
)
from chat.db.health_events import build_health_event, emit_health_event
from chat.db.sessions import list_sessions, soft_delete_session
from chat.graph import run_knowledge_chat_graph
from utils.auth import sample_access
from utils.auth.dependencies import require_permission, resolve_kb_owner_id, PUBLIC_CALLER_SENTINEL
from utils.pydantic_models_usage_limits import EstimatedMetric, Operation
from utils.usage.enforcement import enforce_operation
from utils.logger import logger

router = APIRouter()


class NodeConfig(BaseModel):
    """Per-node LLM config for ONE of this service's LLM roles.

    Mirrors one ``node_configs`` row in the Orchestrator's agent registry
    (fs-agent-chat/agent_registry/pydantic_model_db.py::NodeConfig), minus
    the fields this service has no use for — see ChatRequest.node_configs
    for which node names are accepted and why the rest are dropped.

    Every field is optional: a caller may pin the model but leave the prompt
    to fall through to the DB row / env / hardcoded default, and vice versa.
    An empty object is legal and simply changes nothing for that node.
    """
    provider: Optional[str] = Field(default=None, description="LLM provider, e.g. 'openai', 'anthropic', 'google', 'groq'.")
    model: Optional[str] = Field(default=None, description="LLM model id, e.g. 'gpt-5.4-mini'.")
    temperature: Optional[float] = Field(default=None, description="Sampling temperature. Accepted but NOT yet applied — every get_llm call site in the RAG chain still passes temperature=0. Kept in the schema so callers can send their stored value without it being rejected, and so wiring it later is not a breaking change.")
    max_tokens: Optional[int] = Field(default=None, description="Max output tokens. Only meaningful for 'generate_answer' — the node that actually writes the final response; threaded into its get_llm call.")
    prompt: Optional[str] = Field(default=None, description="Suffix prompt for this node, appended to the node's own base prompt. NOT a full system-prompt replacement.")

    # provider+model must travel together: get_llm resolves a model id
    # against a provider's catalogue, so 'gpt-5.4-mini' with no provider (or
    # a provider with no model) would silently resolve half from the request
    # and half from the DB row/env — a combination the caller never asked
    # for and which may not even be a valid pair. Rejecting is safer than
    # guessing; this mirrors the Orchestrator's own
    # `if node_config.get("provider") and node_config.get("model")` guard
    # (fs-agent-chat/agents/nodes/rag.py:551), which likewise treats the pair
    # as all-or-nothing.
    @model_validator(mode="after")
    def _provider_and_model_together(self):
        if bool(self.provider) != bool(self.model):
            raise ValueError(
                "provider and model must be supplied together (or both omitted)"
            )
        return self


# The LLM roles this service actually runs, and therefore the only keys
# ChatRequest.node_configs accepts. Deliberately NOT the Orchestrator's node
# list — that registry stores 7 distinct node_name values in production
# (generate_answer, rag_agent, refined_query_node, supervisor, response_node,
# plus 'string'/'hello' from API testing), most of which have no counterpart
# here:
#   - supervisor      — this graph has no supervisor. RAG is the sole
#                       producer; there is nothing to route between.
#   - response_node   — the donor's separate rephrase step, deleted in this
#                       service's graph (see the plan's "Two simplifications").
#   - string / hello  — junk rows from someone POSTing the schema placeholder.
# A caller sending any of these gets a 422 naming the accepted keys rather
# than a silent no-op, so a config that cannot be honoured fails loudly at
# the boundary instead of looking applied.
SUPPORTED_NODES = frozenset({"rag_agent", "generate_answer", "refined_query_node"})


class ChatRequest(BaseModel):
    """Request body. knowledge_id itself is a path param (matches
    routers/search.py's SearchRequest convention), not a body field.
    owner_id is optional — if omitted, the router resolves it from the KB's
    own knowledge_config row (utils/auth/dependencies.py::resolve_kb_owner_id),
    the same lookup routers/document_management.py already relies on."""
    query: str = Field(..., description="User's chat query. Already description-enriched if an image was sent to the caller — vision handling stays upstream, see the plan's Vision section.")
    owner_id: Optional[str] = Field(default=None, description="Owner of the knowledge base, for owner LLM credentials and usage attribution. Resolved from knowledge_config when omitted.")
    session_id: Optional[str] = Field(default=None, description="Chat session id. Omit to start a new session (a fresh id is minted and returned).")
    user_id: Optional[str] = Field(default=None, description="Acting user, for audit. Distinct from owner_id. Resolved from the caller's session when omitted.")
    stream: bool = Field(default=True, description="Stream the answer via SSE. When false, the full response is returned as one JSON object.")

    # Vision (option 1, see the plan's "Vision input" section): this service
    # never receives raw image bytes. When an image was attached to the turn
    # upstream, the caller (Orchestrator) sends the description-enriched
    # `query` above, and sets these two fields purely so the transcript row
    # records that the turn HAD an attachment — neither field is read back
    # into retrieval or answer generation.
    has_attachment: bool = Field(default=False, description="Whether an image was attached to this turn upstream. The query above is already description-enriched — this is a transcript-completeness flag only.")
    attachment_url: Optional[str] = Field(default=None, description="URL of the attached image, if any, for transcript/audit purposes only.")

    # Public-embed surface selector (Piece 2, docs/plan/access-and-sharing/archive/design-widget-fullscreen.md).
    # Only meaningful for a credential-less caller — require_permission's public
    # fallback (utils/auth/public_access.py) reads this same flag off the raw
    # request body to decide which pair of knowledge_chat_config fields gates
    # the call: is_public/allowed_domains (widget, the default) or
    # fullscreen_is_public/fullscreen_allowed_domains. An authenticated caller
    # may set it too, but it has no effect on the RBAC path.
    from_widget: bool = Field(default=False, description="Set by the website chat bubble. Records the session's origin as \"widget\" even when the visitor is signed in — a credential-less caller is already detectable server-side, a signed-in one is not, so only the embed can tell us. See SessionSummary.source.")
    from_full_screen: bool = Field(default=False, description="Set by the full-screen chat embed. For a credential-less caller, gates the request against fullscreen_is_public + fullscreen_allowed_domains instead of the widget's is_public + allowed_domains.")

    # Config passthrough — level 1 of chat/config.py::resolve_chat_config's
    # 4-level chain. Threaded through chat/graph.py's _request_chat_config
    # state field into both chat/rag_node.py's and chat/query_refiner.py's
    # resolve_chat_config calls.
    #
    # These four flat fields apply to EVERY node at once. They predate
    # node_configs and are kept for callers that genuinely have one config
    # for the whole turn; node_configs wins per-node where both are given.
    provider: Optional[str] = Field(default=None, description="LLM provider override applied to every node. Falls through to knowledge_chat_config / env / hardcoded default when omitted. Prefer node_configs when nodes differ.")
    model: Optional[str] = Field(default=None, description="LLM model override applied to every node, e.g. 'gpt-5.4-mini'.")
    temperature: Optional[float] = Field(default=None, description="Temperature override applied to every node. Accepted but not yet applied downstream (see NodeConfig.temperature).")
    max_tokens: Optional[int] = Field(default=None, description="Max output tokens override. Only meaningful for the generate_answer node; see NodeConfig.max_tokens.")
    system_prompt: Optional[str] = Field(default=None, description="System prompt suffix applied to the RAG answer. Equivalent to node_configs.rag_agent.prompt; node_configs wins if both are sent.")

    # Per-node overrides. This service runs three distinct LLM roles with
    # genuinely different jobs, and a caller mirroring an Orchestrator agent
    # frequently configures them differently: of the 52 production agents
    # holding node_configs rows, 27 give rag_agent and generate_answer
    # DIFFERENT suffix prompts and 14 give them different models. Collapsing
    # those onto the four flat fields above silently drops one of each pair —
    # which is exactly what happened before this field existed.
    node_configs: Optional[Dict[str, NodeConfig]] = Field(
        default=None,
        description=(
            "Per-node LLM config, keyed by node name. Accepted keys: "
            "'rag_agent' (the retrieval ReAct loop), 'generate_answer' (the "
            "answer writer), 'refined_query_node' (the query rewriter). Any "
            "other key is a 422 — see SUPPORTED_NODES for why the "
            "Orchestrator's 'supervisor'/'response_node' have no counterpart "
            "here. Takes precedence over the flat provider/model/temperature/"
            "system_prompt fields for the nodes it names; unnamed nodes fall "
            "back to those flat fields, then to the DB row / env / default."
        ),
    )

    @field_validator("node_configs")
    @classmethod
    def _reject_unknown_nodes(cls, v):
        """422 on a node name this service cannot honour.

        Silently ignoring an unknown key is the dangerous option: the caller
        sent a config, got a 200, and has no way to learn it was discarded.
        The error names the accepted keys so the fix is obvious from the
        response alone.
        """
        if not v:
            return v
        unknown = sorted(set(v) - SUPPORTED_NODES)
        if unknown:
            raise ValueError(
                f"Unsupported node name(s): {', '.join(unknown)}. "
                f"This service runs only: {', '.join(sorted(SUPPORTED_NODES))}."
            )
        return v

    def _flat_chat_config(self) -> Dict[str, Any]:
        """The four flat request fields as a dict, Nones dropped.

        Nones are dropped rather than passed through so a partial override
        (say, provider+model only) does not null out the temperature and
        system_prompt that the DB row or env would otherwise supply.
        """
        return {
            k: v for k, v in {
                "provider": self.provider,
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "system_prompt": self.system_prompt,
            }.items() if v is not None
        }

    def _request_chat_config(self, node: str) -> Dict[str, Any]:
        """Level-1 override dict for chat/config.py::resolve_chat_config, as
        it applies to ONE node.

        Merge order — flat fields first, then this node's node_configs entry
        on top, so a caller can set a turn-wide default and still special-case
        one node. Only keys actually present survive; everything absent falls
        through to level 2+ of the chain untouched.

        NodeConfig.prompt maps onto the "system_prompt" key because that is
        what resolve_chat_config's request_system_prompt param is named and
        what both consuming nodes read. The rename happens here, once, rather
        than teaching the whole downstream chain a second field name.
        """
        merged = self._flat_chat_config()
        entry = (self.node_configs or {}).get(node)
        if entry:
            if entry.provider and entry.model:
                merged["provider"] = entry.provider
                merged["model"] = entry.model
            if entry.temperature is not None:
                merged["temperature"] = entry.temperature
            if entry.max_tokens is not None:
                merged["max_tokens"] = entry.max_tokens
            if entry.prompt:
                merged["system_prompt"] = entry.prompt
        return merged

    class Config:
        json_schema_extra = {
            "example": {
                "query": "What invoices do we have from March?",
                "owner_id": "owner-123",
                "session_id": None,
                "user_id": "user-456",
                "stream": True,
                "node_configs": {
                    "rag_agent": {
                        "provider": "openai",
                        "model": "gpt-5.4-mini",
                        "prompt": "Prioritise accuracy and completeness in retrieval.",
                    },
                    "generate_answer": {
                        "provider": "google",
                        "model": "gemini-2.5-flash",
                        "prompt": "Answer only from the retrieved context. Include any ClickUp URLs found.",
                    },
                    "refined_query_node": {
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                    },
                },
            }
        }


class DebugInfo(BaseModel):
    """Pipeline diagnostics — HOW the turn was processed, not what it found.

    Mirrors the Orchestrator's build_debug_info shape
    (fs-agent-chat/chat_utils/response_builder/response_builders.py:26-64):
    service-level payloads (sources/summaries/chunks) live ONLY under
    ``data`` — debug_info never duplicates them, it carries latency, which
    code path answered, and the RAG agent's own self-eval signals.
    """
    enabled: bool = True
    latency_ms: Optional[int] = None
    type: Optional[str] = None  # "agentic_rag" | "vanilla_rag" (small-KB direct mode)
    is_answered: Optional[str] = None
    previous_context_used: Optional[bool] = None
    document_ids: Optional[List[str]] = None


class UsageInfo(BaseModel):
    """Aggregate token usage for the turn, plus the per-service-call
    breakdown (chat/utils/service_calls.py::build_service_call — already
    computed inside rag_node/query_refiner_node, just not surfaced until
    now). Matches the Orchestrator's top-level input_tokens/output_tokens/
    total_tokens fields, plus a per-model breakdown the Orchestrator's flat
    fields can't express (this service runs up to 4 distinct LLM roles per
    turn — see the plan's "Per-node model + prompt config" section)."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    service_calls: List[Dict[str, Any]] = Field(default_factory=list)
    # -- Run-level telemetry (chat/run_usage.py; docs/api/usage-telemetry.md) --
    # Counted from EVERY model call of the turn, not only the ones a node
    # reported, so the token totals above include the verifier and follow-ups.
    latency_ms: Optional[int] = Field(None, description="Server wall time of the turn, ms.")
    cached_input_tokens: Optional[int] = Field(None, description="Subset of input_tokens served from the provider's prompt cache.")
    calls: Optional[int] = Field(None, description="Model calls made (embeddings excluded).")
    estimated_cost_usd: Optional[float] = Field(None, description="Sum of the priced operations, from the price table named in price_table. An estimate, not a bill.")
    cost_complete: Optional[bool] = Field(None, description="False when any operation ran on a model with no price; estimated_cost_usd is then a lower bound.")
    unpriced_models: List[str] = Field(default_factory=list, description='"provider/model" of every operation that could not be priced.')
    price_table: Optional[str] = Field(None, description="Version of the price table used, e.g. openai-standard-2026-10-26.")
    operations: List[Dict[str, Any]] = Field(default_factory=list, description="One row per (operation, provider, model): calls, tokens, latency_ms, estimated_cost_usd, fallback, requested_model.")
    operation_latency_ms: Dict[str, int] = Field(default_factory=dict, description="Wall time of each operation's stage, tool time included.")


class ChatResponse(BaseModel):
    """Shaped to match what the Orchestrator's rag_node already puts into
    its own graph state (fs-agent-chat/agents/nodes/rag.py:236-280), so an
    Orchestrator-side migration to this endpoint stays a thin field mapping
    rather than a rewrite. ``data`` nests retrieval evidence under a
    "knowledge" key, matching the Orchestrator's ChatResponse.data
    convention ({"knowledge": {...}, "nl2sql": {...}, ...}) even though
    this service has only one producer — avoids a second, divergent
    envelope shape a future Orchestrator-side consumer would need to learn."""
    query: str
    refined_query: Optional[str] = None
    response: str
    session_id: str
    chat_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    is_answered: Optional[str] = None
    follow_up: Optional[List[str]] = None
    usage: Optional[UsageInfo] = None
    debug_info: Optional[DebugInfo] = None
    error: Optional[str] = None


def _json_safe_rag_context(rag_context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """rag_context["messages"] holds raw LangChain message objects
    (HumanMessage/AIMessage/ToolMessage — see chat/rag_agent/
    agent_execution.py's _extract_retrieved_context_from_messages, which
    stores the ReAct loop's message list verbatim for internal use). Nothing
    in this codebase reads that key back out client-side, and json.dumps
    can't serialize it — strip it before the dict reaches an SSE frame or a
    JSON response body. Everything else in rag_context is already plain
    dicts/lists/primitives (verifier_verdicts is model_dump()'d before
    storage)."""
    if not rag_context:
        return rag_context
    return {k: v for k, v in rag_context.items() if k != "messages"}


def _build_usage(final_state: Dict[str, Any]) -> Dict[str, Any]:
    """The turn's usage block.

    run_knowledge_chat_graph puts the run-level block (chat/run_usage.py --
    every model call, attributed per operation, priced) on final_state["usage"];
    that is returned as-is. The fallback below only serves a final state built
    some other way (tests, an older caller) and is the historical behaviour:

    Sum chat/utils/service_calls.py::build_service_call records
    (query_refiner_node's + rag_node's own LLM calls, accumulated on
    final_state["service_calls"] via the graph's operator.add reducer —
    see chat/graph.py's KnowledgeChatState). Returns zeros, not None, when
    no calls were recorded (e.g. the small-KB direct path, which retrieves
    without an LLM call) so UsageInfo is always well-formed."""
    run_usage = final_state.get("usage")
    if isinstance(run_usage, dict) and "operations" in run_usage:
        return run_usage
    service_calls = final_state.get("service_calls") or []
    input_tokens = sum(int(c.get("input_tokens") or 0) for c in service_calls)
    output_tokens = sum(int(c.get("output_tokens") or 0) for c in service_calls)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "service_calls": service_calls,
    }


def _build_debug_info(final_state: Dict[str, Any]) -> Dict[str, Any]:
    """Pipeline diagnostics, mirroring the Orchestrator's build_debug_info
    (fs-agent-chat/chat_utils/response_builder/response_builders.py:26-64).

    latency_ms sums the RAG step's own service_calls entry (chat/rag_node.py
    already times itself via chat/utils/service_calls.py::build_service_call)
    rather than re-timing here — the endpoint has no wall-clock hook of its
    own, and re-deriving it would double-count query_refiner's latency
    against a metric the Orchestrator scopes to "the RAG turn."

    type: "vanilla_rag" when small-KB direct mode answered (rag_context has
    no "agent_output" key — that field only exists on the agentic ReAct
    path's context dict, see chat/rag_node.py's two return shapes),
    otherwise "agentic_rag".

    previous_context_used / document_ids come from rag_context["agent_output"]
    (chat/rag_agent/agent_output.py's RagAgentOutput fields) — the RAG
    agent's own self-eval, no extra LLM call needed. Cited doc ids reuse the
    same helper rag_node.py's _filter_to_cited already calls, so debug_info
    and the sources actually shown to the user are guaranteed consistent.
    """
    rag_context = final_state.get("rag_context") or {}
    agent_output = rag_context.get("agent_output") or {}

    latency_ms = None
    for call in final_state.get("service_calls") or []:
        if call.get("service") == "rag":
            latency_ms = call.get("latency_ms")
            break

    document_ids = None
    if agent_output:
        try:
            from chat.rag_agent.answer_node import _cited_ids_from_agent_output

            cited_doc_ids, _ = _cited_ids_from_agent_output(agent_output)
            document_ids = sorted(cited_doc_ids) if cited_doc_ids else []
        except Exception as e:
            logger.warning(f"[chat] Failed to extract cited document_ids for debug_info: {e}")

    return {
        "enabled": True,
        "latency_ms": latency_ms,
        "type": "agentic_rag" if agent_output else "vanilla_rag",
        "is_answered": final_state.get("is_answered"),
        "previous_context_used": agent_output.get("previous_context_used"),
        "document_ids": document_ids,
    }


def _build_response_payload(
    final_state: Dict[str, Any], query: str, session_id: str, chat_id: Optional[str] = None,
    show_sources: bool = True, follow_up: Optional[List[str]] = None,
    show_raw_response: bool = True, byok_warnings: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Map run_knowledge_chat_graph's final state dict onto the response
    envelope. response falls back to relevant_context when direct-answer
    mode didn't populate it (rag_node only sets "response" when
    direct_answer_mode is on and the agent produced a non-empty answer —
    see chat/rag_node.py's direct-answer branch).

    data.knowledge nests relevant_sources/summaries/detailed_chunks/
    rag_context under a "knowledge" key, matching the Orchestrator's
    service_data["knowledge"] convention (agents/run.py:788 in
    fs-agent-chat) — kept nested even though this service has one producer,
    so a future Orchestrator-side consumer reads the identical shape it
    already knows rather than learning a second, knowledge-only convention.

    show_sources=False (knowledge_chat_config's per-knowledge_id policy)
    strips relevant_sources/summaries/detailed_chunks from the CLIENT-facing
    response only — the knowledge_chat_logs DB row still carries the full
    evidence via _persist_turn, matching the Orchestrator's own convention
    of stripping knowledge_evidence from the client copy while the DB row
    keeps it (agents/run.py:787-795 in fs-agent-chat).

    show_raw_response=False (same posture as show_sources) omits debug_info
    from the client-facing response entirely — it is still computed (cheap,
    no extra LLM call) so the log line below stays informative, just not
    sent to the caller.

    follow_up is still passed in as a parameter rather than read off
    final_state here, but the caller now takes it straight from
    final_state["follow_up"] — the graph's follow_up node produces it
    (chat/graph.py). Keeping the parameter means a caller that generates
    follow-ups some other way can still supply them."""
    knowledge_data: Dict[str, Any] = {
        "relevant_sources": final_state.get("relevant_sources"),
        "summaries": final_state.get("summaries"),
        "detailed_chunks": final_state.get("detailed_chunks"),
        "rag_context": _json_safe_rag_context(final_state.get("rag_context")),
        # G16: structured chart output from chart_node, already None when
        # include_charts is off, there was nothing to chart, or generation
        # failed — no separate show_sources-style gate needed here.
        "chart_output": final_state.get("chart_output"),
    }
    if not show_sources:
        knowledge_data["relevant_sources"] = None
        knowledge_data["summaries"] = None
        knowledge_data["detailed_chunks"] = None

    payload = {
        "query": query,
        "refined_query": final_state.get("refined_query"),
        "response": final_state.get("response") or "",
        "session_id": session_id,
        "chat_id": chat_id,
        "data": {"knowledge": knowledge_data},
        "is_answered": final_state.get("is_answered"),
        "follow_up": follow_up,
        "usage": _build_usage(final_state),
        "debug_info": _build_debug_info(final_state) if show_raw_response else None,
        "error": final_state.get("error"),
        # Populated only when a BYOK key was rejected mid-turn and the platform key
        # covered for it (chat/byok_fallback.py) — empty/omitted the rest of the
        # time. Tells the client the turn's usage counted against the owner's plan
        # even though a provider key is configured, and that the key needs fixing.
        "byok_warnings": byok_warnings or [],
    }
    return payload


def _resolve_key_type(session_user_id: Optional[str]) -> str:
    """One of this service's own auth modes (docs/modules/auth-and-rbac.md),
    for the health event's key_type — see chat/db/health_events.py's module
    docstring for why session and per-user API keys are NOT split further."""
    if session_user_id == PUBLIC_CALLER_SENTINEL:
        return "public"
    if session_user_id is None:
        return "master_key"
    return "session_or_api_key"


def _resolve_session_source(
    key_type: str,
    from_widget: bool = False,
    from_full_screen: bool = False,
) -> str:
    """Where this conversation is coming from: "widget", "dashboard", or "api".

    Recorded once on the session row (migration 044) so Share -> Activity can split
    its two tabs with a single query instead of inferring origin from a page of
    per-turn health events.

    Two signals, in this order:
      1. either embed flag on the request — the only way to know that a *signed-in*
         visitor is using the website bubble rather than the dashboard. The browser
         has to tell us; nothing in the credentials distinguishes the two.
      2. the auth mode otherwise: a credential-less ("public") caller can only be an
         embed, and the master API key is a server-to-server integration, never a
         person at a screen.

    Note the asymmetry this leaves: an embed that does not send `from_widget` while
    signed in still records as "dashboard". That is the honest answer given what the
    request carries — see docs/plan/chat-and-rag/sessions-api.md.
    """
    if from_widget or from_full_screen:
        return "widget"
    if key_type == "public":
        return "widget"
    if key_type == "master_key":
        return "api"
    return "dashboard"


async def _resolve_caller_profile(request: Request) -> tuple:
    """``(email, name)`` for the caller of a chat turn, best-effort, never raising.

    ``get_auth_details`` is memoized per request (utils/auth/dependencies.py), so for
    a session-authenticated caller this reuses the lookup ``require_permission``
    already made — no second network call. A public caller has no identity at all,
    and a generated-API-key caller has no Cognito session but *is* identified by an
    email (that is what its user_id is), so that case is filled in by the caller
    below rather than here.
    """
    try:
        from utils.auth.dependencies import get_auth_details
        from utils.auth.auth_utils import extract_caller_profile
        return extract_caller_profile(await get_auth_details(request, include_all_claims=True))
    except Exception as e:
        logger.warning(f"[chat] Could not resolve caller profile, session identity unrecorded: {e}")
        return None, None


def _current_trace_id() -> Optional[str]:
    """32-hex OTel trace id for the active span, or None (no-op outside
    tracing, e.g. MODE=LOCAL — see utils/tracing.py)."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context or not span_context.trace_id:
        return None
    return format(span_context.trace_id, "032x")


def _emit_health_event_task(
    *,
    knowledge_id: str,
    owner_id: Optional[str],
    user_id: Optional[str],
    session_id: str,
    chat_id: Optional[str],
    query: str,
    latency_ms: int,
    key_type: str,
    final_state: Dict[str, Any],
) -> None:
    """Fire-and-forget: build + insert the turn's knowledge_health_events row
    without delaying the response already being returned to the caller. No
    chat_id (the chat_logs insert itself failed) means nothing to key the
    dedupe on, so skip rather than mint a substitute id that could never be
    reconciled with the (missing) chat_logs row."""
    if not chat_id:
        logger.warning("[health_events] No chat_id from _persist_turn, skipping health event emission")
        return
    fields = build_health_event(
        knowledge_id=knowledge_id,
        owner_id=owner_id,
        user_id=user_id,
        session_id=session_id,
        chat_id=chat_id,
        query=query,
        latency_ms=latency_ms,
        key_type=key_type,
        trace_id=_current_trace_id(),
        from_widget=key_type == "public",
        final_state=final_state,
    )
    asyncio.create_task(emit_health_event(fields))

    # Then replace this row's ESTIMATED cost with LangSmith's own figures once
    # LangSmith has priced the run. chat/pricing.py's inline number is a
    # placeholder off a hardcoded price map (see
    # utils/knowledge_health/cost_finalizer.py); this is what makes it accurate
    # without anyone running a manual sync.
    #
    # Deliberately a SEPARATE task from the insert above, not chained after it:
    # the finalizer sleeps through LangSmith's async pricing delay (tens of
    # seconds), and the row must be readable immediately, not once pricing lands.
    # It updates by chat_id, so it does not care which task committed first.
    run_id = final_state.get("langsmith_run_id")
    if run_id:
        from utils.knowledge_health.cost_finalizer import finalize_query_cost
        asyncio.create_task(finalize_query_cost(chat_id=chat_id, run_id=run_id))


async def _persist_turn(
    session_id: str,
    knowledge_id: str,
    user_id: Optional[str],
    query: str,
    final_state: Dict[str, Any],
    has_attachment: bool = False,
    attachment_url: Optional[str] = None,
    source: Optional[str] = None,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Optional[str]:
    """Write the turn's knowledge_chat_logs row BEFORE the final SSE frame /
    JSON response is sent to the caller — this is the cross-turn evidence
    memory (service_data.knowledge_evidence) that get_recent_evidence reads
    back next turn (chat/rag_node.py), per the plan's "must be written
    before the final SSE frame" note. Degrades silently on failure —
    insert_chat_log already swallows exceptions (chat/db/chat_logs.py), so a
    DB hiccup here must not fail an otherwise-successful chat turn.

    build_grouped_evidence (chat/evidence_builder.py) does the actual
    grouping from rag_context["tool_responses"] — found missing during this
    phase's wiring (see that module's docstring) and ported alongside this
    endpoint so cross-turn evidence reuse actually works end-to-end, not
    just at the consumer (get_recent_evidence) side.

    Also persists relevant_sources/summaries/detailed_chunks — the same
    already-computed, already-cited-filtered fields the live response sends
    under data.knowledge (_build_response_payload) — alongside
    knowledge_evidence. These are NOT the same thing: knowledge_evidence is a
    lossy, prompt-injection-shaped summary (200-char truncation, no chunk
    text, global-chunk-scan groups carry no summary at all) built for
    re-feeding the next turn's prompt, not for display. Without this, a
    reopened session had nothing behind its sources panel — the DB only ever
    kept the cross-turn-memory shape, never the UI-facing one. Zero extra
    cost: both are already sitting on final_state from this same turn.
    show_sources is deliberately NOT applied here — same posture as the rest
    of this function (Orchestrator convention): the DB row always keeps full
    evidence; stripping for show_sources=False happens at read time (both
    the live response, here, and the session-transcript endpoint).

    Returns the inserted row's chat_id (str) for the response envelope, or
    None when the insert itself failed (insert_chat_log returns None on
    error) — the caller must tolerate a missing chat_id rather than treat it
    as fatal, same degrade-not-fail posture as the rest of this function."""
    rag_context = final_state.get("rag_context") or {}
    try:
        from chat.evidence_builder import build_grouped_evidence
        knowledge_evidence = await build_grouped_evidence(rag_context, knowledge_id=knowledge_id)
    except Exception as e:
        logger.warning(f"[chat] build_grouped_evidence failed, no evidence persisted this turn: {e}")
        knowledge_evidence = []
    service_data: Dict[str, Any] = {
        "knowledge_evidence": knowledge_evidence,
        "relevant_sources": final_state.get("relevant_sources"),
        "summaries": final_state.get("summaries"),
        "detailed_chunks": final_state.get("detailed_chunks"),
        "chart_output": final_state.get("chart_output"),
    }

    usage_by_model = final_state.get("usage_by_model") or []
    # The same usage block the live response carries -- persisted so a thread
    # read back later still shows what each step took and cost.
    run_usage = final_state.get("usage") if isinstance(final_state.get("usage"), dict) else None
    llm_provider = usage_by_model[-1].get("provider") if usage_by_model else None
    model = usage_by_model[-1].get("model") if usage_by_model else None

    # rag_node.py surfaces is_answered as "yes"/"no"/None — convert to a
    # tri-state bool for the DB column (None stays None, not False).
    raw_is_answered = final_state.get("is_answered")
    is_answered = {"yes": True, "no": False}.get(raw_is_answered)

    chat_log = await insert_chat_log(
        session_id=session_id,
        knowledge_id=knowledge_id,
        user_id=user_id,
        query=query,
        response=final_state.get("response") or "",
        refined_query=final_state.get("refined_query"),
        llm_provider=llm_provider,
        model=model,
        service_data=service_data,
        has_attachment=has_attachment,
        attachment_url=attachment_url,
        is_answered=is_answered,
        usage=run_usage,
        # Caller facts, recorded on the session row rather than the log row.
        source=source,
        user_email=user_email,
        user_name=user_name,
        # Pre-minted by the caller so the graph's root LangSmith run could be
        # tagged with it before this row existed (see chat/graph.py).
        chat_id=chat_id,
    )
    return str(chat_log.chat_id) if chat_log is not None else None


async def _sse_stream(
    query: str,
    knowledge_id: str,
    user_id: Optional[str],
    session_id: str,
    owner_id: str,
    past_messages: List[Dict[str, Any]],
    chat_config,
    request_chat_config: Optional[Dict[str, Any]] = None,
    has_attachment: bool = False,
    attachment_url: Optional[str] = None,
    key_type: str = "session_or_api_key",
    source: Optional[str] = None,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Frame contract matches the Orchestrator's SSE generator exactly
    (fs-agent-chat/chat_utils/langchain_processing/request_handler.py):
    plain ``data: <json>\\n\\n`` frames, ``{"chunk": ...}`` per streamed
    token, a final unwrapped full-response frame, ``{"error": ...}`` on
    failure. Identical framing means an Orchestrator caller can forward
    frames to the browser with minimal translation.

    run_knowledge_chat_graph's token_queue has NO completion sentinel (the
    producer, chat/rag_agent/answer_node.py, only ever calls put_nowait —
    see that file's streaming branch) — so the graph run and the queue-drain
    loop are raced as two concurrent tasks, and draining stops once the
    graph-run task is done (plus one final drain pass for anything queued
    in the gap between the last queue.get() and the task completing).
    """
    token_queue: asyncio.Queue = asyncio.Queue()
    turn_started = asyncio.get_event_loop().time()

    # Start collecting BYOK fallback notices for this turn — see
    # chat/byok_fallback.py's module docstring for why a contextvar rather than
    # threading a warning list through every node's return shape. asyncio copies
    # the context into the task created below, so collection follows the graph
    # run even though it happens on a different Task than this generator.
    reset_warnings()

    # Minted here, not left to the chat_logs insert's uuid4 default, because the
    # graph's root LangSmith run is created before that row exists and has to
    # carry this id in its metadata for the cost backfill to key the run back to
    # the row (chat/graph.py, chat/langsmith_cost.py).
    chat_id = str(uuid.uuid4())

    graph_task = asyncio.create_task(run_knowledge_chat_graph(
        query=query,
        knowledge_id=knowledge_id,
        user_id=user_id,
        session_id=session_id,
        owner_id=owner_id,
        past_messages=past_messages,
        token_queue=token_queue,
        request_chat_config=request_chat_config,
        chat_config=chat_config,
        chat_id=chat_id,
    ))

    try:
        while not graph_task.done():
            try:
                chunk = await asyncio.wait_for(token_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if chunk:
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

        # Drain anything queued between the last get() and task completion.
        while not token_queue.empty():
            chunk = token_queue.get_nowait()
            if chunk:
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

        final_state = await graph_task

        # Follow-up questions are produced by the graph's own follow_up node
        # now (chat/graph.py), not fired here alongside the log write — that
        # kept the LLM call inside the turn's LangSmith trace. It costs the
        # old overlap with insert_chat_log, a single INSERT.
        follow_up = final_state.get("follow_up")
        chat_id = await _persist_turn(
            session_id, knowledge_id, user_id, query, final_state,
            has_attachment=has_attachment, attachment_url=attachment_url,
            source=source, user_email=user_email, user_name=user_name,
            chat_id=chat_id,
        )
        _emit_health_event_task(
            knowledge_id=knowledge_id, owner_id=owner_id, user_id=user_id,
            session_id=session_id, chat_id=chat_id, query=query,
            latency_ms=int((asyncio.get_event_loop().time() - turn_started) * 1000),
            key_type=key_type, final_state=final_state,
        )

        payload = _build_response_payload(
            final_state, query, session_id, chat_id=chat_id,
            show_sources=chat_config.show_sources, follow_up=follow_up,
            show_raw_response=chat_config.show_raw_response,
            byok_warnings=drain_warnings(),
        )
        # default=str: never let one unexpected non-serializable value
        # (e.g. a future field that leaks a raw object, the way rag_context
        # used to before _json_safe_rag_context) crash the final frame after
        # the turn was already persisted successfully.
        yield f"data: {json.dumps(payload, default=str)}\n\n"

    except asyncio.CancelledError:
        logger.info(f"[chat] Stream cancelled by client disconnect (session_id={session_id})")
        graph_task.cancel()
        return
    except ByokKeyRejected as e:
        # The owner's key was rejected AND they are over their usage limit, so no
        # fallback was attempted — surface the actionable message rather than a
        # bare "Exception" the client can't act on.
        logger.warning(f"[chat] BYOK key rejected, no fallback available: {e}")
        yield f"data: {json.dumps({'error': e.user_message, 'byok_warnings': [{'code': 'byok_key_rejected', 'provider': e.provider, 'error_type': e.error_type, 'message': e.user_message}]})}\n\n"
    except Exception as e:
        logger.error(f"[chat] Unexpected error in SSE stream: {e}", exc_info=True)
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


@router.post("/v1/{knowledge_id}/chat")
async def chat(
    http_request: Request,
    knowledge_id: str = Path(..., description="Knowledge ID"),
    request: ChatRequest = Body(..., description="Chat request parameters"),
    session_user_id: Optional[str] = Depends(require_permission("use", allow_public=True)),
):
    """Chat over a knowledge base's indexed documents.

    Runs chat/graph.py's query_refiner -> rag graph. Persists the turn to
    knowledge_chat_sessions/knowledge_chat_logs (session ownership: this
    service, per the plan's "Who calls this endpoint" §3, Option (a)).
    Streams via SSE when request.stream is true (default); returns a single
    JSON ChatResponse otherwise. 404s if knowledge_chat_config.chat_enabled
    is explicitly false for this knowledge_id.
    """
    session_id = request.session_id or str(uuid.uuid4())

    # The body's owner_id/user_id are honoured for the master key only — a
    # service-to-service caller acting for an owner. Anyone else is charged to the
    # collection's real owner and recorded as who they are: a session or key that
    # could name another owner would spend that owner's quota and credentials, and
    # one that could name another user would write into their conversations
    # (sample collections depend on both, see utils/auth/sample_access.py).
    is_master_key = session_user_id is None
    owner_id = (request.owner_id if is_master_key else None) or await resolve_kb_owner_id(knowledge_id)

    # A public (credential-less) caller has no real identity —
    # PUBLIC_CALLER_SENTINEL is an internal auth marker, never a user id.
    user_id = (request.user_id if is_master_key else None) or (
        session_user_id if session_user_id != PUBLIC_CALLER_SENTINEL else None
    )

    # A session id is only ever continued inside the collection it belongs to:
    # its past turns are loaded into this answer's context, so a foreign one
    # would read another collection's conversation into this one.
    if request.session_id:
        existing = await sample_access.get_session_identity(request.session_id)
        if existing and existing.get("knowledge_id") != knowledge_id:
            raise HTTPException(status_code=404, detail=f"Session '{request.session_id}' not found")

    chat_config = await resolve_chat_config(knowledge_id)
    if not chat_config.chat_enabled:
        raise HTTPException(status_code=404, detail="Chat is not enabled for this knowledge base")

    # Two gates, both before the graph runs — once SSE streaming has begun, a 429
    # can only be reported as an error event inside a 200, which every client handles
    # differently.
    #
    # `send_message` counts every turn and carries the BYOK exemption: the message
    # aggregate excludes `owner-key` LLM events, so a customer paying their own
    # provider bill is not throttled on it. Public callers still charge the KB's
    # resolved owner — a public caller has no identity but the owner still pays.
    #
    # `start_conversation` runs first, and only when the request opens a genuinely new
    # session (`request.session_id` was empty and we minted one). Continuing a session
    # never charges it: cutting a user off in the middle of a thread they're already in
    # is a worse experience than refusing a new one at the start. The count is
    # `COUNT(DISTINCT session_id)` in the current calendar month, so re-entering a
    # session started this month adds nothing.
    session_is_new = not request.session_id
    if session_is_new:
        await enforce_operation(
            owner_id=owner_id,
            operation=Operation.start_conversation,
            estimated_metrics=[EstimatedMetric(name="conversations", value=1)],
            source="chat:new_session",
        )
    # One physical message feeds two windows — the calendar month and today (00:00
    # UTC) — via two metric names; both ceilings apply, whichever fills first refuses.
    await enforce_operation(
        owner_id=owner_id,
        operation=Operation.send_message,
        estimated_metrics=[
            EstimatedMetric(name="messages", value=1),
            EstimatedMetric(name="messages_today", value=1),
        ],
        source="chat",
    )

    try:
        past_messages = await get_past_messages(session_id)
    except Exception as e:
        logger.warning(f"[chat] Failed to load past messages for session_id={session_id}: {e}")
        past_messages = []

    # One resolved override dict per node, not one for the whole turn — the
    # three roles can carry different providers/models/prompts (see
    # ChatRequest.node_configs). Built once here and threaded through the
    # graph rather than re-derived per node, so the request is parsed exactly
    # once no matter how many nodes run.
    request_chat_config = {
        node: request._request_chat_config(node) for node in SUPPORTED_NODES
    }

    key_type = _resolve_key_type(session_user_id)

    # Origin + person, resolved once per turn and recorded on the session row
    # (migration 044) so Share -> Activity can name both without a second request.
    session_source = _resolve_session_source(
        key_type, from_widget=request.from_widget, from_full_screen=request.from_full_screen
    )
    caller_email, caller_name = await _resolve_caller_profile(http_request)
    if not caller_email and isinstance(user_id, str) and "@" in user_id:
        # A generated-API-key caller has no Cognito session, but its user_id *is* the
        # email captured when the key was made (routers/api_key_management.py).
        caller_email = user_id.strip().lower()

    if request.stream:
        return StreamingResponse(
            _sse_stream(
                query=request.query,
                knowledge_id=knowledge_id,
                user_id=user_id,
                session_id=session_id,
                owner_id=owner_id,
                past_messages=past_messages,
                chat_config=chat_config,
                request_chat_config=request_chat_config,
                has_attachment=request.has_attachment,
                attachment_url=request.attachment_url,
                key_type=key_type,
                source=session_source,
                user_email=caller_email,
                user_name=caller_name,
            ),
            media_type="text/event-stream",
        )

    turn_started = asyncio.get_event_loop().time()
    reset_warnings()
    # See the streaming path above for why this is minted here rather than
    # defaulted by the chat_logs insert.
    chat_id = str(uuid.uuid4())
    try:
        final_state = await run_knowledge_chat_graph(
            query=request.query,
            knowledge_id=knowledge_id,
            user_id=user_id,
            session_id=session_id,
            owner_id=owner_id,
            past_messages=past_messages,
            request_chat_config=request_chat_config,
            chat_config=chat_config,
            chat_id=chat_id,
        )
    except ByokKeyRejected as e:
        # Owner's key is dead and they're already over their usage limit — the
        # platform key intentionally did not cover for them (see
        # chat/byok_fallback.py). 422: the request is well-formed, but the
        # account's own credential is the reason it cannot be served, same
        # spirit as usage_limit_enforcement's 429 being a different, actionable
        # status than a bare 500.
        raise HTTPException(status_code=422, detail={
            "error": "byok_key_rejected", "provider": e.provider,
            "error_type": e.error_type, "message": e.user_message,
        })
    except Exception as e:
        logger.error(f"[chat] run_knowledge_chat_graph failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}")

    # follow_up comes off the graph's final state (chat/graph.py's follow_up
    # node), so the LLM call it makes is part of the turn's LangSmith trace.
    follow_up = final_state.get("follow_up")
    chat_id = await _persist_turn(
        session_id, knowledge_id, user_id, request.query, final_state,
        has_attachment=request.has_attachment, attachment_url=request.attachment_url,
        source=session_source, user_email=caller_email, user_name=caller_name,
        chat_id=chat_id,
    )
    _emit_health_event_task(
        knowledge_id=knowledge_id, owner_id=owner_id, user_id=user_id,
        session_id=session_id, chat_id=chat_id, query=request.query,
        latency_ms=int((asyncio.get_event_loop().time() - turn_started) * 1000),
        key_type=key_type, final_state=final_state,
    )

    return _build_response_payload(
        final_state, request.query, session_id, chat_id=chat_id,
        show_sources=chat_config.show_sources, follow_up=follow_up,
        show_raw_response=chat_config.show_raw_response,
        byok_warnings=drain_warnings(),
    )


# ---------------------------------------------------------------------------
# Chat config CRUD — level 2 of chat/config.py::resolve_chat_config's chain.
#
# Auth differs from the chat turn above on purpose: the turn is a
# service-to-service capability ("use"), while these are administrative
# read/write of a knowledge base's settings, so they take "read"/"write" —
# matching routers/knowledge_config_new.py's GET/PUT /v1/{knowledge_id}/config.
# ---------------------------------------------------------------------------


class ChatConfigResponse(BaseModel):
    """The EFFECTIVE chat config for a knowledge_id, not just the stored row.

    Values come from resolve_chat_config, so they reflect what a chat turn
    would actually use after the DB-row -> env -> hardcoded fallthrough.
    ``configured`` distinguishes "a knowledge_chat_config row exists" from
    "these are inherited defaults" — without it a caller cannot tell whether
    a PUT would create or update, nor whether DELETE has anything to reset.
    """
    knowledge_id: str
    configured: bool = Field(..., description="Whether a knowledge_chat_config row exists for this knowledge_id. False means every value below is an env/hardcoded default.")
    system_prompt: Optional[str] = Field(default=None, description="System prompt suffix appended to the base RAG answer prompt. Not the query refiner's own instructions.")
    llm_provider: str
    llm_model: str
    temperature: float
    max_tokens: int = Field(..., description="Max output tokens for the generate_answer node. Actually threaded into that node's get_llm call — unlike temperature, this is not accepted-but-inert.")
    show_sources: bool = Field(..., description="Whether relevant_sources/summaries/detailed_chunks are included in the client-facing chat response. The transcript row always keeps the full evidence.")
    chat_enabled: bool = Field(..., description="When false, POST /v1/{knowledge_id}/chat returns 404.")
    follow_up: bool = Field(..., description="Whether follow-up questions are generated after each answered turn.")
    suggested_questions: List[str] = Field(default_factory=list, description="Starter prompts to show on an empty conversation. Editorial content the caller writes directly — not generated by this service.")
    voice_input_enabled: bool = Field(default=False, description="Client-facing preference only. This service has no speech-to-text pipeline — setting this gates nothing server-side; it only tells the dashboard whether to render a mic control.")
    voice_output_enabled: bool = Field(default=False, description="Client-facing preference only. This service has no text-to-speech pipeline — setting this gates nothing server-side; it only tells the dashboard whether to render a speaker control.")
    include_charts: bool = Field(default=True, description="Whether the generate_answer node is instructed to format numeric/tabular answers as markdown tables. No structured chart_output field exists on ChatResponse — this only shapes model behavior.")
    show_raw_response: bool = Field(default=True, description="Whether debug_info is included in the client-facing chat response.")
    # Public-access gating (Piece 2, docs/plan/access-and-sharing/archive/design-widget-fullscreen.md).
    # Present only for an authenticated (owner/editor/master-key) caller — omitted
    # entirely for a public embed hitting this same GET with allow_public=True,
    # same posture as fs-agent-chat's equivalent fields ("returned only to
    # authenticated callers"). See the router body for how that's enforced.
    is_public: Optional[bool] = Field(default=None, description="Widget is publicly embeddable. Independent of fullscreen_is_public. Omitted from a public caller's own response.")
    allowed_domains: Optional[List[str]] = Field(default=None, description="Hostnames allowed to embed the widget. Empty/null = any origin, once is_public is true.")
    fullscreen_is_public: Optional[bool] = Field(default=None, description="Full-screen chat is publicly shareable. Independent of is_public.")
    fullscreen_allowed_domains: Optional[List[str]] = Field(default=None, description="Hostnames allowed to load the full-screen chat. Empty/null = any origin, once fullscreen_is_public is true.")
    node_configs: Dict[str, "NodeConfigState"] = Field(
        default_factory=dict,
        description=(
            "Per-node stored overrides, keyed by node name. EVERY supported "
            "node is always present — an unconfigured one has all-null "
            "overrides and configured=false — so a UI can render the full "
            "form without knowing the node list in advance."
        ),
    )


class NodeConfigState(BaseModel):
    """One node's STORED override, plus what it actually resolves to.

    The two halves answer different questions. The override fields say what
    this knowledge base has pinned for this node (null = inherit); the
    ``effective_*`` fields say what a turn would really run, after falling
    through to the knowledge-level config / env / hardcoded default. A UI
    needs both: the former to populate an editable form, the latter to show
    the user what is in force when the form is empty.
    """
    configured: bool = Field(..., description="Whether a knowledge_chat_node_config row exists for this node.")
    provider: Optional[str] = Field(default=None, description="Pinned provider, or null to inherit.")
    model: Optional[str] = Field(default=None, description="Pinned model, or null to inherit.")
    temperature: Optional[float] = Field(default=None, description="Pinned temperature, or null to inherit. Stored but NOT yet applied — see UpdateChatConfigBody.temperature.")
    max_tokens: Optional[int] = Field(default=None, description="Pinned max output tokens, or null to inherit. Only meaningful on generate_answer.")
    prompt: Optional[str] = Field(default=None, description="This node's own suffix prompt, or null for none.")
    enabled: bool = Field(default=True, description="Whether this node runs at all.")
    effective_provider: str = Field(..., description="Provider a turn would actually use for this node, after the full fallthrough.")
    effective_model: str = Field(..., description="Model a turn would actually use for this node.")


class UpdateNodeConfigBody(BaseModel):
    """One node's stored override, as supplied to PUT.

    Distinct from the request-side NodeConfig above: this one carries
    ``enabled`` (stored policy, not a per-turn choice) and permits an explicit
    null to CLEAR an override so the field inherits again — a distinction the
    per-turn model has no need for, since a request that wants to inherit
    simply omits the field.
    """
    provider: Optional[str] = Field(default=None, description="Pinned provider for this node. Null clears the override.")
    model: Optional[str] = Field(default=None, description="Pinned model for this node. Null clears the override.")
    temperature: Optional[float] = Field(default=None, description="Pinned temperature. Stored but NOT yet applied.")
    max_tokens: Optional[int] = Field(default=None, description="Pinned max output tokens. Null clears the override. Only meaningful on generate_answer.")
    prompt: Optional[str] = Field(default=None, description="This node's own suffix prompt, appended to its base prompt. Null clears it.")
    enabled: Optional[bool] = Field(default=None, description="Whether this node runs at all.")


class UpdateChatConfigBody(BaseModel):
    """Partial update — every field optional, only the ones supplied are
    written. Omitting a field leaves the stored value alone (it does NOT
    reset it), mirroring resolve_chat_config's per-field independence. Use
    DELETE to reset the whole knowledge_id back to defaults."""
    system_prompt: Optional[str] = Field(default=None, description="System prompt suffix appended to the base RAG answer prompt.")
    llm_provider: Optional[str] = Field(default=None, description="LLM provider, e.g. 'openai', 'anthropic', 'groq', 'google', 'deepseek'. Applies to the generate_answer node only — refined_query_node/rag_agent resolve from their per-node env vars or hardcoded defaults; pin them via node_configs.")
    llm_model: Optional[str] = Field(default=None, description="LLM model id, e.g. 'gpt-5.4-mini'. Same answer-only scope as llm_provider.")
    temperature: Optional[float] = Field(default=None, description="Sampling temperature. Note: not yet threaded into the RAG chain's get_llm call sites, which still use 0.")
    max_tokens: Optional[int] = Field(default=None, description="Max output tokens for the generate_answer node. Threaded into that node's get_llm call.")
    show_sources: Optional[bool] = Field(default=None, description="Include retrieval evidence in the client-facing chat response.")
    chat_enabled: Optional[bool] = Field(default=None, description="Set false to make POST /v1/{knowledge_id}/chat return 404 for this knowledge base.")
    follow_up: Optional[bool] = Field(default=None, description="Generate follow-up questions after each answered turn.")
    suggested_questions: Optional[List[str]] = Field(default=None, description="Starter prompts to show on an empty conversation. Caller-authored content, not generated.")
    voice_input_enabled: Optional[bool] = Field(default=None, description="Client-facing preference only — no server-side speech-to-text exists. Controls whether the dashboard renders a mic control.")
    voice_output_enabled: Optional[bool] = Field(default=None, description="Client-facing preference only — no server-side text-to-speech exists. Controls whether the dashboard renders a speaker control.")
    include_charts: Optional[bool] = Field(default=None, description="Whether the generate_answer node is instructed to format numeric/tabular answers as markdown tables.")
    show_raw_response: Optional[bool] = Field(default=None, description="Whether debug_info is included in the client-facing chat response.")
    is_public: Optional[bool] = Field(default=None, description="Widget is publicly embeddable. Independent of fullscreen_is_public — toggling one never affects the other.")
    allowed_domains: Optional[List[str]] = Field(default=None, description="Hostnames allowed to embed the widget. Bare lowercase hostnames; '*.example.com' wildcards supported. Empty list = any origin, once is_public is true.")
    fullscreen_is_public: Optional[bool] = Field(default=None, description="Full-screen chat is publicly shareable. Independent of is_public.")
    fullscreen_allowed_domains: Optional[List[str]] = Field(default=None, description="Hostnames allowed to load the full-screen chat. Same format as allowed_domains, independent list.")
    node_configs: Optional[Dict[str, UpdateNodeConfigBody]] = Field(
        default=None,
        description=(
            "Per-node overrides to store, keyed by node name. Accepted keys: "
            "'rag_agent', 'generate_answer', 'refined_query_node'; any other "
            "key is a 422. Only the nodes named are touched — omitting a node "
            "leaves its stored row alone. To make a node inherit again, "
            "DELETE its row via /chat/config/nodes/{node_name}."
        ),
    )

    @field_validator("node_configs")
    @classmethod
    def _reject_unknown_config_nodes(cls, v):
        """Same 422-not-silence rule as ChatRequest.node_configs: a stored
        config for a node this service never runs would look applied forever
        while doing nothing."""
        if not v:
            return v
        unknown = sorted(set(v) - SUPPORTED_NODES)
        if unknown:
            raise ValueError(
                f"Unsupported node name(s): {', '.join(unknown)}. "
                f"This service runs only: {', '.join(sorted(SUPPORTED_NODES))}."
            )
        return v


class ChatConfigWriteResponse(BaseModel):
    knowledge_id: str
    message: str


@router.get("/v1/{knowledge_id}/chat/config", response_model=ChatConfigResponse)
async def get_chat_configuration(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    _caller: Optional[str] = Depends(require_permission("read", allow_public=True)),
):
    """Get the effective chat config for a knowledge base.

    Never 404s on a missing row — absence is the normal default case, so the
    resolved env/hardcoded values are returned with ``configured: false``.
    Chat genuinely works with no row at all.

    GET /v1/{knowledge_id}/config (knowledge config) used to differ here by
    404ing when uninitialized; it no longer does, and now reports its own
    platform defaults the same way this endpoint does. Both configs are
    optional, so creating a collection is a single POST /v1 call.
    """
    try:
        if not knowledge_id:
            raise HTTPException(status_code=400, detail="knowledge_id must be provided")

        row = await get_config_row(knowledge_id)
        resolved = await resolve_chat_config(knowledge_id)

        # Every supported node appears in the response, configured or not, so
        # the UI can render the full form without hardcoding the node list.
        # The per-node resolve_chat_config calls give each node its OWN
        # effective values, which differ from the knowledge-level ones above
        # exactly when that node has a row.
        node_rows = {r.node_name: r for r in await list_node_config_rows(knowledge_id)}
        node_configs = {}
        for node in sorted(SUPPORTED_NODES):
            node_row = node_rows.get(node)
            node_resolved = await resolve_chat_config(knowledge_id, node_name=node)
            node_configs[node] = NodeConfigState(
                configured=node_row is not None,
                provider=node_row.provider if node_row else None,
                model=node_row.model if node_row else None,
                temperature=node_row.temperature if node_row else None,
                max_tokens=node_row.max_tokens if node_row else None,
                prompt=node_row.prompt if node_row else None,
                enabled=node_row.enabled if node_row else True,
                effective_provider=node_resolved.llm_provider,
                effective_model=node_resolved.llm_model,
            )

        from utils.auth.dependencies import PUBLIC_CALLER_SENTINEL
        is_authenticated_caller = _caller != PUBLIC_CALLER_SENTINEL

        return ChatConfigResponse(
            knowledge_id=knowledge_id,
            configured=row is not None,
            system_prompt=resolved.system_prompt,
            llm_provider=resolved.llm_provider,
            llm_model=resolved.llm_model,
            temperature=resolved.temperature,
            max_tokens=resolved.max_tokens,
            show_sources=resolved.show_sources,
            chat_enabled=resolved.chat_enabled,
            follow_up=resolved.follow_up,
            suggested_questions=resolved.suggested_questions or [],
            voice_input_enabled=resolved.voice_input_enabled,
            voice_output_enabled=resolved.voice_output_enabled,
            include_charts=resolved.include_charts,
            show_raw_response=resolved.show_raw_response,
            # Gating fields only for an authenticated caller — never leak them
            # back to the public embed that just used them to get in.
            is_public=row.is_public if (row and is_authenticated_caller) else None,
            allowed_domains=row.allowed_domains if (row and is_authenticated_caller) else None,
            fullscreen_is_public=row.fullscreen_is_public if (row and is_authenticated_caller) else None,
            fullscreen_allowed_domains=row.fullscreen_allowed_domains if (row and is_authenticated_caller) else None,
            node_configs=node_configs,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving chat configuration for {knowledge_id}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to retrieve chat configuration")


@router.put("/v1/{knowledge_id}/chat/config", response_model=ChatConfigWriteResponse)
async def update_chat_configuration(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    body: UpdateChatConfigBody = Body(..., description="Chat config fields to set"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """Create or partially update a knowledge base's chat config (upsert).

    Only the fields present in the body are written. An empty body is a 400
    rather than a silent no-op — exclude_unset means we cannot distinguish
    "sent nothing" from "sent nothing meaningful", and a 200 on a write that
    changed nothing is misleading.
    """
    try:
        if not knowledge_id:
            raise HTTPException(status_code=400, detail="knowledge_id is required")

        # exclude_unset, not exclude_none: an explicitly-sent null is a real
        # intent to clear system_prompt, distinct from omitting the field.
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No config fields supplied")

        # node_configs lives in a child table, not on knowledge_chat_config —
        # split it out before the flat upsert sees it.
        node_updates = updates.pop("node_configs", None) or {}

        # A body carrying ONLY node_configs must not create an empty
        # knowledge-level row: that row's presence is what `configured`
        # reports, and a phantom row would claim the knowledge base is
        # configured when only one of its nodes is.
        if updates:
            await upsert_config_row(knowledge_id, updates)

        for node_name, node_body in node_updates.items():
            # Per node, again exclude_unset — an omitted field leaves the
            # stored override alone, while an explicit null clears it so the
            # field inherits from the knowledge-level config again.
            node_fields = {
                k: v for k, v in node_body.items()
                if k in WRITABLE_NODE_FIELDS
            }
            if not node_fields:
                continue
            await upsert_node_config_row(knowledge_id, node_name, node_fields)

        return ChatConfigWriteResponse(
            knowledge_id=knowledge_id,
            message="Chat configuration saved successfully",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving chat configuration for {knowledge_id}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to save chat configuration")


@router.delete("/v1/{knowledge_id}/chat/config", response_model=ChatConfigWriteResponse)
async def delete_chat_configuration(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """Reset a knowledge base's chat config to env/hardcoded defaults by
    deleting its row AND every per-node override row.

    Deleting both is what "reset to defaults" has to mean: leaving node rows
    behind would keep overriding the very defaults the caller just asked to
    fall back to. 404s only when there was nothing at all to delete — neither
    a knowledge-level row nor any node row — so a caller can tell an actual
    reset from a no-op.
    """
    try:
        if not knowledge_id:
            raise HTTPException(status_code=400, detail="knowledge_id is required")

        node_rows = await list_node_config_rows(knowledge_id)
        deleted_nodes = 0
        for node_row in node_rows:
            if await delete_node_config_row(knowledge_id, node_row.node_name):
                deleted_nodes += 1

        deleted = await delete_config_row(knowledge_id)
        if not deleted and not deleted_nodes:
            raise HTTPException(status_code=404, detail="No chat configuration exists for this knowledge_id")

        return ChatConfigWriteResponse(
            knowledge_id=knowledge_id,
            message="Chat configuration reset to defaults",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting chat configuration for {knowledge_id}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to delete chat configuration")


@router.delete("/v1/{knowledge_id}/chat/config/nodes/{node_name}", response_model=ChatConfigWriteResponse)
async def delete_chat_node_configuration(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    node_name: str = Path(..., description="Node name: rag_agent, generate_answer, or refined_query_node"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """Clear ONE node's override so it inherits the knowledge-level config
    again. There is no PUT here — writes go through the nested node_configs
    field on PUT /chat/config; this exists because clearing every field of a
    node row is not the same as removing the row, and only the latter makes
    `configured` false again."""
    try:
        if node_name not in SUPPORTED_NODES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unsupported node name: {node_name}. "
                    f"This service runs only: {', '.join(sorted(SUPPORTED_NODES))}."
                ),
            )

        deleted = await delete_node_config_row(knowledge_id, node_name)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"No stored config exists for node '{node_name}' on this knowledge_id",
            )

        return ChatConfigWriteResponse(
            knowledge_id=knowledge_id,
            message=f"Config for node '{node_name}' reset to defaults",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting node config {knowledge_id}/{node_name}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to delete node configuration")


# ---------------------------------------------------------------------------
# Sessions — read-back for the Conversations screen. Every turn is already
# persisted by _persist_turn/insert_chat_log above; these three endpoints
# are the missing other half (list + reopen + remove), over rows that
# already exist. See docs/screen-api-flow-document-intelligence.md §2b/§8b
# for why this was previously blocking — the tables were always shaped for
# this, nothing here was invented.
# ---------------------------------------------------------------------------

class SessionSummary(BaseModel):
    session_id: str
    title: Optional[str] = Field(None, description="session_title if ever set, else the session's first query, truncated to 120 chars.")
    exchange_count: int
    user_id: Optional[str] = None
    user_email: Optional[str] = Field(None, description="The caller's address, captured from their session when the conversation started (migration 044). Null for a public/widget visitor, and for any session recorded before that migration — this service cannot resolve an account id to an address after the fact.")
    user_name: Optional[str] = Field(None, description="Display name from the same session, when the identity provider supplied one. Null otherwise — fall back to user_email, not to the id.")
    source: Optional[str] = Field(None, description='Where the conversation came from: "widget" (website bubble or full-screen embed), "dashboard" (signed in at a screen), or "api" (master-key integration). Null for sessions recorded before migration 044. Owner-vs-invited is NOT encoded here — use ?source=shared, which derives it from the collection\'s current owner.')
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = Field(None, description="The most recent turn's timestamp. Null only for a session with no turns yet.")


class SessionListResponse(BaseModel):
    sessions: List[SessionSummary]
    total_count: int
    current_page: int
    total_pages: int


class ChatMessage(BaseModel):
    """One turn split into its user/assistant halves, matching the message-list
    shape a chat transcript UI actually renders (and the shape fs-agent-chat's
    own session endpoint already uses) instead of a query+response pair.

    Both halves of one exchange share `chat_id` and `n`. Fields that only
    apply to one role are null on the other rather than duplicated.
    """
    n: int = Field(..., description="1-indexed position of the exchange this message belongs to.")
    chat_id: str
    role: str = Field(..., description='"user" or "assistant"')
    message: str
    created_at: Optional[datetime] = None
    # assistant-only
    is_answered: Optional[bool] = None
    refined_query: Optional[str] = None
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    user_feedback: Optional[str] = Field(
        None, description='"positive", "negative", or null. Set via PATCH .../messages/{chat_id}/feedback.'
    )
    usage: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "assistant-only. The turn's usage block exactly as the live /chat response "
            "returned it (latency_ms, tokens, operations, estimated_cost_usd, "
            "cost_complete, price_table). Null for turns stored before it was recorded."
        ),
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "assistant-only. data.knowledge.{relevant_sources,summaries,detailed_chunks} "
            "are genuinely the same fields the live /chat response returns (persisted "
            "verbatim by _persist_turn, G14) — not a relabeled stand-in. "
            "data.knowledge.knowledge_evidence is a SEPARATE, lossier shape kept for "
            "cross-turn memory only; don't treat it as a sources-panel substitute. "
            "rag_context is the one live field not persisted. See KnowledgeChatLogs' "
            "docstring."
        ),
    )
    # user-only
    has_attachment: bool = False
    attachment_url: Optional[str] = None


class SessionDetailResponse(BaseModel):
    session_id: str
    knowledge_id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    source: Optional[str] = None
    title: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    exchange_count: int
    messages: List[ChatMessage]


class SessionDeleteResponse(BaseModel):
    session_id: str
    message: str


class MessageFeedbackRequest(BaseModel):
    feedback: Literal["positive", "negative", None] = Field(
        ..., description='"positive", "negative", or null to clear previously-given feedback.'
    )


class MessageFeedbackResponse(BaseModel):
    chat_id: str
    feedback: Optional[str]
    message: str


@router.get("/v1/{knowledge_id}/sessions", response_model=SessionListResponse)
async def list_chat_sessions(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    from_dt: Optional[datetime] = Query(None, alias="from", description="ISO 8601. Filter to sessions created on/after this time."),
    to_dt: Optional[datetime] = Query(None, alias="to", description="ISO 8601. Filter to sessions created on/before this time."),
    q: Optional[str] = Query(None, description="Case-insensitive substring match against session_title and every turn's query/response text."),
    user_id: Optional[str] = Query(None, description="Filter to sessions started by this user."),
    source: Optional[str] = Query(None, description='Filter by origin: "widget", "dashboard", "api", or "shared" (a dashboard conversation held by somebody other than this collection\'s current owner). A session recorded before migration 044 has no origin and matches none of these; it still appears when the filter is omitted.'),
    exclude_user_id: Optional[str] = Query(None, description="Drop this person's own conversations. Matched case-insensitively against both user_id and user_email, so either an account id or an email works."),
    sort: Optional[str] = Query(None, description='Sort key: "last_activity" (default), "questions", "title", "user". An unrecognised value falls back to the default.'),
    order: str = Query("desc", description='"asc" or "desc" (default).'),
    _: Optional[str] = Depends(require_permission("read")),
):
    """List a knowledge base's chat sessions, most recently active first.

    Powers two screens. The Conversations rail (the owner's own threads): search,
    date-range filter, per-session exchange counts. And Share -> Activity, which asks
    the opposite question — what other people did with a shared collection — via
    ``source``/``exclude_user_id``.

    Every filter and the sort are applied in SQL, so one tab is one query:
    ``total_count`` is that tab's real total and a page is a page of the filtered
    result. (Before this, a caller had to split and re-count a page in the browser,
    which meant a page of 20 could render 3 rows and the counts described the page
    rather than the collection.)
    """
    try:
        result = await list_sessions(
            knowledge_id=knowledge_id,
            page=page,
            page_size=page_size,
            from_dt=from_dt,
            to_dt=to_dt,
            q=q,
            user_id=user_id,
            source=source,
            exclude_user_id=exclude_user_id,
            sort=sort,
            order=order,
        )
        return SessionListResponse(**result)
    except Exception as e:
        logger.error(f"Error listing sessions for knowledge_id={knowledge_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list sessions: {e}")


@router.get("/v1/{knowledge_id}/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_chat_session(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    session_id: str = Path(..., description="Session ID"),
    _: Optional[str] = Depends(require_permission("read")),
):
    """Reopen one session's full transcript, oldest turn first, numbered.

    404s for an unknown session_id, a session_id belonging to a different
    knowledge_id, or a soft-deleted session — all three are "not found" from
    the caller's side, and get_session_transcript already can't tell them
    apart from a genuine miss without leaking which knowledge_id a foreign
    session_id belongs to.

    show_sources=False strips relevant_sources/summaries/detailed_chunks from
    every assistant message here, same as the live /chat response — the DB
    row (get_session_transcript) always returns the full stored evidence;
    this endpoint is where that policy is applied for reads, mirroring
    _build_response_payload's client-facing strip.
    """
    try:
        transcript = await get_session_transcript(knowledge_id, session_id)
        if transcript is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

        chat_config = await resolve_chat_config(knowledge_id)
        if not chat_config.show_sources:
            for message in transcript["messages"]:
                knowledge_data = message.get("data", {}).get("knowledge")
                if isinstance(knowledge_data, dict):
                    knowledge_data["relevant_sources"] = None
                    knowledge_data["summaries"] = None
                    knowledge_data["detailed_chunks"] = None

        return SessionDetailResponse(**transcript)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching session {knowledge_id}/{session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch session: {e}")


@router.patch(
    "/v1/{knowledge_id}/sessions/{session_id}/messages/{chat_id}/feedback",
    response_model=MessageFeedbackResponse,
)
async def set_chat_message_feedback(
    body: MessageFeedbackRequest,
    knowledge_id: str = Path(..., description="Knowledge ID"),
    session_id: str = Path(..., description="Session ID"),
    chat_id: str = Path(..., description="chat_id of the turn being rated"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """👍/👎 on one answer (G13). `{"feedback": "positive"}` / `"negative"` /
    `null` (clears it) — always a full replace, not a partial update, so
    `feedback` is required in the body rather than optional.

    Session-managed rather than a bare `/chat/{chat_id}/feedback`: scoping
    the write by knowledge_id + session_id + chat_id together (not chat_id
    alone) means a caller can't rate a turn outside the session/knowledge
    base it's actually looking at, even though chat_id is already globally
    unique on its own.

    404s (not a silent no-op) when the three don't all match one row —
    see set_message_feedback's docstring for why this deliberately doesn't
    swallow like the read-path helpers in this module do.
    """
    try:
        updated = await set_message_feedback(knowledge_id, session_id, chat_id, body.feedback)
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"chat_id '{chat_id}' not found in session '{session_id}'",
            )
        return MessageFeedbackResponse(chat_id=chat_id, feedback=body.feedback, message="Feedback saved")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting feedback for chat_id={chat_id}, session={session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save feedback: {e}")


@router.delete("/v1/{knowledge_id}/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_chat_session(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    session_id: str = Path(..., description="Session ID"),
    _: Optional[str] = Depends(require_permission("delete")),
):
    """Soft-delete a session. Its knowledge_chat_logs rows are left in place
    (evidence/audit trail) — only the session's own visibility flips, the
    same soft-delete posture as DELETE /v1/{knowledge_id} for collections."""
    try:
        deleted = await soft_delete_session(knowledge_id, session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found or already deleted")
        return SessionDeleteResponse(session_id=session_id, message="Session deleted")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session {knowledge_id}/{session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {e}")
