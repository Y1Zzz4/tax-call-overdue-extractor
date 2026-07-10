"""OpenAI 兼容 LLM 客户端封装。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from openai import OpenAI

from tax_call_overdue_extractor.config import LLMSettings
from tax_call_overdue_extractor.exceptions import (
    LLMAuthenticationError,
    LLMClientError,
    LLMConfigError,
    LLMTransientError,
)

from .request_builder import ChatRequest


@dataclass(frozen=True)
class LLMResponse:
    content: str


class LLMClientProtocol(Protocol):
    def complete(self, request: ChatRequest) -> LLMResponse:
        """发送 chat completion 请求并返回文本内容。"""


class OpenAICompatibleLLMClient:
    """带有限重试的 OpenAI 兼容客户端。"""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        client: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if settings.api_key.strip() == "":
            raise LLMConfigError("LLM_API_KEY 未配置，无法进行真实模型调用")
        self._settings = settings
        self._client = client or OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
        )
        self._sleeper = sleeper

    def complete(self, request: ChatRequest) -> LLMResponse:
        attempts = self._settings.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self._call_once(request)
            except Exception as exc:  # noqa: BLE001 - 统一分类第三方 SDK 异常，不记录敏感上下文
                if _is_deterministic_error(exc):
                    raise LLMAuthenticationError("LLM调用失败: 认证、权限、模型或请求配置错误") from exc
                if not _is_retriable_error(exc):
                    raise LLMClientError("LLM调用失败: 非可重试错误") from exc
                if attempt >= attempts:
                    raise LLMTransientError("LLM调用失败: 超时、限流、连接失败或服务端错误达到重试上限") from exc
                self._sleeper(_backoff_seconds(attempt))
        raise LLMTransientError("LLM调用失败: 未知重试状态")

    def _call_once(self, request: ChatRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "messages": list(request.messages),
            "temperature": self._settings.temperature,
            "max_tokens": self._settings.max_output_tokens,
        }
        response_format = _response_format(self._settings.response_format_mode)
        if response_format is not None:
            kwargs["response_format"] = response_format

        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if content is None or str(content).strip() == "":
            raise LLMClientError("LLM返回内容为空")
        return LLMResponse(content=str(content))


def _response_format(mode: str) -> dict[str, str] | None:
    if mode in {"auto", "json_object"}:
        return {"type": "json_object"}
    if mode == "none":
        return None
    raise LLMConfigError("LLM_RESPONSE_FORMAT_MODE 必须是 auto、json_object 或 none")


def _backoff_seconds(attempt: int) -> float:
    return float(min(2 ** (attempt - 1), 8))


def _is_deterministic_error(exc: BaseException) -> bool:
    name = exc.__class__.__name__
    if name in {"AuthenticationError", "PermissionDeniedError", "NotFoundError", "BadRequestError"}:
        return True
    status_code = getattr(exc, "status_code", None)
    return status_code in {400, 401, 403, 404}


def _is_retriable_error(exc: BaseException) -> bool:
    name = exc.__class__.__name__
    if name in {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"}:
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        return False
    return status_code in {408, 409, 429} or int(status_code) >= 500
