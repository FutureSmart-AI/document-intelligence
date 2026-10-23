"""Unit tests for gap G9 (docs/plan/usage-and-limits/state.md): file_count,
text_count, document_vector_count and chunk_vector_count are now groupable/summable
alongside the token metrics — char_count deliberately is not, because it is emitted
under four different (operation, label) pairs and an unfiltered sum would triple-count
(see the comment on ALL_METRICS in utils/usage/analytics.py).
"""

from utils.usage.analytics import ALL_METRICS, NULLABLE_COLUMN_DEFAULTS
from utils.pydantic_models_usage_analytics import AggregatedMetrics


def test_new_metrics_are_in_the_allowlist():
    for m in ("file_count", "text_count", "document_vector_count", "chunk_vector_count"):
        assert m in ALL_METRICS


def test_char_count_is_deliberately_excluded():
    assert "char_count" not in ALL_METRICS


def test_aggregated_metrics_schema_carries_the_new_fields():
    m = AggregatedMetrics(request_count=1, file_count=5, text_count=2, document_vector_count=10, chunk_vector_count=40)
    assert m.file_count == 5
    assert m.chunk_vector_count == 40


def test_knowledge_id_has_a_null_default_for_grouping():
    """Unattributed events must not silently vanish into a None group key."""
    assert NULLABLE_COLUMN_DEFAULTS.get("knowledge_id") == "unattributed"
