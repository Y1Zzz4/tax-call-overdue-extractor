# tax-call-overdue-extractor

用于分析税务电话记录并提取逾期信息的 Python 项目。本轮已实现 Excel 随机抽样能力，并为后续大模型结构化提取、字段标准化、冲突处理、断点续跑和全量处理保留配置与目录结构。

## 数据安全约束

本轮功能只做本地 Excel 抽样，不调用大模型，不读取 `.env`，不会发送任何业务数据。

后续接入模型时，只允许向模型发送以下三列：

- 语音转文本
- 业务内容
- 答复内容

来电号码、登记人姓名、业务编号、企业名称等字段不得发送给外部模型。代码和日志也不得输出 API Key、电话文本、业务内容、答复内容、电话、姓名、企业名称等敏感原始数据。

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
│   └── sample_excel.py
├── src/
│   └── tax_call_overdue_extractor/
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

## Excel 格式保留说明

抽样不会用 pandas 重建工作簿。实现方式是先复制原始 `.xlsx` 到临时文件，再从下到上删除未抽中的数据行，随后更新自动筛选和 Excel 表格对象范围，保存并重新读取验证，最后原子移动到最终输出路径。

可保留常规字体、字号、颜色、填充、边框、对齐、行高、列宽、数字格式、日期格式、冻结窗格、筛选设置、打印设置和常见工作表属性。`openpyxl` 对宏、复杂透视表、部分图表、外部链接、切片器等高级 Excel 对象支持有限，复杂对象可能无法完全无损保留。

## 运行测试

```bash
pytest
```

测试数据会在临时目录中自动生成，不使用真实业务数据。

## 后续开发路线

- LLM 严格 JSON 结构化提取
- 税种标准化
- 所属期本地换算
- 逾期规则判断
- 多企业和多税种拆行
- 冲突清单
- SQLite 断点续跑
- 中转 API 与服务器本地模型切换
- 全量处理与结果校验
