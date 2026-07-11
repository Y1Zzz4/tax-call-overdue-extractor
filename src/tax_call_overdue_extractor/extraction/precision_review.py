"""统一精度复核：补强企业名称、税种、所属期和逾期状态。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .normalization import normalize_enterprise_name_candidate
from .parser import find_explicit_tax_types, find_high_confidence_enterprise, load_json_object
from .schemas import ExtractionResult
from .schemas import STANDARD_TAX_TYPES


SOURCE_PRIORITY = ("业务内容", "答复内容", "电话录音转文本内容")


@dataclass(frozen=True)
class EnterpriseDecision:
    name: str
    source: str
    quote: str
    confidence: str


@dataclass(frozen=True)
class PrecisionReview:
    enterprises: list[EnterpriseDecision]
    tax_types: list[str]
    periods: list[dict[str, Any]]
    explicitly_overdue: bool | None


def load_precision_review_prompt() -> str:
    path = Path(__file__).resolve().parents[1] / "prompt_templates" / "precision_review.txt"
    return path.read_text(encoding="utf-8")


def parse_precision_review(
    raw_response: str,
    source_texts: Mapping[str, str | None],
) -> PrecisionReview:
    data = load_json_object(raw_response)
    enterprises = parse_enterprise_review(raw_response, source_texts)
    tax_types = _review_tax_types(data, source_texts)
    periods = _review_periods(data)
    explicitly_overdue = _review_overdue(data)
    return PrecisionReview(enterprises, tax_types, periods, explicitly_overdue)


def parse_enterprise_review(
    raw_response: str,
    source_texts: Mapping[str, str | None],
) -> list[EnterpriseDecision]:
    """宽容读取企业复核 JSON，但只接受有原文支撑的非口语名称。"""

    data = load_json_object(raw_response)
    raw_candidates: Any = data.get("enterprises", []) if isinstance(data, dict) else []
    if not raw_candidates and isinstance(data, dict) and data.get("enterprise_name"):
        raw_candidates = [data]
    if not raw_candidates and isinstance(data, dict) and isinstance(data.get("items"), list):
        raw_candidates = [
            {
                "name": item.get("enterprise_name"),
                "source": _first_evidence_value(item, "source"),
                "quote": _first_evidence_value(item, "quote"),
                "confidence": "high",
            }
            for item in data["items"]
            if isinstance(item, dict) and item.get("enterprise_name")
        ]
    if isinstance(raw_candidates, dict):
        raw_candidates = [raw_candidates]
    if not isinstance(raw_candidates, list):
        return []

    decisions: list[EnterpriseDecision] = []
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        raw_name = candidate.get("name") or candidate.get("enterprise_name")
        name = normalize_enterprise_name_candidate(str(raw_name)) if raw_name else None
        if name is None:
            continue
        confidence = str(candidate.get("confidence") or "medium").lower()
        source = str(candidate.get("source") or "")
        quote = str(candidate.get("quote") or "").strip()
        source, quote, exact_name = _locate_evidence(name, source, quote, source_texts)
        if source is None or quote is None:
            continue
        if source == "电话录音转文本内容" and confidence != "high":
            continue
        if not exact_name and confidence != "high":
            continue
        decisions.append(EnterpriseDecision(name, source, quote[:200], confidence))
    return _prefer_complete_names(decisions)


def apply_enterprise_review(
    result: ExtractionResult,
    decisions: list[EnterpriseDecision],
    source_texts: Mapping[str, str | None],
) -> ExtractionResult:
    """让专用复核结果成为企业名称最终来源，不改动其他提取字段。"""

    payload = result.model_dump(mode="json")
    items = payload.get("items", [])
    if not decisions:
        local = find_high_confidence_enterprise(dict(source_texts))
        if local is not None:
            name, source, quote = local
            decisions = [EnterpriseDecision(name, source, quote, "high")]
    if not decisions:
        return result

    if not items:
        items = [_enterprise_only_item(decision) for decision in decisions]
    elif len(decisions) == 1:
        for item in items:
            _set_enterprise(item, decisions[0])
    else:
        used: set[int] = set()
        for index, item in enumerate(items):
            decision_index = _best_decision_index(item.get("enterprise_name"), decisions, used)
            if decision_index is not None:
                _set_enterprise(item, decisions[decision_index])
                used.add(decision_index)
        for index, decision in enumerate(decisions):
            if index not in used:
                items.append(_enterprise_only_item(decision))
    payload["items"] = items
    payload["has_relevant_information"] = True
    return ExtractionResult.model_validate(payload)


def apply_precision_review(
    result: ExtractionResult,
    review: PrecisionReview,
    source_texts: Mapping[str, str | None],
) -> ExtractionResult:
    reviewed = apply_enterprise_review(result, review.enterprises, source_texts)
    payload = reviewed.model_dump(mode="json")
    items = payload.get("items", [])
    if not items and (review.tax_types or review.periods or review.explicitly_overdue is not None):
        items = [_blank_item()]
    if not items:
        return reviewed

    all_tax_types = list(dict.fromkeys([
        *[tax for item in items for tax in item.get("tax_types", [])],
        *review.tax_types,
    ]))
    for item in items:
        item["tax_types"] = all_tax_types
        item["tax_type_raw"] = list(dict.fromkeys([*item.get("tax_type_raw", []), *review.tax_types]))

    if review.periods:
        existing = items[0].get("periods", [])
        combined = [*existing, *review.periods]
        items[0]["periods"] = list({
            _period_key(period): period for period in combined if isinstance(period, dict)
        }.values())
    if review.explicitly_overdue is not None:
        items[0]["explicitly_overdue"] = review.explicitly_overdue
    payload["items"] = items
    payload["has_relevant_information"] = True
    return ExtractionResult.model_validate(payload)


def _locate_evidence(
    name: str,
    source: str,
    quote: str,
    source_texts: Mapping[str, str | None],
) -> tuple[str | None, str | None, bool]:
    ordered_sources = [source] if source in SOURCE_PRIORITY else []
    ordered_sources.extend(value for value in SOURCE_PRIORITY if value not in ordered_sources)
    for candidate_source in ordered_sources:
        text = source_texts.get(candidate_source) or ""
        if quote and _normalized(quote) in _normalized(text):
            return candidate_source, quote, _normalized(name) in _normalized(text)
        if _normalized(name) in _normalized(text):
            return candidate_source, name, True
    return None, None, False


def _prefer_complete_names(decisions: list[EnterpriseDecision]) -> list[EnterpriseDecision]:
    deduped: list[EnterpriseDecision] = []
    for decision in decisions:
        if any(decision.name == existing.name for existing in deduped):
            continue
        shorter_index = next(
            (index for index, existing in enumerate(deduped) if existing.name in decision.name),
            None,
        )
        if shorter_index is not None:
            deduped[shorter_index] = decision
        elif not any(decision.name in existing.name for existing in deduped):
            deduped.append(decision)
    return sorted(deduped, key=lambda value: SOURCE_PRIORITY.index(value.source))


def _best_decision_index(
    current_name: Any,
    decisions: list[EnterpriseDecision],
    used: set[int],
) -> int | None:
    current = str(current_name or "")
    for index, decision in enumerate(decisions):
        if index not in used and current and (current in decision.name or decision.name in current):
            return index
    return next((index for index in range(len(decisions)) if index not in used), None)


def _set_enterprise(item: dict[str, Any], decision: EnterpriseDecision) -> None:
    item["enterprise_name"] = decision.name
    item["enterprise_evidence"] = [{"source": decision.source, "quote": decision.quote}]


def _enterprise_only_item(decision: EnterpriseDecision) -> dict[str, Any]:
    return {
        "enterprise_name": decision.name,
        "enterprise_evidence": [{"source": decision.source, "quote": decision.quote}],
        "tax_types": [],
        "tax_type_raw": [],
        "tax_evidence": [],
        "periods": [],
        "amounts": [],
        "explicitly_overdue": None,
        "overdue_evidence": [],
        "relationship_note": "企业名称经专用模型复核",
        "needs_review": False,
        "review_reasons": [],
    }


def _review_tax_types(data: Any, source_texts: Mapping[str, str | None]) -> list[str]:
    raw_values: list[Any] = []
    if isinstance(data, dict):
        if isinstance(data.get("tax_types"), list):
            raw_values.extend(data["tax_types"])
        for item in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("tax_types"), list):
                raw_values.extend(item["tax_types"])
    aliases = {
        "个税": "个人所得税",
        "城建税": "城市建设维护税",
        "社会保险费": "社保费",
        "社保": "社保费",
        "出口退税": "进出口税",
    }
    values = [aliases.get(str(value), str(value)) for value in raw_values]
    values.extend(find_explicit_tax_types(dict(source_texts)))
    normalized = [value if value in STANDARD_TAX_TYPES else "其他" for value in values if value]
    normalized = list(dict.fromkeys(normalized))
    if len(normalized) > 1 and "未识别" in normalized:
        normalized.remove("未识别")
    return normalized


def _review_periods(data: Any) -> list[dict[str, Any]]:
    raw_periods: list[Any] = []
    if isinstance(data, dict):
        if isinstance(data.get("periods"), list):
            raw_periods.extend(data["periods"])
        for item in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("periods"), list):
                raw_periods.extend(item["periods"])
    periods: list[dict[str, Any]] = []
    for period in raw_periods:
        if isinstance(period, str):
            period = {"raw_text": period}
        if not isinstance(period, dict):
            continue
        raw_text = str(period.get("raw_text") or period.get("normalized_text") or period.get("value") or "").strip()
        if not raw_text:
            continue
        period_type = str(period.get("period_type") or "unparsed")
        if period_type not in {"single_month", "month_range", "quarter", "year", "relative", "unparsed"}:
            period_type = "unparsed"
        periods.append({
            "raw_text": raw_text,
            "period_type": period_type,
            "start_year": _optional_int(period.get("start_year")),
            "start_month": _optional_int(period.get("start_month")),
            "start_day": _optional_int(period.get("start_day")),
            "end_year": _optional_int(period.get("end_year")),
            "end_month": _optional_int(period.get("end_month")),
            "end_day": _optional_int(period.get("end_day")),
            "relative_expression": period.get("relative_expression"),
            "evidence": [],
        })
    return periods


def _review_overdue(data: Any) -> bool | None:
    if not isinstance(data, dict):
        return None
    value = data.get("explicitly_overdue")
    if isinstance(value, bool):
        return value
    values = [
        item.get("explicitly_overdue")
        for item in data.get("items", []) if isinstance(item, dict)
    ] if isinstance(data.get("items"), list) else []
    if True in values:
        return True
    if values and all(value is False for value in values):
        return False
    return None


def _blank_item() -> dict[str, Any]:
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
        "relationship_note": "信息经精度复核补充",
        "needs_review": False,
        "review_reasons": [],
    }


def _period_key(period: dict[str, Any]) -> str:
    return "|".join(str(period.get(key)) for key in (
        "start_year", "start_month", "start_day", "end_year", "end_month", "end_day", "raw_text"
    ))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_evidence_value(item: dict[str, Any], key: str) -> Any:
    evidence = item.get("enterprise_evidence")
    if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
        return evidence[0].get(key)
    return None


def _normalized(value: str) -> str:
    return "".join(value.split())
