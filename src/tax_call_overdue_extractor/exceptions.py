"""项目内统一异常类型。"""

from __future__ import annotations


class ExtractorError(Exception):
    """项目可预期错误的基类。"""


class ConfigError(ExtractorError):
    """配置文件缺失、格式错误或配置值非法。"""


class ExcelIOError(ExtractorError):
    """Excel 文件读取、复制、保存或路径校验错误。"""


class HeaderValidationError(ExcelIOError):
    """Excel 表头不符合预期。"""


class SheetNotFoundError(ExcelIOError):
    """指定工作表不存在。"""


class SamplingError(ExtractorError):
    """抽样规则无法满足。"""


class OutputValidationError(ExcelIOError):
    """抽样输出文件验证失败。"""
