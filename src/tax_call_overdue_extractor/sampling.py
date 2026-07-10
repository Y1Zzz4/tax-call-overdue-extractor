"""抽样规则与抽样工作流。"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TYPE_CHECKING

from .exceptions import SamplingError

if TYPE_CHECKING:
    from .config import ProjectSettings


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SamplingResult:
    """一次抽样运行的非敏感结果摘要。"""

    input_path: Path
    output_path: Path
    sheet_name: str
    candidate_count: int
    sample_size: int
    seed: int | None
    selected_rows: tuple[int, ...]
    duration_seconds: float


def is_valid_call_text(value: object) -> bool:
    """判断“电话录音转文本内容”是否满足本轮抽样有效条件。"""

    if value is None:
        return False
    text = str(value).strip()
    return text != "" and text.upper() != "#N/A"


def select_sample_rows(
    candidate_rows: Sequence[int],
    sample_size: int,
    seed: int | None = None,
) -> tuple[int, ...]:
    """从候选 Excel 行号中无放回抽样，并按原始行号排序。"""

    if sample_size <= 0:
        raise SamplingError("抽样数量必须是正整数")
    if len(candidate_rows) < sample_size:
        raise SamplingError(
            f"有效数据不足抽样数量: 候选 {len(candidate_rows)} 行，要求 {sample_size} 行"
        )

    rng = random.Random(seed) if seed is not None else random
    sampled = rng.sample(list(candidate_rows), sample_size)
    return tuple(sorted(sampled))


def sample_excel_file(
    *,
    settings: ProjectSettings,
    input_path: str | Path | None = None,
    output_path: str | Path | None = None,
    sample_size: int | None = None,
    seed: int | None = None,
    sheet_name: str | None = None,
    overwrite: bool = False,
) -> SamplingResult:
    """执行完整 Excel 抽样：定位输入、抽取行号、生成并验证输出文件。"""

    from .excel_io import (
        build_default_sample_output_path,
        create_sample_workbook,
        inspect_source_workbook,
        resolve_input_file,
    )

    started = time.perf_counter()
    effective_sample_size = sample_size or settings.sampling.default_sample_size
    effective_seed = seed if seed is not None else settings.sampling.default_seed
    effective_sheet_name = sheet_name if sheet_name is not None else settings.excel.sheet_name

    source_path = resolve_input_file(input_path, settings.paths.input_dir)
    destination_path = (
        Path(output_path)
        if output_path is not None
        else build_default_sample_output_path(
            source_path,
            settings.paths.samples_dir,
            effective_sample_size,
        )
    )

    source_info = inspect_source_workbook(
        source_path,
        sheet_name=effective_sheet_name,
        use_active_sheet=settings.excel.use_active_sheet_when_sheet_not_set,
        header_row=settings.excel.header_row,
    )
    selected_rows = select_sample_rows(
        source_info.candidate_rows,
        effective_sample_size,
        effective_seed,
    )

    create_sample_workbook(
        input_path=source_path,
        output_path=destination_path,
        selected_rows=selected_rows,
        sheet_name=source_info.sheet_name,
        header_row=settings.excel.header_row,
        use_active_sheet=settings.excel.use_active_sheet_when_sheet_not_set,
        overwrite=overwrite,
    )

    duration = time.perf_counter() - started
    result = SamplingResult(
        input_path=source_path,
        output_path=destination_path,
        sheet_name=source_info.sheet_name,
        candidate_count=len(source_info.candidate_rows),
        sample_size=effective_sample_size,
        seed=effective_seed,
        selected_rows=selected_rows,
        duration_seconds=duration,
    )

    LOGGER.info(
        "抽样完成 input=%s output=%s sheet=%s candidates=%s sample_size=%s seed=%s duration=%.3fs validation=passed",
        result.input_path,
        result.output_path,
        result.sheet_name,
        result.candidate_count,
        result.sample_size,
        result.seed,
        result.duration_seconds,
    )
    return result
