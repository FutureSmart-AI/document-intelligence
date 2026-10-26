"""Unit tests for the LangSmith cost finalization path.

Covers utils/knowledge_health/cost_finalizer.py's write rules and
chat/langsmith_cost.py::fetch_node_data's node attribution. Pure unit tests —
the LangSmith SDK client is replaced with a fake, and nothing here touches a
DB, the network, or an LLM.

Why these two things specifically. The write rules decide when an ESTIMATED
cost gets overwritten, and getting them wrong replaces a usable number with a
confident wrong one. The node attribution decides which node an LLM call is
billed to, and getting it wrong silently splits one node into two series in
every by_node aggregation.
"""

import uuid
from datetime import datetime, timedelta

import pytest


# ── write rules (utils/knowledge_health/cost_finalizer.py) ────────────────────

def test_unpriced_run_writes_tokens_but_never_a_cost():
    """LangSmith prices asynchronously, so a run is routinely readable with
    tokens and no cost yet. Writing 0.0 there would overwrite a real estimate
    with a wrong number."""
    from utils.knowledge_health.cost_finalizer import _usage_to_values

    vals = _usage_to_values({"input_tokens": 10, "output_tokens": 5, "cost_usd": None})

    assert vals == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert "cost_usd" not in vals
    assert "cost_complete" not in vals


def test_priced_run_writes_cost_and_marks_it_complete():
    from utils.knowledge_health.cost_finalizer import _usage_to_values

    vals = _usage_to_values({"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.25})

    assert vals["cost_usd"] == 0.25
    assert vals["cost_complete"] is True
    assert vals["total_tokens"] == 15


def test_a_genuine_zero_cost_is_written_not_swallowed():
    """0.0 is a real price (a free/cached model), and must not be dropped the
    way a falsy check would drop it. Only None means "not priced"."""
    from utils.knowledge_health.cost_finalizer import _usage_to_values

    vals = _usage_to_values({"cost_usd": 0.0})

    assert vals["cost_usd"] == 0.0
    assert vals["cost_complete"] is True


def test_empty_usage_writes_nothing():
    from utils.knowledge_health.cost_finalizer import _usage_to_values

    assert _usage_to_values({}) == {}


def test_as_uuid_coerces_str_and_rejects_junk():
    """chat_id is a PG UUID column — a str left uncoerced matches no row on
    asyncpg rather than erroring, which would look like a silent no-op."""
    from utils.knowledge_health.cost_finalizer import _as_uuid

    u = uuid.uuid4()
    assert _as_uuid(u) is u
    assert _as_uuid(str(u)) == u
    with pytest.raises(ValueError):
        _as_uuid("not-a-uuid")


# ── node attribution (chat/langsmith_cost.py) ────────────────────────────────

class _FakeRun:
    """Minimal stand-in for a langsmith Run."""

    def __init__(self, name, run_type=None, parent=None, it=0, ot=0, ic=None, oc=None,
                 model=None, provider=None, error=None, secs=1):
        self.id = uuid.uuid4()
        self.name = name
        self.run_type = run_type
        self.parent_run_id = parent
        self.input_tokens = it
        self.output_tokens = ot
        self.total_tokens = it + ot
        self.input_cost = ic
        self.output_cost = oc
        self.error = error
        self.start_time = datetime(2026, 1, 1)
        self.end_time = self.start_time + timedelta(seconds=secs)
        self.trace_id = None
        self.extra = {"metadata": {"ls_model_name": model, "ls_provider": provider}}


def _trace():
    """A representative turn's trace.

    Deliberately includes an llm nested under an UNNAMED RunnableSequence: that
    is the normal LangChain shape, so matching only an llm run's direct parent
    would label almost every call "other".
    """
    root = _FakeRun("KnowledgeChat - hi")
    rag = _FakeRun("rag", parent=root.id)
    seq = _FakeRun("RunnableSequence", parent=rag.id)
    llm_rag = _FakeRun("gpt", run_type="llm", parent=seq.id, it=100, ot=20,
                       ic=0.001, oc=0.002, model="gpt-5.4-mini", provider="openai", secs=2)
    ans = _FakeRun("rag_answer_node", parent=root.id)
    llm_ans = _FakeRun("gpt", run_type="llm", parent=ans.id, it=500, ot=80,
                       ic=0.01, oc=0.02, model="gpt-5.4", provider="openai", secs=3)
    refiner = _FakeRun("query_refiner", parent=root.id)
    llm_unpriced = _FakeRun("gpt", run_type="llm", parent=refiner.id, it=7, ot=3,
                            ic=None, oc=None, model="mystery-model", provider="openai")
    orphan = _FakeRun("Detached", run_type="llm", parent=None, it=1, ot=1, ic=0.5, oc=0.5)
    return root, [root, rag, seq, llm_rag, ans, llm_ans, refiner, llm_unpriced, orphan]


@pytest.fixture
def node_data(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_PROJECT", "test-project")
    root, runs = _trace()

    class _FakeClient:
        def read_run(self, run_id):
            return root

        def list_runs(self, **kwargs):
            return runs

    import langsmith
    monkeypatch.setattr(langsmith, "Client", _FakeClient)

    from chat.langsmith_cost import fetch_node_data
    return {d["node_name"]: d for d in fetch_node_data(str(root.id))}


def test_walks_past_unnamed_parents_to_the_owning_node(node_data):
    assert "rag_agent" in node_data
    assert node_data["rag_agent"]["cost_usd"] == pytest.approx(0.003)
    assert node_data["rag_agent"]["input_tokens"] == 100


def test_answer_synthesis_stays_its_own_line(node_data):
    """rag_answer_node must NOT fold into rag_agent — the split between
    retrieval spend and answer spend is the point of the breakdown."""
    assert node_data["generate_answer"]["model"] == "gpt-5.4"
    assert node_data["generate_answer"]["cost_usd"] == pytest.approx(0.03)


def test_unpriced_call_nulls_its_node_cost_but_keeps_tokens(node_data):
    """Summing only the priced calls would understate the node without saying
    so — same rule as chat/pricing.py::price_breakdown."""
    assert node_data["refiner"]["cost_usd"] is None
    assert node_data["refiner"]["input_tokens"] == 7


def test_llm_with_no_named_ancestor_falls_back_to_other(node_data):
    assert node_data["other"]["cost_usd"] == pytest.approx(1.0)


def test_latency_comes_from_the_runs_own_span(node_data):
    assert node_data["rag_agent"]["latency_ms"] == 2000
    assert node_data["generate_answer"]["latency_ms"] == 3000


def test_only_canonical_local_node_names_are_emitted(node_data):
    """The by_node aggregation groups on node_name, so a LangSmith-native name
    leaking through would split one node into two series across a finalize."""
    canonical = {"refiner", "rag_agent", "generate_answer", "chart", "follow_up", "other"}
    assert set(node_data) <= canonical


def test_node_data_uses_this_repos_cost_key(node_data):
    """utils/knowledge_health/analytics.py::_node_agg reads `cost_usd`. The
    donor emits `total_cost` instead; emitting that shape here would null out
    every by_node cost figure after a finalize."""
    for entry in node_data.values():
        assert "cost_usd" in entry


def test_no_root_run_id_returns_empty():
    from chat.langsmith_cost import fetch_node_data

    assert fetch_node_data("") == []


def test_node_labels_all_map_to_canonical_names():
    from chat.langsmith_cost import _NODE_LABELS

    assert set(_NODE_LABELS.values()) <= {
        "refiner", "rag_agent", "generate_answer", "chart", "follow_up",
    }
