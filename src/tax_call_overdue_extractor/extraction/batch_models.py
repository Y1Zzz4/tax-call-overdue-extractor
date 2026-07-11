"""批处理内部数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tax_call_overdue_extractor.llm.request_builder import ModelInput

from .schemas import Conflict, ExtractionResult


BatchStatus = Literal[
    "pending",
    "processing",
    "success",
    "conflict",
    "needs_review",
    "skipped_no_text",
    "input_too_long",
    "api_error",
    "validation_error",
]


@dataclass(frozen=True)
class ReferenceMonth:
    year: int
    month: int


@dataclass(frozen=True)
class BatchRecord:
    original_row_number: int
    sequence_number: object
    business_id: object
    model_input: ModelInput
    input_hash: str
    reference_month: ReferenceMonth | None


@dataclass(frozen=True)
class TextStats:
    total_chars: int
    min_chars: int
    max_chars: int
    avg_chars: float


@dataclass(frozen=True)
class BatchPlan:
    input_path: Path
    output_path: Path
    conflicts_output_path: Path
    review_output_path: Path
    state_db_path: Path
    sheet_name: str
    original_data_rows: int
    eligible_rows: int
    planned_records: int
    reusable_records: int
    estimated_api_calls: int
    input_too_long_records: int
    text_stats: TextStats
    model_name: str
    concurrency: int


@dataclass(frozen=True)
class NormalizedPeriod:
    text: str
    start_year: int | None
    start_month: int | None
    end_year: int | None
    end_month: int | None
    granularity: str
    reliable: bool
    raw_text: str


@dataclass(frozen=True)
class NormalizedItem:
    enterprise_name: str | None
    tax_types_text: str | None
    periods_text: str | None
    amounts_text: str | None
    overdue_text: str | None
    needs_review: bool
    review_reasons: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)


@dataclass(frozen=True)
class RowBatchOutcome:
    record: BatchRecord
    status: BatchStatus
    extraction_result: ExtractionResult | None
    normalized_items: list[NormalizedItem]
    structured_result_path: Path | None
    raw_response_path: Path | None
    error_type: str | None = None
    error_message_sanitized: str | None = None


@dataclass(frozen=True)
class BatchRunSummary:
    plan: BatchPlan
    success_count: int
    conflict_count: int
    needs_review_count: int
    skipped_count: int
    input_too_long_count: int
    api_error_count: int
    validation_error_count: int
    output_path: Path
    conflicts_output_path: Path
    review_output_path: Path
    state_db_path: Path
