"""Unit tests for utils/config/field_type_resolver.py — resolving a described
enrichment field to a code-level field_type, so a caller never has to send one
(docs/plan/field-and-retrieval-precision/terminology.md).

Pure unit tests: get_llm is monkeypatched with a fake chain (same seam as
tests/unit/test_field_generation.py and test_follow_up.py); no real LLM call,
no DB, no network I/O.
"""

import asyncio

import pytest

from utils.config.field_type_resolver import (
    CURRENCY_FIELD_NAME,
    FieldTypeResolution,
    FieldTypeResolutionResponse,
    ensure_currency_field,
    _guard,
    needs_type_resolution,
    resolve_field_types,
)


class _FakeStructuredChain:
    def __init__(self, response, raise_error=None):
        self._response = response
        self._raise_error = raise_error

    async def ainvoke(self, inputs):
        if self._raise_error:
            raise self._raise_error
        return self._response


class _FakePrompt:
    def __init__(self, chain):
        self._chain = chain

    def __or__(self, other):
        return self._chain


class _FakeLLM:
    def __init__(self, chain):
        self._chain = chain

    def with_structured_output(self, schema):
        return self._chain


def _patch_llm(monkeypatch, response, raise_error=None):
    chain = _FakeStructuredChain(response, raise_error)
    import langchain_core.prompts as prompts_module
    monkeypatch.setattr(
        prompts_module.ChatPromptTemplate,
        "from_messages",
        staticmethod(lambda messages: _FakePrompt(chain)),
    )

    async def _fake_get_llm(*args, **kwargs):
        return _FakeLLM(chain), "openai", "platform-key"

    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)


def _run(coro):
    # asyncio.run, not get_event_loop().run_until_complete — the latter picks up
    # a loop another test module has already closed (test_field_generation.py
    # uses asyncio.run), which fails only when the modules run together.
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# needs_type_resolution — the deterministic-first rule
# ---------------------------------------------------------------------------

def test_explicit_field_type_needs_no_resolution():
    assert needs_type_resolution({"field_name": "x", "field_type": "str"}) is False


def test_missing_or_blank_field_type_needs_resolution():
    assert needs_type_resolution({"field_name": "x"}) is True
    assert needs_type_resolution({"field_name": "x", "field_type": None}) is True
    assert needs_type_resolution({"field_name": "x", "field_type": "  "}) is True


def test_explicitly_typed_batch_makes_no_llm_call(monkeypatch):
    """The whole point of deterministic-first: an existing integration that
    still sends field_type must not pay for, or be changed by, an LLM call."""
    def _explode(*args, **kwargs):
        raise AssertionError("resolver must not call the LLM when every type is explicit")

    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _explode)

    fields = [{"field_name": "vendor", "field_type": "str", "description": "d"}]
    out, resolutions, added = _run(resolve_field_types(fields))
    assert out == fields
    assert resolutions == []
    assert added == []


# ---------------------------------------------------------------------------
# _guard — never trust the model's type discipline
# ---------------------------------------------------------------------------

def test_guard_passes_a_valid_type():
    ft, schema, warnings = _guard(
        FieldTypeResolution(field_name="f", field_type="date", reason="a single calendar date")
    )
    assert (ft, schema, warnings) == ("date", None, [])


@pytest.mark.parametrize("bogus", ["Text", "Money", "datetime", "List[Dict]", "", "number"])
def test_guard_rejects_a_hallucinated_type_and_reports_it(bogus):
    ft, schema, warnings = _guard(
        FieldTypeResolution(field_name="f", field_type=bogus, reason="r")
    )
    assert ft == "str"
    assert any("unsupported field_type" in w for w in warnings)


@pytest.mark.parametrize("internal", ["chunks", "text"])
def test_guard_refuses_the_internal_types(internal):
    """'chunks' is the real internal type the worker writes, and a caller-facing
    path must never be able to produce it — it selects the dynamic chunk model.
    'text' is a retired value nothing writes anymore; it is tested here
    defensively, to confirm it still can never leak through as if it were a
    valid caller-facing type."""
    ft, _, warnings = _guard(
        FieldTypeResolution(field_name="f", field_type=internal, reason="r")
    )
    assert ft == "str"
    assert warnings


def test_guard_drops_a_schema_on_a_scalar_type():
    ft, schema, warnings = _guard(
        FieldTypeResolution(
            field_name="f", field_type="str", field_schema={"a": "str"}, reason="r"
        )
    )
    assert ft == "str"
    assert schema is None
    assert any("does not take one" in w for w in warnings)


def test_guard_keeps_a_valid_table_schema():
    ft, schema, warnings = _guard(
        FieldTypeResolution(
            field_name="transactions",
            field_type="List[dict]",
            field_schema={"date": "date", "amount": "float", "description": "str"},
            reason="rows of transactions",
        )
    )
    assert ft == "List[dict]"
    assert schema == {"date": "date", "amount": "float", "description": "str"}
    assert warnings == []


def test_guard_defaults_an_unsupported_subfield_type_and_reports_it():
    ft, schema, warnings = _guard(
        FieldTypeResolution(
            field_name="rows",
            field_type="List[dict]",
            field_schema={"amount": "decimal", "label": "str"},
            reason="r",
        )
    )
    assert ft == "List[dict]"
    assert schema == {"amount": "str", "label": "str"}
    assert any("decimal" in w for w in warnings)


def test_guard_drops_a_badly_named_subfield():
    _, schema, warnings = _guard(
        FieldTypeResolution(
            field_name="rows",
            field_type="List[dict]",
            field_schema={"Amount Paid": "float", "ok_name": "str"},
            reason="r",
        )
    )
    assert schema == {"ok_name": "str"}
    assert any("Amount Paid" in w for w in warnings)


# ---------------------------------------------------------------------------
# End-to-end resolution
# ---------------------------------------------------------------------------

def test_resolves_a_described_field(monkeypatch):
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(
            field_name="statement_date", field_type="date",
            reason="one specific calendar day",
        )
    ]))
    fields = [{"field_name": "statement_date", "description": "The date on the statement"}]
    out, resolutions, added = _run(resolve_field_types(fields))

    assert out[0]["field_type"] == "date"
    assert resolutions[0]["resolved_by"] == "llm"
    assert resolutions[0]["reason"] == "one specific calendar day"
    assert added == []


def test_table_resolution_writes_the_schema_under_the_schema_key(monkeypatch):
    """sync_field_configurations_sqlmodel_v2 reads 'schema', not 'field_schema'
    — a schema written under the wrong key silently produces the no-declared-
    schema defect this is meant to avoid."""
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(
            field_name="transactions", field_type="List[dict]",
            field_schema={"date": "date", "amount": "float"},
            is_monetary=True, reason="repeating rows",
        )
    ]))
    out, _, _ = _run(resolve_field_types(
        [{"field_name": "transactions", "description": "Table of transactions"}]
    ))
    assert out[0]["schema"] == {"date": "date", "amount": "float"}


def test_missing_resolution_falls_back_and_says_so(monkeypatch):
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[]))
    out, resolutions, _ = _run(resolve_field_types(
        [{"field_name": "mystery", "description": "?"}]
    ))
    assert out[0]["field_type"] == "str"
    assert resolutions[0]["resolved_by"] == "fallback"
    assert any("no type" in w for w in resolutions[0]["warnings"])


def test_table_without_a_schema_is_reported_not_swallowed(monkeypatch):
    """Defect 1 in field-recommendation.md: a List[dict] with no declared
    schema cannot succeed. The field is still created, but the caller has to be
    able to see why it will fail."""
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(field_name="rows", field_type="List[dict]", reason="a table")
    ]))
    _, resolutions, _ = _run(resolve_field_types(
        [{"field_name": "rows", "description": "some rows"}]
    ))
    assert any("no sub-fields were declared" in w for w in resolutions[0]["warnings"])


def test_llm_failure_propagates(monkeypatch):
    """Never degrade to 'everything is str' — that is indistinguishable from a
    successful resolution."""
    _patch_llm(monkeypatch, None, raise_error=RuntimeError("provider down"))
    with pytest.raises(Exception):
        _run(resolve_field_types([{"field_name": "x", "description": "d"}]))


def test_only_untyped_fields_are_resolved(monkeypatch):
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(field_name="untyped", field_type="float", reason="r")
    ]))
    out, resolutions, _ = _run(resolve_field_types([
        {"field_name": "typed", "field_type": "List[dict]", "description": "d"},
        {"field_name": "untyped", "description": "an amount"},
    ]))
    assert out[0]["field_type"] == "List[dict]"   # untouched
    assert out[1]["field_type"] == "float"
    assert [r["field_name"] for r in resolutions] == ["untyped"]


# ---------------------------------------------------------------------------
# Money: float amount + one document-level currency field
# ---------------------------------------------------------------------------

def test_monetary_field_gets_a_currency_field_added(monkeypatch):
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(
            field_name="closing_balance", field_type="float",
            is_monetary=True, reason="a sum of money",
        )
    ]))
    out, resolutions, added = _run(resolve_field_types(
        [{"field_name": "closing_balance", "description": "Closing balance"}]
    ))

    assert out[0]["field_type"] == "float"
    assert len(added) == 1
    assert added[0]["field_name"] == CURRENCY_FIELD_NAME
    assert added[0]["field_type"] == "str"
    assert added[0]["level"] == "document"
    assert resolutions[0]["is_monetary"] is True


def test_monetary_description_points_at_the_currency_field(monkeypatch):
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(
            field_name="amount", field_type="float", is_monetary=True, reason="money"
        )
    ]))
    out, _, _ = _run(resolve_field_types(
        [{"field_name": "amount", "description": "Transaction amount"}]
    ))
    description = out[0]["description"]
    assert "Transaction amount" in description
    assert CURRENCY_FIELD_NAME in description
    assert "no currency symbol" in description


def test_currency_field_is_not_duplicated_when_one_already_exists(monkeypatch):
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(
            field_name="total", field_type="float", is_monetary=True, reason="money"
        )
    ]))
    _, _, added = _run(resolve_field_types(
        [{"field_name": "total", "description": "Total"}],
        existing_field_names=["currency", "title"],
    ))
    assert added == []


def test_no_currency_field_when_nothing_is_monetary(monkeypatch):
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(field_name="vendor", field_type="str", reason="a name")
    ]))
    _, _, added = _run(resolve_field_types(
        [{"field_name": "vendor", "description": "Vendor name"}]
    ))
    assert added == []


def test_monetary_table_also_gets_the_currency_field(monkeypatch):
    """The bank-statement case: amounts live in table rows as bare floats, and
    the currency is stated once at document level."""
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(
            field_name="transactions", field_type="List[dict]",
            field_schema={"date": "date", "amount": "float", "balance": "float"},
            is_monetary=True, reason="rows with monetary columns",
        )
    ]))
    out, _, added = _run(resolve_field_types(
        [{"field_name": "transactions", "description": "Transactions with date, amount, balance"}]
    ))
    assert out[0]["schema"]["amount"] == "float"
    assert "currency" not in out[0]["schema"]      # never a per-row currency
    assert [f["field_name"] for f in added] == [CURRENCY_FIELD_NAME]


@pytest.mark.parametrize("existing", ["currency_code", "iso_currency", "currency_type", "CURRENCY"])
def test_currency_variants_are_recognised(existing):
    added = ensure_currency_field(
        any_monetary=True, submitted=[], existing_field_names=[existing]
    )
    assert added == []


# ---------------------------------------------------------------------------
# One type authority, shared with the generation flow
# ---------------------------------------------------------------------------
# Both flows decide a field's type from prose: this resolver (one field a user
# described) and field_generation (a whole schema at collection creation). If
# their vocabularies or rules diverge, the same field described the same way
# gets a different type depending on which door it came through — and the drift
# is silent. These tests fail if the two ever separate.

def test_generation_and_resolution_share_the_same_type_allow_list():
    import utils.config.field_generation as fg
    from utils.models.enums import CALLER_FIELD_TYPES
    assert fg._ALLOWED_FIELD_TYPES is CALLER_FIELD_TYPES


def test_generation_allows_date():
    """Regression: field_generation's allow-list was a local literal of seven
    values, so adding `date` to FieldType would have left this flow dropping
    every date field it proposed — reported to the user as a hallucinated type."""
    import utils.config.field_generation as fg
    assert "date" in fg._ALLOWED_FIELD_TYPES
    assert "date" in fg._ALLOWED_SCHEMA_VALUE_TYPES


def test_generation_prompt_carries_the_shared_type_rules():
    import utils.config.field_generation as fg
    from utils.config.field_type_resolver import TYPE_RULES_BLOCK
    system = fg._SYSTEM_PROMPT.format(
        default_fields_block="",
        protected_names="",
        max_fields=12,
        type_rules_block=TYPE_RULES_BLOCK,
    )
    assert TYPE_RULES_BLOCK in system


def test_internal_types_are_never_caller_facing():
    """'chunks' — the one remaining internal type — must stay out of every
    caller-facing allow-list; it selects the dynamic chunk model. ('text' was
    a second internal type; it has been retired and is no longer in
    INTERNAL_FIELD_TYPES.)"""
    from utils.models.enums import CALLER_FIELD_TYPES, INTERNAL_FIELD_TYPES
    assert CALLER_FIELD_TYPES.isdisjoint(INTERNAL_FIELD_TYPES)


def test_generated_monetary_field_gets_currency_and_the_amount_wording(monkeypatch):
    """The generation flow applies the same money rule: bare float amounts, one
    document-level currency field."""
    import utils.config.field_generation as fg

    spec = fg.GeneratedFieldSpec(
        field_name="invoice_total", field_type="float",
        description="Total payable on the invoice", is_monetary=True,
    )
    accepted, rejected = fg._validate_and_filter([spec])
    assert rejected == []
    assert CURRENCY_FIELD_NAME in accepted[0]["description"]

    added = ensure_currency_field(
        any_monetary=any(f.get("_is_monetary") for f in accepted),
        submitted=accepted,
        existing_field_names=[],
    )
    assert [f["field_name"] for f in added] == [CURRENCY_FIELD_NAME]


def test_generated_field_dict_carries_no_private_marker_to_the_db():
    """_is_monetary is a private marker, not a field_configurations column."""
    import utils.config.field_generation as fg
    spec = fg.GeneratedFieldSpec(
        field_name="fee", field_type="float", description="A fee", is_monetary=True
    )
    accepted, _ = fg._validate_and_filter([spec])
    # present for the currency decision...
    assert accepted[0]["_is_monetary"] is True
    # ...and generate_field_schema strips it before returning; assert the sync
    # payload shape has no other private keys leaking through.
    assert [k for k in accepted[0] if k.startswith("_")] == ["_is_monetary"]


def test_a_generated_date_field_is_accepted_end_to_end():
    import utils.config.field_generation as fg
    spec = fg.GeneratedFieldSpec(
        field_name="due_date", field_type="date", description="Invoice due date"
    )
    accepted, rejected = fg._validate_and_filter([spec])
    assert rejected == []
    assert accepted[0]["field_type"] == "date"


def test_a_generated_table_may_use_date_subfields():
    import utils.config.field_generation as fg
    spec = fg.GeneratedFieldSpec(
        field_name="transactions", field_type="List[dict]",
        description="Transaction rows",
        field_schema={"date": "date", "amount": "float", "description": "str"},
    )
    accepted, rejected = fg._validate_and_filter([spec])
    assert rejected == []
    assert accepted[0]["schema"]["date"] == "date"


# ---------------------------------------------------------------------------
# field_type_label — stored, not mapped on every read
# ---------------------------------------------------------------------------
# The label is persisted per field (migration 052) rather than derived from
# field_type on each response. Two reasons, both tested below: the map is not
# invertible (int and float both read "Number"), and the label is user-editable
# data — recomputing it would silently undo an edit.

def test_resolution_persists_a_canonical_label():
    from utils.models.enums import label_for_field_type
    assert label_for_field_type("List[dict]") == "Table"
    assert label_for_field_type("date") == "Date"
    assert label_for_field_type("str") == "Text"
    assert label_for_field_type("List[str]") == "Text (multiple values)"
    assert label_for_field_type("bool") == "Yes / No"
    assert label_for_field_type("dict") == "Group of fields"


def test_int_and_float_share_one_label_so_the_map_is_not_invertible():
    """The reason a label can never be mapped back to a type by lookup, and
    therefore the reason resolution exists at all."""
    from utils.models.enums import label_for_field_type
    assert label_for_field_type("int") == label_for_field_type("float") == "Number"


def test_unknown_or_missing_type_still_gets_a_label():
    """Runs on the response path, where a bad row cannot be un-stored — showing
    a user `List[Dict]` in a type column helps nobody."""
    from utils.models.enums import label_for_field_type
    assert label_for_field_type("List[Dict]") == "Text"
    assert label_for_field_type(None) == "Text"
    assert label_for_field_type("") == "Text"


def test_resolved_field_carries_a_label(monkeypatch):
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(field_name="txns", field_type="List[dict]",
                            field_schema={"amount": "float"}, reason="rows")
    ]))
    out, resolutions, _ = _run(resolve_field_types(
        [{"field_name": "txns", "description": "Transaction rows"}]
    ))
    assert out[0]["field_type_label"] == "Table"
    assert resolutions[0]["field_type_label"] == "Table"


def test_a_user_supplied_label_is_kept_verbatim(monkeypatch):
    """"Saved properly, not mapped always": replacing the user's wording with
    the canonical label would silently undo their edit."""
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(field_name="txns", field_type="List[dict]",
                            field_schema={"amount": "float"}, reason="label says table")
    ]))
    out, _, _ = _run(resolve_field_types(
        [{"field_name": "txns", "description": "stuff", "field_type_label": "Statement rows"}]
    ))
    assert out[0]["field_type_label"] == "Statement rows"   # NOT "Table"
    assert out[0]["field_type"] == "List[dict]"


def test_a_user_label_is_fed_to_the_resolver_as_a_signal(monkeypatch):
    """A label the user typed is stronger evidence than a description they may
    not have touched."""
    seen = {}

    class _Capturing(_FakeStructuredChain):
        async def ainvoke(self, inputs):
            seen.update(inputs)
            return await super().ainvoke(inputs)

    chain = _Capturing(FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(field_name="f", field_type="date", reason="r")
    ]))
    import langchain_core.prompts as prompts_module
    monkeypatch.setattr(prompts_module.ChatPromptTemplate, "from_messages",
                        staticmethod(lambda messages: _FakePrompt(chain)))

    async def _fake_get_llm(*a, **k):
        return _FakeLLM(chain), "openai", "platform-key"
    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    _run(resolve_field_types([{"field_name": "f", "description": "d", "field_type_label": "Renewal date"}]))
    assert "Renewal date" in seen["fields_block"]


def test_the_added_currency_field_carries_a_label(monkeypatch):
    """Otherwise currency would be the one field in a new collection with a
    NULL label."""
    _patch_llm(monkeypatch, FieldTypeResolutionResponse(resolutions=[
        FieldTypeResolution(field_name="total", field_type="float",
                            is_monetary=True, reason="money")
    ]))
    _, _, added = _run(resolve_field_types([{"field_name": "total", "description": "Total"}]))
    assert added[0]["field_type_label"] == "Text"


def test_generated_fields_carry_a_label():
    """/generate is the main way fields come into existence — if it wrote no
    label, most rows in a new collection would have none."""
    import utils.config.field_generation as fg
    specs = [
        fg.GeneratedFieldSpec(field_name="due_date", field_type="date", description="Due date"),
        fg.GeneratedFieldSpec(field_name="line_items", field_type="List[dict]",
                              description="Line items", field_schema={"qty": "int"}),
    ]
    accepted, rejected = fg._validate_and_filter(specs)
    assert rejected == []
    assert [f["field_type_label"] for f in accepted] == ["Date", "Table"]


def test_response_model_prefers_the_stored_label_over_the_map():
    from utils.models.enrichment import FieldConfigurationResponseV2 as R

    def row(**kw):
        return R.model_validate({"id": 1, "knowledge_id": "k", "field_name": "f",
                                 "is_custom": True, **kw})

    # stored label wins, even when it differs from what the map would say
    assert row(field_type="List[dict]", field_type_label="Statement rows").field_type_label == "Statement rows"
    # NULL (a pre-052 row) falls back to the map rather than returning blank
    assert row(field_type="List[dict]").field_type_label == "Table"
    assert row(field_type="date").field_type_label == "Date"


# ---------------------------------------------------------------------------
# Re-typing a relabelled field — allowed only while nothing is extracted
# ---------------------------------------------------------------------------
# The settings flow: a user reworded a field's type label. The LLM re-derives
# the type, but applying it is gated on there being no extracted values, because
# re-typing does NOT re-extract the documents already processed.

def _patch_retype(monkeypatch, *, stored, resolved_type, used_count, label="Table"):
    """Wire the router helper against fakes: one existing field, one resolution,
    and a controllable document count."""
    import routers.enrichment_configuration_new as R

    async def _rows(_kid):
        return [stored]

    async def _config(_kid):
        return {"collection_name": "c", "service_description": "d", "owner_id": "o"}

    async def _resolve(fields, **kw):
        out = [{**f, "field_type": resolved_type} for f in fields]
        return out, [{"field_name": f["field_name"], "field_type": resolved_type} for f in out], []

    async def _count(_kid, _field):
        return used_count

    monkeypatch.setattr(R, "get_field_configurations_sqlmodel_v2", _rows)
    monkeypatch.setattr(R, "get_knowledge_config", _config)
    monkeypatch.setattr(R, "resolve_field_types", _resolve)
    monkeypatch.setattr(R, "count_documents_with_field", _count)
    return R


_STORED = {"id": 12, "field_name": "txns", "field_type": "str", "field_type_label": "Text"}


def test_retype_applied_when_no_documents_carry_the_field(monkeypatch):
    R = _patch_retype(monkeypatch, stored=_STORED, resolved_type="List[dict]", used_count=0)
    sent = [{"id": 12, "field_name": "txns", "field_type_label": "Table"}]
    out, resolutions, warnings = _run(R._retype_relabelled_fields("k", sent))
    assert out[0]["field_type"] == "List[dict]"
    assert out[0]["_allow_field_type_change"] is True
    assert warnings == []


def test_retype_refused_once_documents_have_values(monkeypatch):
    R = _patch_retype(monkeypatch, stored=_STORED, resolved_type="List[dict]", used_count=240)
    sent = [{"id": 12, "field_name": "txns", "field_type_label": "Table"}]
    out, _, warnings = _run(R._retype_relabelled_fields("k", sent))
    assert "field_type" not in out[0]              # type left alone
    assert out[0]["field_type_label"] == "Table"   # label still saved
    assert "_allow_field_type_change" not in out[0]
    assert len(warnings) == 1
    assert "240 document(s) already have" in warnings[0]


def test_retype_refused_when_the_count_is_unknown(monkeypatch):
    """None is not zero. An unverifiable count must never permit a change that
    would strand already-extracted values."""
    R = _patch_retype(monkeypatch, stored=_STORED, resolved_type="List[dict]", used_count=None)
    sent = [{"id": 12, "field_name": "txns", "field_type_label": "Table"}]
    out, _, warnings = _run(R._retype_relabelled_fields("k", sent))
    assert "_allow_field_type_change" not in out[0]
    assert len(warnings) == 1


def test_no_llm_call_when_the_label_is_unchanged(monkeypatch):
    """Every PATCH echoes the label back; only a CHANGED one may spend a call."""
    import routers.enrichment_configuration_new as R

    async def _rows(_kid):
        return [_STORED]

    def _explode(*a, **k):
        raise AssertionError("must not re-resolve when the label did not change")

    monkeypatch.setattr(R, "get_field_configurations_sqlmodel_v2", _rows)
    monkeypatch.setattr(R, "resolve_field_types", _explode)

    sent = [{"id": 12, "field_name": "txns", "field_type_label": "Text"}]
    out, resolutions, warnings = _run(R._retype_relabelled_fields("k", sent))
    assert (out, resolutions, warnings) == (sent, [], [])


def test_new_fields_are_left_to_the_other_path(monkeypatch):
    """A field with no id is handled by _resolve_missing_field_types; this helper
    must not touch it, or it would resolve twice."""
    import routers.enrichment_configuration_new as R

    def _explode(*a, **k):
        raise AssertionError("must not run for new fields")
    monkeypatch.setattr(R, "get_field_configurations_sqlmodel_v2", _explode)

    sent = [{"field_name": "brand_new", "field_type_label": "Table"}]
    assert _run(R._retype_relabelled_fields("k", sent)) == (sent, [], [])


def test_a_resolver_failure_still_saves_the_label(monkeypatch):
    """The label edit is valid on its own; a provider outage must not lose it."""
    import routers.enrichment_configuration_new as R

    async def _rows(_kid):
        return [_STORED]

    async def _config(_kid):
        return {}

    async def _boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(R, "get_field_configurations_sqlmodel_v2", _rows)
    monkeypatch.setattr(R, "get_knowledge_config", _config)
    monkeypatch.setattr(R, "resolve_field_types", _boom)

    sent = [{"id": 12, "field_name": "txns", "field_type_label": "Table"}]
    out, resolutions, warnings = _run(R._retype_relabelled_fields("k", sent))
    assert out[0]["field_type_label"] == "Table"
    assert "field_type" not in out[0]
    assert len(warnings) == 1 and "left unchanged" in warnings[0]


def test_the_private_marker_cannot_be_set_by_a_client():
    """The immutability exemption must be unreachable from a request body."""
    from utils.models.enrichment import FieldConfigurationRequestV2
    req = FieldConfigurationRequestV2.model_validate({
        "field_name": "f", "is_custom": True, "_allow_field_type_change": True,
    })
    assert "_allow_field_type_change" not in req.model_dump(exclude_unset=True)


def test_retype_applied_when_stored_label_is_null(monkeypatch):
    """2026-09-02 live bug report worried the NULL-stored-label case (every
    field /generate or a pre-052 row leaves unlabelled) was untested and
    might be where a relabel PATCH silently no-ops. It is not: the
    comparison `(sent or '').strip() != (stored or '')` already handles a
    None stored label correctly, same as any other differing string. Kept as
    a permanent regression test for exactly the stored shape the live field
    actually had, since every other test here starts from a non-null
    _STORED label."""
    stored = {"id": 12, "field_name": "txns", "field_type": "str", "field_type_label": None}
    R = _patch_retype(monkeypatch, stored=stored, resolved_type="List[dict]", used_count=0)
    sent = [{"id": 12, "field_name": "txns", "field_type_label": "Table"}]
    out, resolutions, warnings = _run(R._retype_relabelled_fields("k", sent))
    assert out[0]["field_type"] == "List[dict]"
    assert out[0]["_allow_field_type_change"] is True
    assert resolutions and warnings == []


def test_retype_probe_carries_the_stored_description_not_just_the_sent_delta(monkeypatch):
    """The actual 2026-09-02 root cause: a settings-screen relabel PATCH
    resends only id/field_name/is_custom/field_type_label — never the
    field's own description — because that is the whole point of a PATCH.
    The old `probe = [{**f, 'field_type': None} for f, _ in relabelled]`
    built the LLM probe from that sent delta ALONE, discarding the stored
    row entirely except for the label comparison. So the resolver always
    saw "(no description given)" for a relabelled field regardless of how
    good its real description was, and TYPE_RULES_BLOCK rule 4 ("if the
    description is too vague to place, choose str") could keep the existing
    type with nothing distinguishing that from a broken no-op: no warning,
    no type_resolutions entry the caller could see was ever weighed.

    This asserts the probe handed to resolve_field_types carries the
    STORED description (and sample_values) merged under the sent label —
    not the sent dict alone.
    """
    import routers.enrichment_configuration_new as R

    stored = {
        "id": 11465,
        "field_name": "account_holder_name",
        "field_type": "str",
        "field_type_label": "Text",
        "description": "The name of the account holder as printed on the statement",
        "sample_values": ["Jane Doe"],
        "level": "document",
    }

    async def _rows(_kid):
        return [stored]

    async def _config(_kid):
        return {"collection_name": "c", "service_description": "d", "owner_id": "o"}

    captured = {}

    async def _resolve(fields, **kw):
        captured["fields"] = fields
        out = [{**f, "field_type": "dict"} for f in fields]
        return out, [{"field_name": f["field_name"], "field_type": "dict"} for f in out], []

    async def _count(_kid, _field):
        return 0

    monkeypatch.setattr(R, "get_field_configurations_sqlmodel_v2", _rows)
    monkeypatch.setattr(R, "get_knowledge_config", _config)
    monkeypatch.setattr(R, "resolve_field_types", _resolve)
    monkeypatch.setattr(R, "count_documents_with_field", _count)

    # Exactly what a settings-screen relabel actually sends: no description.
    sent = [{"id": 11465, "field_name": "account_holder_name", "is_custom": True,
             "field_type_label": "Group of fields"}]
    out, resolutions, warnings = _run(R._retype_relabelled_fields("k", sent))

    probed = captured["fields"][0]
    assert probed["description"] == stored["description"]
    assert probed["sample_values"] == stored["sample_values"]
    # The sent label — the actual new label — must win over the stored one.
    assert probed["field_type_label"] == "Group of fields"
    assert probed["field_type"] is None  # forced open for resolution

    assert out[0]["field_type"] == "dict"
    assert out[0]["_allow_field_type_change"] is True
    assert warnings == []


# ---------------------------------------------------------------------------
# GET /v1/{id}/enrichment — field_type_label must reach the response
# ---------------------------------------------------------------------------
# 2026-09-02 live bug: get_enrichment built every FieldConfigurationResponseV2
# from the stored row dict but never passed field_type_label through, so the
# key was always None going in — and, being None, was indistinguishable from
# "not stored", so the response model's own _fill_field_type_label fallback
# papered over it silently on every field, every time, live evidence included.

def test_get_enrichment_returns_field_type_label_for_every_field(monkeypatch):
    import routers.enrichment_configuration_new as R
    from utils.models.knowledge import MetadataResponse

    stored_with_label = {
        "id": 1, "knowledge_id": "k", "field_name": "chunk_text", "description": "d",
        "field_type": "str", "field_type_label": "Text", "level": "chunk",
        "is_custom": False, "is_required": False, "is_active": True,
        "sample_values": None, "restrict_to_choices": False, "max_items": None,
        "schema": None, "created_at": None, "updated_at": None, "deprecated_at": None,
    }
    # A pre-migration-052 row: NULL stored label, must fall back to the map.
    stored_null_label = {**stored_with_label, "id": 2, "field_name": "topics",
                          "field_type": "List[str]", "field_type_label": None}

    async def _exists(_kid):
        return True

    async def _rows(_kid):
        return [stored_with_label, stored_null_label]

    async def _manifest(_kid, active_only=True):
        return []

    async def _udm(**kw):
        return MetadataResponse(fields=[])

    monkeypatch.setattr(R, "knowledge_exists_check", _exists)
    monkeypatch.setattr(R, "get_field_configurations_sqlmodel_v2", _rows)
    monkeypatch.setattr(R, "get_field_manifest", _manifest)
    monkeypatch.setattr(R, "get_user_defined_metadata_from_qdrant_object_ids", _udm)

    resp = _run(R.get_enrichment(
        knowledge_id="k", include_metadata=False, field_name=None,
        filters=None, limit=None, _=None,
    ))

    by_name = {f.field_name: f for f in resp.fields}
    # Real stored label wins, unchanged.
    assert by_name["chunk_text"].field_type_label == "Text"
    # NULL stored label still falls back to a sensible default, not None.
    assert by_name["topics"].field_type_label
    assert by_name["topics"].field_type_label != "None"
