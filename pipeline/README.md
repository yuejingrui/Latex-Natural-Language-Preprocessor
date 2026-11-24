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

## 2. 链上有哪几步

| 步 | 脚本 | 输入 → 输出 |
|--:|:--|:--|
| 1 | `scripts/normalize.py` | `.tex` → `normalized.tex` |
| 2 | `scripts/latex2nl.py` | 句子流 + 公式表 + 题目文件 |
| 3 | `pipeline/scripts/lexicon.py` | 并置构造的读法（`D\cdot o` 是乘法还是调用）→ `paper_lexicon.json` |
| 4 | `pipeline/scripts/roles.py` | 定义句、假设句里公式的角色 → `roles.json` |
| 5 | `pipeline/scripts/ask_*.py` | 把题目交给模型，写回答案文件 |
| 6 | 回到 2、3 重跑 | 答案在这一步才生效 |
| 7 | `pipeline/scripts/verify_formulas.py` | 公式表有没有丢东西 |

第 2、3 步在问答前后各跑一次，这是设计：**答案不改输出，改的是输入的判断依据**。

## 3. 四类题目

| 题目 | 出题 | 答案文件 | 允许的答案 |
|:--|:--|:--|:--|
| 句点这里断不断 | `latex2nl` | `sentence_answers.json` | `split` / `join` |
| 公式里 `\text{…}` 的角色 | `latex2nl` | `slot_answers.json` | 13 类角色 |
| 并置构造的读法 | `lexicon` | `construction_answers.json` | `multiply` / `apply` / `label` / `index` |
| 定义句里公式的角色 | `roles` | `role_todo.json` | `constraint` / `proposition` / `unsure` |

题目文件的外形、答案的校验规则、没答时的默认值，见切分包 `README.md` 的附录 A。

## 4. 接自己的模型

**这一层不含任何厂商地址**。要提问，先配端点：

```sh
export NL_PREP_API_BASE=https://your-host/v1     # OpenAI 兼容
export NL_PREP_API_KEY=...                       # 你的 key
export NL_PREP_API_MODEL=your-model              # 可选，默认 qwen3.7-flash

python3 pipeline/scripts/pending_names.py --check-config   # 核对，key 打码
```

`NL_PREP_BACKEND` 可选 `api-openai`（默认）、`api-anthropic`、`local-openai`；本地服务
（Ollama 等）走 `local-openai`，默认 `http://127.0.0.1:11434/v1`，不需要 key。

**先看题、不花钱**：每个提问器都支持 `--dry-run` 和 `--limit N`：

```sh
python3 pipeline/scripts/ask_lexicon.py OUT/construction_todo.json --dry-run
python3 pipeline/scripts/ask_lexicon.py OUT/construction_todo.json --limit 5
```

答案会记 `source`、`model`、`asked_at`，答错了能查到出处；换模型重问时已经答过的题不会重问，
删掉答案文件才会。

## 5. 历史答案

`pipeline/data/` 下两份表是累积的答案，跑的时候会自动合并：

| 文件 | 内容 |
|:--|:--|
| `judgements.json` | 383 条并置构造的读法，主要来自模型（`source: llm`），未经人工逐条校验 |
| `judgements_roles.json` | 125 条公式角色，90 条由助手给出、35 条由人给出 |

两份表只记**答案与题目身份**，不含论文原句：`judgements_roles.json` 的 `sentence` 字段在
公开前已移除，答题时的上下文不再随包分发。

它们不是标准答案，是**起点**：换领域时按 `judgements.json` 里 `question` / `answer` 的形状
补自己的表，或直接删掉让模型重新答。

## 6. 这一层不做什么

- **不做句法解析**：DepCCG / SPRING 是外部模型，不随包发布；
- **不碰语料**：批量跑（`run_corpus` 那类）依赖论文语料，不在公开包内；
- **不判断数学对错**：只给每个公式一个位置标签，判断留给下游。
