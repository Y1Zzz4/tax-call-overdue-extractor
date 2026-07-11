from __future__ import annotations

import json

import pytest

from tax_call_overdue_extractor.exceptions import ResponseParseError
from tax_call_overdue_extractor.extraction.parser import parse_extraction_response


SOURCE_TEXTS = {
    "电话录音转文本内容": "电话中提到甲测试企业，增值税税款已经逾期缴纳。共同证据",
    "业务内容": "业务记录中的甲测试企业。共同证据",
    "答复内容": "答复原文",
}


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


def test_string_evidence_is_converted_only_when_source_is_unique() -> None:
    item = base_item()
    item["enterprise_name"] = "甲测试企业"
    item["enterprise_evidence"] = ["业务记录中的甲测试企业"]

    result = parse_extraction_response(
        json.dumps(relevant_result_with_item(item), ensure_ascii=False),
        SOURCE_TEXTS,
    )

    evidence = result.items[0].enterprise_evidence[0]
    assert evidence.source == "业务内容"
    assert evidence.quote == "业务记录中的甲测试企业"
    assert result.needs_review is False


def test_unlocatable_or_ambiguous_evidence_is_removed_and_reviewed() -> None:
    item = base_item()
    item["enterprise_name"] = "甲测试企业"
    item["enterprise_evidence"] = ["模型改写的企业名称", "共同证据"]

    result = parse_extraction_response(
        json.dumps(relevant_result_with_item(item), ensure_ascii=False),
        SOURCE_TEXTS,
    )

    assert result.items[0].enterprise_evidence == []
    assert result.items[0].needs_review is True
    assert result.needs_review is True
    assert any("无法在三个允许输入字段中唯一定位" in reason for reason in result.review_reasons)


def test_evidence_object_with_wrong_source_is_removed_and_reviewed() -> None:
    item = base_item()
    item["enterprise_name"] = "甲测试企业"
    item["enterprise_evidence"] = [
        {"source": "答复内容", "quote": "业务记录中的甲测试企业"}
    ]

    result = parse_extraction_response(
        json.dumps(relevant_result_with_item(item), ensure_ascii=False),
        SOURCE_TEXTS,
    )

    assert result.items[0].enterprise_evidence == []
    assert any("证据对象无法在指定输入字段" in reason for reason in result.review_reasons)


def test_hypothetical_overdue_question_cannot_be_explicitly_overdue() -> None:
    source_texts = {
        "电话录音转文本内容": "如果以后税款会不会逾期？",
        "业务内容": None,
        "答复内容": None,
    }
    item = base_item()
    item["tax_types"] = ["未识别"]
    item["explicitly_overdue"] = True
    item["overdue_evidence"] = ["如果以后税款会不会逾期"]

    result = parse_extraction_response(
        json.dumps(relevant_result_with_item(item), ensure_ascii=False),
        source_texts,
    )

    assert result.items[0].explicitly_overdue is None
    assert result.items[0].overdue_evidence == []
    assert result.items[0].needs_review is True


def test_row_2_refund_workflow_is_not_tax_overdue_or_tax_period() -> None:
    source_texts = {
        "电话录音转文本内容": (
            "之前我们2月底就发起过流程。他没有审核的话会不会过期？"
            "之前存在过逾期作废。现在的流程不会作废或者过期。"
        ),
        "业务内容": (
            "来电人于2026年6月12日提交了企业退个税的申请，之前2026年2月发起过一次"
            "企业退个税流程，由于老师没有及时审核导致该流程逾期作废。"
        ),
        "答复内容": "已反馈至个税处",
    }
    current = base_item()
    current.update({
        "enterprise_name": "重庆海尔家电销售有限公司上海分公司",
        "enterprise_evidence": [],
        "tax_types": ["个人所得税"],
        "tax_type_raw": ["个税"],
        "tax_evidence": ["企业退个税的申请"],
        "periods": [{
            "raw_text": "2026年6月12日",
            "period_type": "unparsed",
            "start_year": 2026,
            "start_month": 6,
            "end_year": 2026,
            "end_month": 6,
            "relative_expression": None,
            "evidence": ["2026年6月12日提交了企业退个税的申请"],
        }],
        "explicitly_overdue": False,
        "overdue_evidence": ["他没有审核的话会不会过期"],
        "relationship_note": "当前退税申请审核流程",
    })
    history = base_item()
    history.update({
        "enterprise_name": current["enterprise_name"],
        "enterprise_evidence": [],
        "tax_types": ["个人所得税"],
        "tax_type_raw": ["个税"],
        "tax_evidence": ["企业退个税流程"],
        "periods": [{
            "raw_text": "2026年2月",
            "period_type": "single_month",
            "start_year": 2026,
            "start_month": 2,
            "end_year": 2026,
            "end_month": 2,
            "relative_expression": None,
            "evidence": ["之前2026年2月发起过一次企业退个税流程"],
        }],
        "explicitly_overdue": True,
        "overdue_evidence": ["老师没有及时审核导致该流程逾期作废"],
        "relationship_note": "以前退税申请审核流程逾期作废",
    })
    payload = relevant_result_with_item(current)
    payload["items"] = [current, history]

    result = parse_extraction_response(json.dumps(payload, ensure_ascii=False), source_texts)

    assert result.has_relevant_information is False
    assert result.items == []
    assert all("2026年2月" not in period.raw_text for item in result.items for period in item.periods)
