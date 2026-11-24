# 管线层：切分之后的三步

`pipeline/` 是切分包的下一层。切分包把一篇 `.tex` 变成句子流和公式表；这一层把句子流变成
**能交给逻辑检查的东西**：每个公式的并置读法、每个公式在句中的角色，以及四类可以问人/问模型的问题。

```
scripts/          切分层（本包的另一半）：normalize / latex2nl / verify_output
pipeline/scripts/ 这一层：lexicon / roles / ask_* / human / verify_formulas
pipeline/data/    这一层的历史答案（judgements.json、judgements_roles.json）
data/             两个层共用的规则表（名称表、槽角色表、缩写表……）
```

## 1. 一条命令

```sh
sh pipeline/scripts/run_pipeline_llm.sh PAPER.tex OUT          # 含提问
sh pipeline/scripts/run_pipeline_llm.sh PAPER.tex OUT --no-ask # 只跑离线部分
sh pipeline/scripts/run_pipeline.sh     PAPER.tex OUT          # 同上，输出更精简
```

退出码沿用切分层的三级：**0 干净 / 1 失败 / 3 有待答**。

两个脚本最后都调用 `scripts/verify_output.py --strict`，所以“跑完”与“能交付”是同一件事。

两个入口使用 `--no-ask` 时都会计算词表和公式角色。尚有角色问题时生成 `role_todo.json`，返回 3。

### 原稿、TeX 引擎与论文身份

两个入口均以**调用者的当前目录**解析论文和输出路径，再使用绝对路径运行各阶段。默认依次查找 `pdflatex`、`xelatex`、`lualatex`；可以用 `--tex-engine xelatex` 等选项指定论文实际使用的引擎。引擎需要 `pdftotext`。指定的引擎缺失或已找到引擎但缺少 `pdftotext` 时退出 `2`；编译或执行回放失败时保留原文、进入待办。只有自动查找不到任何引擎时才走静态路径。静态路径遇到依赖章节选择或子文件提前结束的原稿，会保留原文并返回 `3`。

`normalize-stats.json` 的 `source_sha256` 记录各本地输入的指纹。`paper_id` 综合原始主文件的绝对路径、输入相对路径和各文件的 SHA-256，供归一化、切分、词表、名称与角色问答共用。移机、移动原稿或修改输入会产生新身份；同名文件、旧版本、仅以文件名登记的历史答案都不能自动覆盖当前稿件。角色答案还要匹配当前句子与上下文。可在 `role_todo.json` 中填写角色决定后原目录重跑。`unsure` 或累计两次无答复会保留待办并转交 `human_todo.json`。

### 公式判断、核对与验收

文字槽按出现位置保存。对同一条公式，模型一次收到完整 LaTeX、带 ID 的目标和所在句，每个 ID 单独作答。例如在两个操作数之间的 `outranks` 可以记为 `predicate`，在 `R_{\text{outranks}}` 的下标中可以记为 `label`。同词出现两次也有两个位置。`slot_answers.json` 只在对应稿件和位置生效。

并置按公式 ID 和公式内字符偏移保存。`paper_lexicon.json` 的每条构造记录含 `shape`、`location`、`anchor` 和 `reading`。词形重复时键名附带位置。写明函数取值的 `f(x)` 与另一个例子中明确定义为标量乘积的 `f(x)` 可分别保留 `apply` 和 `multiply`。孤立括号、参数个数和建议默认值均不能独立决定读法。否定句、疑问句及带条件的乘积说明保留为待判断。

两个入口用 `slot_questions.json`、`construction_questions.json`、`role_questions.json` 提问，同时导出各自的 `*_todo.json` 供处理必答项。未决位置阻止整篇通过。已有明确规则的项目在离线运行时即可结案。模型返回相同答案记为 `confirmed`，返回不同答案记为 `disagreement`，终端打印 `WARNING`，最终结果取规则值。角色题回答 `unsure` 时，已有规则的位置记为 `undecided`。`rule_reviews.json` 列出实际核对的位置和双方答案，警告本身不改变退出码。例如 `for each` 直接限定的成员关系取 `constraint`。仅有 `Let` 和等号时仍可以由模型判断。

同一次出现的两字裸词和字母并置若收到互斥答案，又没有明确规则，会同时回到待办。公式验收按原记录检查操作符、操作数、数字及其顺序，并核对提取出的文字槽、运算符名称和附加条件。

在代码包根目录可运行 `python -X utf8 pipeline/tests/test_occurrence_decisions.py`。这组测试使用固定回答，覆盖逐位置合并、漏答、重复 ID、同一展示公式拆成多条记录、局部运算符和规则冲突。要检查一次真实模型运行，在输出目录打开 `formulas.json` 的 `text_slots`、`paper_lexicon.json` 的 `constructions`、`roles.json` 的 `roles`，再按 ID 或偏移对照 `rule_reviews.json`。

## 2. 链上有哪几步

| 步 | 脚本 | 输入 → 输出 |
|--:|:--|:--|
| 1 | `scripts/normalize.py` | `.tex` → `normalized.tex` |
| 2 | `scripts/latex2nl.py` | 句子流 + 公式表 + 题目文件 |
| 3 | `pipeline/scripts/lexicon.py` | 并置构造的读法（`D\cdot o` 是乘法还是调用）→ `paper_lexicon.json` |
| 4 | `pipeline/scripts/roles.py` | 定义句、假设句里公式的角色 → `roles.json` |
| 5 | `pipeline/scripts/ask_*.py` | 把题目交给模型，写回答案文件 |
| 6 | 回到 2、3 重跑 | 答案在这一步才生效 |
| 7 | `pipeline/scripts/verify_formulas.py` | 核对数字、控制序列、关系符及锚点；例如把 `x<y` 改成 `x>y` 会返回 1 |

第 2、3 步在问答前后各跑一次，这是设计：**答案不改输出，改的是输入的判断依据**。

## 3. 四类题目

| 题目 | 出题 | 答案文件 | 允许的答案 |
|:--|:--|:--|:--|
| 句点这里断不断 | `latex2nl` | `sentence_answers.json` | `split` / `join` |
| 公式里 `\text{…}` 的角色 | `latex2nl` | `slot_answers.json` | 13 类角色 |
| 并置构造的读法 | `lexicon` | `construction_answers.json` | `multiply` / `apply` / `label` / `index` |
| 定义句里公式的角色 | `roles` | `role_questions.json` | `constraint` / `proposition` / `unsure` |

题目文件的外形、答案的校验规则、没答时的默认值，见切分包 `README.md` 的附录 A。

## 4. 接自己的模型

两个入口和五类提问器共用一份 UTF-8 JSON 配置。首次使用，在代码包根目录复制模板，再按服务的实际设置编辑。

```sh
cp pipeline/model_config.example.json pipeline/model_config.json
python3 pipeline/scripts/pending_names.py --check-config
sh pipeline/scripts/run_pipeline_llm.sh paper.tex out --model-config pipeline/model_config.json --limit 5
```

```json
{
  "backend": "local-openai",
  "base_url": "http://127.0.0.1:8080/v1",
  "model": "qwen3.5-9b",
  "api_key": "",
  "max_tokens": 768,
  "temperature": 0,
  "timeout": 180
}
```

| 设置 | 填写方式 |
|---|---|
| 本机模型 | 启动推理服务，填写其 OpenAI 兼容地址和服务公布的模型名。免密服务可将 `api_key` 留空。 |
| 云端 OpenAI 兼容服务 | 选 `api-openai`，`base_url` 填服务的 API 根地址（通常以 `/v1` 结尾）。 |
| Anthropic Messages 协议 | 选 `api-anthropic`，填写主机根地址。客户端追加 `/v1/messages`。 |
| 认证 | 云端服务填 `api_key`，或用 `"api_key_env": "MY_MODEL_TOKEN"` 指定存放密钥的环境变量。两种方式选其一。私有配置已列入 Git 忽略规则。 |
| 生成参数 | `max_tokens` 为每个回复的输出上限，`timeout` 单位为秒。二者为正整数。模板为本机 4096 token 上下文预留了 768 个输出 token。 |

`--model-config FILE` 按调用目录解析路径。独立执行 Python 提问器时，可用 `NL_PREP_MODEL_CONFIG` 指定同一个文件。在 PowerShell 中可用 `$env:NL_PREP_MODEL_CONFIG = (Resolve-Path 'pipeline/model_config.json').Path`。显式指定文件具有最高优先级。默认读取 `pipeline/model_config.json`。显式选择旧式 `--backend` 或在进程环境设置 `NL_PREP_BACKEND` 会选用原环境变量方案。没有私有文件时也兼容环境变量和 `.env`。

`--check-config` 检查地址、模型和所需凭据，只显示密钥是否已设置。实际请求可验证连接和模型名。分步入口 `--dry-run` 可预览题目；`--no-ask` 完全离线。`--limit N` 按问题类别限量，接受 1 至 2147483647 的整数，省略时处理全部。角色回答 `unsure` 或名称回答 `unknown` 时，相关问题会保留并转交人工。

## 5. 历史答案

`pipeline/data/` 的两份表按论文匹配历史答案。并置答案使用条目中的 `paper` 或文件的 `meta.paper`；旧库中明确写有 `judged on <论文标识>` 的条目也能匹配。没有论文归属的历史读法留待确认。论文自己的明确声明优先于历史答案。管线也记录本地原稿路径，两篇都叫 `main.tex` 的稿件即使使用同一输出目录，也能区分其并置答案。

| 文件 | 使用条件 |
|---|---|
| `judgements.json` | 383 条并置读法。历史条目的论文标识须与当前标识匹配；原稿中的明确声明优先。 |
| `judgements_roles.json` | 125 条角色答案，来自 21 篇论文。按论文标识、公式编号和公式文本核对；旧库中的文件名标识可由持有原稿的人核对后迁移。 |

## 6. 这一层不做什么

- **不做句法解析**：DepCCG / SPRING 是外部模型，不随包发布；
- **不碰语料**：批量跑（`run_corpus` 那类）依赖论文语料，不在公开包内；
- **不判断数学对错**：只给每个公式一个位置标签，判断留给下游。
