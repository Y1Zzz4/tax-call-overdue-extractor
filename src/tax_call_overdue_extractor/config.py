"""配置加载与路径解析。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .exceptions import ConfigError


DEFAULT_CONFIG_PATH = Path("config/settings.yaml")


@dataclass(frozen=True)
class PathSettings:
    input_dir: Path
    samples_dir: Path
    output_dir: Path
    conflicts_dir: Path
    state_dir: Path
    logs_dir: Path


@dataclass(frozen=True)
class ExcelSettings:
    header_row: int
    sheet_name: str | None
    use_active_sheet_when_sheet_not_set: bool


@dataclass(frozen=True)
class SamplingSettings:
    default_sample_size: int
    default_seed: int | None


@dataclass(frozen=True)
class LoggingSettings:
    level: str


@dataclass(frozen=True)
class LLMReservedSettings:
    interface: str
    max_concurrency: int
    max_retries: int
    timeout_seconds: int


@dataclass(frozen=True)
class ProjectSettings:
    paths: PathSettings
    excel: ExcelSettings
    sampling: SamplingSettings
    logging: LoggingSettings
    llm_reserved: LLMReservedSettings


def load_settings(config_path: str | Path | None = None) -> ProjectSettings:
    """读取 YAML 配置，并把相对路径解析为项目内路径。"""

    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    if not path.is_file():
        raise ConfigError(f"配置路径不是文件: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"配置文件无法读取: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 格式错误: {path}") from exc

    data = _as_mapping(raw, "配置文件根节点")
    project_root = _project_root_for_config(path)

    paths = _as_mapping(data.get("paths"), "paths")
    excel = _as_mapping(data.get("excel"), "excel")
    sampling = _as_mapping(data.get("sampling"), "sampling")
    logging_config = _as_mapping(data.get("logging"), "logging")
    llm_reserved = _as_mapping(data.get("llm_reserved"), "llm_reserved")

    return ProjectSettings(
        paths=PathSettings(
            input_dir=_resolve_path(paths.get("input_dir"), project_root, "paths.input_dir"),
            samples_dir=_resolve_path(paths.get("samples_dir"), project_root, "paths.samples_dir"),
            output_dir=_resolve_path(paths.get("output_dir"), project_root, "paths.output_dir"),
            conflicts_dir=_resolve_path(paths.get("conflicts_dir"), project_root, "paths.conflicts_dir"),
            state_dir=_resolve_path(paths.get("state_dir"), project_root, "paths.state_dir"),
            logs_dir=_resolve_path(paths.get("logs_dir"), project_root, "paths.logs_dir"),
        ),
        excel=ExcelSettings(
            header_row=_positive_int(excel.get("header_row"), "excel.header_row"),
            sheet_name=_optional_str(excel.get("sheet_name"), "excel.sheet_name"),
            use_active_sheet_when_sheet_not_set=bool(
                excel.get("use_active_sheet_when_sheet_not_set", True)
            ),
        ),
        sampling=SamplingSettings(
            default_sample_size=_positive_int(
                sampling.get("default_sample_size"), "sampling.default_sample_size"
            ),
            default_seed=_optional_int(sampling.get("default_seed"), "sampling.default_seed"),
        ),
        logging=LoggingSettings(
            level=str(logging_config.get("level", "INFO")).upper(),
        ),
        llm_reserved=LLMReservedSettings(
            interface=str(llm_reserved.get("interface", "openai_compatible")),
            max_concurrency=_positive_int(
                llm_reserved.get("max_concurrency"), "llm_reserved.max_concurrency"
            ),
            max_retries=_non_negative_int(
                llm_reserved.get("max_retries"), "llm_reserved.max_retries"
            ),
            timeout_seconds=_positive_int(
                llm_reserved.get("timeout_seconds"), "llm_reserved.timeout_seconds"
            ),
        ),
    )


def _project_root_for_config(config_path: Path) -> Path:
    resolved = config_path.resolve()
    if resolved.parent.name == "config":
        return resolved.parent.parent
    return Path.cwd().resolve()


def _as_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"配置项 {name} 必须是映射对象")
    return value


def _resolve_path(value: Any, root: Path, name: str) -> Path:
    if value is None or str(value).strip() == "":
        raise ConfigError(f"配置项 {name} 不能为空")
    path = Path(str(value))
    return path if path.is_absolute() else root / path


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置项 {name} 必须是正整数") from exc
    if parsed <= 0:
        raise ConfigError(f"配置项 {name} 必须是正整数")
    return parsed


def _non_negative_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置项 {name} 必须是非负整数") from exc
    if parsed < 0:
        raise ConfigError(f"配置项 {name} 必须是非负整数")
    return parsed


def _optional_int(value: Any, name: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置项 {name} 必须是整数或 null") from exc


def _optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    parsed = str(value).strip()
    if parsed == "":
        raise ConfigError(f"配置项 {name} 不能是空字符串")
    return parsed
