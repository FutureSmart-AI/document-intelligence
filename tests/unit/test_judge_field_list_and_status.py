"""Unit tests for the two Tier 2 fixes in utils/knowledge_health/extraction_validator.py.

Pure unit tests — both targets are pure functions, so nothing here touches a DB,
the network, or an LLM.

Why these specifically.

**The field list.** `_JUDGE_PROMPT` opened with "For each field below, decide
whether the extracted value is SUPPORTED" and no field list was ever supplied:
`field_configs` reached `run_llm_judge`, sat in the signature, and was never
interpolated. The judge inferred its own field list from whichever keys survived
in `extracted`, which moved the denominator between runs (the source of the
±0.12 variance) and made a dropped field unjudgeable — the judge never knew to
look for it. A regression here is silent: the prompt still renders, the judge
still answers, and the field list quietly goes back to being whatever the
extraction happened to contain.

**The status.** `ST_PASSED if (correctness is None or correctness >= 1.0)` meant
a judge that decided nothing reported a clean bill of health. The refusal was
right; the label was not.

See docs/modules/extraction-validation-tiers.md for both mechanisms and
docs/plan/enrichment-analytics/state.md open items 1 and 2.
"""

import pytest

from utils.knowledge_health.extraction_validator import (
    ST_FAILED,
    ST_PASSED,
    ST_SKIPPED,
    _JUDGE_PROMPT,
    _render_field_list,
)


CONFIGS = [
    {"field_name": "governing_law", "field_type": "str", "is_required": True,
     "description": "Jurisdiction whose law governs the contract."},
    {"field_name": "parties", "field_type": "List[str]",
     "description": "Legal names of the contracting entities."},
    {"field_name": "renewal_term", "field_type": "str",
     "description": "Length of each automatic renewal period."},
]


# ---------------------------------------------------------------------------
# the field list
# ---------------------------------------------------------------------------

def test_every_configured_field_is_rendered():
    rendered = _render_field_list(CONFIGS, {"governing_law": "Delaware"})
    for name in ("governing_law", "parties", "renewal_term"):
        assert name in rendered


def test_declared_type_and_description_reach_the_judge():
    """The judge must rule against the schema the extractor was given, not one
    it reconstructs from the values it can see."""
    rendered = _render_field_list(CONFIGS, {})
    assert "List[str]" in rendered
    assert "Jurisdiction whose law governs the contract." in rendered
    assert ", required" in rendered


def test_a_field_missing_from_the_extraction_is_marked_not_present():
    """The whole point of the fix. A dropped field is invisible to a judge
    reading only `extracted`, so absence has to be stated explicitly."""
    rendered = _render_field_list(CONFIGS, {"governing_law": "Delaware"})
    line = next(l for l in rendered.splitlines() if l.startswith("- parties"))
    assert "NOT PRESENT" in line
    present = next(l for l in rendered.splitlines() if l.startswith("- governing_law"))
    assert "NOT PRESENT" not in present


@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_an_empty_value_counts_as_not_present(empty):
    """An empty list is a dropped field wearing a different costume — the
    extractor emitted the key and no content."""
    rendered = _render_field_list(CONFIGS, {"parties": empty})
    line = next(l for l in rendered.splitlines() if l.startswith("- parties"))
    assert "NOT PRESENT" in line


def test_retired_fields_are_not_judged():
    """`is_active=False` is a field withdrawn from the contract the extractor
    was working to; judging against it would report a defect for a field nobody
    asked for."""
    configs = CONFIGS + [{"field_name": "retired_field", "field_type": "str",
                          "is_active": False}]
    rendered = _render_field_list(configs, {})
    assert "retired_field" not in rendered


def test_no_configs_says_so_rather_than_rendering_an_empty_block():
    """An empty block reads to the judge as 'no fields exist', which is a
    different claim from 'nobody told me'."""
    assert _render_field_list([], {}) == "(no field configuration was supplied)"


def test_a_runaway_description_cannot_crowd_out_the_source():
    """The field list shares a token budget with the source text; a schema must
    not be able to evict the document it is judged against."""
    configs = [{"field_name": f"f{i}", "field_type": "str",
                "description": "x" * 5000} for i in range(50)]
    rendered = _render_field_list(configs, {})
    assert len(rendered) < 5000
    assert "truncated" in rendered


def test_the_prompt_actually_interpolates_the_field_list():
    """Guards the original bug directly: the placeholder must exist in the
    template, or the renderer is dead code again."""
    assert "{fields}" in _JUDGE_PROMPT
    filled = _JUDGE_PROMPT.replace("{fields}", _render_field_list(CONFIGS, {}))
    assert "{fields}" not in filled
    assert "governing_law" in filled


# ---------------------------------------------------------------------------
# the status
# ---------------------------------------------------------------------------

def _status_for(correctness):
    """Mirrors the expression in run_llm_judge's return. Kept as a local so the
    rule is asserted without constructing a full judge run."""
    if correctness is None:
        return ST_SKIPPED
    return ST_PASSED if correctness >= 1.0 else ST_FAILED


@pytest.mark.parametrize("correctness,expected", [
    (None, ST_SKIPPED),   # the fix: was ST_PASSED
    (1.0, ST_PASSED),
    (0.99, ST_FAILED),
    (0.0, ST_FAILED),
])
def test_no_opinion_is_skipped_not_passed(correctness, expected):
    assert _status_for(correctness) == expected


def test_the_source_expression_matches_this_rule():
    """Reads the shipped expression so this file cannot drift into asserting a
    rule the module no longer implements."""
    import inspect

    from utils.knowledge_health import extraction_validator

    source = inspect.getsource(extraction_validator.run_llm_judge)
    assert "ST_PASSED if (correctness is None" not in source, (
        "the passed-on-no-opinion expression is back"
    )
    assert "ST_SKIPPED if correctness is None" in source
