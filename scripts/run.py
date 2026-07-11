#!/usr/bin/env python3
"""唯一运行入口：抽样、检查样本、单条调试、服务器全量提取。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tax_call_overdue_extractor.cli import main  # noqa: E402


COMMANDS = {
    "sample": ["sample"],
    "check": ["extract-batch", "--execute", "--resume"],
    "one": ["extract-one"],
    "all": ["extract-batch", "--execute", "--resume", "--allow-large-run"],
}


def run(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print("用法: python scripts/run.py {sample|check|one|all} [参数]")
        print("  sample  从 data/input 抽样 50 条")
        print("  check   用模型处理 data/samples 中的 50 条样本")
        print("  one     调试样本中的单条记录")
        print("  all     在服务器用本地模型处理 --input 指定的完整 Excel")
        return 2 if argv else 0
    command, *rest = argv
    if command == "all" and "--input" not in rest:
        print("all 必须显式提供完整 Excel：--input /path/to/full.xlsx")
        return 2
    return main([*COMMANDS[command], *rest])


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
