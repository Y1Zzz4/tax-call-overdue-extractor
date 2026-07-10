"""日志配置。日志内容不得包含原始敏感业务字段。"""

from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """配置控制台日志；传入 log_file 时同步写入安全日志文件。"""

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=handlers,
        force=True,
    )
