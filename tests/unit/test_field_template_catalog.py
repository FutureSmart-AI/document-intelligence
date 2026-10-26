"""GET /v1/field-templates data (CC1/CC2, docs/plan/collection-creation-flow).

Covers: the catalog only lists visible templates, invoice/contract are split
apart (not the legacy combined invoice_contract), the legacy value stays a
valid FieldTemplate member so existing PUT /config callers are unaffected,
and every field in the catalog carries a real field_type_label.
"""

from utils.config.enrichment_config import (
    get_field_template_catalog,
    DOMAIN_FIELD_TEMPLATES,
    FIELD_TEMPLATE_METADATA,
)
from utils.models.enums import FieldTemplate, label_for_field_type


def test_visible_templates_match_the_wizards_five_choices():
    catalog = get_field_template_catalog()
    keys = [t["key"] for t in catalog]
    # "Something else" (default), plus the four document types from the
    # Product requirement: Resumes/CVs, Invoices, Contracts,
    # Support tickets. Combined invoice_contract and the internal
    # software_docs preset must NOT appear here.
    assert keys == ["default", "resume", "invoice", "contract", "support_ticket"]


def test_hidden_templates_excluded_by_default_but_reachable_with_include_hidden():
    default_catalog = get_field_template_catalog()
    assert "invoice_contract" not in [t["key"] for t in default_catalog]
    assert "software_docs" not in [t["key"] for t in default_catalog]

    full_catalog = get_field_template_catalog(include_hidden=True)
    full_keys = [t["key"] for t in full_catalog]
    assert "invoice_contract" in full_keys
    assert "software_docs" in full_keys


def test_default_template_seeds_no_extra_fields():
    catalog = get_field_template_catalog()
    default_entry = next(t for t in catalog if t["key"] == "default")
    assert default_entry["fields"] == []


def test_invoice_and_contract_are_genuinely_different_field_sets():
    catalog = get_field_template_catalog()
    invoice_fields = {f["field_name"] for f in next(t for t in catalog if t["key"] == "invoice")["fields"]}
    contract_fields = {f["field_name"] for f in next(t for t in catalog if t["key"] == "contract")["fields"]}
    # Not a relabeled copy of one another.
    assert invoice_fields != contract_fields
    assert "line_items" in invoice_fields
    assert "auto_renews" in contract_fields


def test_legacy_invoice_contract_enum_value_still_accepted():
    # A caller already sending "invoice_contract" on PUT /config must keep working —
    # CC2's decision was to split for NEW callers, not to remove the old value.
    assert FieldTemplate("invoice_contract") == FieldTemplate.INVOICE_CONTRACT
    assert "invoice_contract" in DOMAIN_FIELD_TEMPLATES
    assert DOMAIN_FIELD_TEMPLATES["invoice_contract"], "legacy template must keep its original field set"


def test_every_new_enum_member_resolves_and_has_metadata():
    for key in ("invoice", "contract", "support_ticket"):
        member = FieldTemplate(key)
        assert member.value == key
        assert key in FIELD_TEMPLATE_METADATA
        assert key in DOMAIN_FIELD_TEMPLATES


def test_every_catalog_field_has_a_real_type_label():
    catalog = get_field_template_catalog()
    for template in catalog:
        for field in template["fields"]:
            assert field["field_type_label"] == label_for_field_type(field["field_type"])
            assert field["field_type_label"], f"{template['key']}.{field['field_name']} has no label"


def test_every_visible_template_with_fields_has_default_prefill_text():
    # Step 1's collection_name/service_description/extraction_instructions
    # prefill, hand-written per template — not mechanically derived from the
    # fields' own descriptions.
    catalog = get_field_template_catalog()
    for key in ("resume", "invoice", "contract", "support_ticket"):
        entry = next(t for t in catalog if t["key"] == key)
        assert entry["default_collection_name"], key
        assert entry["default_service_description"], key
        assert entry["default_extraction_instructions"], key

    default_entry = next(t for t in catalog if t["key"] == "default")
    assert default_entry["default_collection_name"] is None
    assert default_entry["default_service_description"] is None
    assert default_entry["default_extraction_instructions"] is None


def test_nested_fields_carry_their_schema_others_dont():
    catalog = get_field_template_catalog()
    resume_fields = {f["field_name"]: f for f in next(t for t in catalog if t["key"] == "resume")["fields"]}
    assert resume_fields["work_experience"]["schema"] == {
        "company": "str", "title": "str", "start_year": "int", "end_year": "int", "summary": "str",
    }
    assert resume_fields["education"]["schema"] == {
        "institution": "str", "degree": "str", "field_of_study": "str", "graduation_year": "int",
    }
    # A flat field never carries the key at all -- not schema: null.
    assert "schema" not in resume_fields["candidate_name"]

    invoice_fields = {f["field_name"]: f for f in next(t for t in catalog if t["key"] == "invoice")["fields"]}
    assert invoice_fields["line_items"]["schema"] == {
        "description": "str", "quantity": "float", "unit_price": "float", "amount": "float",
    }


def test_router_response_model_round_trips_schema_and_defaults():
    # The dict shape alone isn't proof -- "schema" collides with a reserved
    # BaseModel attribute in Pydantic v2, so this has to go through the actual
    # response model FastAPI serializes, the same way list_field_templates
    # does, with the same by_alias/exclude_none FastAPI applies.
    from routers.knowledge_config_new import FieldTemplateCatalogResponse

    catalog = get_field_template_catalog()
    response = FieldTemplateCatalogResponse(templates=catalog)
    dumped = response.model_dump(mode="json", by_alias=True, exclude_none=True)

    resume = next(t for t in dumped["templates"] if t["key"] == "resume")
    assert resume["default_collection_name"] == FIELD_TEMPLATE_METADATA["resume"]["default_collection_name"]
    work_experience = next(f for f in resume["fields"] if f["field_name"] == "work_experience")
    assert work_experience["schema"]["company"] == "str"
    candidate_name = next(f for f in resume["fields"] if f["field_name"] == "candidate_name")
    assert "schema" not in candidate_name

    default_entry = next(t for t in dumped["templates"] if t["key"] == "default")
    assert "default_collection_name" not in default_entry
    assert "default_service_description" not in default_entry
    assert "default_extraction_instructions" not in default_entry
