"""命令行入口。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_PATH, load_settings
from .exceptions import ExtractorError
from .extraction.batch_service import BatchExtractionService, BatchOptions
from .extraction.service import SingleRecordExtractionService
from .logging_config import setup_logging
from .sampling import sample_excel_file


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """创建 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(
        prog="tax-call-overdue-extractor",
        description="税务电话记录逾期信息分析工具",
    )
    subparsers = parser.add_subparsers(dest="command")

    sample_parser = subparsers.add_parser("sample", help="从 Excel 中随机抽取有效电话文本行")
    sample_parser.add_argument("--input", type=Path, help="输入 .xlsx 文件路径")
    sample_parser.add_argument("--output", type=Path, help="输出 .xlsx 文件路径")
    sample_parser.add_argument("--sample-size", type=int, help="抽样数量，默认读取配置")
    sample_parser.add_argument("--seed", type=int, help="随机种子；不传且配置为 null 时使用普通随机抽样")
    sample_parser.add_argument("--sheet", help="工作表名称；不传时按配置使用活动工作表")
    sample_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    sample_parser.add_argument("--log-level", help="日志等级，如 INFO、DEBUG、WARNING")
    sample_parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出文件")
    sample_parser.set_defaults(handler=_handle_sample)

    extract_parser = subparsers.add_parser("extract-one", help="对单条 Excel 记录执行结构化提取")
    extract_parser.add_argument("--input", type=Path, help="输入 .xlsx 文件路径；默认读取 data/samples 下唯一文件")
    extract_parser.add_argument("--row-number", type=int, default=2, help="Excel 工作表实际行号，默认 2")
    extract_parser.add_argument("--output", type=Path, help="输出 JSON 路径，默认 data/state/preview/row_<row>.json")
    extract_parser.add_argument("--sheet", help="工作表名称；不传时按配置使用活动工作表")
    extract_parser.add_argument("--dry-run", action="store_true", help="只构建请求摘要，不调用 API")
    extract_parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出 JSON")
    extract_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    extract_parser.add_argument("--log-level", help="日志等级，如 INFO、DEBUG、WARNING")
    extract_parser.set_defaults(handler=_handle_extract_one)

    batch_parser = subparsers.add_parser("extract-batch", help="批量提取样本、标准化并回填 Excel")
    batch_parser.add_argument("--input", type=Path, help="输入 .xlsx 文件路径；默认读取 data/samples 下唯一文件")
    batch_parser.add_argument("--output", type=Path, help="输出 Excel 路径")
    batch_parser.add_argument("--conflicts-output", type=Path, help="冲突清单 Excel 路径")
    batch_parser.add_argument("--review-output", type=Path, help="人工复核清单 Excel 路径")
    batch_parser.add_argument("--state-db", type=Path, help="SQLite 状态数据库路径")
    batch_parser.add_argument("--sheet", help="工作表名称；不传时按配置使用活动工作表")
    batch_parser.add_argument("--rows", help="指定 Excel 行号，如 2,5,8")
    batch_parser.add_argument("--max-records", type=int, help="最多处理多少条记录")
    batch_parser.add_argument("--concurrency", type=int, help="并发模型调用数，默认读取配置")
    batch_parser.add_argument("--resume", action="store_true", help="复用状态库中输入、提示词、Schema和模型均未变化的结果")
    batch_parser.add_argument("--execute", action="store_true", help="实际调用模型；不传时只预检")
    batch_parser.add_argument("--overwrite", action="store_true", help="允许覆盖最终输出 Excel")
    batch_parser.add_argument("--allow-large-run", action="store_true", help="允许本次计划处理超过100条")
    batch_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    batch_parser.add_argument("--log-level", help="日志等级，如 INFO、DEBUG、WARNING")
    batch_parser.set_defaults(handler=_handle_extract_batch)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主函数，返回进程退出码。"""

    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not args_list:
        parser.print_help()
        return 0

    args = parser.parse_args(args_list)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0

    try:
        return args.handler(args)
    except ExtractorError as exc:
        LOGGER.error("%s", exc)
        return 1


def _handle_sample(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    log_level = args.log_level or settings.logging.level
    setup_logging(log_level, settings.paths.logs_dir / "sampling.log")

    result = sample_excel_file(
        settings=settings,
        input_path=args.input,
        output_path=args.output,
        sample_size=args.sample_size,
        seed=args.seed,
        sheet_name=args.sheet,
        overwrite=args.overwrite,
    )
    LOGGER.info("输出文件: %s", result.output_path)
    return 0


def _handle_extract_one(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    log_level = args.log_level or settings.logging.level
    setup_logging(log_level, settings.paths.logs_dir / "extraction.log")
    service = SingleRecordExtractionService(settings)

    if args.dry_run:
        summary = service.build_dry_run(
            input_path=args.input,
            row_number=args.row_number,
            sheet_name=args.sheet,
        )
        _print_dry_run_summary(summary)
        return 0

    result = service.extract_one(
        input_path=args.input,
        row_number=args.row_number,
        output_path=args.output,
        sheet_name=args.sheet,
        overwrite=args.overwrite,
    )
    print(f"row_number={result.row_number}")
    print(f"status={result.status}")
    print(f"called_api={result.called_api}")
    print(f"item_count={result.item_count}")
    print(f"conflict_count={result.conflict_count}")
    print(f"needs_review={result.needs_review}")
    print(f"output_path={result.output_path}")
    print(f"duration_seconds={result.duration_seconds:.3f}")
    return 0


def _print_dry_run_summary(summary) -> None:
    print(f"dry_run=True")
    print(f"row_number={summary.row_number}")
    for field in summary.field_summaries:
        status = "空" if field.is_empty else "有效"
        print(f"{field.name}：{status}，字符数={field.char_count}")
    print(f"total_char_count={summary.total_chars}")
    print(f"request_sha256={summary.request_sha256}")
    print(f"model={summary.model}")
    print(f"base_url={summary.base_url}")


def _handle_extract_batch(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    log_level = args.log_level or settings.logging.level
    setup_logging(log_level, settings.paths.logs_dir / "batch_extraction.log")
    service = BatchExtractionService(settings)
    options = BatchOptions(
        input_path=args.input,
        output_path=args.output,
        conflicts_output_path=args.conflicts_output,
        review_output_path=args.review_output,
        state_db_path=args.state_db,
        sheet_name=args.sheet,
        rows=_parse_rows(args.rows),
        max_records=args.max_records,
        concurrency=args.concurrency,
        resume=args.resume,
        execute=args.execute,
        overwrite=args.overwrite,
        allow_large_run=args.allow_large_run,
    )
    if not args.execute:
        _print_batch_plan(service.preflight(options))
        return 0

    summary = service.run(options)
    print(f"success_count={summary.success_count}")
    print(f"conflict_count={summary.conflict_count}")
    print(f"needs_review_count={summary.needs_review_count}")
    print(f"skipped_count={summary.skipped_count}")
    print(f"input_too_long_count={summary.input_too_long_count}")
    print(f"api_error_count={summary.api_error_count}")
    print(f"validation_error_count={summary.validation_error_count}")
    print(f"output_path={summary.output_path}")
    print(f"conflicts_output_path={summary.conflicts_output_path}")
    print(f"review_output_path={summary.review_output_path}")
    print(f"state_db_path={summary.state_db_path}")
    return 0


def _parse_rows(value: str | None) -> tuple[int, ...] | None:
    if value is None or value.strip() == "":
        return None
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def _print_batch_plan(plan) -> None:
    print("preflight=True")
    print(f"input_path={plan.input_path}")
    print(f"sheet={plan.sheet_name}")
    print(f"original_data_rows={plan.original_data_rows}")
    print(f"eligible_rows={plan.eligible_rows}")
    print(f"planned_records={plan.planned_records}")
    print(f"reusable_records={plan.reusable_records}")
    print(f"estimated_api_calls={plan.estimated_api_calls}")
    print(f"input_too_long_records={plan.input_too_long_records}")
    print(f"text_total_chars={plan.text_stats.total_chars}")
    print(f"text_min_chars={plan.text_stats.min_chars}")
    print(f"text_max_chars={plan.text_stats.max_chars}")
    print(f"text_avg_chars={plan.text_stats.avg_chars:.2f}")
    print(f"model={plan.model_name}")
    print(f"concurrency={plan.concurrency}")
    print(f"output_path={plan.output_path}")
    print(f"conflicts_output_path={plan.conflicts_output_path}")
    print(f"review_output_path={plan.review_output_path}")
    print(f"state_db_path={plan.state_db_path}")


if __name__ == "__main__":
    raise SystemExit(main())
