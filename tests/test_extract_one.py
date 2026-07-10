from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from tax_call_overdue_extractor.config import (
    ExcelSettings,
    LLMReservedSettings,
    LLMSettings,
    LoggingSettings,
    PathSettings,
    ProjectSettings,
    SamplingSettings,
)
from tax_call_overdue_extractor.excel_io import EXPECTED_COLUMNS
from tax_call_overdue_extractor.exceptions import ExtractionError, LLMAuthenticationError, LLMTransientError
from tax_call_overdue_extractor.extraction.service import SingleRecordExtractionService
from tax_call_overdue_extractor.llm.client import LLMResponse, OpenAICompatibleLLMClient


def make_settings(tmp_path: Path, *, max_input_chars: int = 12000, max_retries: int = 2) -> ProjectSettings:
    return ProjectSettings(
        paths=PathSettings(
            input_dir=tmp_path / "data" / "input",
            samples_dir=tmp_path / "data" / "samples",
            output_dir=tmp_path / "data" / "output",
            conflicts_dir=tmp_path / "data" / "conflicts",
            state_dir=tmp_path / "data" / "state",
            logs_dir=tmp_path / "data" / "logs",
        ),
        excel=ExcelSettings(header_row=1, sheet_name=None, use_active_sheet_when_sheet_not_set=True),
        sampling=SamplingSettings(default_sample_size=50, default_seed=None),
        logging=LoggingSettings(level="INFO"),
        llm_reserved=LLMReservedSettings(
            interface="openai_compatible",
            max_concurrency=4,
            max_retries=max_retries,
            timeout_seconds=60,
            max_input_chars=max_input_chars,
        ),
        llm=LLMSettings(
            base_url="https://example.test/v1",
            api_key="test-key",
            model="mock-model",
            max_retries=max_retries,
            max_input_chars=max_input_chars,
        ),
    )


def create_workbook(path: Path, row_values: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "测试表"
    worksheet.append(list(EXPECTED_COLUMNS))
    worksheet.append(row_values)
    workbook.save(path)
    workbook.close()


def row_with_texts(voice: object, business: object, reply: object) -> list[object]:
    values: list[object] = []
    for index, column in enumerate(EXPECTED_COLUMNS, start=1):
        if column == "语音转文本":
            values.append(voice)
        elif column == "业务内容":
            values.append(business)
        elif column == "答复内容":
            values.append(reply)
        else:
            values.append(f"FORBIDDEN_{index}_{column}")
    return values


def success_json() -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "has_relevant_information": True,
            "items": [
                {
                    "enterprise_name": None,
                    "enterprise_evidence": [],
                    "tax_types": ["未识别"],
                    "tax_type_raw": [],
                    "tax_evidence": [],
                    "periods": [],
                    "amounts": [],
                    "explicitly_overdue": True,
                    "overdue_evidence": [{"source": "业务内容", "quote": "存在逾期"}],
                    "relationship_note": None,
                    "needs_review": True,
                    "review_reasons": ["信息不足"],
                }
            ],
            "conflicts": [],
            "needs_review": True,
            "review_reasons": ["信息不足"],
        },
        ensure_ascii=False,
    )


def enterprise_only_json(name: str = "甲测试企业") -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "has_relevant_information": True,
            "items": [
                {
                    "enterprise_name": name,
                    "enterprise_evidence": [{"source": "业务内容", "quote": name}],
                    "tax_types": [],
                    "tax_type_raw": [],
                    "tax_evidence": [],
                    "periods": [],
                    "amounts": [],
                    "explicitly_overdue": None,
                    "overdue_evidence": [],
                    "relationship_note": None,
                    "needs_review": False,
                    "review_reasons": [],
                }
            ],
            "conflicts": [],
            "needs_review": False,
            "review_reasons": [],
        },
        ensure_ascii=False,
    )


def no_relevant_json() -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "has_relevant_information": False,
            "items": [],
            "conflicts": [],
            "needs_review": False,
            "review_reasons": [],
        },
        ensure_ascii=False,
    )


class FakeLLMClient:
    def __init__(self, content: str = "") -> None:
        self.content = content or success_json()
        self.calls = 0
        self.last_request = None

    def complete(self, request):
        self.calls += 1
        self.last_request = request
        return LLMResponse(content=self.content)


class FailingLLMClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        raise AssertionError("API should not be called")


def test_extract_one_success_with_mock_client(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = settings.paths.samples_dir / "sample.xlsx"
    output = settings.paths.state_dir / "preview" / "row_2.json"
    create_workbook(source, row_with_texts("语音文本", "存在逾期", "答复文本"))
    client = FakeLLMClient()

    result = SingleRecordExtractionService(settings, llm_client=client).extract_one(
        input_path=source,
        row_number=2,
        output_path=output,
        sheet_name=None,
        overwrite=False,
    )

    assert client.calls == 1
    assert result.called_api is True
    assert result.item_count == 1
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["status"] == "success"
    assert data["result"]["items"][0]["tax_types"] == ["未识别"]


def test_only_business_content_enterprise_is_extracted(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = settings.paths.samples_dir / "sample.xlsx"
    output = settings.paths.state_dir / "preview" / "row_2.json"
    create_workbook(source, row_with_texts(None, "甲测试企业", None))
    client = FakeLLMClient(enterprise_only_json("甲测试企业"))

    result = SingleRecordExtractionService(settings, llm_client=client).extract_one(
        input_path=source,
        row_number=2,
        output_path=output,
        sheet_name=None,
        overwrite=False,
    )

    user_data = json.loads(client.last_request.messages[1]["content"])
    assert user_data["电话录音转文本内容"] is None
    assert user_data["业务内容"] == "甲测试企业"
    assert user_data["答复内容"] is None
    assert result.item_count == 1
    data = json.loads(output.read_text(encoding="utf-8"))
    item = data["result"]["items"][0]
    assert data["result"]["has_relevant_information"] is True
    assert item["enterprise_name"] == "甲测试企业"
    assert item["tax_types"] == []
    assert item["tax_type_raw"] == []
    assert item["periods"] == []
    assert item["amounts"] == []
    assert item["explicitly_overdue"] is None


def test_empty_voice_text_does_not_block_business_content_extraction(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = settings.paths.samples_dir / "sample.xlsx"
    output = settings.paths.state_dir / "preview" / "row_2.json"
    create_workbook(source, row_with_texts("   ", "乙测试企业", "#N/A"))
    client = FakeLLMClient(enterprise_only_json("乙测试企业"))

    result = SingleRecordExtractionService(settings, llm_client=client).extract_one(
        input_path=source,
        row_number=2,
        output_path=output,
        sheet_name=None,
        overwrite=False,
    )

    user_data = json.loads(client.last_request.messages[1]["content"])
    assert user_data["电话录音转文本内容"] is None
    assert user_data["业务内容"] == "乙测试企业"
    assert result.called_api is True
    assert result.item_count == 1


def test_pronoun_only_can_return_no_relevant_information(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = settings.paths.samples_dir / "sample.xlsx"
    output = settings.paths.state_dir / "preview" / "row_2.json"
    create_workbook(source, row_with_texts(None, "我们公司", None))
    client = FakeLLMClient(no_relevant_json())

    result = SingleRecordExtractionService(settings, llm_client=client).extract_one(
        input_path=source,
        row_number=2,
        output_path=output,
        sheet_name=None,
        overwrite=False,
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert result.item_count == 0
    assert data["result"]["has_relevant_information"] is False
    assert data["result"]["items"] == []


def test_one_allowed_field_can_be_empty_or_na(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = settings.paths.samples_dir / "sample.xlsx"
    create_workbook(source, row_with_texts("语音文本", None, "#N/A"))
    client = FakeLLMClient()

    SingleRecordExtractionService(settings, llm_client=client).extract_one(
        input_path=source,
        row_number=2,
        output_path=settings.paths.state_dir / "preview" / "row_2.json",
        sheet_name=None,
        overwrite=False,
    )

    user_data = json.loads(client.last_request.messages[1]["content"])
    assert user_data["电话录音转文本内容"] == "语音文本"
    assert user_data["业务内容"] is None
    assert user_data["答复内容"] is None


def test_all_empty_fields_do_not_call_api(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = settings.paths.samples_dir / "sample.xlsx"
    output = settings.paths.state_dir / "preview" / "row_2.json"
    create_workbook(source, row_with_texts(None, "   ", "#N/A"))
    client = FailingLLMClient()

    result = SingleRecordExtractionService(settings, llm_client=client).extract_one(
        input_path=source,
        row_number=2,
        output_path=output,
        sheet_name=None,
        overwrite=False,
    )

    assert client.calls == 0
    assert result.status == "no_text"
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["called_api"] is False
    assert data["result"]["review_reasons"] == ["无可分析文本"]


def test_input_too_long_does_not_call_api(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_input_chars=5)
    source = settings.paths.samples_dir / "sample.xlsx"
    output = settings.paths.state_dir / "preview" / "row_2.json"
    create_workbook(source, row_with_texts("超过长度", "业务", "答复"))
    client = FailingLLMClient()

    result = SingleRecordExtractionService(settings, llm_client=client).extract_one(
        input_path=source,
        row_number=2,
        output_path=output,
        sheet_name=None,
        overwrite=False,
    )

    assert client.calls == 0
    assert result.status == "input_too_long"
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["status"] == "input_too_long"
    assert data["result"] is None


def test_existing_output_is_rejected_by_default(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = settings.paths.samples_dir / "sample.xlsx"
    output = settings.paths.state_dir / "preview" / "row_2.json"
    create_workbook(source, row_with_texts("语音", "业务", "答复"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(ExtractionError, match="默认拒绝覆盖"):
        SingleRecordExtractionService(settings, llm_client=FakeLLMClient()).extract_one(
            input_path=source,
            row_number=2,
            output_path=output,
            sheet_name=None,
            overwrite=False,
        )

    assert output.read_text(encoding="utf-8") == "existing"


class RateLimitError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class FakeOpenAICompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))])


class FakeOpenAIClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.chat = SimpleNamespace(completions=FakeOpenAICompletions(outcomes))


def test_timeout_or_rate_limit_is_retried(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_retries=2)
    fake_openai = FakeOpenAIClient([RateLimitError("limited"), RateLimitError("limited"), success_json()])
    client = OpenAICompatibleLLMClient(settings.llm, client=fake_openai, sleeper=lambda _: None)
    source = settings.paths.samples_dir / "sample.xlsx"
    create_workbook(source, row_with_texts("语音", "业务", "答复"))
    request = SingleRecordExtractionService(settings).build_dry_run(
        input_path=source,
        row_number=2,
        sheet_name=None,
    )
    # build_dry_run 只用于确认不访问网络；正式 request 由 service 内部构建即可
    assert request.total_chars > 0

    from tax_call_overdue_extractor.llm.request_builder import build_chat_request, build_model_input

    response = client.complete(build_chat_request("system", build_model_input(
        voice_text="语音",
        business_content="业务",
        reply_content="答复",
    )))

    assert response.content
    assert fake_openai.chat.completions.calls == 3


def test_authentication_error_is_not_retried(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_retries=3)
    fake_openai = FakeOpenAIClient([AuthenticationError("bad key")])
    client = OpenAICompatibleLLMClient(settings.llm, client=fake_openai, sleeper=lambda _: None)

    from tax_call_overdue_extractor.llm.request_builder import build_chat_request, build_model_input

    with pytest.raises(LLMAuthenticationError):
        client.complete(build_chat_request("system", build_model_input(
            voice_text="语音",
            business_content="业务",
            reply_content="答复",
        )))

    assert fake_openai.chat.completions.calls == 1
