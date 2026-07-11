"""模型响应解析与 Schema 校验。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from tax_call_overdue_extractor.exceptions import ResponseParseError

from .normalization import normalize_enterprise_name_candidate
from .schemas import ExtractionResult, STANDARD_TAX_TYPES


ALLOWED_SOURCE_NAMES = (
    "电话录音转文本内容",
    "业务内容",
    "答复内容",
)
SOURCE_PRIORITY = ("业务内容", "答复内容", "电话录音转文本内容")
EVIDENCE_FIELDS = ("enterprise_evidence", "tax_evidence", "overdue_evidence")
STRING_EVIDENCE_REASON = "模型返回字符串形式证据，但无法在三个允许输入字段中唯一定位原文来源"
INVALID_EVIDENCE_REASON = "模型返回的证据对象无法在指定输入字段中定位原文"
PROCESS_WORDS = ("退个税", "申请", "流程", "审核", "审批", "工单", "任务", "作废", "超时")
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

    try:
        data: Any = load_json_object(raw_response)
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
    local_enterprise = find_high_confidence_enterprise(texts)
    if local_enterprise is not None:
        normalized["conflicts"] = []
    items = normalized.get("items")
    if not isinstance(items, list):
        return normalized
    explicit_tax_types = find_explicit_tax_types(texts)

    kept_items: list[Any] = []
    top_notes = _reason_list(normalized.get("review_reasons"))
    for item in items:
        if not isinstance(item, dict):
            kept_items.append(item)
            continue
        notes: list[str] = _reason_list(item.get("review_reasons"))
        if local_enterprise is not None:
            enterprise_name, source, quote = local_enterprise
            current_name = normalize_enterprise_name_candidate(str(item.get("enterprise_name") or ""))
            should_replace = (
                current_name is None
                or len(items) == 1
                or current_name in enterprise_name
                or enterprise_name in current_name
            )
            if should_replace and current_name != enterprise_name:
                notes.append(f"按{source}补充企业名称")
            if should_replace:
                item["enterprise_name"] = enterprise_name
                item["enterprise_evidence"] = [{"source": source, "quote": quote}]
        elif item.get("enterprise_name"):
            cleaned_enterprise = normalize_enterprise_name_candidate(str(item["enterprise_name"]))
            if cleaned_enterprise is None:
                notes.append("已忽略口语化企业代称")
                item["enterprise_name"] = None
                item["enterprise_evidence"] = []
            else:
                item["enterprise_name"] = cleaned_enterprise
        if explicit_tax_types:
            item["tax_types"] = list(dict.fromkeys([
                *_string_list(item.get("tax_types")),
                *explicit_tax_types,
            ]))
            item["tax_type_raw"] = list(dict.fromkeys([
                *_string_list(item.get("tax_type_raw")),
                *explicit_tax_types,
            ]))
        for field in EVIDENCE_FIELDS:
            item[field] = _repair_evidence_list(item.get(field, []), texts, notes)
        for child_field in ("periods", "amounts"):
            children = item.get(child_field)
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        child["evidence"] = _repair_evidence_list(
                            child.get("evidence", []), texts, notes
                        )

        _apply_source_priority(item, texts, notes)
        _correct_explicit_overdue(item, notes)
        _remove_procedural_periods(item, texts, notes)
        item["review_reasons"] = _dedupe_reasons(notes)
        item["needs_review"] = False

        if _is_process_only_item(item):
            if item.get("enterprise_name"):
                item.update(
                    tax_types=[],
                    tax_type_raw=[],
                    tax_evidence=[],
                    periods=[],
                    amounts=[],
                    explicitly_overdue=None,
                    overdue_evidence=[],
                    relationship_note="仅识别到企业名称，未发现明确税款或申报逾期信息",
                )
                kept_items.append(item)
        else:
            if _item_has_content(item):
                kept_items.append(item)

    if not kept_items and local_enterprise is not None:
        enterprise_name, source, quote = local_enterprise
        kept_items.append(_enterprise_only_item(enterprise_name, source, quote))

    kept_items = _dedupe_items(kept_items)
    normalized["items"] = kept_items
    normalized["has_relevant_information"] = bool(kept_items)

    clear_conflict = bool(normalized.get("conflicts"))
    normalized["needs_review"] = clear_conflict
    normalized["review_reasons"] = _dedupe_reasons(top_notes) if clear_conflict else []
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
                repaired.append({"source": source, "quote": quote[:200]})
            else:
                matching_sources = _matching_sources(source_texts, quote) if isinstance(quote, str) else []
                if matching_sources:
                    repaired.append({"source": matching_sources[0], "quote": quote[:200]})
                    review_reasons.append("证据来源已按原文自动修正")
                else:
                    review_reasons.append(INVALID_EVIDENCE_REASON)
            continue
        if isinstance(evidence, str) and evidence.strip():
            matching_sources = _matching_sources(source_texts, evidence)
            if matching_sources:
                repaired.append({"source": matching_sources[0], "quote": evidence[:200]})
            else:
                review_reasons.append(STRING_EVIDENCE_REASON)
            continue
        review_reasons.append(INVALID_EVIDENCE_REASON)
    return repaired


def _matching_sources(source_texts: dict[str, str | None], quote: str) -> list[str]:
    return [source for source in SOURCE_PRIORITY if _contains_quote(source_texts.get(source), quote)]


ENTERPRISE_SUFFIX = (
    r"(?:股份有限公司|有限责任公司|有限公司|分公司|公司|个人独资企业|合伙企业|合作社|"
    r"事务所|经营部|商行|中心|工作室|服务部|门市部|厂|店)"
)
ENTERPRISE_AFTER_CREDIT_CODE = re.compile(
    rf"[0-9A-Z]{{18}}\s*[-—:：]?\s*([^\s，,；;。！？?\n]{{2,80}}{ENTERPRISE_SUFFIX})"
)
LABELED_ENTERPRISE = re.compile(
    rf"(?:企业(?:名称)?|公司名称|纳税人)\s*[:：为]\s*([^，,；;。！？?\n]{{2,80}}{ENTERPRISE_SUFFIX})"
)
def find_high_confidence_enterprise(
    source_texts: dict[str, str | None],
) -> tuple[str, str, str] | None:
    """按业务内容、答复内容、电话录音顺序做保守的企业名称兜底。"""

    for source in SOURCE_PRIORITY:
        text = source_texts.get(source) or ""
        for pattern in (ENTERPRISE_AFTER_CREDIT_CODE, LABELED_ENTERPRISE):
            match = pattern.search(text)
            if not match:
                continue
            name = normalize_enterprise_name_candidate(match.group(1).strip(" # -—:："))
            if name is not None:
                return name, source, name
    return None


def find_explicit_tax_types(source_texts: dict[str, str | None]) -> list[str]:
    """补齐原文中明确出现的税种；语义映射仍由模型精度复核负责。"""

    text = " ".join(source_texts.get(source) or "" for source in SOURCE_PRIORITY)
    aliases = (
        ("出口退（免）税", "进出口税"),
        ("出口退免税", "进出口税"),
        ("出口退税", "进出口税"),
        ("社会保险费", "社保费"),
        ("社保", "社保费"),
        ("个税", "个人所得税"),
        ("城建税", "城市建设维护税"),
    )
    found = [mapped for phrase, mapped in aliases if phrase in text]
    found.extend(
        tax_type
        for tax_type in STANDARD_TAX_TYPES
        if tax_type not in {"其他", "未识别"} and tax_type in text
    )
    return list(dict.fromkeys(found))


def _enterprise_only_item(name: str, source: str, quote: str) -> dict[str, Any]:
    return {
        "enterprise_name": name,
        "enterprise_evidence": [{"source": source, "quote": quote}],
        "tax_types": [],
        "tax_type_raw": [],
        "tax_evidence": [],
        "periods": [],
        "amounts": [],
        "explicitly_overdue": None,
        "overdue_evidence": [],
        "relationship_note": "仅识别到企业名称，未发现明确税款或申报逾期信息",
        "needs_review": False,
        "review_reasons": [f"按{source}补充企业名称"],
    }


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(
            {
                "enterprise_name": item.get("enterprise_name"),
                "tax_types": item.get("tax_types"),
                "periods": item.get("periods"),
                "amounts": item.get("amounts"),
                "explicitly_overdue": item.get("explicitly_overdue"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


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


def _apply_source_priority(
    item: dict[str, Any],
    source_texts: dict[str, str | None],
    notes: list[str],
) -> None:
    for source in SOURCE_PRIORITY[:2]:
        text = source_texts.get(source) or ""
        if not text:
            continue
        process_only = any(word in text for word in PROCESS_WORDS) and not any(
            re.search(pattern, text) for pattern in TAX_OVERDUE_PATTERNS
        )
        if process_only:
            continue
        if re.search(r"(?:尚未|并未|没有|未).{0,8}(?:逾期|到期|超过期限)", text):
            if item.get("explicitly_overdue") is not False:
                notes.append(f"按{source}优先，将逾期状态确定为未逾期")
            item["explicitly_overdue"] = False
            return
        if any(re.search(pattern, text) for pattern in TAX_OVERDUE_PATTERNS):
            if item.get("explicitly_overdue") is not True:
                notes.append(f"按{source}优先，将逾期状态确定为已逾期")
            item["explicitly_overdue"] = True
            return


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


def load_json_object(raw_response: str) -> Any:
    """接受纯 JSON、代码块以及 JSON 前后夹有少量说明文字的常见响应。"""

    text = _strip_code_fence(raw_response)
    try:
        return json.loads(text)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise original_error


def _normalize_response_payload(data: Any) -> Any:
    """把模型常见的近义字段统一成一个简单、稳定的内部结构。"""

    if not isinstance(data, dict):
        return data

    raw_items = data.get("items", [])
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    items = [_normalize_item(item) for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
    items = [item for item in items if _item_has_content(item)]
    conflicts = _normalize_conflicts(data.get("conflicts", []))
    has_information = bool(items)
    return {
        "schema_version": "1.0",
        "has_relevant_information": has_information,
        "items": items,
        "conflicts": conflicts,
        "needs_review": bool(conflicts),
        "review_reasons": _reason_list(data.get("review_reasons")),
    }


def _normalize_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item

    fallback_evidence = item.get("evidence", [])
    enterprise = _optional_string(item.get("enterprise_name"))
    raw_tax_types = _string_list(item.get("tax_types"))
    tax_raw = _string_list(item.get("tax_type_raw")) or list(raw_tax_types)
    tax_types = _normalize_tax_types(raw_tax_types, tax_raw)
    normalized = {
        "enterprise_name": enterprise,
        "enterprise_evidence": item.get("enterprise_evidence") or (fallback_evidence if enterprise else []),
        "tax_types": tax_types,
        "tax_type_raw": tax_raw,
        "tax_evidence": item.get("tax_evidence") or [],
        "periods": _normalize_periods(item.get("periods", []), fallback_evidence),
        "amounts": _normalize_amounts(item.get("amounts", []), fallback_evidence),
        "explicitly_overdue": _optional_bool(item.get("explicitly_overdue")),
        "overdue_evidence": item.get("overdue_evidence") or [],
        "relationship_note": _optional_string(item.get("relationship_note")),
        "needs_review": False,
        "review_reasons": _reason_list(item.get("review_reasons")),
    }
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
            raw_text = _first_text(period, "raw_text", "original_text", "period_value", "period_raw", "value", "raw", "mention")
            if raw_text is None:
                raw_text = _period_from_parts(period)
            if raw_text is None:
                continue
            period_type = _period_type(period.get("period_type") or period.get("type"), raw_text)
            normalized_periods.append({
                "raw_text": raw_text,
                "period_type": period_type,
                "start_year": _safe_int(period.get("start_year")),
                "start_month": _safe_month(period.get("start_month")),
                "start_day": _safe_day(period.get("start_day")),
                "end_year": _safe_int(period.get("end_year")),
                "end_month": _safe_month(period.get("end_month")),
                "end_day": _safe_day(period.get("end_day")),
                "relative_expression": _optional_string(period.get("relative_expression")),
                "evidence": period.get("evidence") or fallback_evidence,
            })
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
            raw_text = _first_text(amount, "raw_text", "amount_raw", "amount", "value", "raw")
            if raw_text is None:
                continue
            normalized_amounts.append({
                "raw_text": raw_text,
                "role": _amount_role(amount.get("role") or amount.get("amount_type") or amount.get("type")),
                "is_calculated": bool(amount.get("is_calculated", False)),
                "calculation_note": _optional_string(amount.get("calculation_note")),
                "evidence": amount.get("evidence") or fallback_evidence,
            })
            continue
        normalized_amounts.append(amount)
    return normalized_amounts


def _valid_evidence_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _normalize_tax_types(values: list[str], raw_values: list[str]) -> list[str]:
    aliases = {
        "个税": "个人所得税",
        "城建税": "城市建设维护税",
        "企业所的税": "企业所得税",
        "社保": "社保费",
        "社会保险费": "社保费",
        "出口退税": "进出口税",
    }
    normalized: list[str] = []
    for value in [*values, *raw_values]:
        mapped = aliases.get(value, value)
        if mapped in {
            "增值税", "个人所得税", "企业所得税", "消费税", "印花税", "城镇土地使用税",
            "车辆购置税", "城市建设维护税", "契税", "房产税", "车船税", "耕地占用税",
            "资源税", "环境保护税", "烟叶税", "进出口税", "社保费", "残保金", "非税收入", "其他", "未识别",
        }:
            if mapped not in normalized:
                normalized.append(mapped)
        elif value.strip() and "其他" not in normalized:
            normalized.append("其他")
    if len(normalized) > 1 and "未识别" in normalized:
        normalized.remove("未识别")
    return normalized


def _normalize_conflicts(value: Any) -> list[dict[str, Any]]:
    """只保留答复内容与其他来源之间明确的企业名称冲突。"""

    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for conflict in value:
        if not isinstance(conflict, dict):
            continue
        field = conflict.get("field")
        if field != "enterprise_name":
            continue
        raw_claims = conflict.get("claims") or conflict.get("sources") or conflict.get("values") or []
        claims: list[dict[str, str]] = []
        if isinstance(raw_claims, list):
            for claim in raw_claims:
                if not isinstance(claim, dict) or claim.get("source") not in ALLOWED_SOURCE_NAMES:
                    continue
                text = _optional_string(claim.get("value") or claim.get("quote"))
                quote = _optional_string(claim.get("quote") or claim.get("value"))
                if text and quote:
                    claims.append({"source": claim["source"], "value": text, "quote": quote[:200]})
        sources = {claim["source"] for claim in claims}
        values = {claim["value"] for claim in claims}
        if "答复内容" in sources and len(sources) >= 2 and len(values) >= 2:
            result.append({
                "field": "enterprise_name",
                "description": _optional_string(conflict.get("description") or conflict.get("note")) or "答复内容与其他来源的企业名称明确不一致",
                "claims": claims,
            })
    return result


def _item_has_content(item: dict[str, Any]) -> bool:
    return any(
        (
            item.get("enterprise_name"),
            item.get("tax_types"),
            item.get("tax_type_raw"),
            item.get("periods"),
            item.get("amounts"),
            item.get("explicitly_overdue") is not None,
            item.get("overdue_evidence"),
        )
    )


def _first_text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        text = _optional_string(value.get(key))
        if text:
            return text
    return None


def _optional_string(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _optional_string(item))]


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "是", "已逾期"}:
        return True
    if text in {"false", "no", "0", "否", "未逾期"}:
        return False
    return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_month(value: Any) -> int | None:
    parsed = _safe_int(value)
    return parsed if parsed is not None and 1 <= parsed <= 12 else None


def _safe_day(value: Any) -> int | None:
    parsed = _safe_int(value)
    return parsed if parsed is not None and 1 <= parsed <= 31 else None


def _period_from_parts(period: dict[str, Any]) -> str | None:
    year = _safe_int(period.get("year"))
    month = _safe_month(period.get("month"))
    quarter = _safe_int(period.get("quarter"))
    if year and month:
        return f"{year}年{month}月"
    if year and quarter in {1, 2, 3, 4}:
        return f"{year}年第{quarter}季度"
    return f"{year}年" if year else None


def _period_type(value: Any, raw_text: str) -> str:
    allowed = {"single_month", "month_range", "quarter", "year", "relative", "unparsed"}
    if value in allowed:
        return str(value)
    if re.search(r"季度", raw_text):
        return "quarter"
    if re.search(r"(?:至|到|—|-)", raw_text) and re.search(r"\d{4}年", raw_text):
        return "month_range"
    if re.fullmatch(r"\d{4}年", raw_text):
        return "year"
    if re.search(r"\d{4}年\s*\d{1,2}月", raw_text):
        return "single_month"
    if re.search(r"去年|今年|上个月|本月|上季度|本季度", raw_text):
        return "relative"
    return "unparsed"


def _amount_role(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"tax", "late_fee", "penalty", "total", "other", "unknown"}:
        return text
    if "滞纳" in text:
        return "late_fee"
    if "罚" in text:
        return "penalty"
    if "税" in text:
        return "tax"
    return "unknown"


def _safe_validation_error_summary(exc: ValidationError) -> str:
    """返回不包含 input_value 的校验错误摘要，避免日志泄露原文。"""

    safe_errors: list[str] = []
    for error in exc.errors(include_input=False):
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        error_type = error.get("type", "unknown")
        message = error.get("msg", "validation error")
        safe_errors.append(f"{location}: {error_type}: {message}")
    return "; ".join(safe_errors)
