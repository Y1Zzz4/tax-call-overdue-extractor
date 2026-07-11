from pathlib import Path


PROMPT_PATH = Path("src/tax_call_overdue_extractor/prompt_templates/extraction_system.txt")


def test_prompt_defines_relevance_beyond_overdue_word() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert "原文即使没有出现“逾期”二字" in prompt
    assert "企业名称、明确企业简称、税种、所属期" in prompt
    assert "不能把“没有明确逾期表述”当作无相关信息" in prompt
    assert "如果只识别出企业名称，也必须生成 item" in prompt
    assert "tax_types、tax_type_raw、periods、amounts 为空列表" in prompt
    assert "item 不要求同时具备企业、税种、所属期和金额" in prompt
    assert "只有三个来源中完全不存在任何目标信息时" in prompt
    assert "periods 必须是 PeriodMention 对象数组" in prompt
    assert "每个 item 必须包含 needs_review" in prompt
    assert "不允许在 item 根级输出 evidence 字段" in prompt
    assert "只能是 Evidence 对象数组" in prompt
    assert '"source": "电话录音转文本内容"' in prompt
    assert '"quote": "重庆海尔家电销售有限公司上海分公司"' in prompt
    assert "严禁输出字符串 Evidence" in prompt
    assert "退个税申请因未及时审核而逾期作废" in prompt
    assert "2026年2月发起申请" in prompt
    assert "不同企业，或不同税种、所属期、金额" in prompt
