"""Tests for core/utils.py"""
import pytest
from core.utils import extract_json


def test_extract_json_from_code_block():
    text = '```json\n{"key": "value"}\n```'
    assert extract_json(text) == {"key": "value"}


def test_extract_json_from_unlabelled_code_block():
    text = "```\n{\"a\": 1}\n```"
    assert extract_json(text) == {"a": 1}


def test_extract_json_bare_in_prose():
    text = 'Here is the result: {"severity": "high", "type": "KeyError"} — done.'
    result = extract_json(text)
    assert result["severity"] == "high"
    assert result["type"] == "KeyError"


def test_extract_json_nested():
    text = '{"outer": {"inner": [1, 2, 3]}}'
    assert extract_json(text) == {"outer": {"inner": [1, 2, 3]}}


def test_extract_json_malformed_code_block_falls_back_to_bare():
    # Code block JSON is malformed; bare JSON in the rest of the text should be found.
    text = "```json\nnot valid json\n```\n{\"ok\": true}"
    assert extract_json(text) == {"ok": True}


def test_extract_json_raises_when_no_json():
    with pytest.raises(ValueError, match="No valid JSON"):
        extract_json("There is absolutely no JSON here at all.")


def test_extract_json_raises_on_empty_string():
    with pytest.raises(ValueError):
        extract_json("")
