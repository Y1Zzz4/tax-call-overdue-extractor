#!/usr/bin/env python3
"""项目源码未安装时，也可以直接运行的批处理入口。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tax_call_overdue_extractor.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["extract-batch", *sys.argv[1:]]))
