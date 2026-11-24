# 单篇管线与切分审核台

## 1. 从原稿到审核

从仓库根目录运行。将 `PAPER.tex` 和 `OUT` 换成主文件和该篇的输出目录。Windows 可在 Git Bash 中运行第一条命令。

```sh
sh pipeline/scripts/run_pipeline.sh "PAPER.tex" "OUT"
python -X utf8 pipeline/review.py "OUT" --open
```

第一条命令默认离线，生成句子、公式、记号读法、验收结果和待办。第二条打开审核台。要从网页调用模型，先按第 4 节配置服务，再选择本条或批量判断。点击“应用并复验”将已保存的决定写入新一轮产物。模型审核接口与请求字段见[审核台模型判断](#审核台模型判断)。

| 管线退出码 | 含义 |
|---|---|
| `0` | 通过严格验收；可选标签、参考值和分歧警告仍可查看 |
| `3` | 有必办项；打开已有产物查看所缺内容或证据 |
| `1` | 处理阶段或验收失败；查看该阶段终端输出和账本 |
| `2` | 入口参数或环境配置错误，如显式指定的引擎缺失。独立验收器也用 `2` 表示产物无效 |

### 原稿、TeX 引擎与论文身份

两个入口均以**调用者的当前目录**解析论文和输出路径，再使用绝对路径运行各阶段。默认依次查找 `pdflatex`、`xelatex`、`lualatex`；可以用 `--tex-engine xelatex` 等选项指定论文实际使用的引擎。引擎需要 `pdftotext`。指定的引擎缺失或已找到引擎但缺少 `pdftotext` 时退出 `2`；编译或执行回放失败时保留原文、进入待办。只有自动查找不到任何引擎时才走静态路径。静态路径遇到依赖章节选择或子文件提前结束的原稿，会保留原文并返回 `3`。

`normalize-stats.json` 的 `source_sha256` 记录各本地输入的指纹。`paper_id` 综合原始主文件的绝对路径、输入相对路径和各文件的 SHA-256，供归一化、切分、词表、名称与角色问答共用。移机、移动原稿或修改输入会产生新身份；同名文件、旧版本、仅以文件名登记的历史答案都不能自动覆盖当前稿件。角色答案还要匹配当前句子与上下文。旧命令行也可在 `role_todo.json` 中填写角色决定后原目录重跑。该接口的 `unsure` 或累计两次无答复会保留题目并转交 `human_todo.json`。

### 公式判断、核对与验收

文字槽按出现位置保存。对同一条公式，模型一次收到完整 LaTeX、带 ID 的目标和所在句，每个 ID 单独作答。例如在两个操作数之间的 `outranks` 可以记为 `predicate`，在 `R_{\text{outranks}}` 的下标中可以记为 `label`。同词出现两次也有两个位置。旧命令行的 `slot_answers.json` 只在对应稿件和位置生效。网页按一个目标槽发一次请求，已确认的决定保存在输出目录的 SQLite 库中。

并置按公式 ID 和公式内字符偏移保存。`paper_lexicon.json` 的每条构造记录含 `shape`、`location`、`anchor` 和 `reading`。词形重复时键名附带位置。写明函数取值的 `f(x)` 与另一个例子中明确定义为标量乘积的 `f(x)` 可分别保留 `apply` 和 `multiply`。孤立括号、参数个数和建议默认值均不能独立决定读法。否定句、疑问句及带条件的乘积说明保留为待判断。

### 公式关系、续行和引用的人工复核

运行切分后，用 `python pipeline/review.py OUT --open` 打开该次输出。在两级目录中选择“公式关系与续行”或“公式引用目标”。有完整参考值的位置放在“条目标签确认”；没有参考值或目标只覆盖组引用的一部分时进入必办。网页显示本公式、上下文、原始行和可用的 TeX 编号。可以调整操作数、关系符、参与行号或引用键对应的目标公式／行，也可以新增、移除记录。保存后点“应用并复验”。撤销确认并复验会恢复程序参考值。

`formulas.json` 分开保存排版行 `rows` 与跨行解释 `logical_relations`。例如 `s&=a+b\\ &+c+d` 的逻辑关系覆盖第 1、2 行，右侧为 `a+b +c+d`。单条有效关系同步到 `top_relation`，有效引用同步到 `ref_destinations` 和 `resolved_references`。`structure_reviews` 包含自动建议 `proposal`、来源 `reference`、`manual` 或 `model`，审核不会改写 `raw`、`expanded`、排版行或编译见证。引用目标若与已核实的锚点冲突，严格验收仍报错。

`source_evidence.json` 保存本次读取的源码文本、选择分支后的输入和展示标签的源码位置。`execution.json` 保存独立编译输出的标签编号、观察到的计数器和受支持的直接行内关系。行号由外层展示环境确定，TeX 编号单独保存，自定义编号如 `(2a)` 不会当作行号。无法判断的计数器保留原文并进入审核。独立编译核对只覆盖已建立对应关系的位置；源码备份本身不能证明所有语句都已正确切分。

### 同一记号的多次定义

`paper_lexicon.json` 的 `notation_definitions` 按出现顺序列出识别到的定义。每条记录给出记号、类型、定义公式与原句、章节、作用域路径和起止公式编号。`ranges` 两端均包含在内。同层重新定义从该公式起生效，子节和例子继承外层定义；局部遮蔽结束后，原定义可以有第二段有效区间。同名标题使用出现序号区分。明确写有 “Throughout this paper” 的定义放在全篇作用域。每处有定义证据的并置可从 `scope_definition.formula` 回查。

例如先定义全篇函数 `f`，在一章中令 `f=2`，再在该章的例子里临时把 `f` 定为函数。实测三条定义的有效区间依次为 `MATH1–2、MATH8`、`MATH3–4、MATH7` 和 `MATH5–6`。因此 `MATH7` 的 `f(x)` 读作乘法，到下一章的 `MATH8` 恢复为函数应用。

规则使用箭头声明、明确的英文类型声明、带引导词的赋值和显式记号定义。在某一节给出的定义默认限于该节及下级。作者靠暗示延续到其他章节的约定、任意自然语言定义和缺少明确结构的局部段落仍需结合上下文复核。定义区间描述管线采用的解释。旧产物可从原始 TeX 重新生成这些字段。

显式 `\operatorname{mix}` 保留算符写法，`R^{\text{mix}}` 把上标保存在文字槽中，并标为 `label`。已知算符名出现在 `\text{mix}(x)` 这种调用位置时，文字槽可以判为 `operator`，仍保留原始文字。同一篇中的算符名不再覆盖其他位置的文字标签。

可在包根目录运行 `python -X utf8 pipeline/tests/test_fidelity_scopes.py`。七项回归覆盖内容保留、整式与行锚点、篡改验收、不同章节与例子的定义、带下标的符号和文字／算符区分。

旧命令行问答用 `slot_questions.json`、`construction_questions.json`、`role_questions.json` 提问，同时导出各自的 `*_todo.json` 供追踪待答位置。公式标签有正式判断或有效默认建议即可通过该项验收。完全空白或同一位置已有互斥读法的条目仍需处理。已有明确规则的项目在离线运行时即可结案。模型返回相同答案记为 `confirmed`，返回不同答案记为 `disagreement`，终端打印 `WARNING`，旧命令行问答的最终结果取规则值。网页模型审核以模型答案为最终值。角色题回答 `unsure` 时，已有规则的位置记为 `undecided`。`rule_reviews.json` 列出实际核对的位置和双方答案，警告本身不改变退出码。例如 `for each` 直接限定的成员关系取 `constraint`。仅有 `Let` 和等号时仍可以由模型判断。

同一次出现的两字裸词和字母并置若收到互斥答案，又没有明确规则，会同时回到待办。公式验收按原记录检查操作符、操作数、数字及其顺序，并核对提取出的文字槽、运算符名称和附加条件。

在代码包根目录可运行 `python -X utf8 pipeline/tests/test_occurrence_decisions.py`。这组测试使用固定回答，覆盖逐位置合并、漏答、重复 ID、同一展示公式拆成多条记录、局部运算符和规则冲突。要检查一次真实模型运行，在输出目录打开 `formulas.json` 的 `text_slots`、`paper_lexicon.json` 的 `constructions`、`roles.json` 的 `roles`，再按 ID 或偏移对照 `rule_reviews.json`。

### 网页人工审核

在 Windows 的代码包根目录运行以下命令，将 **OUT** 换成已经完成切分的输出目录。服务使用 Python 标准库，只监听本机地址。启动时会打印网址，`--open` 会同时打开浏览器。关闭服务后，再用同一输出目录启动可继续审核。

```powershell
python -X utf8 pipeline/review.py "OUT" --open
```

左侧有两个一级目录。两边各自列出相同的类别，包括公式关系与续行、公式引用目标、正文句界、展示公式句界、公式文字槽、并置读法、公式角色、命令名称、保留内容、条件与执行、人工转交、公式诊断、验收与警告和其他未决项。每个条目只计一次，空类别显示 0。

| 目录 | 收录内容 | 对整篇验收的影响 |
|---|---|---|
| 空白条目标签（必办） | 完全空白或有互斥答案的标签、未决句界、未处理内容、硬错误等 | 待办返回 `3`，硬错误返回失败 |
| 条目标签确认（可选） | 有正式标签或有效默认建议的条目、人工或模型已确认的判断、供查阅的诊断和警告 | 可按需复核 |

例如 `f(x)` 的默认读法 `apply` 与规则已经确定的 `apply` 都可复核。详情分别显示「默认建议」和「当前标签」。跳过默认建议会保留空的最终答案。网页人工确认记作 `manual`，有效模型答案记作 `model`。旧命令行模型答 `unsure` 或两次未答时，有效的角色初判仍可供复核。单独运行切分器时，公式文字槽仍归入下游诊断。两个完整管线入口都会检查空白标签。句界、未定条件、未处理的正文与硬错误继续按原规则验收。「只看必办」可跨类别筛选。

1. 点击一级目录查看该组全部条目，或展开后选择具体类别。点击“查看全部”可跨两组搜索公式编号、原文或命令。筛选器可单独查看规则分歧、警告或已确认的条目。点选条目后，右侧展示原文、上下文、定位和来源文件。
2. 选择判断，点击**确认**。人工决定和备注立即存入输出目录的 `manual_reviews.sqlite3`。在“人工确认”或“模型确认”筛选下可修改决定、修改备注或撤销。两页同时修改同一条目时，过期页面会提示刷新。
3. 点击**应用并复验**，用本机原稿离线重跑管线。原稿存在且有该次运行的论文身份时可从网页启动。重新从任一 pipeline 入口处理同稿、同一输出目录时，也会读取有效确认。退出 `0` 表示整篇通过，`3` 表示还有待办；硬错误照常阻止通过。若移动或修改了原稿，新论文身份与旧确认分开保存。

| 类别 | 确认的效果 |
|---|---|
| 句界、展示公式后句界 | `split` 或 `join` 参与重建句子流，`split_status.json` 在 `manual_boundaries` 中记录已应用的句界及其 `manual` 或 `model` 来源。展示公式条目提供前后句和原式。 |
| 文字槽、并置、公式角色 | 按本篇锚点应用已保存的读法，来源为 `manual` 或 `model`。网页模型与规则分歧时采用模型答案；人工可覆盖或撤销。撤销后恢复原有规则、缓存答案或未决状态。 |
| 未知名称 | 选择保留、展开等动作，并按需要填写类别或展开体。决定作用于本篇和当前输出目录；内置 TeX 定义保留其现有处理方式。 |
| 已完整保留的图表 | 选择“接受完整保留原块”，保留原文与锚点，状态为 `retained`，来源记录为 `manual` 或 `model`。完整性检查继续执行，撤销后该块重新待办。 |
| 源码缺失、未定条件、硬错误、其他诊断 | 通过“标记已阅”记录人工核对和备注；源码或执行问题仍需修复后复验。 |

### 审核台模型判断

先用 `sh pipeline/scripts/run_pipeline.sh PAPER.tex OUT` 切分，再用 `python -X utf8 pipeline/review.py OUT --open` 打开审核台。模型栏显示服务端配置的模型名。选择“当前条目”“整篇未确认的必办项”或“整篇全部未确认项”，再点击判断按钮。批量范围覆盖整篇稿件，按钮显示可处理数量。选择“停止审核”会保留已写入的决定，丢弃仍在请求中的回答。

审核台读取 `pipeline/model_config.json`，也可在启动审核台的终端设置 `NL_PREP_MODEL_CONFIG` 指向自己的配置文件。模型服务须已启动。支持本地 OpenAI 兼容地址、托管 OpenAI 兼容地址和 Anthropic 协议。密钥由服务端从配置或 `api_key_env` 读取。每次请求发送所选条目的题型、允许值、原式和可用的局部证据。程序在答复落库前检查选项、结构化字段、稿件和条目版本。弃权、无效答案及连接失败会显示在“查看未写入原因”中。只供已阅的诊断、缺失源码和未定执行分支留给人工。

有效判断立即写入本输出目录的 `manual_reviews.sqlite3`，记录 `source=model`、模型名、答案及备注。与明确规则冲突时，最终存储和切分产物均采用模型意见，双方答案和 `disagreement` 可在详情及复验后的 `rule_reviews.json`（角色、文字槽、并置）查看。模型给出的读法仍须经过独立保真验收。点击“应用并复验”会离线更新产物。原稿及条目锚点相同时，重跑继续使用已确认值，批量审核也会跳过它。点“保存为人工确认”可把当前值或修改后的值记为 `manual`。点“撤销确认”并复验可恢复原自动结果或待办。

本地自动化客户端可读取 `GET /api/items` 获取稿件、条目及版本，向 `POST /api/model-review` 提交 `{"paper":"稿件标识","scope":"required"}`。整篇用 `all`，单条用 `item` 并附该条的 `kind`、`target` 和 `version`。POST 使用页面 `meta[name="review-token"]` 中的令牌作为 `X-Review-Token` 请求头。用 `GET /api/status` 中的 `model_job` 读取进度和未写入项，用 `POST /api/model-stop` 提交空对象停止本轮任务。

常规入口显式传入 `--ask` 或 `--model-config FILE` 时仍可运行旧命令行问答。`run_pipeline_llm.sh` 保留原有行为，传入 `--no-ask` 可离线运行。审核台的模型与这两种离线复验相互独立。

`human_todo.json` 中有对应判断题的转交项会链接到该题。`unresolved.log`、逐公式诊断、验收账本、规则与模型的分歧均可在网页查阅。备份或迁移审核工作时，应保留输出目录中的 SQLite 文件。

## 2. 交付步骤与产物

| 阶段 | 写入输出目录的主要文件 |
|---|---|
| 选择有效源码并归一化 | `normalized.tex`、`normalize-stats.json`、`source_evidence.json`；启用 TeX 时还有 `execution.json` |
| 切句、提取公式、挂接附属内容 | `sentences.jsonl`、`formulas.json`、`content_blocks.json`、`split_status.json` |
| 解释和验收 | `paper_lexicon.json`、`roles.json`、`issues.json`、`rule_reviews.json` 及相关待办 |
| 网页确认与复验 | 新决定立即写入 `manual_reviews.sqlite3`。复验时按稿件与锚点读取，重新生成上列产物 |

## 3. 旧命令行问答文件

| 题目 | 出题 | 答案文件 | 允许的答案 |
|:--|:--|:--|:--|
| 句点这里断不断 | `latex2nl` | `sentence_answers.json` | `split` / `join` |
| 公式里 `\text{…}` 的角色 | `latex2nl` | `slot_answers.json` | 13 类角色 |
| 并置构造的读法 | `lexicon` | `construction_answers.json` | `multiply` / `apply` / `label` / `index` |
| 定义句里公式的角色 | `roles` | `role_questions.json` | `constraint` / `proposition` / `unsure` |

常规入口显式传入 `--ask` 或 `--model-config FILE` 时会走旧问答。兼容入口 `run_pipeline_llm.sh` 默认提问，`--no-ask` 可离线运行。命令名称另由 `pending_names.py` 处理。网页可直接判断句界、文字槽、并置、角色、未知名称、完整保留块、公式关系和引用目标。

## 4. 接自己的模型

审核台与兼容命令行共用 UTF-8 JSON 配置。首次使用时复制模板，编辑服务地址和模型名称，再在已启动的模型服务上从网页试问一条。

```sh
cp pipeline/model_config.example.json pipeline/model_config.json
python3 pipeline/scripts/pending_names.py --check-config
python -X utf8 pipeline/review.py "OUT" --open
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

兼容环境变量配置中，`api-openai` 使用 `NL_PREP_API_BASE`、`NL_PREP_API_MODEL`、`NL_PREP_API_KEY`；Anthropic 协议把地址换为 `NL_PREP_ANTHROPIC_BASE`；本机协议使用 `NL_PREP_LOCAL_BASE`、`NL_PREP_LOCAL_MODEL` 和可选的 `NL_PREP_LOCAL_KEY`。自定义代理可用 `NL_PREP_PROXY` 设置。

若要核对旧命令行如何出题，可对实际生成的 `OUT/slot_questions.json` 运行 `python pipeline/scripts/ask_slots.py OUT/slot_questions.json --dry-run`。单独调用句界提问器时，可显式用 `-o OUT/sentence_answers.json` 将答案放在本篇目录。角色比较与名称校准的参数可分别从 `ask_roles.py --help` 和 `pending_names.py --help` 查看。

## 5. 历史答案

`pipeline/data/` 的两份表按论文匹配历史答案。并置答案使用条目中的 `paper` 或文件的 `meta.paper`；旧库中明确写有 `judged on <论文标识>` 的条目也能匹配。没有论文归属的历史读法留待确认。论文自己的明确声明优先于历史答案。管线也记录本地原稿路径，两篇都叫 `main.tex` 的稿件即使使用同一输出目录，也能区分其并置答案。

| 文件 | 使用条件 |
|---|---|
| `judgements.json` | 383 条并置读法。历史条目的论文标识须与当前标识匹配；原稿中的明确声明优先。 |
| `judgements_roles.json` | 125 条角色答案，来自 21 篇论文。按论文标识、公式编号和公式文本核对；旧库中的文件名标识可由持有原稿的人核对后迁移。 |

## 6. 下游接口

下游可通过 `sentences.jsonl` 的占位符定位 `formulas.json`，再读取本篇词表、结构参考值与审核来源。自然语言句法树和数学结论验证由下游提供。修复反例、实验范围和交付测试命令见[11/24 修改汇总](../REPAIR-2026-11-24.md)。
