# 税务电话逾期信息抽取

这个项目只做一件事：先随机抽 50 条检查效果，确认合适后，在服务器本地模型上处理完整 Excel。

## 1. 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

在 `.env` 中配置 OpenAI 兼容接口：

```text
LLM_BASE_URL=http://127.0.0.1:8000/v1
LLM_API_KEY=你的密钥
LLM_MODEL=你的本地模型名
LLM_RESPONSE_FORMAT_MODE=none
```

兼容接口不支持 `response_format` 时使用 `none`。项目不会假定接口支持 JSON Schema。

## 2. 本地抽 50 条

把原始 Excel 放入 `data/input/`，然后运行：

```bash
python scripts/run.py sample
```

样本输出到 `data/samples/`。抽样只在本地进行，不调用模型。

## 3. 用模型检查这 50 条

```bash
python scripts/run.py check --overwrite
```

结果在 `data/output/`，人工复核表文件名以 `_review.xlsx` 结尾。调试单条记录：

```bash
python scripts/run.py one --row-number 2 --overwrite
```

## 4. 服务器全量运行

把项目、完整 Excel 和 `.env` 放到服务器，确保 `.env` 指向本地模型，然后运行：

```bash
python scripts/run.py all --input /path/to/full.xlsx --overwrite
```

`all` 默认启用断点续跑。中断后执行同一命令即可；未变化的已完成行会直接复用。

## 数据安全

模型请求的 user message 只有三个键：

- `电话录音转文本内容`（Excel 中的 `语音转文本`）
- `业务内容`
- `答复内容`

登记日期、月份、号码、人员、部门、业务编号、Excel 行号、路径和工作表名都不会进入模型请求。登记日期和月份只在本地用于相对日期换算。

模型请求最多使用 `response_format={"type":"json_object"}`，不会发送 JSON Schema。三个字段按“答复内容 → 业务内容 → 电话录音转文本内容”的顺序发送；有明确结论时以答复内容为准。

模型输出不要求一次完全符合内部格式。程序会自动兼容常见的 `value`、`original_text`、`period_raw`、`amount_raw`、字符串 Evidence、JSON 代码块和 JSON 前后说明文字，再统一成最终结果。

最终 Excel 在原五个结果字段后增加“说明”列。未识别字段会显示“未识别”“未提及”或“未明确”，不会留空。普通缺失和自动修复只写入说明，不进入 review。

review 只保留以下明显问题：

- 模型接口失败或响应完全不是可解析 JSON
- 三列均无有效文本或输入过长
- 答复内容与其他来源明确给出不同企业名称，且明显不是同音字或转录错误

## 结果与断点状态

批量状态保存在 `data/state/batch_state.sqlite3`：

- 可复用：`success`、`conflict`、`needs_review`、`skipped_no_text`、`input_too_long`
- 下次重试：`api_error`、`validation_error`
- 复用条件：工作表、Excel 原行号、三列内容哈希、提示词哈希、Schema 版本和模型名一致

文件指纹和业务编号会在本地状态/输出流程中使用，但业务编号不参与模型请求，也不是单独的缓存键。

## 代码结构

```text
scripts/run.py                         唯一运行入口
src/tax_call_overdue_extractor/
  sampling.py                          抽 50 条
  llm/                                 只构建三列请求并调用兼容接口
  extraction/                          Schema、解析、批处理和本地日期规则
  prompt_templates/extraction_system.txt
tests/                                 安全、Evidence 和 row 2 回归测试
```

运行测试：

```bash
pytest -q
```
