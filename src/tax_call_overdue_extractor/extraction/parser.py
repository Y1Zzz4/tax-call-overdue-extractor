"""模型响应解析与 Schema 校验。"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from tax_call_overdue_extractor.exceptions import ResponseParseError

from .schemas import ExtractionResult


CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_extraction_response(raw_response: str) -> ExtractionResult:
    """解析模型响应并执行完整 Schema 校验。"""

    text = _strip_code_fence(raw_response)
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResponseParseError(f"模型响应不是合法 JSON: {exc.msg}") from exc

    try:
        return ExtractionResult.model_validate(data)
    except ValidationError as exc:
        raise ResponseParseError(f"模型响应不符合提取 Schema: {exc}") from exc


def _strip_code_fence(raw_response: str) -> str:
    match = CODE_FENCE_PATTERN.match(raw_response)
    if not match:
        return raw_response.strip()
    return match.group(1).strip()
