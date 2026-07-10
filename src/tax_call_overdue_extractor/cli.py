"""命令行入口。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_PATH, load_settings
from .exceptions import ExtractorError
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
        print(f"field={field.name} empty={field.is_empty} char_count={field.char_count}")
    print(f"total_char_count={summary.total_chars}")
    print(f"request_sha256={summary.request_sha256}")
    print(f"model={summary.model}")
    print(f"base_url={summary.base_url}")


if __name__ == "__main__":
    raise SystemExit(main())
