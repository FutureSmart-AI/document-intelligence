"""Unit tests for utils/config/field_generation.py — LLM-driven enrichment
field generation for collection creation (docs/plan/field-and-retrieval-precision,
ClickUp UI-API flow doc, 2026-09-01).

Pure unit tests: get_llm is monkeypatched with a fake chain (same seam as
tests/unit/test_follow_up.py); no real LLM call, no DB, no network I/O.
"""

import asyncio

import utils.config.field_generation as field_generation
from utils.config.field_generation import (
    GeneratedFieldSpec,
    FieldGenerationResponse,
    MAX_GENERATED_FIELDS,
    _SYSTEM_PROMPT,
    _validate_and_filter,
    deactivate_chunk_fields_if_disabled,
)
from utils.config.enrichment_config import PROTECTED_SYSTEM_FIELDS


class _FakeStructuredChain:
    def __init__(self, response: FieldGenerationResponse, raise_error=None):
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


def _patch_prompt_template(monkeypatch, chain):
    fake_prompt = _FakePrompt(chain)
    import langchain_core.prompts as prompts_module
    monkeypatch.setattr(
        prompts_module.ChatPromptTemplate, "from_messages", staticmethod(lambda messages: fake_prompt)
    )


def _patch_get_llm(monkeypatch, chain, provider="openai", key_type="platform-key"):
    """Same seam as test_follow_up.py: patch chat.byok_fallback.get_llm, the
    name run_with_key_fallback actually calls."""
    async def _fake(*args, **kwargs):
        return _FakeLLM(chain), provider, key_type
    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake)


# ---------------------------------------------------------------------------
# Prompt contract (2026-09-22): no default-field verdicts, no stated field cap
# ---------------------------------------------------------------------------

def test_prompt_no_longer_asks_about_default_fields():
    assert "KEEP or DROP" not in _SYSTEM_PROMPT
    assert "default_fields_block" not in _SYSTEM_PROMPT
    assert "default_verdicts" not in FieldGenerationResponse.model_fields


def test_prompt_states_no_field_count_limit():
    # The cap is enforced in code only - the model proposes what it should.
    assert "max_fields" not in _SYSTEM_PROMPT
    assert "at most" not in _SYSTEM_PROMPT


def test_prompt_still_names_every_protected_field():
    rendered = _SYSTEM_PROMPT.format(
        protected_names=", ".join(sorted(PROTECTED_SYSTEM_FIELDS)), type_rules_block="",
    )
    for name in PROTECTED_SYSTEM_FIELDS:
        assert name in rendered


# ---------------------------------------------------------------------------
# _validate_and_filter — generated field validation
# ---------------------------------------------------------------------------

def test_valid_field_is_accepted():
    spec = GeneratedFieldSpec(field_name="vendor_name", field_type="str", description="Vendor's legal name")
    accepted, rejected = _validate_and_filter([spec])
    assert rejected == []
    assert len(accepted) == 1
    assert accepted[0]["field_name"] == "vendor_name"
    assert accepted[0]["is_custom"] is True


def test_unrecognized_field_type_is_rejected_not_coerced():
    """The exact bug this generator must not repeat: utils/models/enrichment.py
    silently coerces any unknown field_type to 'str'. A hallucinated type here
    must be dropped and reported, never silently written as str."""
    spec = GeneratedFieldSpec(field_name="due_date", field_type="Date", description="When it's due")
    accepted, rejected = _validate_and_filter([spec])
    assert accepted == []
    assert len(rejected) == 1
    assert "Date" in rejected[0]["reason"]


def test_field_colliding_with_protected_field_is_rejected():
    spec = GeneratedFieldSpec(field_name="summary", field_type="str", description="dup")
    accepted, rejected = _validate_and_filter([spec])
    assert accepted == []
    assert rejected[0]["field_name"] == "summary"
    assert "protected" in rejected[0]["reason"]


def test_optional_default_name_is_accepted_when_the_collection_lacks_it():
    """The wizard no longer seeds optional defaults, so a proposed `topics`
    is an ordinary new field, not a clash."""
    spec = GeneratedFieldSpec(field_name="topics", field_type="List[str]", description="themes")
    accepted, rejected = _validate_and_filter([spec])
    assert rejected == []
    assert accepted[0]["field_name"] == "topics"


def test_field_colliding_with_an_existing_collection_field_is_rejected():
    spec = GeneratedFieldSpec(field_name="topics", field_type="str", description="dup")
    accepted, rejected = _validate_and_filter([spec], existing_field_names=["topics", "title"])
    assert accepted == []
    assert "already has" in rejected[0]["reason"]


def test_duplicate_generated_names_second_one_rejected():
    a = GeneratedFieldSpec(field_name="vendor_name", field_type="str", description="a")
    b = GeneratedFieldSpec(field_name="vendor_name", field_type="str", description="b")
    accepted, rejected = _validate_and_filter([a, b])
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0]["reason"].startswith("duplicate")


def test_field_name_is_normalized_to_snake_case():
    spec = GeneratedFieldSpec(field_name="Vendor Name", field_type="str", description="x")
    assert spec.field_name == "vendor_name"


def test_cap_is_20():
    assert MAX_GENERATED_FIELDS == 20


def test_fields_over_the_cap_are_reported_not_silently_dropped():
    """Regression: the old fields[:12] slice dropped a 13th proposal
    (education_history, LangSmith run 01a0ca42...) with no entry in
    rejected_fields."""
    specs = [
        GeneratedFieldSpec(field_name=f"field_{i}", field_type="str", description="x")
        for i in range(MAX_GENERATED_FIELDS + 5)
    ]
    accepted, rejected = _validate_and_filter(specs)
    assert len(accepted) == MAX_GENERATED_FIELDS
    assert [r["field_name"] for r in rejected] == [f"field_{i}" for i in range(20, 25)]
    assert all(r["reason"] == "over the 20-field limit" for r in rejected)


def test_an_invalid_early_field_does_not_cost_a_valid_one_its_slot():
    bad = GeneratedFieldSpec(field_name="bad", field_type="Nope", description="x")
    good = [
        GeneratedFieldSpec(field_name=f"field_{i}", field_type="str", description="x")
        for i in range(MAX_GENERATED_FIELDS)
    ]
    accepted, rejected = _validate_and_filter([bad] + good)
    assert len(accepted) == MAX_GENERATED_FIELDS
    assert [r["field_name"] for r in rejected] == ["bad"]


def test_list_dict_field_requires_valid_schema_subfield_types():
    good = GeneratedFieldSpec(
        field_name="line_items", field_type="List[dict]", description="Invoice line items",
        field_schema={"description": "str", "amount": "float"},
    )
    accepted, rejected = _validate_and_filter([good])
    assert rejected == []
    assert accepted[0]["schema"] == {"description": "str", "amount": "float"}

    bad = GeneratedFieldSpec(
        field_name="line_items2", field_type="List[dict]", description="x",
        field_schema={"nested": "dict"},  # not one-level-deep-safe
    )
    accepted2, rejected2 = _validate_and_filter([bad])
    assert accepted2 == []
    assert "subfield" in rejected2[0]["reason"]


def test_schema_on_a_flat_type_is_rejected():
    spec = GeneratedFieldSpec(
        field_name="tags", field_type="str", description="x",
        field_schema={"sub": "str"},
    )
    accepted, rejected = _validate_and_filter([spec])
    assert accepted == []
    assert "field_schema only allowed" in rejected[0]["reason"]


def test_restrict_to_choices_without_sample_values_is_downgraded_not_rejected():
    spec = GeneratedFieldSpec(
        field_name="status", field_type="str", description="x",
        restrict_to_choices=True, sample_values=None,
    )
    accepted, rejected = _validate_and_filter([spec])
    assert rejected == []
    assert accepted[0]["restrict_to_choices"] is False


# ---------------------------------------------------------------------------
# generate_field_schema — end to end against a fake LLM chain
# ---------------------------------------------------------------------------

def test_generate_field_schema_end_to_end(monkeypatch):
    response = FieldGenerationResponse(
        new_fields=[
            GeneratedFieldSpec(
                field_name="invoice_number", field_type="str",
                description="The invoice's unique identifier", is_required=True,
            ),
        ],
    )
    chain = _FakeStructuredChain(response)
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    result = asyncio.run(field_generation.generate_field_schema(
        owner_id="owner-1",
        knowledge_id="kb-1",
        collection_name="Vendor Invoices",
        service_description="Invoices from vendors",
        extraction_instructions="filter by invoice number and vendor",
        doc_previews=[("invoice1.pdf", "Invoice #12345 from Acme Corp")],
    ))

    assert [f["field_name"] for f in result.accepted_fields] == ["invoice_number"]
    assert result.rejected_fields == []
    assert result.provider == "openai"
    assert result.key_type == "platform-key"


def test_generate_field_schema_propagates_llm_failure(monkeypatch):
    """A genuine LLM/provider failure must raise, not silently return an
    empty schema — the caller (the /enrichment/generate endpoint) needs to
    tell 'nothing to change' apart from 'the call failed'."""
    chain = _FakeStructuredChain(response=None, raise_error=RuntimeError("provider down"))
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    try:
        asyncio.run(field_generation.generate_field_schema(
            owner_id=None, knowledge_id="kb-1", collection_name="X",
            service_description=None, extraction_instructions=None, doc_previews=[],
        ))
        assert False, "expected RuntimeError to propagate"
    except RuntimeError as e:
        assert "provider down" in str(e)


# --- deactivate_chunk_fields_if_disabled -----------------------------------
# Regression coverage for the live-verified bug (docs/plan/field-and-retrieval-
# precision/decisions.md, 2026-09-01): the endpoint's own default-field seeding
# step activates every chunk-level default regardless of this knowledge base's
# own chunk_level_metadata setting. This is the override that undoes it.

_CHUNK_FIELDS = [
    {"id": 1, "field_name": "chunk_idx", "level": "chunk", "is_active": True},
    {"id": 2, "field_name": "chunk_text", "level": "chunk", "is_active": True},
    {"id": 3, "field_name": "chunk_topic", "level": "chunk", "is_active": True},
    {"id": 4, "field_name": "document_type", "level": "document", "is_active": True},
]


def test_chunk_metadata_enabled_leaves_fields_untouched():
    updates, deactivated = deactivate_chunk_fields_if_disabled(_CHUNK_FIELDS, [], True)
    assert updates == []
    assert deactivated == []


def test_chunk_metadata_disabled_deactivates_every_active_chunk_field():
    updates, deactivated = deactivate_chunk_fields_if_disabled(_CHUNK_FIELDS, [], False)
    assert sorted(deactivated) == ["chunk_idx", "chunk_text", "chunk_topic"]
    assert {u["id"] for u in updates} == {1, 2, 3}
    assert all(u["is_active"] is False for u in updates)
    # document-level field is never touched
    assert not any(u["field_name"] == "document_type" for u in updates)


def test_chunk_metadata_disabled_skips_already_inactive_fields():
    fields = [{"id": 1, "field_name": "chunk_idx", "level": "chunk", "is_active": False}]
    updates, deactivated = deactivate_chunk_fields_if_disabled(fields, [], False)
    assert updates == []
    assert deactivated == []


def test_chunk_metadata_disabled_overrides_a_conflicting_queued_update():
    """The config setting wins even over a stray queued update that would
    have left a chunk field active — queued once, corrected to False, not
    left as the LLM's is_active: True."""
    stray_verdict_update = {"id": 3, "field_name": "chunk_topic", "is_active": True}
    updates, deactivated = deactivate_chunk_fields_if_disabled(
        _CHUNK_FIELDS, [stray_verdict_update], False
    )
    chunk_topic_updates = [u for u in updates if u["id"] == 3]
    assert len(chunk_topic_updates) == 1
    assert chunk_topic_updates[0]["is_active"] is False
    assert sorted(deactivated) == ["chunk_idx", "chunk_text", "chunk_topic"]


# ---------------------------------------------------------------------------
# generate_field_schema — real token counts via include_raw=True
# ---------------------------------------------------------------------------

class _FakeRawMessage:
    """Stands in for the raw AIMessage carrying usage_metadata — same shape
    test_enrichment_preview.py uses for preview_run's identical pattern."""

    def __init__(self, input_tokens=321, output_tokens=87):
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}


class _FakeIncludeRawChain:
    """A structured-output chain that honors include_raw=True, mirroring what
    a real langchain chat model returns."""

    def __init__(self, response: FieldGenerationResponse):
        self._response = response

    async def ainvoke(self, inputs):
        return {"raw": _FakeRawMessage(), "parsed": self._response}


class _FakeLLMWithRaw:
    def __init__(self, chain):
        self._chain = chain

    def with_structured_output(self, schema, include_raw=False):
        assert include_raw is True  # this is the call generate_field_schema must make first
        return self._chain


def test_generate_field_schema_captures_real_token_counts(monkeypatch):
    response = FieldGenerationResponse(new_fields=[])
    chain = _FakeIncludeRawChain(response)
    _patch_prompt_template(monkeypatch, chain)

    async def _fake_get_llm(*args, **kwargs):
        return _FakeLLMWithRaw(chain), "openai", "platform-key"
    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    result = asyncio.run(field_generation.generate_field_schema(
        owner_id="owner-1", knowledge_id=None, collection_name="X",
        service_description="Y", extraction_instructions=None, doc_previews=[],
    ))
    assert result.input_tokens == 321
    assert result.output_tokens == 87


def test_generate_field_schema_defaults_tokens_to_zero_when_fake_rejects_include_raw(monkeypatch):
    """The existing tests' _FakeLLM.with_structured_output(self, schema) takes no
    include_raw kwarg — generate_field_schema must fall back to the plain call
    (TypeError) rather than breaking, and report 0 tokens rather than crashing."""
    response = FieldGenerationResponse(new_fields=[])
    chain = _FakeStructuredChain(response)
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    result = asyncio.run(field_generation.generate_field_schema(
        owner_id="owner-1", knowledge_id=None, collection_name="X",
        service_description="Y", extraction_instructions=None, doc_previews=[],
    ))
    assert result.input_tokens == 0
    assert result.output_tokens == 0


# ---------------------------------------------------------------------------
# run_generate_preview — Step 1's owner-scoped orchestration (quota + usage)
# ---------------------------------------------------------------------------

def test_run_generate_preview_checks_quota_before_calling_the_llm(monkeypatch):
    calls = {"enforced": [], "recorded": []}

    async def _fake_enforce(owner_id, estimated_tokens):
        calls["enforced"].append((owner_id, estimated_tokens))

    async def _fake_record(**kwargs):
        calls["recorded"].append(kwargs)

    monkeypatch.setattr(field_generation, "_enforce_generation_quota", _fake_enforce)
    monkeypatch.setattr(field_generation, "_record_generation_usage", _fake_record)

    response = FieldGenerationResponse(
        new_fields=[GeneratedFieldSpec(field_name="vendor_name", field_type="str", description="Vendor's name")],
    )
    chain = _FakeStructuredChain(response)
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    result = asyncio.run(field_generation.run_generate_preview(
        owner_id="owner-1",
        collection_name="Vendor Invoices",
        service_description="Invoices from vendors",
        extraction_instructions="filter by vendor",
        doc_previews=[],
    ))

    assert len(calls["enforced"]) == 1
    assert calls["enforced"][0][0] == "owner-1"
    assert len(calls["recorded"]) == 1
    assert calls["recorded"][0]["owner_id"] == "owner-1"

    assert result["proposed_fields"][0]["field_name"] == "vendor_name"
    assert "default_field_verdicts" not in result
    assert result["run"]["persisted"] is False
    assert "id" not in result  # nothing was written, so there is no row to point at


def test_run_generate_preview_propagates_quota_refusal(monkeypatch):
    """A 429 from the pre-flight must stop before the LLM is ever called."""
    from fastapi import HTTPException

    async def _fake_enforce(owner_id, estimated_tokens):
        raise HTTPException(status_code=429, detail="over budget")

    llm_called = {"value": False}

    async def _fake_get_llm(*args, **kwargs):
        llm_called["value"] = True
        raise AssertionError("LLM must not be called when quota is refused")

    monkeypatch.setattr(field_generation, "_enforce_generation_quota", _fake_enforce)
    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    try:
        asyncio.run(field_generation.run_generate_preview(
            owner_id="owner-1", collection_name=None, service_description=None,
            extraction_instructions=None, doc_previews=[],
        ))
        assert False, "expected HTTPException(429)"
    except HTTPException as e:
        assert e.status_code == 429
    assert llm_called["value"] is False
