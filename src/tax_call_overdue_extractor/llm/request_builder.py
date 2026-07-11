"""安全构建发送给模型的请求内容。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tax_call_overdue_extractor.excel_io import CALL_TEXT_COLUMN


MODEL_VOICE_TEXT_KEY = "电话录音转文本内容"
BUSINESS_CONTENT_KEY = "业务内容"
REPLY_CONTENT_KEY = "答复内容"
ALLOWED_MODEL_DATA_KEYS: tuple[str, str, str] = (
    BUSINESS_CONTENT_KEY,
    REPLY_CONTENT_KEY,
    MODEL_VOICE_TEXT_KEY,
)
EXCEL_TO_MODEL_KEY: Mapping[str, str] = {
    CALL_TEXT_COLUMN: MODEL_VOICE_TEXT_KEY,
    BUSINESS_CONTENT_KEY: BUSINESS_CONTENT_KEY,
    REPLY_CONTENT_KEY: REPLY_CONTENT_KEY,
}


@dataclass(frozen=True)
class FieldSummary:
    name: str
    is_empty: bool
    char_count: int


@dataclass(frozen=True)
class ModelInput:
    data: Mapping[str, str | None]
    serialized_user_message: str
    sha256: str
    field_summaries: tuple[FieldSummary, ...]
    total_chars: int

    @property
    def has_any_text(self) -> bool:
        return any(value is not None for value in self.data.values())


@dataclass(frozen=True)
class ChatRequest:
    messages: tuple[dict[str, str], ...]
    model_input: ModelInput


def normalize_optional_text(value: object) -> str | None:
    """把空单元格、空白字符串和 #N/A 统一转换为 None。"""

    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.upper() == "#N/A":
        return None
    return str(value)


def build_model_input(
    *,
    voice_text: object,
    business_content: object,
    reply_content: object,
) -> ModelInput:
    """纯函数：构建模型业务输入，序列化后只包含三个允许键。"""

    data: dict[str, str | None] = {
        MODEL_VOICE_TEXT_KEY: normalize_optional_text(voice_text),
        BUSINESS_CONTENT_KEY: normalize_optional_text(business_content),
        REPLY_CONTENT_KEY: normalize_optional_text(reply_content),
    }
    return build_model_input_from_allowed_data(data)


def build_model_input_from_allowed_data(data: Mapping[str, object]) -> ModelInput:
    """纯函数：从三个允许字段构建 user message。"""

    if set(data.keys()) != set(ALLOWED_MODEL_DATA_KEYS):
        raise ValueError("模型输入只能包含三个允许字段")

    normalized = {
        key: normalize_optional_text(data[key])
        for key in ALLOWED_MODEL_DATA_KEYS
    }
    serialized = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )
    summaries = tuple(
        FieldSummary(
            name=key,
            is_empty=normalized[key] is None,
            char_count=0 if normalized[key] is None else len(str(normalized[key])),
        )
        for key in ALLOWED_MODEL_DATA_KEYS
    )
    total_chars = sum(summary.char_count for summary in summaries)
    return ModelInput(
        data=normalized,
        serialized_user_message=serialized,
        sha256=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        field_summaries=summaries,
        total_chars=total_chars,
    )


def build_chat_request(system_prompt: str, model_input: ModelInput) -> ChatRequest:
    """纯函数：构建 chat messages，不加入文件路径、行号或工作表名。"""

    return ChatRequest(
        messages=(
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": model_input.serialized_user_message},
        ),
        model_input=model_input,
    )


def load_system_prompt(path: Path | None = None) -> str:
    """读取独立维护的系统提示词。"""

    prompt_path = path or Path(__file__).resolve().parents[1] / "prompt_templates" / "extraction_system.txt"
    return prompt_path.read_text(encoding="utf-8")
