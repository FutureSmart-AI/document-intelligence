"""POST /v1/enrichment/preview — the single-document, nothing-saved preview run.

Covers CC6 (extract the requested fields) and CC9 (suggest fields nobody asked
for), docs/plan/collection-creation-flow/.

Four layers:

  1. **Parity** — the headline deliverable (phased-plan.md item 2.7). The ported
     builder in utils/enrichment/preview_model_builder.py is fed the SAME field
     configs as fs-agent-workers' real ``create_dynamic_pydantic_model``, loaded
     off that repo's disk, and the two JSON schemas must be identical. A preview
     that drifts from the real ingest is worse than no preview at all, and this is
     the only thing that detects the drift.
  2. **Type coverage** — every one of the eight supported field types through the
     ported builder, plus the behaviours the port must preserve (nested row model
     for List[dict], ISO-8601 instruction on date, Literal for restrict_to_choices,
     truncation for max_items).
  3. **Request validation and auth** — session-only, exactly-one-source, and the
     ``fields`` allow-list.
  4. **Round trip** — a full request → response with the LLM call mocked, the same
     way tests/unit/test_byok_fallback.py mocks it: no real provider call, no DB.

No test here makes a network call, an LLM call, or a database query.
"""

import importlib.util
import json
import logging
import sys
import types
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import chat.byok_fallback as byok_fallback
import routers.enrichment_configuration_new as preview_router_module
import utils.enrichment.preview_run as preview_run
from utils.enrichment.preview_model_builder import (
    PreviewFieldSpec,
    create_dynamic_pydantic_model,
    specs_from_request_fields,
)
from utils.enrichment.preview_run import (
    SuggestedField,
    build_fields_run,
    estimate_prompt_tokens,
    filter_suggested_fields,
    parse_fields_json,
)
from utils.enrichment.preview_samples import PREVIEW_SAMPLES, available_sample_keys
from utils.config.field_type_resolver import needs_type_resolution

OWNER_EMAIL = "owner@futuresmart.ai"

# The sibling repo the builder was ported from. Read-only here — this test never
# writes to it and never imports its packages, only the one source file.
WORKERS_SCHEMA_BUILDER = Path("D:/FutureSmartAI/fs-agent-workers/celery_utils/enrichment/schema_builder.py")


# ---------------------------------------------------------------------------
# Layer 1 — parity with the real worker builder (phased-plan.md 2.7)
# ---------------------------------------------------------------------------

def _load_worker_schema_builder():
    """Load fs-agent-workers' schema_builder.py directly, with its DB/logging/
    tracing imports stubbed.

    Loaded by file path rather than by package import on purpose: importing
    ``celery_utils.enrichment.schema_builder`` normally would execute that repo's
    package __init__ chain and pull in Celery, its database engine and its
    settings. Only five module-level names are actually needed by the two
    functions under comparison, so those five are stubbed and the file itself is
    executed unchanged. What is compared is therefore the REAL current source of
    that repo, not a copy of it.
    """
    def _stub(name, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module

    def _passthrough_decorator(_label):
        def _deco(fn):
            return fn
        return _deco

    class _FieldConfiguration:  # only referenced in type hints
        pass

    class _ExtractedMetadata:
        pass

    for pkg in ("services", "celery_utils", "celery_utils.db", "celery_utils.shared"):
        _stub(pkg)
    _stub("services.pydantic_models_db", FieldConfiguration=_FieldConfiguration, ExtractedMetadata=_ExtractedMetadata)
    _stub("celery_utils.db.db_utils", get_sync_session=lambda: None)
    _stub("services.celery_logger", logger=logging.getLogger("worker_stub"))
    _stub("celery_utils.shared.tracing", track_time=_passthrough_decorator)
    _stub("services.pydantic_models", ChunkMetadata=object)

    spec = importlib.util.spec_from_file_location("workers_schema_builder_under_test", WORKERS_SCHEMA_BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parity_field_specs():
    """One field per interesting behaviour the two builders must agree on."""
    return [
        PreviewFieldSpec(field_name="person_name", field_type="str", description="Candidate's full name"),
        PreviewFieldSpec(field_name="years_worked", field_type="int", description="Total years of experience", is_required=True),
        PreviewFieldSpec(field_name="skill_list", field_type="List[str]", description="Technical skills", max_items=5),
        PreviewFieldSpec(field_name="status", field_type="str", description="Application status",
                         restrict_to_choices=True, sample_values=["open", "closed"]),
        PreviewFieldSpec(field_name="start_date", field_type="date", description="Date this role started"),
        PreviewFieldSpec(field_name="work_history", field_type="List[dict]", description="Previous roles",
                         schema={"company": "str", "started_on": "date", "tags": "List[str]"}),
        PreviewFieldSpec(field_name="is_remote", field_type="bool", description="Remote role?"),
        PreviewFieldSpec(field_name="salary", field_type="float", description="Annual salary"),
        PreviewFieldSpec(field_name="extra", field_type="dict", description="Anything else structured"),
    ]


requires_workers_repo = pytest.mark.skipif(
    not WORKERS_SCHEMA_BUILDER.exists(),
    reason=f"fs-agent-workers not checked out at {WORKERS_SCHEMA_BUILDER}",
)


@requires_workers_repo
def test_ported_builder_matches_the_worker_json_schema_exactly():
    """THE parity test. Same field configs into both builders, identical schema out.

    ``PreviewFieldSpec`` is passed to both: the worker's builder only ever reads
    attributes off its ``configs`` (field_name, field_type, description,
    sample_values, schema, is_active, is_required, is_custom, restrict_to_choices,
    max_items), and the dataclass exposes exactly those — so both functions see
    byte-identical input and any difference in the output is a difference in the
    builder, which is the whole point.
    """
    worker = _load_worker_schema_builder()
    specs = _parity_field_specs()

    ported_schema = create_dynamic_pydantic_model("X", specs).model_json_schema()
    # chunking_params is only read for the 'chunks' field type, which a preview
    # can never carry — see deviation 1 in preview_model_builder's docstring.
    worker_schema = worker.create_dynamic_pydantic_model(
        "X", {"enable_chunking": True, "chunk_size": None}, specs
    ).model_json_schema()

    assert ported_schema == worker_schema, (
        "The ported preview builder has drifted from fs-agent-workers' real one. "
        "Re-diff utils/enrichment/preview_model_builder.py against "
        f"{WORKERS_SCHEMA_BUILDER} before changing this test.\n"
        f"ported={json.dumps(ported_schema, sort_keys=True)}\n"
        f"worker={json.dumps(worker_schema, sort_keys=True)}"
    )


@requires_workers_repo
def test_ported_nested_row_model_matches_the_worker_one():
    """The List[dict] row model is where a drift would do the most damage — it is
    the type that decides whether a table comes back as rows or as prose."""
    worker = _load_worker_schema_builder()
    schema = {"description": "str", "quantity": "int", "unit_price": "float", "billed_on": "date", "tags": "List[str]"}

    from utils.enrichment.preview_model_builder import create_nested_model_from_schema as ported_nested

    assert (
        ported_nested(schema, "line_items_Nested").model_json_schema()
        == worker.create_nested_model_from_schema(schema, "line_items_Nested").model_json_schema()
    )


# ---------------------------------------------------------------------------
# Layer 2 — every field type through the ported builder
# ---------------------------------------------------------------------------

def test_every_supported_field_type_builds():
    """All eight caller-visible types produce a field. A type that silently
    disappeared would show up in the preview as a permanently 'not_found' field."""
    specs = [
        PreviewFieldSpec(field_name="a_str", field_type="str", description="s"),
        PreviewFieldSpec(field_name="a_list_str", field_type="List[str]", description="ls"),
        PreviewFieldSpec(field_name="an_int", field_type="int", description="i"),
        PreviewFieldSpec(field_name="a_float", field_type="float", description="f"),
        PreviewFieldSpec(field_name="a_bool", field_type="bool", description="b"),
        PreviewFieldSpec(field_name="a_date", field_type="date", description="d"),
        PreviewFieldSpec(field_name="a_dict", field_type="dict", description="dd"),
        PreviewFieldSpec(field_name="a_list_dict", field_type="List[dict]", description="ld"),
    ]
    model = create_dynamic_pydantic_model("AllTypes", specs)
    assert set(model.model_fields) == {s.field_name for s in specs}


def test_list_dict_with_a_schema_builds_a_real_row_model():
    specs = [PreviewFieldSpec(
        field_name="line_items", field_type="List[dict]", description="Rows",
        schema={"description": "str", "amount": "float"},
    )]
    schema = create_dynamic_pydantic_model("Invoice", specs).model_json_schema()
    assert "line_items_Nested" in schema["$defs"]
    assert set(schema["$defs"]["line_items_Nested"]["properties"]) == {"description", "amount"}


def test_date_field_carries_the_iso_8601_instruction():
    """Without this sentence the model returns whatever the document printed
    ('16 Jun 19'), which is unfilterable. The instruction is part of the port."""
    specs = [PreviewFieldSpec(field_name="signed_on", field_type="date", description="Signature date")]
    described = create_dynamic_pydantic_model("D", specs).model_fields["signed_on"].description
    assert "ISO-8601 format (YYYY-MM-DD)" in described
    assert "never infer or" in described


def test_restrict_to_choices_becomes_a_literal():
    specs = [PreviewFieldSpec(field_name="status", field_type="str", description="s",
                              restrict_to_choices=True, sample_values=["open", "closed"])]
    schema = create_dynamic_pydantic_model("S", specs).model_json_schema()
    assert schema["properties"]["status"]["anyOf"][0]["enum"] == ["open", "closed"]


def test_max_items_truncates_a_list_str_instead_of_rejecting_it():
    specs = [PreviewFieldSpec(field_name="skills", field_type="List[str]", description="s", max_items=2)]
    model = create_dynamic_pydantic_model("M", specs)
    assert model(skills=["a", "b", "c", "d"]).skills == ["a", "b"]


def test_a_str_field_coerces_a_list_answer_into_one_string():
    """LLMs answer 'one value' questions with a list constantly; the port's
    BeforeValidator is what stops that being a failed extraction."""
    specs = [PreviewFieldSpec(field_name="name", field_type="str", description="n")]
    model = create_dynamic_pydantic_model("N", specs)
    assert model(name=["Ada", "Lovelace"]).name == "Ada, Lovelace"


def test_inactive_fields_are_skipped():
    specs = [
        PreviewFieldSpec(field_name="kept", field_type="str", description="k"),
        PreviewFieldSpec(field_name="dropped", field_type="str", description="d", is_active=False),
    ]
    assert set(create_dynamic_pydantic_model("A", specs).model_fields) == {"kept"}


def test_specs_from_request_fields_accepts_either_schema_key():
    specs = specs_from_request_fields([
        {"field_name": "a", "field_type": "List[dict]", "schema": {"x": "str"}},
        {"field_name": "b", "field_type": "List[dict]", "field_schema": {"y": "int"}},
    ])
    assert specs[0].schema == {"x": "str"}
    assert specs[1].schema == {"y": "int"}


# ---------------------------------------------------------------------------
# Layer 3 — request validation, auth, and response shaping
# ---------------------------------------------------------------------------

def test_fields_must_be_valid_json():
    with pytest.raises(HTTPException) as exc:
        parse_fields_json("{not json")
    assert exc.value.status_code == 400
    assert "not valid JSON" in exc.value.detail


def test_fields_must_be_a_non_empty_array():
    for bad in ("[]", '{"field_name": "x"}'):
        with pytest.raises(HTTPException) as exc:
            parse_fields_json(bad)
        assert exc.value.status_code == 400


def test_field_type_is_checked_against_the_services_own_allow_list():
    body = json.dumps([{"field_name": "amount", "field_type": "Decimal", "description": "d"}])
    with pytest.raises(HTTPException) as exc:
        parse_fields_json(body)
    assert exc.value.status_code == 400
    assert "not a supported type" in exc.value.detail


def test_a_field_may_omit_its_type_and_is_left_for_the_resolver():
    """The regression this guards: preview used to 400 on a missing field_type.

    The user never picks a Python type in this product — they describe the field
    and utils/config/field_type_resolver resolves it, which is why field_type is
    optional on both write endpoints. Step 3 demanding one rejected every field a
    user added by hand (only step 2's generated fields carry a type), which
    presented as "'fields[2].field_type' is '(missing)'" in prod on 2026-09-15.

    The key must be ABSENT rather than defaulted, because needs_type_resolution()
    is what decides whether the resolver runs at all.
    """
    for body in (
        [{"field_name": "renewal_date", "description": "When the contract renews"}],
        [{"field_name": "renewal_date", "description": "When the contract renews", "field_type": ""}],
        [{"field_name": "renewal_date", "description": "When the contract renews", "field_type": None}],
    ):
        parsed = parse_fields_json(json.dumps(body))
        assert "field_type" not in parsed[0], f"field_type should be left unset, got {parsed[0]}"
        assert needs_type_resolution(parsed[0]) is True


def test_an_explicit_type_still_skips_resolution():
    """Deterministic-first: a type the caller sent is never second-guessed."""
    parsed = parse_fields_json(json.dumps([{"field_name": "total", "field_type": "float"}]))
    assert parsed[0]["field_type"] == "float"
    assert needs_type_resolution(parsed[0]) is False


def test_duplicate_field_names_are_rejected():
    body = json.dumps([
        {"field_name": "vendor", "field_type": "str"},
        {"field_name": "vendor", "field_type": "str"},
    ])
    with pytest.raises(HTTPException) as exc:
        parse_fields_json(body)
    assert exc.value.status_code == 400
    assert "duplicate field_name" in exc.value.detail


def test_valid_fields_pass_through_normalized():
    parsed = parse_fields_json(json.dumps([{"field_name": "  vendor ", "field_type": "str", "description": "d"}]))
    assert parsed == [{"field_name": "vendor", "field_type": "str", "description": "d"}]


def test_master_api_key_is_rejected_the_same_way_api_key_management_rejects_it():
    """There is no knowledge base for a master key to be scoped to, and this call
    spends tokens that must be charged to a specific account."""
    with pytest.raises(HTTPException) as exc:
        preview_router_module._require_session_owner_id({"auth_type": "master_api_key", "api_key": "k"})
    assert exc.value.status_code == 401
    assert "logged-in session" in exc.value.detail


def test_generated_user_api_key_is_also_rejected():
    with pytest.raises(HTTPException) as exc:
        preview_router_module._require_session_owner_id({"auth_type": "user_api_key", "user_id": OWNER_EMAIL})
    assert exc.value.status_code == 401


def test_a_session_with_no_resolvable_email_is_rejected():
    with pytest.raises(HTTPException) as exc:
        preview_router_module._require_session_owner_id({"auth_type": "session", "session_data": {}})
    assert exc.value.status_code == 401
    assert "Could not resolve user identity" in exc.value.detail


def test_a_real_session_resolves_the_owner_id():
    owner = preview_router_module._require_session_owner_id(
        {"auth_type": "session", "session_data": {"email": "Owner@FutureSmart.ai"}}
    )
    assert owner == OWNER_EMAIL


def test_not_found_is_distinct_from_extracted():
    """A field the document does not answer is the most useful signal in step 3 —
    it must never be indistinguishable from a value that came back."""
    specs = specs_from_request_fields([
        {"field_name": "person_name", "field_type": "str"},
        {"field_name": "notice_period", "field_type": "str"},
        {"field_name": "skills", "field_type": "List[str]"},
    ])
    rows = build_fields_run(specs, {"person_name": "Ananya Rao", "notice_period": None, "skills": []})
    by_name = {r["field_name"]: r for r in rows}
    assert by_name["person_name"]["status"] == "extracted"
    assert by_name["notice_period"]["status"] == "not_found"
    # An empty list is "the document said nothing", same as a null.
    assert by_name["skills"]["status"] == "not_found"
    assert by_name["person_name"]["field_type_label"] == "Text"


def test_suggested_fields_drop_duplicates_and_hallucinated_types():
    suggestions = [
        SuggestedField(field_name="purchase_order_ref", description="PO number", field_type="str",
                       sample_value="PO-48213", reason="Present on the document"),
        SuggestedField(field_name="vendor_name", description="dup", field_type="str", reason="already asked for"),
        SuggestedField(field_name="tax_amount", description="Tax", field_type="Decimal", reason="bad type"),
    ]
    kept = filter_suggested_fields(suggestions, requested_names={"vendor_name"})
    assert [k["field_name"] for k in kept] == ["purchase_order_ref"]
    assert kept[0]["field_type_label"] == "Text"


def test_token_estimate_is_the_right_order_of_magnitude():
    small = estimate_prompt_tokens("x" * 400, 3)
    large = estimate_prompt_tokens("x" * 40_000, 3)
    assert small < 1_000 < large


def test_bundled_samples_exist_for_the_wizards_document_types():
    assert "resume" in PREVIEW_SAMPLES and "invoice" in PREVIEW_SAMPLES
    for key in available_sample_keys():
        sample = PREVIEW_SAMPLES[key]
        assert len(sample["text"]) > 200
        # Synthetic only: every address in the bundled text uses a reserved TLD.
        assert "@" not in sample["text"] or ".invalid" in sample["text"]


def test_the_preview_route_is_registered_and_is_not_knowledge_scoped():
    paths = {r.path for r in preview_router_module.router.routes}
    assert "/v1/enrichment/preview" in paths
    assert "/v1/{knowledge_id}/enrichment/preview" not in paths


# ---------------------------------------------------------------------------
# Layer 4 — full request → response round trip, LLM mocked
# ---------------------------------------------------------------------------

class _FakeRaw:
    """Stands in for the raw AIMessage that carries usage_metadata."""

    def __init__(self, input_tokens=1234, output_tokens=210):
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}


class _FakeLLM:
    """Stands in for the chat model ``run_with_key_fallback`` would have built.

    ``with_structured_output`` returns a real ``RunnableLambda`` so the production
    code's ``prompt | structured_llm`` composes exactly as it does against a real
    provider — the prompt is genuinely rendered, so a broken template variable
    fails the test instead of being skipped over.
    """

    def __init__(self, build_result):
        self._build_result = build_result
        self.seen_models = []
        self.rendered_prompts = []

    def with_structured_output(self, model, include_raw=False):
        from langchain_core.runnables import RunnableLambda

        self.seen_models.append(model)

        def _respond(prompt_value):
            self.rendered_prompts.append(prompt_value.to_string())
            parsed = self._build_result(model)
            return {"raw": _FakeRaw(), "parsed": parsed} if include_raw else parsed

        return RunnableLambda(_respond)


def _install_fake_llm(monkeypatch, build_result, *, provider="openai", key_type="platform-key"):
    """Replace run_with_key_fallback with one that runs the caller's own _invoke
    against a fake model — so the prompt build, the structured-output wiring and
    the response assembly are all exercised, only the provider is not."""
    captured = {}

    async def _fake_run(call, **kwargs):
        captured.update(kwargs)
        llm = _FakeLLM(build_result)
        value = await call(llm)
        captured["llm"] = llm
        return byok_fallback.ByokOutcome(value, provider, key_type)

    monkeypatch.setattr(byok_fallback, "run_with_key_fallback", _fake_run)
    return captured


@pytest.fixture
def preview_client(monkeypatch):
    """Router mounted with the auth dependency overridden to a logged-in session,
    and every DB-touching helper stubbed."""
    app = FastAPI()
    app.include_router(preview_router_module.router)
    app.dependency_overrides[preview_router_module.get_api_key_or_session] = lambda: {
        "auth_type": "session",
        "session_data": {"email": OWNER_EMAIL},
    }

    async def _fake_model():
        return preview_run.PreviewModels("openai", "gpt-5.4-mini", 0.0, None, None, "test")

    monkeypatch.setattr(preview_run, "_resolve_preview_model", _fake_model)
    return TestClient(app)


@pytest.fixture
def usage_spy(monkeypatch):
    """Record the quota check and the usage row instead of hitting the usage DB."""
    calls = {"enforced": [], "recorded": []}

    async def _fake_enforce(owner_id, estimated_tokens):
        calls["enforced"].append((owner_id, estimated_tokens))

    async def _fake_record(**kwargs):
        calls["recorded"].append(kwargs)

    monkeypatch.setattr(preview_run, "_enforce_llm_quota", _fake_enforce)
    monkeypatch.setattr(preview_run, "_record_preview_usage", _fake_record)
    return calls


_RESUME_FIELDS = json.dumps([
    {"field_name": "person_name", "field_type": "str", "description": "Full name"},
    {"field_name": "years_worked", "field_type": "int", "description": "Years of experience"},
    {"field_name": "skill_list", "field_type": "List[str]", "description": "Skills"},
    {"field_name": "notice_period_days", "field_type": "int", "description": "Notice period in days"},
    {"field_name": "graduation_date", "field_type": "date", "description": "Graduation date"},
])


def test_full_round_trip_on_a_bundled_sample(preview_client, usage_spy, monkeypatch):
    def _build(model):
        return model(
            extracted={
                "person_name": "Ananya Rao",
                "years_worked": 7,
                "skill_list": ["Python", "Go", "Kubernetes"],
                "notice_period_days": None,      # the document does not answer this one
                "graduation_date": date(2017, 6, 1),
            },
            suggested_fields=[
                SuggestedField(field_name="current_location", description="City the candidate is based in",
                               field_type="str", sample_value="Pune", reason="Stated in the header"),
            ],
        )

    captured = _install_fake_llm(monkeypatch, _build)

    response = preview_client.post(
        "/v1/enrichment/preview",
        data={"sample_key": "resume", "fields": _RESUME_FIELDS},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # --- CC6: the requested fields came back, typed ---
    assert body["extracted"]["person_name"] == "Ananya Rao"
    assert body["extracted"]["skill_list"] == ["Python", "Go", "Kubernetes"]
    # A real array, not the UI's display string, and a date serialized as ISO-8601.
    assert body["extracted"]["graduation_date"] == "2017-06-01"

    statuses = {row["field_name"]: row["status"] for row in body["fields_run"]}
    assert statuses["person_name"] == "extracted"
    assert statuses["notice_period_days"] == "not_found"

    # --- CC9: a field nobody asked for ---
    assert [s["field_name"] for s in body["suggested_fields"]] == ["current_location"]
    assert body["suggested_fields"][0]["sample_value"] == "Pune"

    # --- nothing was saved, and the payload says so ---
    assert body["run"]["persisted"] is False
    assert body["document"]["file_name"] == "sample_resume.txt"
    assert body["document"]["text_truncated"] is False
    assert body["document"]["document_processing_mode"] == "auto"

    # --- one LLM call, owner-scoped, with no knowledge_id (Q2) ---
    assert captured["knowledge_id"] is None
    assert captured["owner_id"] == OWNER_EMAIL
    assert captured["source"] == "enrichment_preview"
    assert len(captured["llm"].seen_models) == 1, "CC9 must be folded into the single extraction call"

    # The one prompt really carried both jobs: the document, and the requested
    # field names the suggestion job must not duplicate.
    sent = captured["llm"].rendered_prompts[0]
    assert "ANANYA RAO" in sent
    assert "notice_period_days" in sent
    assert "suggested_fields" in sent

    # --- metered: checked before, recorded after (Q1) ---
    assert usage_spy["enforced"] and usage_spy["enforced"][0][0] == OWNER_EMAIL
    assert len(usage_spy["recorded"]) == 1
    recorded = usage_spy["recorded"][0]
    assert recorded["input_tokens"] == 1234 and recorded["output_tokens"] == 210
    assert recorded["owner_id"] == OWNER_EMAIL


def test_quota_refusal_happens_before_the_llm_is_called(preview_client, monkeypatch):
    """429 must cost nothing. If the provider were called first the user would be
    billed for a run they were then refused."""
    called = {"llm": False}

    async def _refuse(owner_id, estimated_tokens):
        raise HTTPException(status_code=429, detail={"error": "quota_exceeded"})

    def _build(model):
        called["llm"] = True
        return model(extracted={})

    monkeypatch.setattr(preview_run, "_enforce_llm_quota", _refuse)
    _install_fake_llm(monkeypatch, _build)

    response = preview_client.post(
        "/v1/enrichment/preview",
        data={"sample_key": "resume", "fields": _RESUME_FIELDS},
    )
    assert response.status_code == 429
    assert called["llm"] is False


def test_provider_failure_is_502_never_an_empty_extracted(preview_client, usage_spy, monkeypatch):
    async def _boom(call, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(byok_fallback, "run_with_key_fallback", _boom)

    response = preview_client.post(
        "/v1/enrichment/preview",
        data={"sample_key": "resume", "fields": _RESUME_FIELDS},
    )
    assert response.status_code == 502
    assert "extracted" not in response.json()
    # A failed run must not be billed as a successful one.
    assert usage_spy["recorded"] == []


def test_exactly_one_document_source_is_required(preview_client, usage_spy):
    none_sent = preview_client.post("/v1/enrichment/preview", data={"fields": _RESUME_FIELDS})
    assert none_sent.status_code == 400
    assert "Exactly one of" in none_sent.json()["detail"]

    two_sent = preview_client.post(
        "/v1/enrichment/preview",
        data={"fields": _RESUME_FIELDS, "sample_key": "resume", "file_url": "https://example.invalid/a.pdf"},
    )
    assert two_sent.status_code == 400
    assert "Exactly one of" in two_sent.json()["detail"]


def test_unknown_sample_key_lists_the_real_ones(preview_client, usage_spy):
    response = preview_client.post(
        "/v1/enrichment/preview",
        data={"fields": _RESUME_FIELDS, "sample_key": "bank_statement"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Unknown sample_key" in detail
    for key in available_sample_keys():
        assert key in detail


def test_an_uploaded_text_file_is_read_and_previewed(preview_client, usage_spy, monkeypatch):
    def _build(model):
        return model(extracted={"person_name": "Ada Lovelace", "years_worked": 12,
                                "skill_list": [], "notice_period_days": 30,
                                "graduation_date": None})

    _install_fake_llm(monkeypatch, _build)

    content = b"Ada Lovelace\nAnalytical Engine Programmer\n12 years experience\nNotice period: 30 days\n"
    response = preview_client.post(
        "/v1/enrichment/preview",
        data={"fields": _RESUME_FIELDS},
        files={"file": ("ada.txt", content, "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document"]["file_name"] == "ada.txt"
    assert body["extracted"]["person_name"] == "Ada Lovelace"
    assert body["run"]["persisted"] is False


def test_a_document_with_no_readable_text_is_422_not_a_wasted_llm_call(preview_client, usage_spy, monkeypatch):
    called = {"llm": False}

    def _build(model):
        called["llm"] = True
        return model(extracted={})

    _install_fake_llm(monkeypatch, _build)

    response = preview_client.post(
        "/v1/enrichment/preview",
        data={"fields": _RESUME_FIELDS},
        files={"file": ("empty.txt", b"   ", "text/plain")},
    )
    assert response.status_code == 422
    assert called["llm"] is False


def test_long_documents_are_truncated_and_say_so(preview_client, usage_spy, monkeypatch):
    """A 300-page PDF must not turn one click into an unbounded call — and the
    user must be told they are looking at a partial read, not a document that
    happened to answer nothing."""
    def _build(model):
        return model(extracted={})

    _install_fake_llm(monkeypatch, _build)

    huge = ("Vendor: Harborlight. Amount: 100. " * 2000).encode()
    assert len(huge) > preview_run.MAX_PREVIEW_TEXT_CHARS

    response = preview_client.post(
        "/v1/enrichment/preview",
        data={"fields": json.dumps([{"field_name": "vendor", "field_type": "str", "description": "v"}])},
        files={"file": ("big.txt", huge, "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document"]["text_truncated"] is True
    assert body["document"]["text_chars"] == preview_run.MAX_PREVIEW_TEXT_CHARS


def test_a_hand_added_untyped_field_previews_instead_of_400ing(
    preview_client, usage_spy, monkeypatch
):
    """End-to-end regression for the prod 400 of 2026-09-15.

    Reproduces the exact request the wizard sent: two fields carrying the type
    step 2 generated, plus one the user added by hand with only a name and a
    description. That third field used to fail the whole run with
    "'fields[2].field_type' is '(missing)'". It must now be resolved and run.
    """
    resolver_calls = {}

    async def _fake_resolve(fields, **kwargs):
        resolver_calls["fields"] = [dict(f) for f in fields]
        resolver_calls["kwargs"] = kwargs
        out = [dict(f) for f in fields]
        for f in out:
            if "field_type" not in f:
                f["field_type"] = "date"
        return out, [{"field_name": "renewal_date", "field_type": "date",
                      "reason": "A calendar date"}], []

    monkeypatch.setattr(preview_router_module, "resolve_field_types", _fake_resolve)

    def _build(model):
        return model(
            extracted={"person_name": "Ananya Rao", "years_worked": 7,
                       "renewal_date": date(2027, 4, 1)},
            suggested_fields=[],
        )

    _install_fake_llm(monkeypatch, _build)

    response = preview_client.post(
        "/v1/enrichment/preview",
        data={
            "sample_key": "resume",
            "fields": json.dumps([
                {"field_name": "person_name", "field_type": "str", "description": "Full name"},
                {"field_name": "years_worked", "field_type": "int", "description": "Years of experience"},
                {"field_name": "renewal_date", "description": "When the contract renews"},
            ]),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # The untyped field actually ran, and its value came back.
    assert body["extracted"]["renewal_date"] == "2027-04-01"
    statuses = {row["field_name"]: row["status"] for row in body["fields_run"]}
    assert statuses["renewal_date"] == "extracted"

    # Never silent: the type this service chose is reported back.
    assert body["type_resolutions"] == [
        {"field_name": "renewal_date", "field_type": "date", "reason": "A calendar date"}
    ]

    # Only the untyped field was up for resolution; the typed ones passed through.
    sent = {f["field_name"]: f for f in resolver_calls["fields"]}
    assert sent["person_name"]["field_type"] == "str"
    assert "field_type" not in sent["renewal_date"]
    # No collection exists at step 3 (Q2), so the resolver is owner-scoped only.
    assert resolver_calls["kwargs"] == {"owner_id": OWNER_EMAIL}


def test_a_resolver_failure_is_a_502_not_a_silent_str_fallback(
    preview_client, usage_spy, monkeypatch
):
    """Defaulting to `str` would look identical to a successful resolution and
    would extract a differently shaped value than the real collection — the exact
    silent coercion field_type_resolver exists to remove."""
    async def _boom(fields, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(preview_router_module, "resolve_field_types", _boom)

    response = preview_client.post(
        "/v1/enrichment/preview",
        data={
            "sample_key": "resume",
            "fields": json.dumps([{"field_name": "renewal_date", "description": "When it renews"}]),
        },
    )
    assert response.status_code == 502, response.text
    assert "Could not resolve the type" in response.json()["detail"]
