"""Unit tests for range operators in chat/rag_tools/helpers.py and the Qdrant
condition they build in utils/db/retrieval_ops.py.

Why these exist: normalize_metadata_filter used to DROP every range bound, on a
comment asserting "the backend metadata_filter API does NOT support any
operators". It does — utils/db/retrieval_ops.py::_build_simple_field_condition
has handled bare gte/lte/gt/lt via Qdrant Range all along; the two halves just
disagreed about the `$` prefix. A `date` field is only worth having if an
interval survives the trip, so these pin both ends of it.

No DB and no network: normalization is pure, and the condition builders return
qdrant_client model objects without talking to a server.
"""

import pytest
from qdrant_client.models import DatetimeRange, Range

from chat.rag_tools.helpers import normalize_metadata_filter
from utils.db.retrieval_ops import (
    _build_filter_conditions_from_dict,
    _get_field_index_type,
    _looks_like_date_bound,
)


# ---------------------------------------------------------------------------
# normalize_metadata_filter — bounds survive, in the form retrieval matches
# ---------------------------------------------------------------------------

def test_an_interval_keeps_both_bounds():
    """The regression that mattered: keeping only the first bound silently
    widens the query, turning "in July" into "from July onwards"."""
    out = normalize_metadata_filter(
        {"statement_date": {"$gte": "2019-07-01", "$lte": "2019-07-31"}}
    )
    assert out == {"statement_date": {"gte": "2019-07-01", "lte": "2019-07-31"}}


def test_a_single_bound_survives():
    assert normalize_metadata_filter({"closing_balance": {"$gte": 1000}}) == {
        "closing_balance": {"gte": 1000}
    }


def test_the_dollar_prefix_is_stripped():
    """retrieval_ops matches the BARE form. A `$`-prefixed key falls through its
    range branch and is misread as a nested-field filter."""
    out = normalize_metadata_filter({"d": {"$gt": 1, "$lt": 9}})
    assert set(out["d"]) == {"gt", "lt"}


def test_an_already_bare_range_is_left_alone():
    assert normalize_metadata_filter({"d": {"gte": "2024-01-01"}}) == {
        "d": {"gte": "2024-01-01"}
    }


def test_a_range_is_not_flattened_into_a_nested_field_key():
    """The bug this file was written for. A bare {"gte": ...} left in the dict
    is indistinguishable from a nested-field filter, and the nested handling
    rewrites it to `d.gte` / `d[].gte` — a payload key that does not exist, so
    the filter matches nothing and reports no error."""
    out = normalize_metadata_filter({"d": {"$gte": "2019-01-01"}})
    assert "d" in out
    assert not any("." in k or "[]" in k for k in out)


def test_a_range_alongside_a_flat_filter_keeps_both():
    out = normalize_metadata_filter(
        {"d": {"$gte": "2019-01-01"}, "status": "final"}
    )
    assert out == {"status": "final", "d": {"gte": "2019-01-01"}}


def test_a_range_mixed_with_a_set_operator_keeps_the_interval():
    """No single representation exists for both. The interval is the narrower,
    safer half; the drop is logged, not silent."""
    out = normalize_metadata_filter({"d": {"$gte": "2019-01-01", "$in": ["x"]}})
    assert out == {"d": {"gte": "2019-01-01"}}


# --- everything else must be unaffected ------------------------------------

def test_set_operators_still_pass_through_untouched():
    assert normalize_metadata_filter({"status": {"$nin": ["draft"]}}) == {
        "status": {"$nin": ["draft"]}
    }


def test_nested_dict_flattening_still_works():
    assert normalize_metadata_filter({"address": {"city": "Pune", "state": "MH"}}) == {
        "address.city": "Pune",
        "address.state": "MH",
    }


def test_plain_filters_and_empty_inputs_are_unchanged():
    assert normalize_metadata_filter({"org": "TechNova"}) == {"org": "TechNova"}
    assert normalize_metadata_filter(None) is None
    assert normalize_metadata_filter({}) == {}


# ---------------------------------------------------------------------------
# The Qdrant condition — DatetimeRange vs numeric Range
# ---------------------------------------------------------------------------

def test_date_bounds_build_a_datetime_range():
    """A numeric Range against a DATETIME-indexed field does not match — it
    fails silently and returns nothing, the worst failure mode for a filter."""
    conds = _build_filter_conditions_from_dict(
        {"statement_date": {"gte": "2019-07-01", "lte": "2019-07-31"}}
    )
    assert isinstance(conds[0].range, DatetimeRange)


def test_numeric_bounds_still_build_a_numeric_range():
    conds = _build_filter_conditions_from_dict({"closing_balance": {"gte": 1000}})
    assert isinstance(conds[0].range, Range)
    assert not isinstance(conds[0].range, DatetimeRange)


def test_single_op_syntax_routes_by_value_type_too():
    date_cond = _build_filter_conditions_from_dict(
        {"d": {"op": "gte", "value": "2024-01-01"}}
    )
    num_cond = _build_filter_conditions_from_dict({"n": {"op": "gte", "value": 5}})
    assert isinstance(date_cond[0].range, DatetimeRange)
    assert isinstance(num_cond[0].range, Range) and not isinstance(num_cond[0].range, DatetimeRange)


@pytest.mark.parametrize("value,expected", [
    ("2019-07-01", True),
    ("2019-07-01T10:30:00", True),
    ("2019-07-01T10:30:00Z", True),
    ("16 Jun 19", False),       # the measured unspecified-format defect
    ("2019-07", False),         # a period, not a day — belongs in a str field
    (1000, False),
    (1000.5, False),
    (None, False),
])
def test_date_bound_detection(value, expected):
    assert _looks_like_date_bound(value) is expected


# ---------------------------------------------------------------------------
# Payload index type
# ---------------------------------------------------------------------------

def test_a_date_field_is_indexed_as_datetime():
    """A KEYWORD index on the same ISO string supports only equality; the range
    is the only question worth asking of a date."""
    assert _get_field_index_type("d", [{"field_name": "d", "field_type": "date"}]).lower() == "datetime"


@pytest.mark.parametrize("field_type,expected", [
    ("float", "float"),
    ("int", "integer"),
    ("str", "keyword"),
    ("List[str]", "keyword"),
])
def test_other_index_types_are_unchanged(field_type, expected):
    got = _get_field_index_type("f", [{"field_name": "f", "field_type": field_type}])
    assert got.lower() == expected
