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

模型请求最多使用 `response_format={"type":"json_object"}`，不会发送 JSON Schema。三个字段按“业务内容 → 答复内容 → 电话录音转文本内容”的顺序发送；业务内容出现完整企业名称时优先采用。

模型输出不要求一次完全符合内部格式。程序会自动兼容常见的 `value`、`original_text`、`period_raw`、`amount_raw`、字符串 Evidence、JSON 代码块和 JSON 前后说明文字，再统一成最终结果。

每条记录调用模型两次：第一次完成整体提取，第二次用同样的三列原文统一复核企业名称、完整税种、所属期和逾期状态。税种支持“社保费”；“出口退税/出口退（免）税”归入“进出口税”。所有明确出现的税种都会合并写入税种列。

所属期使用最短且明确的规范写法：单月写 `2026年5月`，整年写 `2024年`，跨期写 `2026年2月3日至2026年2月18日`。`二四年`、`24年`、`二零二四年`都会转换为 `2024年`；原文只说月份时按 2026 年处理，“去年”按 2025 年处理。申请、提交、审核或流程时间不会当作税款所属期。

税种列中“未识别”与明确税种互斥。列表外税费统一写为“其他”，例如明确的增值税加一个列表外税费会输出 `增值税；其他`。

最终 Excel 在原五个结果字段后增加“说明”列。未识别字段会显示“未识别”“未提及”或“未明确”，不会留空。普通缺失和自动修复只写入说明，不进入 review。

抽样会逐单元格校验所选原始记录。“月份”如果是固定值，值、类型和格式保持不变；如果是 `=MONTH(H原行号)` 公式，则只把相对行号平移到样本当前行，例如原第145行抽到样本第2行后改为 `=MONTH(H2)`。样本会压缩掉未抽中的行，因此样本行号不等于原文件行号；应按业务编号或序号对应同一条记录。

生成最终结果时不增加、不删除、不拆分数据行，也不修改前 11 列；程序只填写第 12–17 列（企业名称到说明）。多个提取 item 会合并写回同一原始行。

本地逾期判断规则：明确说已经逾期时直接输出“已逾期”；没有明确表述但识别出所属期时，2025年及以前视为已逾期，2026年早于固定“月份”列月份时视为已逾期，当月及以后输出“未明确”。

review 只保留以下明显问题：

- 模型接口失败或响应完全不是可解析 JSON
- 三列均无有效文本或输入过长
- 业务内容没有明确企业名称时，答复内容与电话录音明确给出不同企业名称，且明显不是同音字或转录错误

## 结果与断点状态

批量状态保存在 `data/state/batch_state.sqlite3`：

- 可复用：`success`、`conflict`、`needs_review`、`skipped_no_text`、`input_too_long`
- 下次重试：`api_error`、`validation_error`
- 复用条件：工作表、Excel 原行号、三列内容哈希、提示词哈希、Schema 版本和模型名一致

文件指纹和业务编号会在本地状态/输出流程中使用，但业务编号不参与模型请求，也不是单独的缓存键。

每次批量运行的模型文件按批次和 Excel 行号存放，不再全部堆在两个目录中：

```text
data/state/runs/20260711_120000_ab12cd/
  row_000002/
    response.txt
    precision_review.txt
    result.json
  row_000003/
    response.txt
    precision_review.txt
    result.json
```

## 最简单的服务器迁移方式

本地生成不含数据和密钥的最小部署包：

```bash
python scripts/run.py pack
```

上传 `dist/tax_extractor_server.zip` 和完整 Excel 到服务器。服务器只需：

```bash
unzip tax_extractor_server.zip -d tax-extractor
cd tax-extractor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
# 编辑 .env，指向服务器本地模型
python scripts/run.py all --input /path/to/full.xlsx --overwrite
```

部署包只包含 `src/`、`config/`、`scripts/run.py`、`pyproject.toml`、README 和 `.env.example`，不会包含 `.env`、Excel、测试、raw、structured、SQLite 或历史输出。

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
