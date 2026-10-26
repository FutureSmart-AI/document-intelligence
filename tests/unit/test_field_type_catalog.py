"""GET /v1/field-types data — the create-collection wizard's step 2 type
dropdown (per the mock), docs/plan/collection-creation-flow.

Covers: every caller-choosable FieldType is present with its real label,
"chunks" (internal) never appears, int/float both carry "Number" as two
distinct entries, the order is stable (the enum's declaration order), and
each entry carries a `requires_schema` flag that is true for exactly the two
schema-bearing types (dict, List[dict]).
"""

from utils.models.enums import (
    CALLER_FIELD_TYPES,
    SCHEMA_BEARING_FIELD_TYPES,
    FieldType,
    get_field_type_catalog,
    label_for_field_type,
)


def test_catalog_covers_every_caller_field_type_exactly_once():
    catalog = get_field_type_catalog()
    types = [entry["field_type"] for entry in catalog]
    assert set(types) == CALLER_FIELD_TYPES
    assert len(types) == len(set(types))


def test_internal_chunks_type_is_never_offered():
    catalog = get_field_type_catalog()
    assert "chunks" not in [entry["field_type"] for entry in catalog]


def test_every_entry_has_a_real_label():
    catalog = get_field_type_catalog()
    for entry in catalog:
        assert entry["field_type_label"] == label_for_field_type(entry["field_type"])
        assert entry["field_type_label"]


def test_int_and_float_are_two_entries_sharing_one_label():
    catalog = get_field_type_catalog()
    by_type = {entry["field_type"]: entry["field_type_label"] for entry in catalog}
    assert by_type["int"] == "Number"
    assert by_type["float"] == "Number"
    assert "int" in by_type and "float" in by_type  # distinct keys, not collapsed


def test_order_matches_the_enum_declaration_order():
    catalog = get_field_type_catalog()
    assert [entry["field_type"] for entry in catalog] == [ft.value for ft in FieldType]


def test_dict_and_list_dict_require_schema():
    catalog = get_field_type_catalog()
    by_type = {entry["field_type"]: entry["requires_schema"] for entry in catalog}
    assert by_type["dict"] is True
    assert by_type["List[dict]"] is True


def test_every_other_type_does_not_require_schema():
    catalog = get_field_type_catalog()
    for entry in catalog:
        if entry["field_type"] in SCHEMA_BEARING_FIELD_TYPES:
            continue
        assert entry["requires_schema"] is False


def test_requires_schema_flag_matches_schema_bearing_set_exactly():
    catalog = get_field_type_catalog()
    flagged = {entry["field_type"] for entry in catalog if entry["requires_schema"]}
    assert flagged == SCHEMA_BEARING_FIELD_TYPES
