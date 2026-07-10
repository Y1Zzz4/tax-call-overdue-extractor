from pathlib import Path

import yaml

from tax_call_overdue_extractor.config import load_settings


def test_load_default_settings() -> None:
    settings = load_settings()

    assert settings.sampling.default_sample_size == 50
    assert settings.sampling.default_seed is None
    assert settings.excel.header_row == 1
    assert settings.llm_reserved.interface == "openai_compatible"
    assert settings.paths.input_dir.name == "input"


def test_tax_types_config_contains_required_labels() -> None:
    data = yaml.safe_load(Path("config/tax_types.yaml").read_text(encoding="utf-8"))

    assert data["standard_tax_types"][0] == "增值税"
    assert "其他" in data["standard_tax_types"]
    assert "未识别" in data["standard_tax_types"]
    assert data["notes"]["其他"]
    assert data["notes"]["未识别"]
