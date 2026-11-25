# LaTeX 切分与审核管线

把数学论文的 LaTeX 主文件转换为按原文顺序定位的句子、公式和引用。比如 `Let $x>0$.` 会生成句子 `Let MATH1.`，下游可用 `MATH1` 取回 `x>0`。这些产物供自然语言句法解析和形式化保真度分析使用。

## 架构与交付

| 阶段 | 职责 | 输出目录中的主要文件 |
|---|---|---|
| 源码选择与归一化 | 读取主文件及依赖，按声明、分组和实际执行顺序选择正文，归并等价的排版形式 | `normalized.tex`、`normalize-stats.json`、`source_evidence.json`；启用 TeX 时另有 `execution.json` |
| 切分 | 划分正文单元和句子，以 `MATHn` 关联公式，以 `REFn` 关联引用、脚注和保留块 | `sentences.jsonl`、`sentences.tok.txt`、`formulas.json`、`content_blocks.json` |
| 本篇解释与验收 | 结合基础词表、论文声明与局部上下文判断记号读法和公式角色；核对原式、锚点、词元和待办 | `paper_lexicon.json`、`roles.json`、`issues.json`、`split_status.json` 及待办文件 |
| 网页审核 | 查看原文和判断依据，确认、修改或撤销条目，重跑同一稿件 | `manual_reviews.sqlite3` 保存人工和模型决定，`rule_reviews.json` 记录规则比对 |

`scripts/` 实现源码处理、切分与独立验收，`data/` 保存通用规则表。`pipeline/` 串联各阶段并提供审核台。公式边界与公式解释分开保存，因此结构读法待定时，下游仍可取得完整原式。

## 运行要求

| 用途 | 所需环境 |
|---|---|
| 单篇切分、验收与人工审核 | 仓库 CI 使用 Python 3.11。单篇流程使用 Python 标准库和可运行 `sh` 的终端。Windows 可用 Git Bash；`PYTHON` 可指定解释器。输入为 UTF-8 主文件及其本地依赖。 |
| 根据实际编译确定有效正文 | 可从 `PATH` 调用的 `pdflatex`、`xelatex` 或 `lualatex`、论文所需宏包，以及 Poppler 的 `pdftotext`。入口按上述顺序自动选引擎，仅在找不到任何引擎时回退到静态处理。 |
| 网页模型审核 | 已启动的本地推理服务，或可访问的远端模型 API。常规切分和人工审核可离线使用。 |

## 运行与复验

从仓库根目录运行。下例使用随包夹具，可将第一项路径换成论文主文件，`OUT` 换成该篇的输出目录。使用 XeLaTeX 的论文可在第一条命令末尾加 `--tex-engine xelatex`。

```sh
sh pipeline/scripts/run_pipeline.sh "pipeline/tests/fixtures/paper.tex" "OUT"
python -X utf8 pipeline/review.py "OUT" --open
```

1. 在审核台打开「空白条目标签（必办）」。有正式标签或有效默认建议的条目在「条目标签确认（可选）」中，两个目录都可按类别展开。
2. 人工确认记为 `manual`。配置模型后可判断当前条目、全篇未确认必办项或全部未确认项，有效答案记为 `model`。网页会保留模型与规则的分歧，最终值采用模型答案。
3. 点击「应用并复验」更新输出。同稿件、同输出目录、条目锚点匹配时继续采用已确认值。可在网页改判或撤销后再复验；备份审核工作时保留整个输出目录。

| 管线退出码 | 含义及下一步 |
|---|---|
| `0` | 严格验收通过，可读取交付文件；可选意见仍可复核。 |
| `3` | 有必办项，在审核台查看所缺的内容、判断或执行证据。 |
| `1` | 处理或验收失败，查看终端阶段、`output_checks.txt` 和 `issues.json`。 |
| `2` | 检查命令参数、输入路径、引擎及 `pdftotext`。单独运行验收器时，此码也表示产物缺失或无效。 |

只复查已有切分产物可运行 `python -X utf8 scripts/verify_output.py OUT --strict`。只做基础切分可使用 `sh run.sh PAPER.tex OUT --tex-engine pdflatex`；此入口须显式指定引擎才会编译。完整管线还会检查本篇词表、角色与审核队列。

## 模型配置

首次使用时，复制 [配置模板](pipeline/model_config.example.json) 为 `pipeline/model_config.json`。若使用其他路径，在启动审核台的终端设置 `NL_PREP_MODEL_CONFIG`。私有配置已加入 Git 忽略规则。

| 字段 | 填写方式 |
|---|---|
| `backend` | 本地 OpenAI 兼容服务用 `local-openai`，远端兼容服务用 `api-openai`，Anthropic Messages 用 `api-anthropic`。 |
| `base_url`、`model` | 填接口根地址与服务公布的模型名。模板示例为 `http://127.0.0.1:8080/v1` 和 `qwen3.5-9b`；Anthropic 填主机根地址。 |
| `api_key_env` 或 `api_key` | 前者填写保存密钥的环境变量名，后者填写密钥。免密本地服务可留空。 |
| `max_tokens`、`temperature`、`timeout` | 回复 token 上限、采样温度、请求超时秒数。模板默认值依次为 `768`、`0`、`180`。 |

在 Git Bash 中可用 `cp pipeline/model_config.example.json pipeline/model_config.json` 创建配置。编辑后运行 `python -X utf8 pipeline/scripts/pending_names.py --check-config` 检查参数，再从网页发起单条判断验证连接。

## 规则、问题与历史

[切分及验收规则](RULES.md) · [已知问题与覆盖缺口](OPEN.md) · [11/23 修复历史](REPAIR-2026-11-23.md) · [11/24 修复历史](REPAIR-2026-11-24.md)

[示例输出](out/example/2005.01702/) 来自 Alex Weekes 的 *Quiver gauge theories and symplectic singularities*（*Advances in Mathematics*, 2022）。示例中的论文文字版权归原作者，仓库的 MIT 许可适用于代码。[代码许可](LICENSE) · [arXiv 原文](https://arxiv.org/abs/2005.01702) · [DOI](https://doi.org/10.1016/j.aim.2022.108185)

示例包含 362 句、795 条公式及引用记录。运行 `python scripts/sentences_md.py --run out/example/2005.01702 -o OUT/sentences.md` 可生成逐句对照。
