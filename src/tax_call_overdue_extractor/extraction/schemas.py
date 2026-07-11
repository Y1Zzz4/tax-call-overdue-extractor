"""大模型结构化提取结果的 Pydantic Schema。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


STANDARD_TAX_TYPES: tuple[str, ...] = (
    "增值税",
    "个人所得税",
    "企业所得税",
    "消费税",
    "印花税",
    "城镇土地使用税",
    "车辆购置税",
    "城市建设维护税",
    "契税",
    "房产税",
    "车船税",
    "耕地占用税",
    "资源税",
    "环境保护税",
    "烟叶税",
    "进出口税",
    "残保金",
    "非税收入",
    "其他",
    "未识别",
)


class SourceName(StrEnum):
    voice_text = "电话录音转文本内容"
    business_content = "业务内容"
    reply_content = "答复内容"


class PeriodType(StrEnum):
    single_month = "single_month"
    month_range = "month_range"
    quarter = "quarter"
    year = "year"
    relative = "relative"
    unparsed = "unparsed"


class AmountRole(StrEnum):
    tax = "tax"
    late_fee = "late_fee"
    penalty = "penalty"
    total = "total"
    other = "other"
    unknown = "unknown"


class ConflictField(StrEnum):
    enterprise_name = "enterprise_name"
    tax_types = "tax_types"
    period = "period"
    amount = "amount"
    overdue = "overdue"
    relationship = "relationship"


class StrictSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Evidence(StrictSchemaModel):
    source: Literal[
        "电话录音转文本内容",
        "业务内容",
        "答复内容",
    ]
    quote: str = Field(min_length=1, max_length=200)

    @field_validator("quote")
    @classmethod
    def quote_must_not_be_blank(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("证据 quote 不能为空白字符串")
        return value


class PeriodMention(StrictSchemaModel):
    raw_text: str = Field(min_length=1)
    period_type: PeriodType
    start_year: int | None = None
    start_month: int | None = Field(default=None, ge=1, le=12)
    end_year: int | None = None
    end_month: int | None = Field(default=None, ge=1, le=12)
    relative_expression: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class AmountMention(StrictSchemaModel):
    raw_text: str = Field(min_length=1)
    role: AmountRole
    is_calculated: bool
    calculation_note: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class ExtractionItem(StrictSchemaModel):
    enterprise_name: str | None = None
    enterprise_evidence: list[Evidence] = Field(default_factory=list)
    tax_types: list[str] = Field(default_factory=list)
    tax_type_raw: list[str] = Field(default_factory=list)
    tax_evidence: list[Evidence] = Field(default_factory=list)
    periods: list[PeriodMention] = Field(default_factory=list)
    amounts: list[AmountMention] = Field(default_factory=list)
    explicitly_overdue: bool | None = None
    overdue_evidence: list[Evidence] = Field(default_factory=list)
    relationship_note: str | None = None
    needs_review: bool
    review_reasons: list[str] = Field(default_factory=list)

    @field_validator("tax_types")
    @classmethod
    def validate_and_dedupe_tax_types(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            if value not in STANDARD_TAX_TYPES:
                raise ValueError(f"非法税种名称: {value}")
            if value not in seen:
                seen.add(value)
                deduped.append(value)
        return deduped

    @model_validator(mode="after")
    def item_must_contain_target_information(self) -> "ExtractionItem":
        has_target = any(
            [
                self.enterprise_name is not None,
                bool(self.tax_types),
                bool(self.tax_type_raw),
                bool(self.periods),
                bool(self.amounts),
                self.explicitly_overdue is not None,
                bool(self.overdue_evidence),
            ]
        )
        if not has_target:
            raise ValueError("ExtractionItem 至少需要包含一个目标字段")
        return self


class ConflictClaim(StrictSchemaModel):
    source: SourceName
    value: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=200)

    @field_validator("value", "quote")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("冲突 claim 的文本不能为空白字符串")
        return value


class Conflict(StrictSchemaModel):
    field: ConflictField
    description: str = Field(min_length=1)
    claims: list[ConflictClaim] = Field(min_length=2)


class ExtractionResult(StrictSchemaModel):
    schema_version: Literal["1.0"]
    has_relevant_information: bool
    items: list[ExtractionItem] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    needs_review: bool
    review_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def no_items_when_irrelevant(self) -> "ExtractionResult":
        if not self.has_relevant_information and self.items:
            raise ValueError("has_relevant_information 为 false 时不应包含 items")
        if self.has_relevant_information and not self.items:
            raise ValueError("has_relevant_information 为 true 时必须包含至少一个 item")
        return self


def no_text_result() -> ExtractionResult:
    """三个允许字段均无可分析文本时的本地结果。"""

    return ExtractionResult(
        schema_version="1.0",
        has_relevant_information=False,
        items=[],
        conflicts=[],
        needs_review=False,
        review_reasons=["无可分析文本"],
    )
