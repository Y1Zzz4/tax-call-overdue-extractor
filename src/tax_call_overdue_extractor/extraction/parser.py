"""模型响应解析与 Schema 校验。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from tax_call_overdue_extractor.exceptions import ResponseParseError

from .schemas import ExtractionResult


ALLOWED_SOURCE_NAMES = (
    "电话录音转文本内容",
    "业务内容",
    "答复内容",
)
EVIDENCE_FIELDS = ("enterprise_evidence", "tax_evidence", "overdue_evidence")
STRING_EVIDENCE_REASON = "模型返回字符串形式证据，但无法在三个允许输入字段中唯一定位原文来源"
INVALID_EVIDENCE_REASON = "模型返回的证据对象无法在指定输入字段中定位原文"
PROCESS_WORDS = ("退税", "退个税", "申请", "流程", "审核", "审批", "工单", "任务", "作废", "超时")
TAX_OVERDUE_PATTERNS = (
    r"税款.{0,12}逾期",
    r"逾期.{0,12}(?:缴纳|申报)",
    r"申报.{0,12}逾期",
    r"欠税",
    r"超过.{0,12}(?:税款|申报|缴纳|纳税).{0,8}期限",
    r"(?:税款|申报|缴纳).{0,12}超过.{0,8}期限",
    r"滞纳金",
)


CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_extraction_response(
    raw_response: str,
    source_texts: dict[str, str | None] | None = None,
) -> ExtractionResult:
    """解析模型响应并执行完整 Schema 校验。"""

    text = _strip_code_fence(raw_response)
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResponseParseError(f"模型响应不是合法 JSON: {exc.msg}") from exc

    normalized = _normalize_response_payload(data)
    if source_texts is not None:
        normalized = preprocess_response_payload(normalized, source_texts)
    try:
        return ExtractionResult.model_validate(normalized)
    except ValidationError as exc:
        raise ResponseParseError(f"模型响应不符合提取 Schema: {_safe_validation_error_summary(exc)}") from exc


def preprocess_response_payload(
    data: Any,
    source_texts: dict[str, str | None],
) -> Any:
    """在严格 Schema 校验前，用本次实际发送的三列原文保守修复 Evidence。"""

    if not isinstance(data, dict):
        return data
    texts = {name: source_texts.get(name) for name in ALLOWED_SOURCE_NAMES}
    normalized = deepcopy(data)
    items = normalized.get("items")
    if not isinstance(items, list):
        return normalized

    kept_items: list[Any] = []
    top_review_reasons = _reason_list(normalized.get("review_reasons"))
    for item in items:
        if not isinstance(item, dict):
            kept_items.append(item)
            continue
        evidence_reasons: list[str] = []
        for field in EVIDENCE_FIELDS:
            item[field] = _repair_evidence_list(item.get(field, []), texts, evidence_reasons)
        for child_field in ("periods", "amounts"):
            children = item.get(child_field)
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        child["evidence"] = _repair_evidence_list(
                            child.get("evidence", []), texts, evidence_reasons
                        )

        _correct_explicit_overdue(item, evidence_reasons)
        _remove_procedural_periods(item, texts, evidence_reasons)
        if evidence_reasons:
            item["needs_review"] = True
            item["review_reasons"] = _dedupe_reasons(
                [*_reason_list(item.get("review_reasons")), *evidence_reasons]
            )
            top_review_reasons.extend(evidence_reasons)

        if not _is_process_only_item(item):
            kept_items.append(item)

    normalized["items"] = kept_items
    if items and not kept_items:
        normalized["has_relevant_information"] = False
        normalized["conflicts"] = []

    if top_review_reasons:
        normalized["needs_review"] = True
        normalized["review_reasons"] = _dedupe_reasons(top_review_reasons)
    elif not kept_items:
        normalized["needs_review"] = False
        normalized["review_reasons"] = []
    return normalized


def _repair_evidence_list(
    value: Any,
    source_texts: dict[str, str | None],
    review_reasons: list[str],
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        review_reasons.append(INVALID_EVIDENCE_REASON)
        return []

    repaired: list[dict[str, str]] = []
    for evidence in value:
        if isinstance(evidence, dict):
            source = evidence.get("source")
            quote = evidence.get("quote")
            if (
                source in ALLOWED_SOURCE_NAMES
                and isinstance(quote, str)
                and quote.strip()
                and _contains_quote(source_texts.get(source), quote)
            ):
                repaired.append({"source": source, "quote": quote})
            else:
                review_reasons.append(INVALID_EVIDENCE_REASON)
            continue
        if isinstance(evidence, str) and evidence.strip():
            matching_sources = [
                source
                for source, text in source_texts.items()
                if _contains_quote(text, evidence)
            ]
            if len(matching_sources) == 1:
                repaired.append({"source": matching_sources[0], "quote": evidence})
            else:
                review_reasons.append(STRING_EVIDENCE_REASON)
            continue
        review_reasons.append(INVALID_EVIDENCE_REASON)
    return repaired


def _contains_quote(source_text: str | None, quote: str) -> bool:
    if not source_text:
        return False
    return _normalize_whitespace(quote) in _normalize_whitespace(source_text)


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _remove_procedural_periods(
    item: dict[str, Any],
    source_texts: dict[str, str | None],
    review_reasons: list[str],
) -> None:
    periods = item.get("periods")
    if not isinstance(periods, list):
        return
    kept: list[Any] = []
    for period in periods:
        if not isinstance(period, dict):
            kept.append(period)
            continue
        evidence_text = " ".join(
            str(entry.get("quote", ""))
            for entry in period.get("evidence", [])
            if isinstance(entry, dict)
        )
        raw_text = str(period.get("raw_text", ""))
        source_context = " ".join(_contexts_around(raw_text, source_texts.values()))
        context = f"{raw_text} {evidence_text} {source_context}"
        procedural = re.search(r"申请|提交|发起|审核|审批|流程", context)
        tax_period = re.search(r"所属期|申报期|纳税期|税款所属|税款.{0,8}(?:期间|月份|季度|年度)", context)
        if procedural and not tax_period:
            review_reasons.append("模型将申请、提交或审核时间误作税款所属期，已删除该 period")
        else:
            kept.append(period)
    item["periods"] = kept


def _contexts_around(needle: str, texts: Any, radius: int = 40) -> list[str]:
    if not needle:
        return []
    contexts: list[str] = []
    for text in texts:
        if not text:
            continue
        start = 0
        while True:
            index = text.find(needle, start)
            if index < 0:
                break
            contexts.append(text[max(0, index - radius): index + len(needle) + radius])
            start = index + len(needle)
    return contexts


def _correct_explicit_overdue(item: dict[str, Any], review_reasons: list[str]) -> None:
    if item.get("explicitly_overdue") is not True:
        return
    evidence = item.get("overdue_evidence")
    if not isinstance(evidence, list):
        return
    text = " ".join(
        str(entry.get("quote", "")) for entry in evidence if isinstance(entry, dict)
    )
    if re.search(r"(?:尚未|并未|没有|未).{0,8}(?:逾期|到期|超过期限)", text):
        item["explicitly_overdue"] = False
        review_reasons.append("模型把明确尚未逾期误判为已逾期，已在本地纠正")
        return
    if re.search(r"会不会|是否会|会否|假如|如果.{0,20}(?:逾期|过期|超过期限)", text):
        item["explicitly_overdue"] = None
        item["overdue_evidence"] = []
        review_reasons.append("模型把假设性逾期咨询误判为已逾期，已在本地纠正")


def _is_process_only_item(item: dict[str, Any]) -> bool:
    evidence_quotes: list[str] = []
    for field in EVIDENCE_FIELDS:
        value = item.get(field)
        if isinstance(value, list):
            evidence_quotes.extend(
                str(entry.get("quote", "")) for entry in value if isinstance(entry, dict)
            )
    text = " ".join(
        [
            str(item.get("relationship_note") or ""),
            *evidence_quotes,
            *[str(value) for value in item.get("tax_type_raw", []) if value],
        ]
    )
    if not any(word in text for word in PROCESS_WORDS):
        return False
    return not any(re.search(pattern, text) for pattern in TAX_OVERDUE_PATTERNS)


def _reason_list(value: Any) -> list[str]:
    return [str(reason) for reason in value] if isinstance(value, list) else []


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    return list(dict.fromkeys(reasons))


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
