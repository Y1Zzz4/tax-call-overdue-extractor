from __future__ import annotations

import json

import pytest

from tax_call_overdue_extractor.exceptions import ResponseParseError
from tax_call_overdue_extractor.extraction.parser import parse_extraction_response


def minimal_result() -> dict:
    return {
        "schema_version": "1.0",
        "has_relevant_information": False,
        "items": [],
        "conflicts": [],
        "needs_review": False,
        "review_reasons": [],
    }


def test_parse_plain_json() -> None:
    result = parse_extraction_response(json.dumps(minimal_result(), ensure_ascii=False))

    assert result.schema_version == "1.0"
    assert result.has_relevant_information is False


def test_parse_markdown_json_code_fence() -> None:
    raw = "```json\n" + json.dumps(minimal_result(), ensure_ascii=False) + "\n```"

    result = parse_extraction_response(raw)

    assert result.items == []


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(ResponseParseError, match="不是合法 JSON"):
        parse_extraction_response("{not json")


def test_schema_validation_error_is_rejected() -> None:
    payload = minimal_result()
    del payload["needs_review"]

    with pytest.raises(ResponseParseError, match="不符合提取 Schema"):
        parse_extraction_response(json.dumps(payload, ensure_ascii=False))
