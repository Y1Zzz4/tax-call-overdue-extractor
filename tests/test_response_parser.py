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


def relevant_result_with_item(item: dict) -> dict:
    return {
        "schema_version": "1.0",
        "has_relevant_information": True,
        "items": [item],
        "conflicts": [],
        "needs_review": False,
        "review_reasons": [],
    }


def base_item() -> dict:
    return {
        "enterprise_name": None,
        "enterprise_evidence": [],
        "tax_types": [],
        "tax_type_raw": [],
        "tax_evidence": [],
        "periods": [],
        "amounts": [],
        "explicitly_overdue": None,
        "overdue_evidence": [],
        "relationship_note": None,
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


def test_parser_keeps_enterprise_only_item() -> None:
    item = base_item()
    item["enterprise_name"] = "甲测试企业"
    item["enterprise_evidence"] = [{"source": "业务内容", "quote": "甲测试企业"}]

    result = parse_extraction_response(json.dumps(relevant_result_with_item(item), ensure_ascii=False))

    assert result.has_relevant_information is True
    assert len(result.items) == 1
    assert result.items[0].enterprise_name == "甲测试企业"
    assert result.items[0].tax_types == []
    assert result.items[0].periods == []
    assert result.items[0].amounts == []


def test_tax_type_only_is_relevant_information() -> None:
    item = base_item()
    item["tax_types"] = ["增值税"]
    item["tax_type_raw"] = ["增值税"]
    item["tax_evidence"] = [{"source": "业务内容", "quote": "增值税"}]

    result = parse_extraction_response(json.dumps(relevant_result_with_item(item), ensure_ascii=False))

    assert result.has_relevant_information is True
    assert result.items[0].tax_types == ["增值税"]
    assert result.items[0].enterprise_name is None


def test_period_only_is_relevant_information() -> None:
    item = base_item()
    item["periods"] = [
        {
            "raw_text": "2025年第四季度",
            "period_type": "quarter",
            "start_year": 2025,
            "start_month": 10,
            "end_year": 2025,
            "end_month": 12,
            "relative_expression": None,
            "evidence": [{"source": "答复内容", "quote": "2025年第四季度"}],
        }
    ]

    result = parse_extraction_response(json.dumps(relevant_result_with_item(item), ensure_ascii=False))

    assert result.has_relevant_information is True
    assert result.items[0].periods[0].period_type == "quarter"
    assert result.items[0].enterprise_name is None


def test_enterprise_and_period_without_overdue_word_are_relevant() -> None:
    item = base_item()
    item["enterprise_name"] = "丙测试企业"
    item["enterprise_evidence"] = [{"source": "业务内容", "quote": "丙测试企业"}]
    item["periods"] = [
        {
            "raw_text": "2025年12月",
            "period_type": "single_month",
            "start_year": 2025,
            "start_month": 12,
            "end_year": 2025,
            "end_month": 12,
            "relative_expression": None,
            "evidence": [{"source": "业务内容", "quote": "2025年12月"}],
        }
    ]

    result = parse_extraction_response(json.dumps(relevant_result_with_item(item), ensure_ascii=False))

    assert result.has_relevant_information is True
    assert result.items[0].enterprise_name == "丙测试企业"
    assert result.items[0].explicitly_overdue is None


def test_parser_normalizes_model_shorthand_period_and_item_evidence() -> None:
    payload = {
        "schema_version": "1.0",
        "has_relevant_information": True,
        "items": [
            {
                "enterprise_name": "丁测试企业",
                "enterprise_evidence": [],
                "tax_types": [],
                "tax_type_raw": [],
                "tax_evidence": [],
                "periods": ["2026年2月"],
                "amounts": [],
                "explicitly_overdue": None,
                "overdue_evidence": [],
                "relationship_note": None,
                "review_reasons": [],
                "evidence": [{"source": "业务内容", "quote": "丁测试企业"}],
            }
        ],
        "conflicts": [],
        "needs_review": False,
        "review_reasons": [],
    }

    result = parse_extraction_response(json.dumps(payload, ensure_ascii=False))

    assert result.has_relevant_information is True
    item = result.items[0]
    assert item.enterprise_name == "丁测试企业"
    assert item.needs_review is True
    assert "模型未提供 item.needs_review" in item.review_reasons[0]
    assert item.enterprise_evidence[0].source == "业务内容"
    assert item.periods[0].raw_text == "2026年2月"
    assert item.periods[0].period_type == "unparsed"


def test_validation_error_message_does_not_include_sensitive_input_value() -> None:
    payload = relevant_result_with_item(base_item())
    payload["items"][0]["enterprise_name"] = "敏感企业名称"
    payload["items"][0]["enterprise_evidence"] = [
        {"source": "非法来源", "quote": "敏感证据片段"}
    ]

    with pytest.raises(ResponseParseError) as exc_info:
        parse_extraction_response(json.dumps(payload, ensure_ascii=False))

    message = str(exc_info.value)
    assert "模型响应不符合提取 Schema" in message
    assert "items.0.enterprise_evidence.0.source" in message
    assert "敏感企业名称" not in message
    assert "敏感证据片段" not in message
    assert "input_value" not in message
