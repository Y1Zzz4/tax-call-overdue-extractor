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


def test_missing_review_flag_is_tolerated() -> None:
    payload = minimal_result()
    del payload["needs_review"]

    result = parse_extraction_response(json.dumps(payload, ensure_ascii=False))

    assert result.needs_review is False


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
    assert item.needs_review is False
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


def test_unlocatable_evidence_is_removed_and_ambiguous_uses_source_priority() -> None:
    item = base_item()
    item["enterprise_name"] = "甲测试企业"
    item["enterprise_evidence"] = ["模型改写的企业名称", "共同证据"]

    result = parse_extraction_response(
        json.dumps(relevant_result_with_item(item), ensure_ascii=False),
        SOURCE_TEXTS,
    )

    assert len(result.items[0].enterprise_evidence) == 1
    assert result.items[0].enterprise_evidence[0].source == "业务内容"
    assert result.items[0].needs_review is False
    assert result.needs_review is False


def test_evidence_object_with_wrong_source_is_remapped_without_review() -> None:
    item = base_item()
    item["enterprise_name"] = "甲测试企业"
    item["enterprise_evidence"] = [
        {"source": "答复内容", "quote": "业务记录中的甲测试企业"}
    ]

    result = parse_extraction_response(
        json.dumps(relevant_result_with_item(item), ensure_ascii=False),
        SOURCE_TEXTS,
    )

    assert result.items[0].enterprise_evidence[0].source == "业务内容"
    assert result.items[0].needs_review is False
    assert "证据来源已按原文自动修正" in result.items[0].review_reasons


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
    assert result.items[0].needs_review is False


def test_common_period_amount_variants_and_wrapped_json_are_tolerated() -> None:
    item = base_item()
    item.update({
        "tax_types": ["个税"],
        "tax_type_raw": ["个税"],
        "periods": [{"period_value": "去年7月份"}],
        "amounts": [{"amount_raw": "50块钱", "amount_type": "罚款"}],
    })
    raw = "以下是结果：\n" + json.dumps(relevant_result_with_item(item), ensure_ascii=False) + "\n完成"

    result = parse_extraction_response(raw)

    assert result.items[0].tax_types == ["个人所得税"]
    assert result.items[0].periods[0].raw_text == "去年7月份"
    assert result.items[0].amounts[0].raw_text == "50块钱"
    assert result.items[0].amounts[0].role == "penalty"


def test_reply_content_overrides_overdue_status() -> None:
    item = base_item()
    item["tax_types"] = ["增值税"]
    item["explicitly_overdue"] = True
    source_texts = {
        "电话录音转文本内容": "系统好像说逾期了",
        "业务内容": "疑似逾期",
        "答复内容": "经核实目前尚未逾期",
    }

    result = parse_extraction_response(
        json.dumps(relevant_result_with_item(item), ensure_ascii=False), source_texts
    )

    assert result.items[0].explicitly_overdue is False
    assert result.items[0].needs_review is False


def test_only_clear_reply_enterprise_conflict_requires_review() -> None:
    item = base_item()
    item["enterprise_name"] = "甲公司"
    payload = relevant_result_with_item(item)
    payload["conflicts"] = [{
        "field": "enterprise_name",
        "sources": [
            {"source": "答复内容", "quote": "乙公司"},
            {"source": "业务内容", "quote": "甲公司"},
        ],
        "description": "两个明确企业名称不一致",
    }]

    result = parse_extraction_response(json.dumps(payload, ensure_ascii=False))

    assert result.needs_review is True
    assert len(result.conflicts) == 1


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

    assert result.has_relevant_information is True
    assert len(result.items) == 1
    assert result.items[0].enterprise_name == "重庆海尔家电销售有限公司上海分公司"
    assert result.items[0].explicitly_overdue is None
    assert all("2026年2月" not in period.raw_text for item in result.items for period in item.periods)


@pytest.mark.parametrize(
    ("business_content", "expected_name"),
    [
        (
            "ED:91310115MA1HBJ4F4P-上海昊源化工有限公司5月25日申请的分配额度流程逾期？",
            "上海昊源化工有限公司",
        ),
        (
            "【相关工单】咨询税务注销调查巡查问题\n"
            "企业：91310120MA7BYN7TXE 上海凯捷特精密机械制造有限公司\n"
            "内容：调查巡查派发及结果录入流程逾期",
            "上海凯捷特精密机械制造有限公司",
        ),
    ],
)
def test_business_content_enterprise_fallback_even_for_process_only_record(
    business_content: str,
    expected_name: str,
) -> None:
    raw = json.dumps(minimal_result(), ensure_ascii=False)
    result = parse_extraction_response(
        raw,
        {
            "业务内容": business_content,
            "答复内容": None,
            "电话录音转文本内容": None,
        },
    )

    assert result.has_relevant_information is True
    assert len(result.items) == 1
    assert result.items[0].enterprise_name == expected_name
    assert result.items[0].explicitly_overdue is None
    assert result.needs_review is False


@pytest.mark.parametrize(
    "generic_phrase",
    ["因为这个公司", "我们现在上海成立的家公司", "那个公司", "一家公司"],
)
def test_generic_company_phrases_are_not_enterprise_names(generic_phrase: str) -> None:
    item = base_item()
    item["enterprise_name"] = generic_phrase
    payload = relevant_result_with_item(item)

    result = parse_extraction_response(
        json.dumps(payload, ensure_ascii=False),
        {
            "业务内容": generic_phrase,
            "答复内容": None,
            "电话录音转文本内容": None,
        },
    )

    assert result.has_relevant_information is False
    assert result.items == []
