from pathlib import Path


PROMPT_PATH = Path("src/tax_call_overdue_extractor/prompt_templates/extraction_system.txt")


def test_prompt_is_completion_oriented_and_business_first() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert "请尽量完成提取" in prompt
    assert "业务内容优先" in prompt
    assert "答复内容补充" in prompt
    assert "最后才使用电话录音转文本内容" in prompt
    assert "未提到的字符串用 null，数组用 []，不要因此设置 needs_review" in prompt
    assert "找不到证据时返回空数组" in prompt
    assert "字段缺失、金额不清、税种不确定、所属期无法换算" in prompt
    assert "业务内容出现完整企业名称，就必须提取" in prompt
    assert "只有业务内容没有明确企业名称" in prompt
    assert "申请、提交、审核时间不是税款所属期" in prompt
    assert "只有流程逾期时返回 has_relevant_information=false" in prompt
