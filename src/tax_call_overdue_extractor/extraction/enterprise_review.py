"""企业名称专用复核：第二次模型调用只负责这个高精度字段。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .normalization import normalize_enterprise_name_candidate
from .parser import find_high_confidence_enterprise, load_json_object
from .schemas import ExtractionResult


SOURCE_PRIORITY = ("业务内容", "答复内容", "电话录音转文本内容")


@dataclass(frozen=True)
class EnterpriseDecision:
    name: str
    source: str
    quote: str
    confidence: str


def load_enterprise_review_prompt() -> str:
    path = Path(__file__).resolve().parents[1] / "prompt_templates" / "enterprise_review.txt"
    return path.read_text(encoding="utf-8")


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
        for item in items:
            if isinstance(item, dict):
                item["enterprise_name"] = None
                item["enterprise_evidence"] = []
        payload["items"] = [item for item in items if _item_still_has_content(item)]
        payload["has_relevant_information"] = bool(payload["items"])
        return ExtractionResult.model_validate(payload)

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


def _item_still_has_content(item: dict[str, Any]) -> bool:
    return any(
        (
            item.get("tax_types"),
            item.get("tax_type_raw"),
            item.get("periods"),
            item.get("amounts"),
            item.get("explicitly_overdue") is not None,
            item.get("overdue_evidence"),
        )
    )


def _first_evidence_value(item: dict[str, Any], key: str) -> Any:
    evidence = item.get("enterprise_evidence")
    if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
        return evidence[0].get(key)
    return None


def _normalized(value: str) -> str:
    return "".join(value.split())
