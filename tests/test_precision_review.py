from __future__ import annotations

import json

from tax_call_overdue_extractor.extraction.precision_review import (
    apply_precision_review,
    parse_precision_review,
)
from tax_call_overdue_extractor.extraction.normalization import (
    normalize_extraction_result,
    parse_reference_month,
)
from tax_call_overdue_extractor.extraction.parser import parse_extraction_response


def empty_result():
    return parse_extraction_response(json.dumps({
        "schema_version": "1.0",
        "has_relevant_information": False,
        "items": [],
        "conflicts": [],
        "needs_review": False,
        "review_reasons": [],
    }, ensure_ascii=False))


def result_with_enterprise(name: str):
    return parse_extraction_response(json.dumps({
        "schema_version": "1.0",
        "has_relevant_information": True,
        "items": [{
            "enterprise_name": name,
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
        }],
        "conflicts": [],
        "needs_review": False,
        "review_reasons": [],
    }, ensure_ascii=False))


def test_precision_review_semantically_adds_export_tax_and_social_security() -> None:
    texts = {
        "业务内容": "咨询出口退税，同时存在社会保险费补缴情形",
        "答复内容": None,
        "电话录音转文本内容": None,
    }
    review = parse_precision_review(
        '{"enterprises":[],"tax_types":[],"periods":[],"explicitly_overdue":null}',
        texts,
    )
    result = apply_precision_review(empty_result(), review, texts)
    normalized = normalize_extraction_result(
        result,
        reference_month=parse_reference_month("2026-06-01", "6"),
    )[0]

    assert result.items[0].tax_types == ["进出口税", "社保费"]
    assert normalized.tax_types_text == "进出口税；社保费"


def test_precision_review_keeps_every_explicit_tax_type() -> None:
    texts = {
        "业务内容": "涉及增值税、企业所得税、印花税和社保费",
        "答复内容": None,
        "电话录音转文本内容": None,
    }
    raw = json.dumps({
        "enterprises": [],
        "tax_types": ["增值税", "企业所得税", "印花税", "社保费"],
        "periods": [],
        "explicitly_overdue": None,
    }, ensure_ascii=False)
    result = apply_precision_review(empty_result(), parse_precision_review(raw, texts), texts)

    assert result.items[0].tax_types == ["增值税", "企业所得税", "印花税", "社保费"]


def test_first_pass_also_keeps_explicit_tax_types_if_precision_call_fails() -> None:
    texts = {
        "业务内容": "涉及增值税、企业所得税、印花税和社保费",
        "答复内容": None,
        "电话录音转文本内容": None,
    }
    result = parse_extraction_response(json.dumps({
        "items": [{
            "enterprise_name": None,
            "enterprise_evidence": [],
            "tax_types": ["增值税"],
            "tax_type_raw": ["增值税"],
            "tax_evidence": [],
            "periods": [],
            "amounts": [],
            "explicitly_overdue": None,
            "overdue_evidence": [],
        }],
    }, ensure_ascii=False), texts)

    assert result.items[0].tax_types == ["增值税", "社保费", "企业所得税", "印花税"]


def test_precision_review_normalizes_relative_month_and_exact_date_range() -> None:
    texts = {
        "业务内容": "去年7月以及2026年2月3日至2026年2月18日所属期",
        "答复内容": None,
        "电话录音转文本内容": None,
    }
    raw = json.dumps({
        "enterprises": [],
        "tax_types": ["增值税"],
        "periods": [
            {
                "raw_text": "去年7月",
                "period_type": "single_month",
                "start_year": 2025,
                "start_month": 7,
                "start_day": None,
                "end_year": 2025,
                "end_month": 7,
                "end_day": None,
            },
            {
                "raw_text": "2026年2月3日至2026年2月18日",
                "period_type": "unparsed",
                "start_year": 2026,
                "start_month": 2,
                "start_day": 3,
                "end_year": 2026,
                "end_month": 2,
                "end_day": 18,
            },
        ],
        "explicitly_overdue": None,
    }, ensure_ascii=False)
    result = apply_precision_review(empty_result(), parse_precision_review(raw, texts), texts)
    normalized = normalize_extraction_result(
        result,
        reference_month=parse_reference_month("2026-06-01", "6"),
    )[0]

    assert normalized.periods_text == "2025年7月；2026年2月3日至2026年2月18日"
    assert normalized.overdue_text == "已逾期"


def test_bare_month_defaults_to_2026_and_current_month_is_not_declared_overdue() -> None:
    result = parse_extraction_response(json.dumps({
        "schema_version": "1.0",
        "has_relevant_information": True,
        "items": [{
            "enterprise_name": None,
            "enterprise_evidence": [],
            "tax_types": ["增值税"],
            "tax_type_raw": ["增值税"],
            "tax_evidence": [],
            "periods": ["6月"],
            "amounts": [],
            "explicitly_overdue": None,
            "overdue_evidence": [],
            "relationship_note": None,
            "needs_review": False,
            "review_reasons": [],
        }],
        "conflicts": [],
        "needs_review": False,
        "review_reasons": [],
    }, ensure_ascii=False))
    normalized = normalize_extraction_result(
        result,
        reference_month=parse_reference_month("2026-06-20", "=MONTH(H2)"),
    )[0]

    assert normalized.periods_text == "2026年6月"
    assert normalized.overdue_text is None


def test_relative_year_normalization_does_not_depend_on_reference_month() -> None:
    result = parse_extraction_response(json.dumps({
        "schema_version": "1.0",
        "has_relevant_information": True,
        "items": [{
            "enterprise_name": None,
            "enterprise_evidence": [],
            "tax_types": ["增值税"],
            "tax_type_raw": ["增值税"],
            "tax_evidence": [],
            "periods": ["5月", "去年"],
            "amounts": [],
            "explicitly_overdue": None,
            "overdue_evidence": [],
            "relationship_note": None,
            "needs_review": False,
            "review_reasons": [],
        }],
        "conflicts": [],
        "needs_review": False,
        "review_reasons": [],
    }, ensure_ascii=False))
    normalized = normalize_extraction_result(result, reference_month=None)[0]

    assert normalized.periods_text == "2026年5月；2025年1月至2025年12月"
    assert normalized.overdue_text == "已逾期"


def test_spoken_and_short_years_are_normalized() -> None:
    result = parse_extraction_response(json.dumps({
        "items": [{
            "enterprise_name": None,
            "enterprise_evidence": [],
            "tax_types": ["个人所得税"],
            "tax_type_raw": ["个人所得税"],
            "tax_evidence": [],
            "periods": ["二四年", "24年10月"],
            "amounts": [],
            "explicitly_overdue": None,
            "overdue_evidence": [],
        }],
    }, ensure_ascii=False))
    normalized = normalize_extraction_result(result, reference_month=None)[0]

    assert normalized.periods_text == "2024年；2024年10月"
    assert normalized.overdue_text == "已逾期"


def test_known_and_unknown_tax_types_use_other_without_unrecognized() -> None:
    result = parse_extraction_response(json.dumps({
        "items": [{
            "enterprise_name": None,
            "enterprise_evidence": [],
            "tax_types": ["增值税", "教育费附加", "未识别"],
            "tax_type_raw": ["增值税", "教育费附加"],
            "tax_evidence": [],
            "periods": [],
            "amounts": [],
            "explicitly_overdue": None,
            "overdue_evidence": [],
        }],
    }, ensure_ascii=False))
    normalized = normalize_extraction_result(result, reference_month=None)[0]

    assert result.items[0].tax_types == ["增值税", "其他"]
    assert normalized.tax_types_text == "增值税；其他"


def test_month_range_suppresses_redundant_single_months() -> None:
    result = parse_extraction_response(json.dumps({
        "items": [{
            "enterprise_name": None,
            "enterprise_evidence": [],
            "tax_types": ["城市建设维护税"],
            "tax_type_raw": ["城建税"],
            "tax_evidence": [],
            "periods": [
                {"raw_text": "24年1~2月", "period_type": "month_range", "start_year": 2024, "start_month": 1, "end_year": 2024, "end_month": 2},
                {"raw_text": "1月", "period_type": "single_month", "start_year": 2024, "start_month": 1, "end_year": 2024, "end_month": 1},
                {"raw_text": "2月", "period_type": "single_month", "start_year": 2024, "start_month": 2, "end_year": 2024, "end_month": 2},
            ],
            "amounts": [],
            "explicitly_overdue": True,
            "overdue_evidence": [],
        }],
    }, ensure_ascii=False))
    normalized = normalize_extraction_result(result, reference_month=None)[0]

    assert normalized.periods_text == "2024年1月至2024年2月"


def test_high_confidence_voice_confirmation_can_correct_incomplete_name() -> None:
    texts = {
        "业务内容": None,
        "答复内容": None,
        "电话录音转文本内容": "坐席逐字确认：昉是日字旁一个方，智是智慧的智，完整名称确认无误。",
    }
    raw = json.dumps({
        "enterprises": [{
            "name": "上海昉智信息技术有限公司",
            "source": "电话录音转文本内容",
            "quote": texts["电话录音转文本内容"],
            "confidence": "high",
        }],
        "tax_types": [],
        "periods": [],
        "explicitly_overdue": None,
    }, ensure_ascii=False)
    result = apply_precision_review(
        result_with_enterprise("信息技术有限公司"),
        parse_precision_review(raw, texts),
        texts,
    )

    assert result.items[0].enterprise_name == "上海昉智信息技术有限公司"


def test_empty_precision_enterprise_result_does_not_erase_first_pass_name() -> None:
    texts = {
        "业务内容": "上海富悦电器有限公司咨询申报问题",
        "答复内容": None,
        "电话录音转文本内容": None,
    }
    review = parse_precision_review(
        '{"enterprises":[],"tax_types":[],"periods":[],"explicitly_overdue":null}',
        texts,
    )
    result = apply_precision_review(result_with_enterprise("上海富悦电器有限公司"), review, texts)

    assert result.items[0].enterprise_name == "上海富悦电器有限公司"
