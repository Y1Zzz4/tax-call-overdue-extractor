"""本地字段标准化、所属期换算和逾期判断。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from .batch_models import NormalizedItem, NormalizedPeriod, ReferenceMonth
from .schemas import Conflict, ExtractionItem, ExtractionResult, PeriodMention, STANDARD_TAX_TYPES


PRONOUN_ENTERPRISE_NAMES = {"我们公司", "我司", "本公司", "我们单位", "我公司", "本单位"}
GENERIC_ENTERPRISE_FRAGMENTS = {
    "这个公司",
    "那个公司",
    "一家公司",
    "家公司",
    "我们公司",
    "我公司",
    "本公司",
    "所在公司",
    "成立的公司",
}
CHINESE_SEMICOLON = "；"


def normalize_extraction_result(
    result: ExtractionResult,
    *,
    reference_month: ReferenceMonth | None,
) -> list[NormalizedItem]:
    """把模型结构化结果转换为最终 Excel 可写入字段。"""

    if not result.has_relevant_information:
        return []

    normalized_items = [
        normalize_item(item, reference_month=reference_month, model_conflicts=result.conflicts)
        for item in result.items
    ]
    return normalized_items


def normalize_item(
    item: ExtractionItem,
    *,
    reference_month: ReferenceMonth | None,
    model_conflicts: list[Conflict] | None = None,
) -> NormalizedItem:
    enterprise_name = _normalize_enterprise_name(item.enterprise_name)
    tax_types = _dedupe([tax_type for tax_type in item.tax_types if tax_type in STANDARD_TAX_TYPES])
    periods, period_notes = _normalize_periods(item.periods, reference_month)
    amounts, amount_notes = _normalize_amounts(item)
    conflicts = list(model_conflicts or [])
    overdue_text, overdue_notes, overdue_conflicts = _determine_overdue(
        explicitly_overdue=item.explicitly_overdue,
        periods=periods,
        reference_month=reference_month,
    )
    conflicts.extend(overdue_conflicts)

    notes = _dedupe(
        [
            *item.review_reasons,
            *period_notes,
            *amount_notes,
            *overdue_notes,
        ]
    )
    needs_review = item.needs_review or bool(conflicts)
    missing = []
    if enterprise_name is None:
        missing.append("企业名称")
    if not tax_types:
        missing.append("税种")
    if not periods:
        missing.append("所属期")
    if not amounts:
        missing.append("金额")
    explanation_parts = [item.relationship_note or ""]
    if missing:
        explanation_parts.append(f"未识别/未提及：{'、'.join(missing)}")
    explanation_parts.extend(notes)

    return NormalizedItem(
        enterprise_name=enterprise_name,
        tax_types_text=_join_or_none(tax_types),
        periods_text=_join_or_none(_dedupe([period.text for period in periods])),
        amounts_text=_join_or_none(amounts),
        overdue_text=overdue_text,
        explanation=CHINESE_SEMICOLON.join(_dedupe([part for part in explanation_parts if part])) or "已完成提取",
        needs_review=needs_review,
        review_reasons=notes,
        conflicts=conflicts,
    )


def parse_reference_month(registration_date: object, month_value: object) -> ReferenceMonth | None:
    """按“登记日期年份 + 月份列优先”的规则解析本地参考年月。"""

    year = _year_from_value(registration_date)
    month = _month_from_value(month_value)
    if month is None:
        month = _month_from_value(registration_date)
    if year is None or month is None:
        return None
    return ReferenceMonth(year=year, month=month)


def _normalize_enterprise_name(value: str | None) -> str | None:
    return normalize_enterprise_name_candidate(value)


def normalize_enterprise_name_candidate(value: str | None) -> str | None:
    """拒绝代称和口语短语，只保留具有实体名称形态的候选。"""

    if value is None:
        return None
    text = value.strip(" \t\r\n，,。；;：:")
    if text == "" or text in PRONOUN_ENTERPRISE_NAMES:
        return None
    if any(fragment in text for fragment in GENERIC_ENTERPRISE_FRAGMENTS):
        return None
    if text.startswith(("因为", "所以", "然后", "现在", "目前", "我们", "这个", "那个")):
        return None
    return text


def _normalize_periods(
    periods: list[PeriodMention],
    reference_month: ReferenceMonth | None,
) -> tuple[list[NormalizedPeriod], list[str]]:
    normalized: list[NormalizedPeriod] = []
    reviews: list[str] = []
    for period in periods:
        converted = _normalize_period(period, reference_month)
        normalized.append(converted)
        if not converted.reliable:
            reviews.append("unresolved_period")
    return normalized, _dedupe(reviews)


def _normalize_period(period: PeriodMention, reference_month: ReferenceMonth | None) -> NormalizedPeriod:
    direct = _from_structured_period(period)
    if direct is not None:
        return direct

    raw_text = period.relative_expression or period.raw_text
    if reference_month is not None:
        relative = _from_relative_text(raw_text, reference_month)
        if relative is not None:
            return relative

    parsed = _from_raw_absolute_text(period.raw_text)
    if parsed is not None:
        return parsed

    return NormalizedPeriod(
        text=period.raw_text,
        start_year=None,
        start_month=None,
        end_year=None,
        end_month=None,
        granularity=str(period.period_type),
        reliable=False,
        raw_text=period.raw_text,
    )


def _from_structured_period(period: PeriodMention) -> NormalizedPeriod | None:
    if period.period_type == "year" and period.start_year:
        return _period(period.start_year, 1, period.start_year, 12, "year", period.raw_text)
    if all([period.start_year, period.start_month, period.end_year, period.end_month]):
        granularity = "month" if period.start_year == period.end_year and period.start_month == period.end_month else str(period.period_type)
        return _period(
            int(period.start_year),
            int(period.start_month),
            int(period.end_year),
            int(period.end_month),
            granularity,
            period.raw_text,
        )
    return None


def _from_raw_absolute_text(raw_text: str) -> NormalizedPeriod | None:
    text = raw_text.strip()
    year_match = re.fullmatch(r"(\d{4})年", text)
    if year_match:
        year = int(year_match.group(1))
        return _period(year, 1, year, 12, "year", raw_text)

    month_match = re.search(r"(\d{4})年\s*(\d{1,2})月", text)
    if month_match:
        year = int(month_match.group(1))
        month = int(month_match.group(2))
        if 1 <= month <= 12:
            return _period(year, month, year, month, "month", raw_text)

    range_match = re.search(r"(\d{4})年\s*(\d{1,2})月.*?(\d{4})年\s*(\d{1,2})月", text)
    if range_match:
        sy, sm, ey, em = (int(value) for value in range_match.groups())
        if 1 <= sm <= 12 and 1 <= em <= 12:
            return _period(sy, sm, ey, em, "month_range", raw_text)

    quarter_match = re.search(r"(\d{4})年\s*第?([一二三四1234])季度", text)
    if quarter_match:
        year = int(quarter_match.group(1))
        quarter = _quarter_number(quarter_match.group(2))
        if quarter is not None:
            start, end = _quarter_months(quarter)
            return _period(year, start, year, end, "quarter", raw_text)
    return None


def _from_relative_text(raw_text: str, reference_month: ReferenceMonth) -> NormalizedPeriod | None:
    text = raw_text.strip()
    if text == "上个月":
        year, month = _add_months(reference_month.year, reference_month.month, -1)
        return _period(year, month, year, month, "relative", raw_text)
    if text == "本月":
        return _period(reference_month.year, reference_month.month, reference_month.year, reference_month.month, "relative", raw_text)
    if text == "今年3月":
        return _period(reference_month.year, 3, reference_month.year, 3, "relative", raw_text)
    this_year_month = re.fullmatch(r"今年\s*(\d{1,2})月", text)
    if this_year_month:
        month = int(this_year_month.group(1))
        if 1 <= month <= 12:
            return _period(reference_month.year, month, reference_month.year, month, "relative", raw_text)
    if text == "上季度":
        quarter = ((reference_month.month - 1) // 3) + 1
        year = reference_month.year
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
        start, end = _quarter_months(quarter)
        return _period(year, start, year, end, "relative", raw_text)
    if text == "本季度":
        quarter = ((reference_month.month - 1) // 3) + 1
        start, end = _quarter_months(quarter)
        return _period(reference_month.year, start, reference_month.year, end, "relative", raw_text)
    last_year_quarter = re.fullmatch(r"去年第?([一二三四1234])季度", text)
    if last_year_quarter:
        quarter = _quarter_number(last_year_quarter.group(1))
        if quarter is not None:
            start, end = _quarter_months(quarter)
            return _period(reference_month.year - 1, start, reference_month.year - 1, end, "relative", raw_text)
    return None


def _normalize_amounts(item: ExtractionItem) -> tuple[list[str], list[str]]:
    amounts: list[str] = []
    reviews: list[str] = []
    for amount in item.amounts:
        amounts.append(amount.raw_text)
    return _dedupe(amounts), _dedupe(reviews)


def _determine_overdue(
    *,
    explicitly_overdue: bool | None,
    periods: list[NormalizedPeriod],
    reference_month: ReferenceMonth | None,
) -> tuple[str | None, list[str], list[Conflict]]:
    reviews: list[str] = []
    conflicts: list[Conflict] = []
    period_status = _period_overdue_status(periods, reference_month)

    if explicitly_overdue is True:
        return "已逾期", reviews, conflicts

    if explicitly_overdue is False:
        return "未逾期", reviews, conflicts

    if period_status == "overdue":
        return "已逾期", reviews, conflicts
    if period_status == "mixed":
        reviews.append("ambiguous_relationship")
    return None, reviews, conflicts


def _period_overdue_status(periods: list[NormalizedPeriod], reference_month: ReferenceMonth | None) -> str:
    if not periods or reference_month is None:
        return "unknown"
    comparable = [period for period in periods if period.reliable]
    if not comparable:
        return "unknown"
    statuses: list[str] = []
    for period in comparable:
        if None in {period.start_year, period.start_month, period.end_year, period.end_month}:
            statuses.append("unknown")
            continue
        end_year = int(period.end_year)
        end_month = int(period.end_month)
        if end_year <= 2025:
            statuses.append("overdue")
        elif end_year == 2026 and end_month < reference_month.month:
            statuses.append("overdue")
        else:
            statuses.append("unknown")
    if "overdue" in statuses:
        return "overdue"
    return "unknown"


def _period(start_year: int, start_month: int, end_year: int, end_month: int, granularity: str, raw_text: str) -> NormalizedPeriod:
    return NormalizedPeriod(
        text=f"{start_year}年{start_month}月到{end_year}年{end_month}月",
        start_year=start_year,
        start_month=start_month,
        end_year=end_year,
        end_month=end_month,
        granularity=granularity,
        reliable=True,
        raw_text=raw_text,
    )


def _quarter_number(value: str) -> int | None:
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
    return mapping.get(value)


def _quarter_months(quarter: int) -> tuple[int, int]:
    return (quarter - 1) * 3 + 1, quarter * 3


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + month - 1 + delta
    return total // 12, total % 12 + 1


def _year_from_value(value: object) -> int | None:
    if isinstance(value, datetime):
        return value.year
    if value is None:
        return None
    text = str(value)
    match = re.search(r"(\d{4})", text)
    return int(match.group(1)) if match else None


def _month_from_value(value: object) -> int | None:
    if isinstance(value, datetime):
        return value.month
    if value is None:
        return None
    text = str(value)
    if text.lstrip().startswith("="):
        return None
    iso_date = re.match(r"^\s*\d{4}[-/]([01]?\d)(?:[-/]\d{1,2})?\s*$", text)
    if iso_date:
        month = int(iso_date.group(1))
        return month if 1 <= month <= 12 else None
    chinese_date = re.search(r"\d{4}年\s*(\d{1,2})月", text)
    if chinese_date:
        month = int(chinese_date.group(1))
        return month if 1 <= month <= 12 else None
    match = re.search(r"(?:^|[^\d])(\d{1,2})(?:月|$)", text)
    if not match:
        parts = re.findall(r"\d{1,2}", text)
        match_value = parts[-1] if parts else None
    else:
        match_value = match.group(1)
    if match_value is None:
        return None
    month = int(match_value)
    return month if 1 <= month <= 12 else None


def _join_or_none(values: Iterable[str]) -> str | None:
    deduped = _dedupe([value for value in values if value])
    return CHINESE_SEMICOLON.join(deduped) if deduped else None


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
