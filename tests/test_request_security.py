from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from tax_call_overdue_extractor.cli import _print_dry_run_summary
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
from tax_call_overdue_extractor.extraction.service import (
    SingleRecordExtractionService,
    read_model_input_from_excel,
)
from tax_call_overdue_extractor.llm.request_builder import (
    ALLOWED_MODEL_DATA_KEYS,
    build_chat_request,
    build_model_input,
)


def make_settings(tmp_path: Path) -> ProjectSettings:
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
            max_retries=3,
            timeout_seconds=60,
        ),
        llm=LLMSettings(
            base_url="https://example.test/v1",
            api_key="SECRET_API_KEY",
            model="mock-model",
            max_input_chars=12000,
        ),
    )


def create_canary_workbook(path: Path) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "安全测试表"
    worksheet.append(list(EXPECTED_COLUMNS))
    canaries = {column: f"CANARY_{index}_{column}" for index, column in enumerate(EXPECTED_COLUMNS, start=1)}
    worksheet.append([canaries[column] for column in EXPECTED_COLUMNS])
    workbook.save(path)
    workbook.close()
    return canaries


def test_model_request_contains_only_allowed_business_fields(tmp_path: Path) -> None:
    source = tmp_path / "sample.xlsx"
    canaries = create_canary_workbook(source)

    model_input = read_model_input_from_excel(
        source,
        row_number=2,
        sheet_name=None,
        use_active_sheet=True,
        header_row=1,
    )
    request = build_chat_request("system prompt", model_input)
    user_data = json.loads(request.messages[1]["content"])
    serialized_request = json.dumps(request.messages, ensure_ascii=False)

    assert set(user_data.keys()) == set(ALLOWED_MODEL_DATA_KEYS)
    assert canaries["语音转文本"] in serialized_request
    assert canaries["业务内容"] in serialized_request
    assert canaries["答复内容"] in serialized_request

    forbidden_columns = set(EXPECTED_COLUMNS) - {"语音转文本", "业务内容", "答复内容"}
    for column in forbidden_columns:
        assert canaries[column] not in serialized_request
    assert str(source) not in serialized_request
    assert "安全测试表" not in serialized_request
    assert "row_number" not in serialized_request


def test_dry_run_and_logs_do_not_expose_raw_text_or_api_key(
    tmp_path: Path,
    capsys,
    caplog,
) -> None:
    settings = make_settings(tmp_path)
    source = settings.paths.samples_dir / "sample.xlsx"
    canaries = create_canary_workbook(source)
    service = SingleRecordExtractionService(settings)

    with caplog.at_level("INFO"):
        summary = service.build_dry_run(input_path=source, row_number=2, sheet_name=None)
    _print_dry_run_summary(summary)

    stdout = capsys.readouterr().out
    logs = caplog.text
    for column in {"语音转文本", "业务内容", "答复内容"}:
        assert canaries[column] not in stdout
        assert canaries[column] not in logs
    assert "SECRET_API_KEY" not in stdout
    assert "SECRET_API_KEY" not in logs
    assert "电话录音转文本内容：有效，字符数=" in stdout
    assert "业务内容：有效，字符数=" in stdout
    assert "答复内容：有效，字符数=" in stdout
    assert "request_sha256=" in stdout


def test_valid_business_content_is_not_converted_to_null() -> None:
    model_input = build_model_input(
        voice_text=None,
        business_content="明确有效的业务内容",
        reply_content="#N/A",
    )
    user_data = json.loads(model_input.serialized_user_message)

    assert user_data["电话录音转文本内容"] is None
    assert user_data["业务内容"] == "明确有效的业务内容"
    assert user_data["答复内容"] is None
