from utils.db.retrieval_ops import _extract_unique_values_by_field_type, _infer_metadata_field_type


def test_infer_metadata_field_type_for_scalar_and_structured_values():
    assert _infer_metadata_field_type(["a", "b"]) == "str"
    assert _infer_metadata_field_type([True, False]) == "bool"
    assert _infer_metadata_field_type([1, 2]) == "int"
    assert _infer_metadata_field_type([1.5, 2.5]) == "float"
    assert _infer_metadata_field_type([{"source": "clickup"}]) == "dict"
    assert _infer_metadata_field_type([["a", "b"]]) == "List[str]"
    assert _infer_metadata_field_type([[{"source": "clickup"}]]) == "List[dict]"


def test_extract_unique_values_by_inferred_user_defined_type():
    dict_values = [{"source": "clickup"}, {"source": "clickup"}, {"source": "drive"}]
    assert _extract_unique_values_by_field_type(dict_values, "dict") == [
        {"source": "clickup"},
        {"source": "drive"},
    ]

    list_values = [["draft", "review"], ["review", "published"]]
    assert sorted(_extract_unique_values_by_field_type(list_values, "List[str]")) == [
        "draft",
        "published",
        "review",
    ]
