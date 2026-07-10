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


class LLMConfigError(ExtractorError):
    """LLM 配置缺失或配置值非法。"""


class LLMClientError(ExtractorError):
    """LLM 客户端调用失败。"""


class LLMAuthenticationError(LLMClientError):
    """认证失败、模型不存在等确定性 LLM 错误。"""


class LLMTransientError(LLMClientError):
    """超时、限流、连接失败或服务端错误等可重试 LLM 错误。"""


class ExtractionError(ExtractorError):
    """单条结构化提取流程失败。"""


class ResponseParseError(ExtractionError):
    """模型响应不是合法 JSON 或不符合结构化 Schema。"""


class InputTooLongError(ExtractionError):
    """输入文本超过当前单条提取上限。"""
