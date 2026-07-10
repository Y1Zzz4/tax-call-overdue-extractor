from __future__ import annotations

import pytest
from pydantic import ValidationError

from tax_call_overdue_extractor.extraction.schemas import ExtractionResult


def valid_result_payload() -> dict:
    return {
        "schema_version": "1.0",
        "has_relevant_information": True,
        "items": [
            {
                "enterprise_name": "甲公司",
                "enterprise_evidence": [{"source": "业务内容", "quote": "甲公司"}],
                "tax_types": ["增值税", "增值税", "城市建设维护税"],
                "tax_type_raw": ["增值税", "城建税"],
                "tax_evidence": [{"source": "答复内容", "quote": "增值税和城建税"}],
                "periods": [
                    {
                        "raw_text": "2025年第四季度",
                        "period_type": "quarter",
                        "start_year": 2025,
                        "start_month": 10,
                        "end_year": 2025,
                        "end_month": 12,
                        "relative_expression": None,
                        "evidence": [{"source": "业务内容", "quote": "2025年第四季度"}],
                    }
                ],
                "amounts": [
                    {
                        "raw_text": "约2.5万元",
                        "role": "tax",
                        "is_calculated": False,
                        "calculation_note": None,
                        "evidence": [{"source": "答复内容", "quote": "约2.5万元"}],
                    }
                ],
                "explicitly_overdue": True,
                "overdue_evidence": [{"source": "电话录音转文本内容", "quote": "已经逾期"}],
                "relationship_note": "同一企业同一期间涉及两个税种",
                "needs_review": False,
                "review_reasons": [],
            }
        ],
        "conflicts": [],
        "needs_review": False,
        "review_reasons": [],
    }


def test_valid_schema_and_tax_type_deduplication() -> None:
    result = ExtractionResult.model_validate(valid_result_payload())

    assert result.items[0].tax_types == ["增值税", "城市建设维护税"]


def test_missing_required_field_is_rejected() -> None:
    payload = valid_result_payload()
    del payload["schema_version"]

    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_illegal_tax_type_is_rejected() -> None:
    payload = valid_result_payload()
    payload["items"][0]["tax_types"] = ["城建税"]

    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_illegal_evidence_source_is_rejected() -> None:
    payload = valid_result_payload()
    payload["items"][0]["enterprise_evidence"][0]["source"] = "企业名称"

    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_illegal_month_is_rejected() -> None:
    payload = valid_result_payload()
    payload["items"][0]["periods"][0]["start_month"] = 13

    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_multi_items_and_conflict_structure_are_valid() -> None:
    payload = valid_result_payload()
    payload["items"].append({**payload["items"][0], "enterprise_name": "乙公司"})
    payload["conflicts"] = [
        {
            "field": "overdue",
            "description": "两个来源给出的逾期状态无法同时成立",
            "claims": [
                {"source": "业务内容", "value": "已逾期", "quote": "已逾期"},
                {"source": "答复内容", "value": "未逾期", "quote": "未逾期"},
            ],
        }
    ]
    payload["needs_review"] = True
    payload["review_reasons"] = ["存在逾期状态冲突"]

    result = ExtractionResult.model_validate(payload)

    assert len(result.items) == 2
    assert len(result.conflicts) == 1


def test_irrelevant_result_cannot_contain_items() -> None:
    payload = valid_result_payload()
    payload["has_relevant_information"] = False

    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)
