# tax-call-overdue-extractor

用于分析税务电话记录并提取逾期信息的 Python 项目。当前已实现 Excel 随机抽样，以及对抽样 Excel 中单条记录进行大模型结构化提取的预览能力。

## 数据安全约束

抽样功能只在本地处理 Excel，不读取 `.env`，不会发送任何业务数据。单条提取功能调用外部 OpenAI 兼容模型时，只允许向模型发送以下三列：

- 电话录音转文本内容
- 业务内容
- 答复内容

当前 Excel 的真实列名为“语音转文本”，程序读取该列后会在模型请求 JSON 中映射为“电话录音转文本内容”。发送给模型的 user message 只包含这三个键。空单元格、空白字符串和 `#N/A` 会转换为 `null`。

来电号码、登记人姓名、业务编号、登记日期、企业名称、Excel 行号、文件路径、工作表名称等字段不得发送给外部模型。代码和日志也不得输出 API Key、Authorization Header、三列原文、模型完整响应、电话、姓名、企业名称等敏感信息。原始模型响应如需保存，只能保存到被 Git 忽略的 `data/state/` 下。

## 目录结构

```text
tax-call-overdue-extractor/
├── .env.example
├── .gitignore
├── README.md
├── pyproject.toml
├── config/
│   ├── settings.yaml
│   └── tax_types.yaml
├── data/
│   ├── input/
│   ├── samples/
│   ├── output/
│   ├── conflicts/
│   ├── state/
│   └── logs/
├── scripts/
│   ├── sample_excel.py
│   └── extract_one.py
├── src/
│   └── tax_call_overdue_extractor/
│       ├── extraction/
│       ├── llm/
│       └── prompt_templates/
└── tests/
```

`data/` 下只提交 `.gitkeep`，真实输入、输出、状态文件和日志不会进入 Git。

## WSL 安装方法

进入项目目录：

```bash
cd /home/caspianwu/workspace/tax-call-overdue-extractor
```

创建并激活虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

安装依赖：

```bash
python -m pip install -e ".[dev]"
```

## 放置输入 Excel

将原始 `.xlsx` 文件放入：

```text
data/input/
```

默认运行时，如果 `data/input/` 下恰好有一个 `.xlsx` 文件，会自动作为输入文件。若有多个文件，需要显式传入 `--input`。

## 运行抽样

方式一：

```bash
python scripts/sample_excel.py
```

方式二：

```bash
python -m tax_call_overdue_extractor.cli sample
```

默认抽取 50 行，输出到：

```text
data/samples/原文件名_sample_50.xlsx
```

使用固定随机种子：

```bash
python -m tax_call_overdue_extractor.cli sample \
  --input data/input/source.xlsx \
  --output data/samples/source_sample_50.xlsx \
  --sample-size 50 \
  --seed 2026
```

默认不覆盖已存在的输出文件。如确需覆盖，显式增加：

```bash
--overwrite
```

## 抽样规则

只根据“语音转文本”判断有效数据行：

- 单元格不为空。
- 去除首尾空白后不为空。
- 内容不等于字符串 `#N/A`。
- 判断 `#N/A` 时兼容大小写和首尾空格。

“业务内容”或“答复内容”为空或为 `#N/A` 不影响抽样。

## 配置 LLM

复制环境变量示例：

```bash
cp .env.example .env
```

编辑 `.env`，至少填写：

```text
LLM_BASE_URL=https://llmapi.paratera.com
LLM_API_KEY=你的密钥
LLM_MODEL=DeepSeek-V4-Pro
```

其他可选项：

```text
LLM_TIMEOUT_SECONDS=120
LLM_MAX_RETRIES=3
LLM_TEMPERATURE=0
LLM_MAX_OUTPUT_TOKENS=4096
LLM_RESPONSE_FORMAT_MODE=auto
LLM_MAX_INPUT_CHARS=12000
```

`LLM_RESPONSE_FORMAT_MODE` 支持 `auto`、`json_object`、`none`。如果中转 API 不支持 `response_format`，将其改为 `none` 即可，不需要改代码。

切换到服务器本地 OpenAI 兼容模型时，只需要修改 `.env` 中的 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。如果 endpoint 需要 `/v1`，应直接写在 `LLM_BASE_URL` 中，例如：

```text
LLM_BASE_URL=http://127.0.0.1:8000/v1
```

## 单条提取 dry-run

先从抽样文件中选择一个 Excel 实际行号，第一条数据通常是第 2 行。

方式一：

```bash
python scripts/extract_one.py --row-number 2 --dry-run
```

方式二：

```bash
python -m tax_call_overdue_extractor.cli extract-one --row-number 2 --dry-run
```

dry-run 会读取指定行并构建即将发送给模型的请求，但不会调用 API，也不会输出原文。终端只显示三个允许字段的字段名、是否为空、字符数、总字符数、请求 SHA-256 摘要、模型名称和 BASE_URL。

## 单条真实调用

确认 `.env` 已配置后执行：

```bash
python -m tax_call_overdue_extractor.cli extract-one --row-number 2
```

默认输入为 `data/samples/` 下唯一的 `.xlsx` 文件。也可以显式指定：

```bash
python -m tax_call_overdue_extractor.cli extract-one \
  --input data/samples/source_sample_50.xlsx \
  --row-number 2 \
  --output data/state/preview/row_2.json
```

默认输出到：

```text
data/state/preview/row_<row-number>.json
```

默认不覆盖已有 JSON，确需覆盖时增加 `--overwrite`。

输出 JSON 顶层包含：

- `status`：`success`、`no_text` 或 `input_too_long`
- `called_api`：是否实际调用模型
- `model`、`base_url`
- `input_total_chars`
- `request_sha256`
- `raw_response_path`
- `result`：通过 Pydantic v2 校验后的结构化 `ExtractionResult`

`ExtractionResult` 包含 `schema_version`、`has_relevant_information`、`items`、`conflicts`、`needs_review`、`review_reasons`。模型输出必须符合 Schema，非法 JSON、非法税种、非法 evidence source、非法月份等都会被拒绝。

相关信息不要求原文已经明确逾期。只要三列中出现企业名称、明确企业简称、税种、所属期、涉税金额或明确期限状态中的任意一项，就应当返回 `has_relevant_information=true` 并至少生成一个 item。若只识别出企业名称，也允许 `tax_types`、`periods`、`amounts` 为空；是否逾期会在后续结合本地月份数据判断。

## 批量提取样本

批处理默认只预检，不调用 API：

```bash
python -m tax_call_overdue_extractor.cli extract-batch
```

只测试指定三行并真实调用：

```bash
python -m tax_call_overdue_extractor.cli extract-batch \
  --rows 2,3,4 \
  --execute \
  --concurrency 1
```

处理 50 条样本并启用断点续跑：

```bash
python -m tax_call_overdue_extractor.cli extract-batch \
  --execute \
  --concurrency 2 \
  --resume
```

中断后恢复使用同一命令：

```bash
python -m tax_call_overdue_extractor.cli extract-batch \
  --execute \
  --concurrency 2 \
  --resume
```

样本阶段默认安全上限为 100 条。超过 100 条时，除非显式传入 `--allow-large-run`，否则拒绝执行真实调用。

批处理输出位置：

- 最终 Excel：`data/output/<原文件名>_extracted.xlsx`
- 冲突清单：`data/conflicts/<原文件名>_conflicts.xlsx`
- 人工复核清单：`data/output/<原文件名>_review.xlsx`
- SQLite 状态库：`data/state/batch_state.sqlite3`
- 原始模型响应：`data/state/raw/`
- 结构化结果：`data/state/structured/`

SQLite 断点续跑复用条件：

- 同一工作表和原始 Excel 行号
- 三列允许文本计算出的 `input_hash` 未变化
- 系统提示词 `prompt_hash` 未变化
- `schema_version` 未变化
- 模型名称未变化
- 上次状态属于已完成可复用状态

状态库不保存三列完整原文。提示词、模型或该行输入变化后会重新调用模型。

批处理本地标准化会写入最后五列：企业名称、逾期税种、所属期、涉及金额、是否确定已逾期。逾期列只会写入 `已逾期` 或保持空白。

## Excel 格式保留说明

抽样不会用 pandas 重建工作簿。实现方式是先复制原始 `.xlsx` 到临时文件，再从下到上删除未抽中的数据行，随后更新自动筛选和 Excel 表格对象范围，保存并重新读取验证，最后原子移动到最终输出路径。

可保留常规字体、字号、颜色、填充、边框、对齐、行高、列宽、数字格式、日期格式、冻结窗格、筛选设置、打印设置和常见工作表属性。`openpyxl` 对宏、复杂透视表、部分图表、外部链接、切片器等高级 Excel 对象支持有限，复杂对象可能无法完全无损保留。

## 运行测试

```bash
pytest
```

测试数据会在临时目录中自动生成，不使用真实业务数据。

## 本阶段尚未实现

- 50 条批量调用
- 最终 Excel 回填
- 多结果插入 Excel 新行
- 全量并发处理
- 完整所属期本地换算
- 最终逾期判断
- SQLite 断点续跑

## 后续开发路线

- 税种标准化
- 所属期本地换算
- 逾期规则判断
- 多企业和多税种拆行
- 冲突清单
- SQLite 断点续跑
- 中转 API 与服务器本地模型切换
- 全量处理与结果校验
