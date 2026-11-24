# 切分层：LaTeX → 句子流 + 公式表

2026-11-23 版支持按源码顺序静态处理论文，也可以用论文指定的 TeX 引擎观测选支、读文件和钩子。数学边界明确时保留未知公式及其参数。缺失的正文定义、未决分支和未切分的内容写入待办。各轮修复、反例与最终验证见 [11 月 23 日修复汇总](REPAIR-2026-11-23.md)。

这一包只做**切分**：把一篇数学论文的 `.tex` 变成两样东西——

- **句子流**：一句一行，公式所在位置换成占位符 `MATHn`（公式）或 `REFn`（引用）；
- **公式表**：每个公式一条记录，带原文、展开后的文本、类型、初判的角色和上下文。

它是 nlprep 管线的前两段。下游的词表（`lexicon`）、公式角色（`roles`）、句法解析（DepCCG/SPRING）
都以这一层的输出为输入。

**本仓库还带有下一层**：[pipeline/README.md](pipeline/README.md) 把句子流接下去——并置构造的读法、
公式在句中的角色、四类问答题，以及两个入口脚本（`run_pipeline.sh` / `run_pipeline_llm.sh`）。
不接这一层也能单独用切分；接上就能从 `.tex` 一路跑到"每条公式都有角色标签"。
句法解析（DepCCG/SPRING）仍是外部模型，不随包发布。

这一层**不做**三件事：不恢复排版；不理解数学（不判断公式的数学含义）；**切分本身不调用模型**——
判不准的地方写成题目文件，由 §7 的问答流程交给模型或人回答，答案再合并回来。

**怎么读这份文档**

| 想做什么 | 看哪里 |
|:--|:--|
| 先跑起来 | §2 |
| 看一个句子是怎么被切出来的 | §3 |
| 查某个输出字段是什么意思 | §4 |
| 了解某个脚本内部做了什么、有哪些选项 | §5 |
| 接入大模型 | §7 |
| 看现在的质量数字和已知问题 | §11、§13 |

---

## 1. 在整条管线中的位置

```
 paper.tex
    │
    ▼  normalize.py      注释、宏展开、条件分支、环境归成五类、去排版；固定点
 normalized.tex
    │
    ▼  latex2nl.py       段落 → 单元 → 公式 span → 占位符 → 句子 → 词元
 ┌──────────────┼──────────────────────────┐
 ▼              ▼                          ▼
sentences.jsonl formulas.json     sentence_todo.json / slot_todo.json
 一句一行        每个公式一条            判不准的句界 / 公式里短语的角色
    │              │                          │
    │              │                          ▼  ask_boundaries.py / ask_slots.py（大模型，闭集答案）
    │              │                     sentence_answers.json / slot_answers.json
    │              │                          │
    │              │◄─────────────────────────┘  latex2nl.py --merge-*-answers 重跑一遍
    │              │
    ▼              ▼
 DepCCG/SPRING    公式解析器
 句法树（每句一棵）  公式树（每式一棵）
    └────► 按 MATHn 把公式树接回句法树 ◄────┘

 verify_output.py  对产物做 C1–C16 检查，汇总账本 issues.json
```

切分决定了下游的"输入单位"：句子切错，解析器拿到的是半句话；公式被卷进正文，下游就少一棵树；
占位符对不上，树就接不回去。所以这一层的产出是**句界、公式边界、占位符**三件事。

---

## 2. 快速开始

宏定义体中的文末标记不会截断后续正文。使用执行观测时，可定位的公式条件会按 TeX 实际取支写入公式表。例如 `\edef\enable{\noexpand\drafttrue}\enable` 启用草稿后，`$\ifdraft x\else y\fi$` 的记录为 `x`。两个原始反例、静态作用域及公式待办的测试命令见 [修复记录](REPAIR-2026-11-23.md#未调用的文末宏与公式选支)。

### 用论文的实际 TeX 构建确定有效正文

原稿使用条件、分章或文末钩子时，在读取**原始主文件**的命令上加 `--tex-engine pdflatex`。项目使用 XeLaTeX 或 LuaLaTeX 时填 `xelatex` 或 `lualatex`。所选引擎及 `pdftotext` 需要能从命令行运行。程序会在两个临时项目副本中执行论文源码，适用于可以在本机执行的项目。

在代码包根目录用 PowerShell 运行仓库附带的三文件样例。

```powershell
$run = Join-Path $env:TEMP 'nlprep-tex-demo'
python -X utf8 scripts/normalize.py tests/fixtures/tex_execution/main.tex --tex-engine pdflatex -o "$run/normalized.tex" --stats "$run/normalize-stats.json" --issues "$run/issues_normalize.json"
python -X utf8 scripts/latex2nl.py "$run/normalized.tex" --canonical -o $run --issues "$run/issues_latex2nl.json"
python -X utf8 scripts/verify_output.py $run --strict
$LASTEXITCODE
Get-Content "$run/sentences.txt" -Encoding utf8
$trace = Get-Content "$run/execution.json" -Raw -Encoding utf8 | ConvertFrom-Json
$trace.events | Format-Table
```

本例的严格退出码为 0，句子依次为 `Draft claim.`、`Included result for MATH1.`、`Final conclusion.`。在 `formulas.json` 中，`MATH1.raw` 为 `x^2`。直接切分也接受该选项，例如 `python -X utf8 scripts/latex2nl.py tests/fixtures/tex_execution/main.tex --tex-engine pdflatex -o "$run-direct"`。

`execution.json` 的 `sites` 给出站点编号、相对文件名、行号和源码偏移，`events` 按执行顺序引用这些编号。`status` 为 `observed` 表示已回放本次跟踪的站点，`partial` 表示未观测的局部条件或整条公式进入待办，`pending` 表示此次构建或回放无法确定有效正文。后两种情况在切分后的 `content_todo.json` 中可查原文。整篇切分是否通过以 `verify_output.py --strict` 为准。

未指定引擎时走静态切分。若原稿依赖 `\includeonly` 排除章节或 `\endinput` 截断子文件，应指定实际 TeX 引擎。最终代码的静态反例分别带入了被排除的章节和结束位置之后的赋值，两次都错误返回 0。见 [当前限制](OPEN.md)。如需切换草稿和定稿，请使用论文实际采用的入口文件或源码配置。`--switch`、额外的 `--style` 和 `--no-inputs` 属于静态模式。`--canonical` 读取上一阶段生成的文件。各版本的产物应分别写入独立运行目录。

### 2.1 不接模型（只做切分）

```sh
# 在含 scripts/ 和 tests/ 的代码包根目录运行
RUN=/tmp/run && mkdir -p $RUN

# 1. 归一化
python3 scripts/normalize.py PAPER.tex -o $RUN/normalized.tex \
        --stats $RUN/normalize-stats.json --issues $RUN/issues_normalize.json

# 2. 切分
python3 scripts/latex2nl.py $RUN/normalized.tex -o $RUN --canonical \
        --issues $RUN/issues_latex2nl.json

# 3. 严格检查：0 无硬错误/待办；1 硬错误；2 产物损坏；3 待处理
python3 scripts/verify_output.py $RUN --strict

# 4. 对一批跑过的目录出"无句末标点"分类清单
python3 scripts/split_audit.py --run '/tmp/runs/*' -o out/split-audit-baseline.md
```

不接模型时，判不准的句界按默认（`split`）处理，公式里的短语角色留空（`unclassified`），
题目留在 `sentence_todo.json`、`slot_todo.json` 里。句界待办阻止严格通过，未定文字槽角色进入下游诊断。

### 2.2 接模型（切分层的两类题）

需要 `scripts/pending_names.py`、`scripts/ask_boundaries.py`、`scripts/ask_slots.py`
三个文件（不在本包里，见 §7.11）和一个 API key（见 §7.4）。

```sh
# 5. 先看题目，不花钱
python3 pipeline/scripts/ask_boundaries.py $RUN/sentence_todo.json --dry-run
python3 pipeline/scripts/ask_slots.py      $RUN/slot_questions.json      --dry-run

# 6. 问模型，答案写到运行目录里
python3 pipeline/scripts/ask_boundaries.py $RUN/sentence_todo.json -o $RUN/sentence_answers.json
python3 pipeline/scripts/ask_slots.py      $RUN/slot_questions.json      -o $RUN/slot_answers.json

# 7. 使用本篇的位置答案重跑切分
python3 scripts/latex2nl.py $RUN/normalized.tex -o $RUN --canonical \
        --issues $RUN/issues_latex2nl.json \
        --merge-sentence-answers $RUN/sentence_answers.json \
        --merge-slot-answers $RUN/slot_answers.json
```

---

切分后可在代码包根目录运行 `python -X utf8 pipeline/review.py "OUT" --open`。所有条目先分入「空白条目标签」（必办）或「条目标签确认」（可选），再按正文句界、并置读法、验收与警告等原有类别查看。有默认建议或正式标签的可选检查，完全空白与明确冲突的标签仍须处理。可由人工确认，也可点击“模型审核”判断当前条目或批量判断。审核台以模型给出的有效答案作为最终值，记录模型名以及与规则的分歧。同一输出目录重跑同稿件时保留 `manual` 和 `model` 决定，均可撤销。详见[网页人工审核](pipeline/README.md#网页人工审核)。

## 3. 一个句子走一遍（danmark 的 s13）

**源文件**（`danmark.tex:508`）：

```latex
When $\on{dim} Y=1$, Katz proved Simpson's conjecture by describing rigid local
systems inductively (\cite[Thm. 5.2.1, 8.4.1]{katz2016rigid}). For higher-...
```

**归一化之后**：论文自定义的 `\on` 被展开成 `\operatorname`，其余不变：

```latex
When $\operatorname{dim} Y=1$, Katz proved Simpson's conjecture by describing ...
```

**切分之后**，`sentences.jsonl` 里的一行：

```json
{"sid": "s13", "unit": "prose#6", "env": "prose",
 "section": "Introduction > Motivic local systems > ", "title": null,
 "text": "When MATH17, Katz proved Simpson's conjecture by describing rigid local systems inductively (REF3).",
 "tokens": ["When", "MATH17", ",", "Katz", "proved", "Simpson", "'s", "conjecture", "by",
            "describing", "rigid", "local", "systems", "inductively", "(", "REF3", ")", "."],
 "words": 18, "speech_act": "assertion", "formulas": ["MATH17", "REF3"], "flags": []}
```

`formulas.json` 里对应的两条：

```json
{"id": "MATH17", "kind": "inline", "raw": "\\operatorname{dim} Y=1",
 "expanded": "\\operatorname{dim} Y=1", "role": "proposition",
 "top_relation": {"symbol": "=", "lhs": "\\operatorname{dim} Y", "rhs": "1"},
 "unit": "prose#6", "context_before": "When",
 "context_after": ", Katz proved Simpson's conjecture by de",
 "label": null, "affix": null, "trailing_punct": "", "side_conditions": [],
 "text_slots": [], "flags": []}

{"id": "REF3", "kind": "ref", "role": "term",
 "raw": "\\cite[Thm. 5.2.1, 8.4.1]{katz2016rigid}",
 "ref_kind": "cite", "ref_target": "katz2016rigid"}
```

要点：

- 公式顶层有关系符号 `=`，所以初判为 `proposition`；这只是初判，见 §12；
- `\cite` 也变成占位符 `REF3`，键 `katz2016rigid` 存在 `ref_target`，句子不会出现"see  for details"这种空洞；
- `tokens` 是 PTB 风格（`'s`、`(`、`.` 单独成词），直接喂给 DepCCG；
- 下游拿到 `MATH17` 的公式树后，按这个占位符接回句法树。

---

## 4. 输出文件逐个说明

一次运行（`latex2nl.py -o RUN`，加上 `normalize.py` 的产物）在 `RUN/` 下留下这些文件：

| 文件 | 由谁写 | 内容 |
|:--|:--|:--|
| `normalized.tex` | normalize | 规范化后的全文 |
| `normalize-stats.json` | normalize | 改写计数与报告 |
| `issues_normalize.json` | normalize | 指定 `--issues` 时写入的归一化账本 |
| `execution.json` | 启用 `--tex-engine` 的入口 | 构建、站点、事件、回放状态和原因 |
| `content_blocks.json` | latex2nl | 脚注、浮动体、未定条件等块的原文与父子句锚点 |
| `content_todo.json` / `conditional_todo.json` | latex2nl | 待处理内容及独立未定条件，有相应项目时生成 |
| `formula_diagnostics.json` | latex2nl | 公式内部未知命令和未定文字槽角色 |
| `split_status.json` | latex2nl | 句子、公式与内容块计数，切分待办与下游诊断 |
| `sentences.jsonl` | latex2nl | 每句一行的元数据（§4.1） |
| `sentences.txt` | latex2nl | 只有句子文本，一句一行 |
| `sentences.tok.txt` | latex2nl | 空格分隔的词元，一句一行，喂 DepCCG |
| `formulas.json` | latex2nl | 公式表（§4.2） |
| `unresolved.log` | latex2nl | 需要复核的项，一行一条（§4.3） |
| `sentence_todo.json` | latex2nl | 判不准的句界，题目文件（§7.3） |
| `slot_todo.json` | latex2nl | 公式里还没有角色的短语，题目文件（§7.3） |
| `slot_questions.json` | latex2nl | 按位置提交给模型的文字槽，含可选的规则核对 |
| `rule_reviews.json` | 两个 pipeline 入口 | 规则与模型的独立答案、定位、确认或分歧 |
| `issues_latex2nl.json` | latex2nl | 切分阶段的账本 |
| `issues.json` | verify_output | 合并后的账本和 C1–C16 结果（§4.5） |

### 4.1 `sentences.jsonl`

| 字段 | 含义 |
|:--|:--|
| `sid` | 句子编号 `s1`、`s2`…，按文档顺序，全篇唯一 |
| `text` | 句子文本，公式换成 `MATHn`，引用换成 `REFn` |
| `tokens` | 分词结果；`"".join(tokens)` 等于 `text` 去掉空白（无损） |
| `words` | 词元数 |
| `unit` | 所属单元：`prose#6`（正文第 6 段）、`proof#357`、`prose#74/item`（列表条目）… |
| `env` | 单元类型：`prose`、`proof`、`item`，或定理类名（`lemma`、`theorem`、`remark`…） |
| `section` | 节标题路径，用 ` > ` 连接 |
| `title` | 定理类环境的可选标题（`\begin{thm}[标题]`） |
| `formulas` | 本句里出现的占位符，按出现顺序 |
| `speech_act` | 句子语气：`assertion`、`definition`、`assumption` |
| `flags` | 句子级标记，如 `too_long_for_spring`（超过 `--max-words`） |
| `part` | 只在单元里有 `\step` 这类部分标记时出现：`{"kind": "step", "index": 1}` |

danmark 的分布：`env` 里 `prose` 856、`proof` 299、`remark` 66、`item` 35、`lemma` 34、`theorem` 33；
`speech_act` 里 `assertion` 1,131、`definition` 207、`assumption` 15。

### 4.2 `formulas.json`

一个 JSON 数组，每个公式一条：

| 字段 | 含义 |
|:--|:--|
| `id` | `MATHn` 或 `REFn`，与句子里的占位符一致 |
| `kind` | `inline`（行内）、`display`（行间）、`ref`（`\ref`/`\eqref`/`\cite`） |
| `raw` | 切出来的原文（规范化之后） |
| `expanded` | 去掉 `\label`、尾部标点、侧条件后的公式体；`\text{…}` 换成 `<TXTn>` |
| `role` | 初判角色（下表） |
| `top_relation` | 顶层关系：`{"symbol", "lhs", "rhs"}`，没有关系符号时为 `null` |
| `label`、`labels` | `label` 保留首个标签（仅有 `\tag` 时保留显示编号）。`labels` 列出可供引用的公式标签 |
| `rows` | 对齐公式按行保存展开式与关系；逐行编号环境另存每行的标签。多条独立关系的 `top_relation` 为空 |
| `ref_destinations` | 指向公式的引用给出 `label`、`formula`；引用独立编号的某一行时另有从 1 开始的 `row` |
| `logical_relations`、`structure_reviews` | 有可审核读法时保留当前有效关系、自动参考值、确认来源和原始排版行号。人工确认后单关系同步到 `top_relation` |
| `display_tags`、`resolved_references` | 显式显示编号及其排版行，以及正文或公式内引用的有效目标列表。组引用可以指向多条子公式 |
| `trailing_punct` | 从公式末尾剥下来的句末标点 |
| `side_conditions` | 分出来的侧条件，如 `\quad(1\le i\le d)` 里的 `(1\le i\le d)` |
| `text_slots` | 公式里每个 `\text{…}` 短语：`id`、`raw`、`role`、前后文 |
| `affix` | 粘在公式后的词缀（`$k$-module` 里的 `-module`） |
| `orphan` | 所在句子被过滤掉时为真（公式仍保留在表里） |
| `unit`、`env` | 所属单元与环境 |
| `context_before`、`context_after`、`prev_sentence` | 上下文 |
| `ref_kind`、`ref_target` | 引用类别与目标。`content` 指向 `content_blocks.json` 中的块 ID |
| `flags` | 公式级标记（下表） |

完整保留的公式使用 `opaque=true` 和 `role="opaque"`。`expanded` 保留公式体和分组，仅分离适用的末尾标点。下表为其余公式的启发式角色及 danmark 历史计数。

| 角色 | 含义 | danmark |
|:--|:--|--:|
| `term` | 名词性的项（`G`、`\mathcal{O}_X`） | 2,202 |
| `proposition` | 一个断言（`x=0`、`A\subset B`） | 429 |
| `modifier` | 修饰语（`$k$-linear` 这类粘连） | 156 |
| `constraint` | 条件（`Let $n\ge 2$` 里的 `n\ge 2`） | 58 |
| `sequence` | 序列（`a_1,\dots,a_n`） | 4 |
| `piecewise` | 分段函数（`cases`） | — |

常见 `flags`（danmark 计数）：`hyphen_affix` 156、`binder_operator` 64、`constraint_from_trigger` 58、
`relation_chain` 41、`unknown_cmd:\name` 34、`sentence_end_unclear` 21、`diagram` 20、
`text_slot_unclassified` 7、`unbalanced` 5、`list_syntax` 4、`mark_dropped` 1。

### 4.3 `unresolved.log`

切分阶段把未解决的句界等问题按位置写入日志。已知数学边界内遇到 `\accentset{\ast}{W}` 时，公式表保留完整操作数，未知命令列入 `formula_diagnostics.json`。早期运行中的 `unresolved.log` 可能混有公式解释问题，重新切分可以得到当前分类。

### 4.4 `normalize-stats.json`

| 键 | 内容 |
|:--|:--|
| `stats` | 每类改写的次数：`env_display:equation`、`name_ignore:vspace`、`definition_dropped:let`… |
| `unknown_envs` | 不认识的环境名及次数 |
| `outside_vocabulary` | 规范化后仍不在词汇里的命令名 |
| `kept_macros` | 因展开后会出现未知名字而保留宏名的论文宏，值是宏体 |
| `labels_unrendered` | 渲染不出的列表标签 |
| `input`、`input_chars`、`output_chars` | 输入文件与前后字数 |

### 4.5 `issues.json`（账本）

| 键 | 内容 |
|:--|:--|
| `counts` | 每个 kind 的条数 |
| `sizes` | 按 kind 汇总的涉及字符数，既可计删除量也可计保留或诊断范围 |
| `hard` | 合并账本中的硬错误数 |
| `status`、`pending` | 严格状态及阻止通过的待处理项目 |
| `scope`、`downstream_diagnostics` | 切分检查范围及独立的公式解释诊断 |
| `kinds` | 本次出现的 kind 及其 `gate`/`route` |
| `entries` | 逐条记录：`kind`、`site`（哪段代码）、`route`、`gate`、`where`、`size` |
| `checks` | C1–C16 每项的命中数 |

一条记录的样子：

```json
{"kind": "check:C5", "site": "verify_output.C5", "route": "check", "gate": "soft",
 "where": "s617 after 'ck parametrizing triples MATH1131, where'", "size": 1}
```

---

## 5. 本包脚本

| 脚本 | 职责 |
|---|---|
| `normalize.py`、`latex2nl.py` | 归一化、切句、公式记录和内容锚点 |
| `conditionals.py`、`format_names.py` | 维护源码顺序、局部状态和宏绑定，查询标准格式中的名称 |
| `tex_execution.py` | 执行原稿和带标记的副本，回放生效源码 |
| `preserved.py` | 将须保留的内容封装进规范化文本，跨进程恢复原文 |
| `verify_output.py`、`issues.py` | 检查产物、关联和账本，给出严格退出状态 |
| `sentences_md.py`、`split_audit.py` | 导出逐句 Markdown，汇总一批运行的句末边界 |

### 5.1 `normalize.py`：归一化

**为什么需要它**：数学论文的写法是无界的（17 篇里就有 62 种环境名、16 种 display 写法、29 种列表写法），
但它们的**功能**是有限的。这个脚本把名字收拢成有限的词汇，并记下每一处改写，不悄悄猜。

**输入**：根 `.tex`。会自动内联 `\input`/`\include`，并读论文目录里的本地 `.sty`。

**按顺序做的事**：

| 步 | 做什么 | 说明 |
|--:|:--|:--|
| 1 | 去注释 | 只有注释的行整行去掉，不留空行（TeX 里这种行不产生输出）；`\%` 保留 |
| 2 | 执行声明和宏调用 | 普通宏按调用点的定义展开，分组退出时恢复局部状态。支持的 `\edef` 在定义时求值。分隔参数和专门声明由宏表处理 |
| 3 | 判条件、文件与钩子 | 静态读取开关、字面数值和受支持的比较。`--tex-engine` 利用当次执行事件选择正文与公式，覆盖章节排除、文件查找和可定位钩子。未决条件连同分支一起保存 |
| 4 | 去掉不带命题内容的机制 | 版式、尺寸与间距（连同操作数）、参考文献（含裸 `\thebibliography`）、浮动块完整保存为 pending，脚注另成单元并挂接正文、公式标签随展示式保留原位 |
| 5 | 环境归成五类 | 见下表；文本块拆壳；论文用 `\newtheorem` 声明的类按定理处理；未知环境保留并报告 |
| 6 | 同形改写与去排版 | `\le`→`\leq` 这类同一字形的不同拼写合并；`\left`/`\bigl` 等尺寸、细空格去掉；**字形不同的不合并**（`\epsilon`/`\varepsilon`、`\subset`/`\subseteq`） |
| 7 | 名字表 | 命令按 `tex_names.json`（TeX 自己说的）、`name_overrides.json`（人工更正）、`name_answers.json`（问答得到的答案，可按篇）决定 `allow`/`font`/`unwrap`/`ignore`/`expand` |
| 8 | 固定点检查 | 输出再跑一遍应不变；`--check` 时核对固定点并报告词表统计 |

五类规范形式：

| 规范形式 | 收进来的写法 |
|:--|:--|
| `\begin{statement}{<kind>}` | theorem、lemma、definition、proof…（含论文 `\newtheorem` 声明的） |
| `\begin{display}` | `$$…$$`、`\[…\]`、`equation`、`align`、`gather`、`multline` 等所有行间公式 |
| `\begin{grid}{<kind>}` | `cases`、`array`、`matrix`、`smallmatrix`、`subarray` |
| `\begin{list}{<kind>}` | `enumerate`/`itemize`/`description`，每个 `\item` 带渲染好的标签 |
| `\begin{diagram}{<kind>}` | `tikzcd`、`tikzpicture`、`xymatrix`、texdraw 等画图，内容不动 |

**不做**：不改写数学、不求值、不重排参数；渲染不出的列表标签记进 `labels_unrendered`，不猜。

| 选项 | 作用 |
|:--|:--|
| `-o FILE` | 规范化结果 |
| `--stats FILE` | 改写计数与报告（§4.4） |
| `--issues FILE` | 本段账本 |
| `--tex-engine ENGINE` | 对原始主文件执行 pdfLaTeX、XeLaTeX 或 LuaLaTeX，按实际事件回放 |
| `--switch ifNAME=true/false` | 设置静态开关初值，后续赋值按源码顺序生效 |
| `--style FILE` | 额外读一个 `.sty` 的宏（可重复） |
| `--no-inputs` | 不内联 `\input`/`\include` |
| `--paper-id ID` | 读 `data/name_answers.json` 里这篇自己的名字答案（同名在不同论文可含义不同） |
| `--check` | 检查再次归一化后文本是否相同 |
| `--self-test` | 内置用例 |

### 5.2 `latex2nl.py`：切分

**内部流程**：

| 步 | 做什么 | 关键规则 |
|--:|:--|:--|
| 1 | 准备正文及附属内容 | 读取导言区定义和静态状态或执行回放结果。脚注和浮动体建立正文锚点，数学重音连同其参数保留 |
| 2 | 切单元 | `statement`、`list`、每个 `\item` 各成单元；正文按空行分段；display 前的空行只在前文有句末标点时才算分段；display 后小写续写时并回；display 和 diagram 内部的空行不算分段 |
| 3 | 找公式 span | `$…$`、`\(…\)`、`math` 环境、`\ensuremath{…}`、`$$…$$`、`\[…\]`、`display`/`grid`/`diagram`；扫 `$` 时跳过组内的 `$`；未闭合的 `$$`/`\[` 把余下部分当 display 并记 `math:unclosed` |
| 4 | 生成公式记录 | 数学边界内的末尾 `!` 留在公式中。适用的 `.,;:?` 可以分离。已知公式可提取文字槽、侧条件或按顶层 `\qquad` 拆分。`opaque` 公式保留分组和构造，包括 `\mathchoice` 的四个分支 |
| 5 | 写占位符 | 公式在句中留下 `MATHn`；相邻占位符之间补空格；粘在后面的词缀（`-module`）记进 `affix` |
| 6 | 切句子 | 句末 `.?!:;`；例外：`e.g.`、`i.e.`、`abbreviations.json` 里的缩写、两个以上连写的首字母、公式内的标点；句点后面是小写词时**仍然断开**，但写进 `sentence_todo.json`（§7） |
| 7 | display 结尾判句界 | 看 display 后面的第一个字符：大写断句、小写续句；看不出时按公式自带的标点，再不行记 `sentence_end_unclear` |
| 8 | 过滤与分词 | 仅丢弃无字母数字、无占位符的标点空片段；保留纯公式与 Unicode 文字，生成 `tokens` |
| 9 | 初判公式角色 | 见 §4.2 的角色表 |
| 10 | 判句子语气 | 看句首第一个词（或 `We` 之后的词）：`let`/`define`/`denote`/`call`/`choose`/`put`/`set`/`write` → `definition`；`assume`/`suppose` → `assumption`；其余 → `assertion` |

| 选项 | 作用 |
|:--|:--|
| `--canonical` | 读取 `normalize.py` 已生成的规范化文本。原始 `.tex` 可直接切分，也可指定 `--tex-engine` |
| `-o DIR` | 输出目录（默认 `nlprep_out`） |
| `--issues FILE` | 账本 |
| `--paper-id ID` | 读这篇的名字答案 |
| `--merge-sentence-answers FILE` | 合并句界答案（§7.7） |
| `--merge-slot-answers FILE` | 按本篇的文字槽 ID 和位置指纹读取答案 |
| `--slot-table FILE` | 短语 → 角色表的位置 |
| `--sentence-todo FILE` / `--slot-todo FILE` | 题目文件写到别处（默认在 `-o` 目录里） |
| `--proposition-form {bare,holds}` | 命题类占位符写成 `MATH7` 还是 `MATH7 holds` |
| `--max-words N` | 超过 N 词的句子标 `too_long_for_spring`（SPRING 会丢弃 100 词以上的输入；默认 90） |
| `--tok-mode {ptb,space}` / `--no-tok` | 词元流的切法 / 不写词元流 |
| `--no-merge-display` / `--no-split-qquad` | 不把 display 并回句子 / 不按 `\qquad` 拆公式 |
| `--tex-engine ENGINE` | 对原始主文件执行 pdfLaTeX、XeLaTeX 或 LuaLaTeX，按实际事件回放 |
| `--switch ifNAME=true/false` | 设置静态开关初值，后续赋值按源码顺序生效 |
| `--style FILE` | 额外读一个 `.sty` |
| `--limit N` | 在标准输出打印前 N 句 |
| `--self-test` | 内置用例 |

### 5.3 `verify_output.py`：输出检查

`latex2nl` 不知道自己犯了哪些错；这个脚本从**输出的样子**去查。读一个运行目录里的
`sentences.jsonl`、`formulas.json`、`normalized.tex`，每处命中记进账本，并把同目录下的
`issues_*.json` 合并成 `issues.json`。

| 检查 | 闸门 | 查什么 |
|:--|:--|:--|
| C1 | 硬 | 孤立 display 的前一句不以 `.!?:` 结尾（引导句被切） |
| C2 | 硬 | 句子里（去掉占位符后）有控制字 `\name` |
| C3 | 硬 | 句子里有 `{` 或 `}` |
| C4 | 软 | 句子不超过 2 个词元且无句末标点 |
| C5 | 软 | 句子以 `MATH`/`REF` 开头且前一句无句末标点 |
| C6 | 硬 | 公式 `expanded` 里有 `$` 或 `\intertext` |
| C7 | 硬 | 检查行内及行间公式花括号的深度和顺序，跳过转义花括号 |
| C8 | 软 | `section`/`title` 里有 `$` |
| C9 | 硬 | 公式里有 4 个以上连续英文词（散文进了公式） |
| C10 | 硬 | `expanded` 里有 `grid`/`diagram` 以外的 `\begin{…}` |
| C11 | 软 | `grid{cases}` 的角色不是 piecewise |
| C12 | 硬 | 句中悬空的 `REFn}`，或引用目标的花括号失配 |
| C13 | 软 | 文内交叉引用缺少对应标签。文献引用及内容块锚点跳过此项 |
| C14 | 硬 | 词汇外的名字出现在句子里 |
| C16 | 软 | display 读起来像散文（3 个以上连续英文词） |

另外检查句子、公式、内容块和词元的关联，核对保留原文、待办状态和公式输出。`opaque` 公式采用完整性检查并跳过 C6/C9/C10。编号没有 C15。各项的来源与抽样精度见 `GAPS.md` §5。

**严格模式退出码**：0 无硬错误/待办，1 硬错误，2 缺失或损坏产物，3 待处理。非严格模式读取成功返回 0，具体状态在 issues.json。

### 5.4 `split_audit.py`：无句末标点的分类清单

读一个或多个运行目录，把"不以 `.!?:;` 结尾"的句子逐条归类，写成 Markdown 报告，
并在标准输出打印各类计数。`--run` 可重复，也可以带通配符（脚本内部展开）。

| 类别 | 含义 | 性质 |
|:--|:--|:--|
| `punctuation-in-formula` | 句号写在公式里 | 良性 |
| `mark-in-bracket` | 句号写在括号里 | 良性 |
| `container-boundary` | 句子跨列表条目被切开 | 设计取舍（§12） |
| `heading` | `Proof of …`、`Step …` 这类标题 | 非句子 |
| `display-final` / `formula-final` | 以公式结尾、正文没有标点 | 多为原文如此 |
| `brace-leak` | 句中残留裸花括号 | 缺陷 |
| `cut-sentence` | 句子被切断 | 缺陷 |
| `source-missing-mark` | 原文就漏了标点 | 人工复核 |
| `other` | 以上都不是 | 要人看 |

### 5.5 `issues.py`：账本

凡是丢内容、放错位置、按默认值猜、或留下没回答的问题的地方，都调用 `record()` 记一笔。
每个 kind 的去向登记在 `data/issue_kinds.json`：

| 去向（route） | 含义 |
|:--|:--|
| `code` | 缺陷，改代码或表 |
| `todo:<文件>` | 开放问题，能从文本回答，进那个题目文件（§7） |
| `human` | 没有正确答案，或源文件本身错了 |
| `check` | 由 `verify_output` 查出 |
| `ledger` | 有意为之，但数量必须可见 |

**未登记的 kind 会直接抛错**，所以新的"放过"不可能悄悄出现。

接口：`record(kind, site, where, size)`、`counts()`、`sizes()`、`hard()`、`by_route(prefix)`、
`write(path)`、`merge_file(path)`、`summary_line()`、`reset()`。

---


### 5.6 `sentences_md.py`：逐句 Markdown

把一次运行渲染成"一句一节"的 Markdown：原句（保留 `MATHn`）、该句用到的公式表、公式内联后的
可读版本。仓库里的 `out/2005.01702/sentences.md` 就是这个格式的示例。

```sh
python3 scripts/sentences_md.py --run RUN -o RUN/sentences.md
```


## 6. 下游脚本（`pipeline/`）

`pipeline/scripts/` 现在随包发布：`lexicon.py`、`roles.py`、`human.py`、`verify_formulas.py`、
四个 `ask_*.py` 和模型客户端 `pending_names.py`，外加两个入口 `run_pipeline.sh` /
`run_pipeline_llm.sh`。详见 [pipeline/README.md](pipeline/README.md)。

下表是**不在本包**的那部分，保留作索引：

| 组 | 脚本 | 作用 |
|:--|:--|:--|
| 编排 | `nlprep.sh` | 唯一入口：`paper`/`corpus`/`check`/`status`/`verify`/`clean`/`names` |
| | `run_paper.sh` | 单篇全链，含问答（§7.8）；退出码 0 干净 / 3 有待答题 / 1 失败 |
| | `run_corpus.py` | 对一批论文跑全链，报告每段做了什么 |
| 下游判定 | `lexicon.py` | 公式的两层词表：并置构造的读法（名字、乘积、函数作用、指标）；写 `construction_todo.json` |
| | `roles.py` | 定义句、假设句里公式角色的第二意见；写 `role_todo.json`；答案合并、重锚定、`--check-answers` |
| 名字层 | `tex_oracle.py` | 问 TeX 一个命令是什么（`\meaning`），写 `tex_names.json` |
| | `pending_names.py` | 判不了的名字的题目、答案合并；**同时是所有问答脚本共用的模型客户端**（§7.4–7.5） |
| | `reclassify_names.py` | 宏体显示不带内容的名字，重新判为忽略 |
| 问答 | `ask_boundaries.py` | 句界题（§7） |
| | `ask_slots.py` | 公式里短语的角色题 |
| | `ask_lexicon.py` | 并置构造读法题 |
| | `ask_roles.py` | 公式角色题（一次一个块） |
| | `ask_triage.py` | 问模型某篇论文里哪些写法会绊倒规则；只写建议，不改输出 |
| | `human.py` | 汇总 `human_todo.json`：不该由机器决定的事 |
| 检查与度量 | `verify_formulas.py` | 保真门：公式表本身有没有丢东西 |
| | `verify_text.py` | 有没有正文丢失、剩下了什么 |
| | `audit_displays.py` | 全语料 display 审计（空 display、带 `$`、像散文） |
| | `ledger_baseline.py` | 跨论文汇总账本，按 kind 出表 |
| | `fixpoint.py` | 找不是固定点的论文，指出第二遍的差异 |
| | `measure.py` | 在语料上量 `normalize.py` 的数字 |
| | `rules_table.py` | 从代码生成 `RULES.md`，二者不一致时失败 |
| 语料与报告 | `survey_formats.py` / `plot_format_growth.py` / `env_growth.py` | 语料里写法的多样性，以及环境和命令词汇随论文数的增长 |
| | `review_tokens.py` | 给人复核分词的检查表 |
| 句法解析 | `parse_depccg.py` | 用 DepCCG 解析一次运行的句子（可选，`NL_PREP_PARSE=1` 时由 `run_paper.sh` 调用） |

---

## 7. 大模型接入

### 7.1 边界：什么交给模型

判据见 `LLM-SCOPE.md`，顺序不能反：

| 类别 | 例子 | 谁回答 |
|:--|:--|:--|
| **有权威来源能查** | 命令是什么（TeX 的 `\meaning`）；论文自己 `\def`/`\newcommand`/`\newtheorem` 声明的东西；宏包的语义 | **代码**，不问模型 |
| **机械检查** | 括号是否配对、答案是否被应用、题目集是否完整 | **代码** |
| **语义题**：没有权威来源，但答案能从文本推出 | 这个句点是不是句末；`\text{has rank}` 在公式里是谓词还是条件；`D\cdot o` 是乘积还是一个名字；这个公式是条件还是断言 | **模型**，答案只能从闭集里选 |
| **策略**：没有正确答案 | 脚注保留还是丢；源文件本身写错了 | **人**（`human_todo.json`） |

三条不变量保证模型只答它该答的：

- **题目由规则生成**：每道题有稳定的 `sig`，模型不自选题目，也不决定看哪段上下文（窗口由代码切好）；
- **答案是闭集**：每类题写明 `allowed_answers`，集合外的回答直接丢弃；
- **答案可追溯**：每条答案带 `source`、`model`、`asked_at`，错了能查到是谁给的。

### 7.2 六类题目总表

| # | 题目 | 谁出题 | 题目文件 | 谁去问 | 允许的答案 | 默认批量 | 答案文件 | 谁合并 | 本包有吗 |
|--:|:--|:--|:--|:--|:--|--:|:--|:--|:--|
| 1 | 句点后面是小写词，这里到底断不断句 | `latex2nl` | `sentence_todo.json` | `pipeline/scripts/ask_boundaries.py` | `split`、`join` | 20 | `sentence_answers.json` | `latex2nl --merge-sentence-answers` | 在 |
| 2 | 公式里 `\text{…}` 短语的角色 | `latex2nl` | `slot_questions.json` | `pipeline/scripts/ask_slots.py` | `condition`、`proposition`、`predicate`、`predicate_part`、`guard`、`consequent`、`scope`、`connective`、`restriction`、`default`、`label`、`text`、`operator` | 每次一条完整公式 | `slot_answers.json` | `latex2nl --merge-slot-answers` | 在 |
| 3 | 并置构造的读法（`D\cdot o`） | `pipeline/scripts/lexicon.py` | `construction_questions.json` | `pipeline/scripts/ask_lexicon.py` | `multiply`、`apply`、`label`、`index` | 15 | `construction_answers.json` | `lexicon --merge-answers` | 在 |
| 4 | 没人认识的命令名 | `run_pipeline.sh`（收集 `lexicon`、`normalize-stats`、`unresolved.log` 里的名字） | `names_todo.json` | `pipeline/scripts/pending_names.py --answer-with-model` | 动作：`allow`、`font`、`unwrap`、`ignore`、`expand`、`unknown`；`allow` 时还要给类（13 类） | 20 | 写回 `names_todo.json` 本身 | `pending_names.py --merge` → `data/name_answers.json` | 客户端在；收集器在 `run_pipeline.sh` 里 |
| 5 | 定义句、假设句里公式的角色 | `pipeline/scripts/roles.py` | `role_questions.json` | `pipeline/scripts/ask_roles.py --answer` | `constraint`、`proposition`、`unsure` | 一次一个块 | 写回 `role_questions.json` | `roles --merge-answers` | 在 |
| 6 | 这篇论文里哪些写法会绊倒规则 | `ask_triage.py` 自己 | — | `ask_triage.py` | 只是建议 | 一篇一次 | `data/paper_watch.json` | 不合并，不改输出 | 否 |

### 7.3 题目文件的格式

所有题目文件同一个外形：

```json
{
  "instructions":   "给回答者的说明（一段英文）",
  "allowed_answers": ["split", "join"],
  "answer_meaning": {"split": "the full stop ends a sentence",
                     "join":  "it does not; the two halves are one sentence"},
  "meta":           {"items": 5, "sentences": 4},
  "items": [ ... ]
}
```

`sentence_todo.json` 的一条（danmark）：

```json
{"sig": "2. ‖ -convolve with the sheaf MATH270", "unit": "prose#74/item",
 "before": "2.", "after": "-convolve with the sheaf MATH270 under the action of MATH271 on MATH272.",
 "count": 1, "default": "split", "answer": null, "sid_before": null, "sid_after": null}
```

文字槽按本篇的出现位置出题。例如在一条公式内，两个相同的英文词可以分别编号为 MATH1.t1 和 MATH1.t2。提问器把标记插在原位置，发送完整公式和所在句，要求模型逐个返回 ID 和角色。缺失、重复或非法的 ID 不能替另一处作答。

位置答案带有论文、ID 和锚点。合并后只改变该位置的记录。内置词表仍由切分器读取。句界题仍使用其原有的签名和计数。

### 7.4 后端配置

所有提问脚本共用 `pending_names.py` 里的客户端，只用标准库（`urllib`），不需要装 SDK。

优先使用 `pipeline/model_config.json`。复制 `pipeline/model_config.example.json` 后，填写 `backend`、`base_url`、`model`，并按服务要求设置 `api_key` 或 `api_key_env`。模板使用本机 OpenAI 兼容服务，模型名为 `qwen3.5-9b`。两个入口都接受 `--model-config FILE`；单独使用提问器时可通过 `NL_PREP_MODEL_CONFIG` 指定配置文件。显式指定文件时只读取该文件的服务配置。配置示例及优先级见 [pipeline 使用说明](pipeline/README.md#4-接自己的模型)。

以下是兼容原有部署的环境变量方案。

| 变量 | 默认值 | 说明 |
|:--|:--|:--|
| `NL_PREP_BACKEND` | `api-openai` | 三选一：`api-openai`、`api-anthropic`、`local-openai`；也可用各脚本的 `--backend` |
| `NL_PREP_API_KEY` | （空） | 两种云端协议共用的 key |
| `NL_PREP_API_BASE` | **（必须自己设）** | OpenAI 兼容协议的地址；没设就报错停下，不发请求 |
| `NL_PREP_ANTHROPIC_BASE` | **（必须自己设）** | Anthropic 协议的地址；同样必须自己设 |
| `NL_PREP_API_MODEL` | `qwen3.7-flash` | 模型名；**换成你自己端点上的名字**，否则服务端会拒绝 |
| `NL_PREP_LOCAL_BASE` | `http://127.0.0.1:11434/v1` | 本地服务（Ollama 等 OpenAI 兼容服务） |
| `NL_PREP_LOCAL_KEY` | （空） | 本地服务一般不需要 |
| `NL_PREP_LOCAL_MODEL` | `qwen3:8b` | 本地模型名 |
| `NL_PREP_PROXY` | （不用代理） | **默认忽略 shell 里的 `HTTP(S)_PROXY`**；要走代理时显式设它 |
| `NL_PREP_ENV_FILE` | — | 额外的配置文件路径 |

**key 放哪里**：除了环境变量，还会依次读 `$NL_PREP_ENV_FILE`、`~/.config/nlprep/env`、`./.env`，
格式是一行一个 `NAME=VALUE`（可以带 `export`、引号、`#` 注释）；只认 `NL_PREP_` 开头的名字；
**已经设置的环境变量总是优先**。建议把 key 写进 `~/.config/nlprep/env` 并 `chmod 600`，
不要放进 shell 历史或仓库。

**检查当前选择的后端及密钥配置状态**

```sh
python3 pipeline/scripts/pending_names.py --check-config
```

### 7.5 一次调用是什么样

| 项 | 值 |
|:--|:--|
| OpenAI 协议 | `POST {base}/chat/completions`，`Authorization: Bearer <key>`，`response_format: json_object` |
| Anthropic 协议 | `POST {base}/v1/messages`，`x-api-key`，`anthropic-version: 2023-06-01` |
| 采样 | 由 JSON 配置决定，模板为 `temperature=0`、`max_tokens=768`；旧式环境变量方案使用 4000 |
| 超时 | 180 秒 |
| 消息 | 一条 system（该类题的规则与闭集）+ 一条 user（这一批题目） |
| 期望的回复 | 只有一个 JSON 对象：`{"answers": [{"n": 1, "answer": "join"}, …]}`（名字题是 `{"answers": [{"name", "action", "class", "body", "note"}]}`） |

user 消息的样子（句界题，`--dry-run` 实际输出）：

```
Items to decide:

1. before: 2.
   after : -convolve with the sheaf MATH270 under the action of MATH271 on MATH272.

2. before: eover, when we talk about the category of quasi-coherent sheaves, D-modules etc.
   after : on a prestack it will be in the derived sense.

Answer as JSON: {"answers": [{"n": 1, "answer": "join"}, ...]}
```

各类题的 system 规则写在对应脚本的 `SYS_PROMPT` 里，例如句界题明确告诉模型：
"句点后是小写词只是 `join` 的弱证据"，并给出语料里反复出现的两种情形（作者首字母 + 动词是一句；
带编号的条目是新句）。

### 7.6 答案的校验

| 环节 | 规则 |
|:--|:--|
| 解析回复 | 容忍 ```` ```json ```` 包裹；取第一个 `{` 到最后一个 `}`；解析失败退回逐行 `1: join` 的格式 |
| 编号 | `n` 必须在本批 1…批量 之内，否则丢弃 |
| 闭集 | 答案不在 `allowed_answers` 里就丢弃（不记为答案，下次还会再问） |
| 名字题额外 | 动作必须在 6 个之内；`allow`/`font` 必须带合法的类，且"`font` ⇔ 类是 `font`"；`expand` 给的宏体必须"可信"：非空、无 `...`/`…` 占位、花括号平衡、无 `@`、不超过 160 字符 |
| 合并名字答案 | **TeX 已经判定的名字拒收**（优先级：TeX > 按篇答案 > 全局答案）；`unknown` 不写入，转人工；`name_answers.json` 读不了时拒绝合并，不会把整个答案库覆盖成空 |
| 合并角色答案 | 答案必须带 `paper` 与公式原文，与当前公式表对不上就跳过并计数（不会落到别的公式上） |
| 失败 | 名字题遇网络错误重试一次；其余提问脚本一批失败只跳过这一批，其余照常（下次运行会再问） |

### 7.7 答案怎样生效

答案文件本身不改变任何输出，**要带着它重跑对应的那一段**：

| 答案 | 生效方式 | 注意 |
|:--|:--|:--|
| 句界 | `latex2nl.py … --merge-sentence-answers FILE` | `join` 把两半合成一句；用不上的答案（`sig` 已不存在）列在报告的 `answers_unused` 里 |
| 文字槽角色 | `latex2nl.py … --merge-slot-answers FILE` | 按位置读取，内置表只读 |
| 构造读法 | `lexicon.py … --merge-answers FILE` | 论文自己的声明优先于旧答案 |
| 名字 | `pending_names.py --merge FILE`，再从 `normalize.py` 重跑 | 写进全局 `data/name_answers.json`；单篇题带 `paper` 字段，只对那篇生效 |
| 公式角色 | `roles.py … --merge-answers role_questions.json` | 按 `paper` + 公式原文核对 |

另外两个默认路径要留意：`ask_boundaries.py` 不给 `-o` 时写到全局 `data/sentence_answers.json`；
`ask_slots.py`、`ask_lexicon.py` 不给 `-o` 时写在题目文件旁边。

**续问**：每个提问脚本都会先读已有的答案文件，已答的 `sig` 跳过，所以中断后重跑只问剩下的；
`--restart` 忽略已有答案全部重问。每批答完立即落盘。

**常用开关**：`--dry-run`（只打印题目，不发请求）、`--limit N`（最多问 N 道）、`--batch N`、
`--show-reply`（打印模型原始回复，调试用）。

### 7.8 全链脚本 `pipeline/scripts/run_pipeline.sh` 的问答顺序

常规入口默认离线切分，模型审核在网页发起。以下顺序适用于显式启用旧命令行问答的 `--ask` 或 `--model-config FILE`。

两个 pipeline 入口默认调用可用的 TeX 引擎。可用 `--tex-engine xelatex` 选择指定引擎；无引擎时回退到静态分析，依赖章节选择或提前结束文件的稿件保留待处理。输入和输出路径从调用目录解析。论文级答案按原稿路径和各本地输入的 SHA-256 绑定，详见 [pipeline 使用说明](pipeline/README.md)。


```
normalize → latex2nl → lexicon                       （带上已有答案）
  ├─ ask_lexicon   construction_questions.json
  ├─ ask_slots     slot_questions.json
  ├─ ask_boundaries sentence_todo.json
  └─ 有新答案：normalize → latex2nl → lexicon 重跑一遍，让答案生效
names：收集没人认识的名字 → names_todo.json
  → pending_names --answer-with-model → --merge → 再重跑一遍
  → 仍没解决的名字 → human_todo.json
roles → role_questions.json → ask_roles（没答完的块再问一次）→ roles --merge-answers
  → 模型答 unsure 的 → human_todo.json
保真门 verify_formulas → 输出检查 verify_output --strict
状态：失败 → 退出码 1；仍有必办项 → 3（待处理）；否则 0
```

`--no-ask` 跳过所有提问（只用已有答案），`--no-names` 只跳过名字层。退出码 3 不算失败：
批量运行照常继续，但状态 3 的论文不能当干净数据交给下游。想要逐段打印、可 `--limit`／
`--dry-run` 的版本，用同目录的 `run_pipeline_llm.sh`（见 [pipeline/README.md](pipeline/README.md)）。

### 7.9 上游全链缺少答案时的处理

| 题目 | 没答时的做法 | 会不会丢内容 |
|:--|:--|:--|
| 句界 | 按 `default`（`split`）断开 | 不丢，只是可能多切一刀 |
| 短语角色 | 角色为 `unclassified`，公式记 `text_slot_unclassified` | 不丢 |
| 构造读法 | 按默认读法（通常 `multiply`），状态标为未答 | 不丢 |
| 名字 | 名字原样留着，进 `unresolved.log` 与账本 | 不丢 |
| 公式角色 | 保留机械初判 | 不丢 |

题目文件只要还在，论文状态就是 3，不会被当成"已经定了"。

### 7.10 规模与成本

20 篇抽样加 danmark（当时的全链脚本 `--no-ask`，2026-11-20 快照）剩下的题：

| 类别 | 合计 | 有这类题的篇数（共 21） |
|:--|--:|--:|
| 构造读法 | 2,304 | 21 |
| 公式角色 | 1,380 | 21 |
| 短语角色 | 288 | 17 |
| 句界 | 45 | 11 |
| 名字 | 0 | 0 |
| 人工表 | 224 | 10 |

以上是旧版按短语和构造合并时的统计。当前文字槽按完整公式发请求，并置按出现位置判断；本篇的必办数见管线汇总的 required 列。

### 7.11 在本包里接模型

`pipeline/scripts/` 里已经带着 `pending_names.py` 和四个 `ask_*.py`，不用另外补文件。先用
`python3 pipeline/scripts/pending_names.py --check-config` 核对配置（仅显示密钥设置状态），再用
`--dry-run` 看题、不花钱：

```sh
python3 pipeline/scripts/ask_boundaries.py OUT/sentence_todo.json --dry-run
python3 pipeline/scripts/ask_slots.py      OUT/slot_questions.json      --dry-run
```

云端模型需填写服务商的地址、模型名与凭据。`local-openai` 可连接免密本地服务。正式提问前，两个入口都会检查所选后端的配置。

### 7.12 模型答得准不准，怎么知道

- **名字题**：`pending_names.py --calibrate --limit 40` 拿 TeX 已经判定的名字当标准答案，把标签藏起来问模型，
  花钱之前先量准确率；
- **公式角色**：`ask_roles.py --compare` 拿 `data/judgements_roles.json` 里**人**给的答案（`by: "person"`）对照。
  模型或助手预填的答案（`by: "assistant:…"`）只能量"跨模型一致率"，**不能当人的答案报准确率**，两者分开存、分开报；
- **句界与短语**：目前没有人工标准集，答案文件里的 `model` 与 `asked_at` 用于事后追查。

---

## 8. 测试

| 文件 | 内容 | 实测（2026-11-24） |
|:--|:--|--:|
| `tests/probe_split.py` | 句界、缩写、注释行、分段、字体切换、display 边界 | 31 绿 |
| `tests/probe_envs.py` | 五条环境规则与回归 | 15 绿 |
| `tests/probe_brace.py` | 裸花括号五条 + `\ensuremath` 三条 | 9 绿 |
| `tests/probe_part.py` | `\partmarker`、`part` 字段、`env` 不变 | 6 绿（带基料夹具时 8） |
| `tests/probe_audit.py` | 未闭合环境、`\ifcase`、`\end {env}` 等复现 | 31 绿，另有 3 组缺夹具跳过（带全部夹具时 34 / 2） |
| `tests/probe_review.py` | 句界、图片、重音、脚注、日期和向量记号 | 27 绿 |
| `tests/test_*.py`（5 个文件） | 内容保留、公式契约、源码顺序、条件验收、TeX 执行回放 | 91 个用例，其中 13 个依赖 TeX/pdftotext |
| `pipeline/tests/test_pipeline.py` | 管线层：两个入口、论文身份、角色待办、并置冲突与公式验收 | 10 个用例 |

合计 **11 个切分套件 + 1 个管线模块**（`tests/run_all.py` 串起前 11 个）。

在代码包根目录用 PowerShell 复验。独立 PDF 对照需能从 PATH 调用 `pdflatex` 和 `pdftotext`。单元测试在终端报告结果，用例使用临时目录。

```powershell
python -X utf8 -m unittest discover -s tests -p "test_*.py" -v
Get-ChildItem tests/probe_*.py | ForEach-Object { python -X utf8 $_.FullName; if ($LASTEXITCODE) { throw $_.Name } }
foreach ($script in 'normalize','latex2nl','verify_output') {
    python -X utf8 "scripts/$script.py" --self-test
    if ($LASTEXITCODE) { throw $script }
}
```

---

## 9. 数据（`data/`）

| 文件 | 谁读 | 作用 |
|:--|:--|:--|
| `env_classes.json` | normalize | 环境名 → 类（display/statement/list/grid/diagram/float/text_block…） |
| `format_aliases.json` | normalize（**必需**） | 同一字形的拼写别名 |
| `tex_names.json` | normalize、latex2nl | 从 TeX 读出的命令含义（`kind`/`class`/`action`），判"已知名字"的主要来源 |
| `name_overrides.json` | normalize | 人工更正 `tex_names.json` 的判读 |
| `name_answers.json` | normalize、latex2nl | 名字层答案：全局 `names` + 按篇 `papers` |
| `latex_lexicon.json` | normalize | LaTeX 层词表（符号、运算符、名称类别） |
| `slot_roles.json` | latex2nl | 公式里 `\text{…}` 短语 → 角色，以及 13 个允许的角色 |
| `abbreviations.json` | latex2nl | 缩写表（缺失时退回内置的较短列表） |
| `issue_kinds.json` | issues | 账本 kind 的登记表：`gate`（hard/soft/none）+ `route` |

---

## 10. 原理

### 10.1 两段分工

归一化**只改形式，不碰语义**；切分只决定边界。语义（并置读法、公式角色）留给下游。
所以这一层可以先做、单独验收。

### 10.2 判据：TeX 的语义优先

| 现象 | 依据 |
|:--|:--|
| 空行是段落边界；只有注释的行不是 | TeX：只有注释的行不产生任何输出 |
| `\begin{X}` 就是执行 `\X` | `\begin{section}{T}` 应当成为节标题 |
| `\ensuremath{X}`：已在数学模式就原样输出，否则进数学 | 不能无条件加 `$`，否则 `$` 的配对会翻转 |
| 宏体与参数都是平衡的 | 所以展开结果也必须平衡；嵌套调用逐轮展开，不重复输出 |
| 环境按类处理，不按名字 | 名字无界，类有界 |
| 未知环境先保留、再拆壳 | 不认识的块不能当正文；拆壳不插段落分隔 |
| 论文自己的定义是权威来源 | 能从 TeX 或论文声明查到的，由代码决定，不问模型 |

### 10.3 错误必须可见

每处"放过、丢弃、猜测"都进账本；输出另有 C1–C16 检查；无句末标点的句子有分类清单；
判不准的写成题目文件；每条规则有探针。严格验收同时检查硬错误、产物有效性和切分待办。


## 10.5 本包附带的一次运行（example/）

`out/example/2005.01702/` 是用本包脚本对 `2005.01702` 跑出来的完整产物（2026-11-24 重跑）：
`normalized.tex`、`normalize-stats.json`、`sentences.jsonl`、`formulas.json`、`content_blocks.json`、
`split_status.json`、`formula_diagnostics.json`、三段账本、`sentences.md`。可以直接对照本文 §4 的
文件说明，也可以拿来试 `verify_output.py` 与 `sentences_md.py`：

```sh
python3 scripts/verify_output.py out/example/2005.01702
python3 scripts/sentences_md.py --run out/example/2005.01702 -o /tmp/x.md
```

---

## 11. 2026-11-23 验收结果

下表保留该轮记录，当前版本的套件和用例数见第 8 节。

| 范围 | 结果 |
|---|---|
| 包内测试 | 115 项探针、87 项单元测试、2 项管线用例和 3 个脚本自测通过。另有 3 组探针缺夹具、13 个用例缺 TeX/pdftotext 而跳过 |
| 46 个原稿的三条切分路径 | 138 次严格验收，129 次通过并核对公式操作数和 PDF 可见文字顺序，9 次保留原文待处理，0 次硬错误 |
| 另测的 9 个早期反例 | 启用 TeX 后，全部与此前独立 PDF 的正文一致，严格退出码均为 0 |
| 附带论文 2005.01702 | **362 句、795 条公式**（inline 574 / display 57 / 引用 164）、4 个内容块；硬错误 0，未决项（1 个未切分内容 + 3 条 `unresolved.log`）使严格退出码为 3 |

仓库中 22 篇账本和 21 篇句末审计是 11 月 21 日的快照。11 月 23 日的统计范围见 [修复汇总](REPAIR-2026-11-23.md)。

## 12. 边界与取舍（不是缺陷）

| 现象 | 处理 |
|:--|:--|
| 句子不跨列表条目 | 每个条目是独立单元；要改就得改容器模型 |
| 句末标点在公式或括号内（`\operatorname{rigidity.}`） | 判为良性，`trailing_punct` 为空 |
| `Proof of Proposition REF257` 这类标题 | 判为非句子 |
| 公式角色只做初判 | 依据是顶层关系符号，不看句中位置（`a large class of $\mathcal N=4$ theories` 里的公式会被初判为 proposition）；最终角色由 `roles` 与下游确认 |
| 句点后是小写词时先断开 | 宁可多切一刀并出题，也不悄悄合并 |
| 未修项 | 见 [当前限制](OPEN.md)，包括未定位的执行事件和复杂宏的源码映射 |

---

## 13. 这一包能跑什么，以及两个需要注意的地方

| 能做 | 不能做 |
|:--|:--|
| 切分：`normalize.py` + `latex2nl.py`（含合并答案） | 句法解析（DepCCG/SPRING 是外部模型） |
| 检查：`verify_output.py`、`split_audit.py` | 语料批量（`run_corpus` 那类）与语料重建 |
| 下游：`pipeline/` 里的词表、角色、四类问答、`run_pipeline.sh` | 名字层的 `--export`/`--report` 需要语料目录 |

**注意 1：数据文件必须齐。** 最初的桌面包只带了 6 个，缺下面三个；现在已补齐（2026-11-20），
重打包时别漏：

| 文件 | 缺了会怎样 |
|:--|:--|
| `format_aliases.json` | `normalize.py` 一启动就 `FileNotFoundError` |
| `slot_roles.json` | `latex2nl.py` 仍可按公式结构辨认部分文字，依赖词表的短语进入待办 |
| `abbreviations.json` | 退回内置的较短缩写表 |

**注意 2：探针验证本包（2026-11-21 起）。** 五个探针现在按脚本自身位置找 `scripts/`
（`tests/` 的上一级），在仓库里和在本包里都会测**对应的那一份**。`probe_audit.py` 里
`fill_failures` 的三条检查只在仓库中存在时运行；`data/name_overrides.json` 也随包提供。
