"""Unit tests for chat/rag_agent/answer_node.py::_stringify_content.

Regression coverage for a prod incident: Gemini (and other providers) can
stream/return message ``content`` as a list of part-dicts instead of a plain
string, which crashed ``"".join(answer_parts)`` in the RAG answer-streaming
path with ``TypeError: sequence item 0: expected str instance, list found``.
"""

from chat.rag_agent.answer_node import _stringify_content


def test_stringify_content_plain_string():
    assert _stringify_content("hello") == "hello"


def test_stringify_content_list_of_text_parts():
    content = [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
    assert _stringify_content(content) == "hello world"


def test_stringify_content_list_with_non_dict_items():
    content = ["hello ", "world"]
    assert _stringify_content(content) == "hello world"


def test_stringify_content_list_with_missing_text_key():
    content = [{"type": "image_url", "image_url": "..."}, {"type": "text", "text": "hi"}]
    assert _stringify_content(content) == "hi"


def test_stringify_content_empty_and_none():
    assert _stringify_content("") == ""
    assert _stringify_content(None) == ""
    assert _stringify_content([]) == ""
