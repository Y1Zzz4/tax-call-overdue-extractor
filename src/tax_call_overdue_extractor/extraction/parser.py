"""模型响应解析与 Schema 校验。"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from tax_call_overdue_extractor.exceptions import ResponseParseError

from .schemas import ExtractionResult


CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_extraction_response(raw_response: str) -> ExtractionResult:
    """解析模型响应并执行完整 Schema 校验。"""

    text = _strip_code_fence(raw_response)
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResponseParseError(f"模型响应不是合法 JSON: {exc.msg}") from exc

    normalized = _normalize_response_payload(data)
    try:
        return ExtractionResult.model_validate(normalized)
    except ValidationError as exc:
        raise ResponseParseError(f"模型响应不符合提取 Schema: {_safe_validation_error_summary(exc)}") from exc


def _strip_code_fence(raw_response: str) -> str:
    match = CODE_FENCE_PATTERN.match(raw_response)
    if not match:
        return raw_response.strip()
    return match.group(1).strip()


def _normalize_response_payload(data: Any) -> Any:
    """规范化模型常见的非标准 JSON 形态；不补造业务事实。"""

    if not isinstance(data, dict):
        return data

    normalized = dict(data)
    items = normalized.get("items")
    if isinstance(items, list):
        normalized["items"] = [_normalize_item(item) for item in items]
    if normalized.get("has_relevant_information") is True and "needs_review" not in normalized:
        item_reviews = [
            item.get("needs_review")
            for item in normalized.get("items", [])
            if isinstance(item, dict)
        ]
        normalized["needs_review"] = any(value is True for value in item_reviews)
    return normalized


def _normalize_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item

    normalized = dict(item)
    fallback_evidence = _valid_evidence_list(normalized.pop("evidence", []))

    if "needs_review" not in normalized:
        normalized["needs_review"] = True
        reasons = normalized.get("review_reasons")
        if not isinstance(reasons, list):
            reasons = []
        normalized["review_reasons"] = [
            *reasons,
            "模型未提供 item.needs_review，已标记待复核",
        ]

    if normalized.get("enterprise_name") and not normalized.get("enterprise_evidence") and fallback_evidence:
        normalized["enterprise_evidence"] = fallback_evidence

    normalized["periods"] = _normalize_periods(normalized.get("periods", []), fallback_evidence)
    normalized["amounts"] = _normalize_amounts(normalized.get("amounts", []), fallback_evidence)
    return normalized


def _normalize_periods(periods: Any, fallback_evidence: list[dict[str, Any]]) -> Any:
    if periods is None:
        return []
    if not isinstance(periods, list):
        periods = [periods]

    normalized_periods: list[Any] = []
    for period in periods:
        if isinstance(period, str):
            normalized_periods.append(
                {
                    "raw_text": period,
                    "period_type": "unparsed",
                    "start_year": None,
                    "start_month": None,
                    "end_year": None,
                    "end_month": None,
                    "relative_expression": None,
                    "evidence": fallback_evidence,
                }
            )
            continue
        if isinstance(period, dict):
            period_dict = dict(period)
            if "period_type" not in period_dict and period_dict.get("raw_text"):
                period_dict["period_type"] = "unparsed"
            if "evidence" not in period_dict and fallback_evidence:
                period_dict["evidence"] = fallback_evidence
            normalized_periods.append(period_dict)
            continue
        normalized_periods.append(period)
    return normalized_periods


def _normalize_amounts(amounts: Any, fallback_evidence: list[dict[str, Any]]) -> Any:
    if amounts is None:
        return []
    if not isinstance(amounts, list):
        amounts = [amounts]

    normalized_amounts: list[Any] = []
    for amount in amounts:
        if isinstance(amount, str):
            normalized_amounts.append(
                {
                    "raw_text": amount,
                    "role": "unknown",
                    "is_calculated": False,
                    "calculation_note": None,
                    "evidence": fallback_evidence,
                }
            )
            continue
        if isinstance(amount, dict):
            amount_dict = dict(amount)
            amount_dict.setdefault("role", "unknown")
            amount_dict.setdefault("is_calculated", False)
            if "evidence" not in amount_dict and fallback_evidence:
                amount_dict["evidence"] = fallback_evidence
            normalized_amounts.append(amount_dict)
            continue
        normalized_amounts.append(amount)
    return normalized_amounts


def _valid_evidence_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _safe_validation_error_summary(exc: ValidationError) -> str:
    """返回不包含 input_value 的校验错误摘要，避免日志泄露原文。"""

    safe_errors: list[str] = []
    for error in exc.errors(include_input=False):
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        error_type = error.get("type", "unknown")
        message = error.get("msg", "validation error")
        safe_errors.append(f"{location}: {error_type}: {message}")
    return "; ".join(safe_errors)
