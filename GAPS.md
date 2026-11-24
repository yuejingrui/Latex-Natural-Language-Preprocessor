# 空档：每一个“没处理就放过”的去处

## 11 月 24 日的已知漏检及复验

| 原稿 | 当前可观察结果 | 影响 |
|---|---|---|
| 第一项后执行 `\setcounter{enumi}{7}` | TeX 辅助的两个入口现均输出 1、8 | 读取实际条目标签。缺少可用标签的受影响列表保留整块并转入审核 |
| 原文说明下标是状态名，并写 `\alpha_{\text{and}}` | 文字槽给出 `connective`；对照 `R_{\text{and}}` 为 `label` | 希腊控制序列作基符号时，初判仍可能取短语词表的角色。网页模型确认与规则不同时采用模型值并提示分歧，人工可改为 `label` |

此次七项新增定向回归已通过。两平台各对 114 份输入运行两个管线入口，456 次运行与上一轮同组输入的退出码一致。附带论文 SympSing 的 13 个被引公式标签、20 条引用均已与原稿定位核对。作用域数据格式和定向测试命令见[管线说明](pipeline/README.md#同一记号的多次定义)。

完整公式和行号保留在产物中。结构参考值、可选标签和警告可随通过验收的稿件交付；需追查解释错误时，查看条目来源和规则分歧。入口、反例及阶段实测见[11/24 修改汇总](REPAIR-2026-11-24.md)。

## 11 月 23 日的严格验收

| 发现的问题 | 记录与结果 |
|---|---|
| 条件或文件查找未定、正文调用缺定义、表格正文尚未切分 | 保留源码并用 REF 挂接句子，写入内容待办。严格验收返回 3 |
| 公式内部有未知命令或文字角色未定 | 原式及分组保留，解释问题写入下游诊断，单独出现可通过切分 |
| 花括号次序错误、公式被改写、锚点或词元失联 | C7 或关联检查记录硬错误，严格验收返回 1 |
| 必需产物缺失、格式损坏或问题类别未登记 | 标记产物无效，返回 2 |
| 已选分支自洽但实际错误 | 产物检查难以单独发现。受支持的站点用 TeX 事件决定分支，回归另用原稿 PDF 和预期公式核对 |

此次 46 个样例的 138 次严格验收中，129 次通过、9 次待处理。附带论文重放的硬错误为 0，原有删除记录和未解决项仍使严格退出码为 3。各条修复、待处理原文和复现方法见 [11 月 23 日修复汇总](REPAIR-2026-11-23.md)。

## 11 月 18 日的上游审计记录

当时的调查回答两个问题：管线里**哪些情况没处理好就被放过了**，以及**怎么保证每一个都有去处**。

**只读排查，没有改代码。** 样本：20 篇抽样（`run_corpus --step 280 --limit 20`）、`selecta`/`danmark`/
`goldbach` 三篇单篇全链（`run_paper.sh --no-ask`），部分条目另在 201 篇抽样（`--step 28`）上计数。
三路并行：`normalize.py`、`latex2nl.py`、路由与闸门（`lexicon`/`roles`/`run_paper.sh`/`run_corpus`/
`verify_*`/`ask_*`/`pending_names`）。最大的三条另用最小输入亲自复现过（第 2 节）。

---

## 1. 上游审计当时的结论

“层出不穷”的直接原因：一个没处理好的情况要经过三层才算有着落——**记下来**（计数/日志）、
**进表**（变成一道有人答的题）、**拦住**（运行失败或在汇总里报警）。排查出约 70 个放过点，状态是：

| 状态 | 大约多少 | 例子 |
|:--|--:|:--|
| 三层都有 | 3 | 构造读法、槽角色、句界（各自的 `*_todo.json`，写完回读） |
| 只记、不进表、不拦 | ~20 | `outside_vocabulary`、`unknown_envs`、`labels_unrendered`、`unresolved.log` 的大部分类别 |
| 进了表但闭环断了 | 5 | `names_todo` 被 `rm`、`role_todo` 的 `unsure` 消失、拒答没有去处 |
| **什么都没有** | **~40** | 删了内容不记、内容放错位置、默认值冒充结论、死分支 |
| 拦住 | **0** | 保真门只打印 `rc`；`verify_text` 退出码恒为 0；`run_corpus` 只在崩溃时失败 |

**什么都没有的那 40 个**，是代码自认为做对了的地方：查表**命中了但表是错的**（`\frenchspacing`
在 `tex_names.json` 里是 `allow`），或者根本没走到查表（扫描、切分、拼接在前面就错了）。
在查表处加记录接不住它们，只能看**输出本身**。

所以方案是四件事：**一个账本**（每个放过点当场记一条、带去处）、**一组输出检查**（兜住代码不自知的错）、
**一张人工表**（没有正确答案或源文件本身有错的）、**真正的闸门**（账本与检查的结果决定退出码）。

---

## 2. 最大的三条（亲自复现）

| 输入 | 11 月 18 日的输出 | 当时的问题 |
|:--|:--|:--|
| `…we consider the map` + `\[ f\colon X\to Y \]` + `defined by $f(x)=x^2$.` | 两句：`In the following, we consider the map` / `defined by MATH2.`；display `MATH1` 标 `orphan` | **句中 display 被切出句子**。normalize 在 display 两侧各写 `\n`，叠上原文的换行成了空行，latex2nl 当成段落边界 |
| `Kept text.` `\iffalse Draft that was commented out. \fi` `More text.` | 三句，`Draft that was commented out.` 在正文里 | **作者注释掉的草稿回到正文**：`resolve_conditionals` 对 `\iffalse` 取第一支 |
| `\frenchspacing` 后接 `The main result follows.` | 一句：`\frenchspacing The main result follows.` | **名字表判错**：`tex_oracle` 对所有宏一律 `allow`，`vocabulary()` 把它算认识，于是不记、不进表 |

规模：

- **句中 display**：20 篇 2,896 个 display 里 **579 个（20%）** 被标 `orphan`；22 篇（加 selecta、
  danmark）里 989 个，其中 **784 组的前一句没有句末标点**——引导句被切断。`verify_formulas` 把
  “已标 orphan”当合格，所以这是**最大的静默损失**。
- **`\iffalse`**：201 篇里 21 处、6 篇。条件分支（`\ifx`、`\ifnum`、未知 `\ifX`）整体没有任何计数。
- **名字表判错**：`tex_names.json` 里 423 条 `allow`+`class: text`，其中 45 条宏体明显是版面或间距
  （`\sfcode`、`\penalty`、`\vskip`…）。201 篇的**输出正文**里这类名字出现 **1,484 次、92 篇**
  （`\nobreakdash` 789、`\th` 204、`\fontseries` 72、`\smallbreak` 60…）。

---

## 3. 全部放过点与去处

**去处只有五种**，按原则选（封闭的归代码，开放的归模型，没有正确答案的归人）：

| 去处 | 含义 |
|:--|:--|
| **代码** | 权威可查（TeX 语义、论文声明、宏包），是缺陷，修代码或改表 |
| **待办→模型** | 开放，有文本证据，闭集答案，进某个 `*_todo.json` |
| **人工表** | 没有正确答案（策略），或源文件本身有错，或模型两次拒答 |
| **检查** | 代码不自知的错，靠输出检查发现（第 5 节的 C1–C14） |
| **账本** | 有意的处理（例如按设计删脚注），但必须记数量，汇总可见 |

每一条都必须同时有：**当场记账**（第 4 节）+ 上面五种之一。

### 3.1 删了内容，没有记账

| # | 位置 | 触发 | 规模 | 去处与修法 |
|:--|:--|:--|:--|:--|
| D1 | `normalize._drop_bare`（`BARE_ONE` 的 `footnote`） | 任何 `\footnote` | 201 篇：457 处、89 篇、约 9.1 万字符 | **人工表**定策略（保留为单独单元还是删）；在定之前**账本**记删除字数 |
| D2 | `env_classes.json` 的 `float` 类 | `tabular`/`minipage`/`longtable`/`table` 整块删 | 含公式的块：201 篇 tabular 432、minipage 88、longtable 685、table 836 | **代码**：`tabular`/`minipage` 移到 `text_block`/`grid`；`figure` 只删图形，`caption` 另定；**账本**记删除量 |
| D3 | `_drop_bare` 的 `proof`（`limit=1`） | `\proof{Of Lemma 2}`、`\proof[\bf Acknowledgements]` | 201 篇 7 处 | **代码**：照 `_as_proof` 保留标题 |
| D4 | `_drop_bare_bibliography`（`normalize.py:934`） | 裸 `\thebibliography` 没有 `\end` | 小例子：附录整段消失 | **代码**：遇 `\section`/`\appendix` 停；**账本**记删除字数 |
| D5 | `read_source`（`normalize.py:155`） | `\input` 文件不存在 | 201 篇 39 处、19 篇 | **账本** + **检查**（缺文件即报警）；源文件不全时**人工表** |
| D6 | `resolve_conditionals`（`latex2nl.py:291`） | `\iffalse` 取第一支 | 201 篇 21 处、6 篇 | **代码**：`\iffalse` 删、`\iftrue` 留 |
| D7 | `latex2nl._process_unit`（约 2761） | 切出的句子里没有 ≥2 个字母 | 22 篇 101 处整句删；条目号随孤儿丢 27 处 | **账本**；条目号放进字段 |
| D8 | `\item[..]` 三处处理不一致（`_blocks`、`_split_items`、`_clean_segment`） | 带标签的条目 | 22 篇 25 个标签被删（`(Brill-Noether)`） | **代码**：只留一条规则，标签进字段 |
| D9 | `find_math_spans`（约 1527） | 一个 display 有多个 `\label`；`normalize._hoist` 同样只提第一个 | 22 篇 15 个；201 篇 301 处 | **代码**：label 改成列表 |
| D10 | `_units_in_chunk` + `_clean_segment` | statement 未闭合，或 `\begin{..}[标题]` 落进正文 | 5 个未闭合、4 个标题被删 | **代码**：标题保留成文字；**账本**记未闭合 |
| D11 | `_drop_section_machinery`、`_unwrap_last_arg` | `{2 in}` 当长度删；`\scalebox{2}x` 的 `{2}` 变内容 | 罕见（小例子） | **代码** |

### 3.2 内容放错位置（代码不自知）

| # | 位置 | 触发 | 规模 | 去处与修法 |
|:--|:--|:--|:--|:--|
| M1 | `normalize.py:1286`、`1547` + `latex2nl._prose_units`（约 2630） | display 两侧写 `\n`，与原文换行叠成空行 | 20 篇 579 个孤儿（20%）；784 个引导句被切 | **代码**：normalize 只保留作者原有的空行（或 latex2nl 忽略紧贴 display 的空行）；**检查** C1 |
| M2 | `tex_oracle.classify_meaning`（`tex_oracle.py:165-180`）→ `vocabulary()` | 宏体是版面/间距，却判 `allow` | 201 篇正文 1,484 次、92 篇 | **代码**：`classify` 按宏体细分出 `ignore`（`\sfcode`/`\penalty`/`\vskip`/`\par`/`\fontsize`…）与 `unwrap`（`\href` 第 2 参、`\url`）；给一张**覆盖表**（现在 `--merge` 会拒绝改 TeX 判过的名字，判错无从纠正）；**检查** C2 |
| M3 | `normalize._apply_names` 的 `ignore` | 原语只删名字、不吃操作数：`\penalty10000`、`\setbox0=…`、`\uppercase{..}`、`\mathchoice{4 组}` | 201 篇几十种（`\cr` 159、`\uppercase` 106、`\setbox` 55） | **代码**：原语按 TeX 语法吃操作数（数字、`=`、尺寸、一个组）——这是封闭的 |
| M4 | `latex2nl.find_math_spans`（约 1505-1512） | `$` 扫描不看花括号：`\mbox{$..$}` 嵌套或作者多一个 `}` | 22 篇 35 条（2 条数学进正文，33 条作者笔误） | **代码**：在 `\mbox`/`\text` 组里跟踪深度；作者笔误→**人工表**；**检查** C7 |
| M5 | `_clean_segment` 的 `\ref\{([^}]*)\}` | 标签里带花括号 | 2 处（`REF170}`） | **代码**：改用 `find_group_end`；**检查** C12 |
| M6 | `_clean_heading`（约 2554） | 标题里有 `$..$` | 53 个标题、2,587 句的 `section` 字段带 `$` | **代码**：标题建占位符与公式记录（`kind: heading`）；**检查** C8 |
| M7 | `_make_records` 不处理 `\intertext` | display 里有 `\intertext{…$x$…}` | 2 条 | **代码**：在 intertext 处切开；**检查** C6 |
| M8 | `MacroTable`（`latex2nl.py:791`） | `\def\7#1{..}`（控制符号宏）、`\def\X(#1,#2)`（分隔参数）、`\let\x=a` | 2410.11622：`\7` 等 59 次；201 篇分隔参数 36 处 | **代码**；现在它们落进名字层，是**进错了表**（论文自己定义过） |
| M9 | 未闭合 `$$`（`find_math_spans` 直接 `break`）、未闭合 `$`/环境 | 定界符不闭合 | 201 篇 181 个残留 `$$`、13 篇；另 5 篇 `$` 为奇数 | **账本** + **检查** C9/C10；源文件错→**人工表** |
| M10 | `normalize_dashes_quotes` 只删空 `{}` | 正文里非空的裸花括号组 | 22 篇 59 句、8 篇 | **代码**：配平的裸组拆括号；含 `_`/`^`/控制字的交给**检查** C3 |
| M11 | `_unwrap_unknown_envs` | 未知环境的花括号参数并进第一句 | 样本里 0（normalize 已兜住大部分） | **代码**：标题放进 `unit.title`；`unwrapped_envs` 落盘 |
| M12 | `strip_comments`（`latex2nl.py:429`） | `\\%`（换行后接注释）被当成 `\%` | 201 篇 82 处、6 篇 | **代码**：数反斜杠的奇偶 |

### 3.3 判错或默认值冒充结论

| # | 位置 | 触发 | 规模 | 去处与修法 |
|:--|:--|:--|:--|:--|
| J1 | `resolve_conditionals` | `\ifx`/`\ifnum`/`\ifcase`/未知 `\ifX` 判不了，取第一支（`\ifcase` 全留） | 201 篇：未知 `\if*` 132 处、26 篇；`\ifx` 16 | **代码**：`\newif` + `\Xtrue`/`\Xfalse` 能读出来；读不出的→**待办→模型**（`conditional_todo`，答案 `branch1`/`branch2`/`both`，证据是两支文本） |
| J2 | `resolve_conditionals`（约 297） | 第一个记号是 `\fi`/`\else` 时，整段不再判任何条件 | 201 篇 11 处 | **代码**：`\newif` 声明的名字算作开头；**检查** |
| J3 | `latex2nl.classify_role`（约 2097） | canonical 文本里分段函数写成 `\begin{grid}{cases}`，判断是死分支 | 22 篇 87 条 `cases`，**0 条判成 piecewise**；`big_delimiter`、set-builder 的 note 也是死的 | **代码**：认 canonical 写法，self-test 加 canonical 用例；**检查** C11 |
| J4 | `classify_speech_act`（约 2266） | 首词不在动词表，默认 `assertion` | Note 213、Recall 115、Given 105、Consider 82、Take 73 | **代码**：动词表移到 `data/`，按闭集扩充；仍不认识的首词→**待办→模型** |
| J5 | `read_theorem_decls`（`normalize.py:310`） | 显示名首词不在 `THEOREM_KINDS`，小写后直接当 kind | 201 篇 401 处、116 篇 | **代码**扩表；剩下的→**待办→模型**（闭集 `THEOREM_KINDS`） |
| J6 | `env_classes.json` 里 `restatable` 是 `text_block`，`normalize.py:1675` 的专门分支是死代码 | `\begin{restatable}{thm}{key}` | 201 篇 4 处 | **代码**：从表里删掉 `restatable` |
| J7 | `META_CMDS` 先删 `\setcounter`；`_split_items` 把首个 `\item` 前的文字当第 1 项 | 列表编号 | 201 篇 27 处、11 篇 | **代码** |
| J8 | `alphabet_font_command` 回退 `\mathrm`；`render_label` 无计数器 | 字族不认识；标签无法编号 | 201 篇 28 / 168 | **账本**；未知字族→**待办→模型** |
| J9 | `normalize()` 第 2–5 轮新开 `Normalizer` | 后续轮的 stats 被丢掉 | 201 篇 199 个事件、20 篇 | **代码**：合并各轮 stats |

### 3.4 记了，但没有路由

| # | 记在哪 | 现在谁读 | 去处与修法 |
|:--|:--|:--|:--|
| R1 | `normalize-stats.json` 的 `outside_vocabulary`（**正文**里的未知名） | `run_corpus` 只计数；`run_paper.sh` 组名字题只读公式里的 `unknown_commands` | **待办→模型**：名字题取三个来源的并集（`unknown_commands`、`outside_vocabulary`、`unresolved` 的 “commands not understood”），每项记 `scope: math/text`。danmark 的 `\step` 16 次、1808.01468 的 `\bqed` 4 次就落在这里 |
| R2 | 统计文件名：`run_paper.sh` 写 `normalize-stats.json`，`run_corpus`/`rules_table`/`env_growth` 认 `normalize_stats.json` | 按名读的下游漏掉单篇产物 | **代码**：统一成一个名字 |
| R3 | `unknown_envs`、`declared_env_kept:X` | 只计数 | **代码**（`SPLITTING.md` 五处）；仍未知的→**账本**，单篇超过阈值→**检查** |
| R4 | `labels_unrendered` | **没有任何读者** | **账本**；编号样式判不了→**人工表** |
| R5 | `unresolved.log` 的十余类行 | `run_paper.sh` 只数行数 | 逐类登记去处：槽→`slot_todo`、小写续句→`sentence_todo`、命令→名字题；tokenisation 不无损、unanchored、unclosed、display 无标点、leftover `\text`→**账本**+**检查** |
| R6 | `unwrapped_envs`、`answers_unused`、`operator_names`、`overridden_by_paper`、`unknown_key` | 只打到 stdout | **账本**（落盘、汇总） |
| R7 | `drawing_kept`、`cyclic`、`nested_skipped`（宏表） | 不进 stats | **账本** |
| R8 | 公式里提升成算子（`\text{log}` → `\operatorname`） | 只打到 stdout | 记录加 `promoted_operator` 标记 |

### 3.5 进了表，但闭环断了

| # | 表 | 断在哪 | 修法 |
|:--|:--|:--|:--|
| Q1 | `names_todo.json` | 合并后直接 `rm`，答 `unknown`/null 的静默跳过，`rejected` 只打印；没有 I2；heredoc 出错与“没有名字”都 exit 1；`--merge` 直接写仓库 `data/name_answers.json` | 写完回读（I2）；重跑证实名字消失前不删；`unknown`/拒答/`rejected` 进**人工表**；`--merge` 有 rejected 时返回非 0；单篇运行的答案先写 `OUT/`，再显式提交到 `data/` |
| Q2 | `role_todo.json` | 模型两次返回空的仍是 pending，没有次数；`unsure` 直接移出，不进任何表；没有回读断言；模型答案只在 `roles.json` | 每项记 `asked` 次数；两次空→**人工表**；`unsure`→**人工表**；加 I2 |
| Q3 | 所有 asker | `ask_lexicon`/`ask_slots`/`ask_boundaries`/`ask_roles` 恒返回 0，`run_paper.sh` 里 8 处 `\|\| true` | 返回码收进最终状态；`\|\| true` 只保留在“允许继续”的地方，并在汇总里列出失败 |
| Q4 | 构造与槽的答案 | 只留在 `OUT/`，不进全局表 | 这是 P11/P13 的**前置约束**（补 `paper` 字段之前不进 `data/`），不是缺陷；汇总里应列出“本地答案 N 条，未提交” |

### 3.6 闸门

| # | 现在 | 修法 |
|:--|:--|:--|
| G1 | `run_paper.sh` 跑 `verify_formulas.py "$(dirname "$OUT")"`，只 `echo rc`，不退出；范围错（gap1 里 danmark 检了 2 篇） | 改成对 `$OUT` 跑；`rc≠0` 时整条运行失败 |
| G2 | `verify_formulas` 把“已标 orphan”当合格 | 拆开：无正文的孤儿（设计如此）与句中孤儿（缺陷，C1）分别计数，后者进闸门 |
| G3 | `verify_text.py` 退出码恒为 0，且没有入口调用它 | 让它读产物并返回非 0；或把它的检查并进 `verify_output.py` |
| G4 | `run_corpus` 只在某一段崩溃时 `ok=False`；`not_converged`、stale 不影响；`summary.json` 不收账本/检查 | 汇总收账本与检查的按类计数；硬检查不过即 `ok=False` |
| G5 | `nlprep.sh corpus` 的汇总只打印顶层标量，实际什么也打印不出来 | 改成逐篇聚合 |

---

## 4. 账本：每个放过点当场记一条

**做法**：每篇产出一份 `OUT/issues.json`；每个放过点调用同一个入口记一条：

```
{"kind": "drop:footnote", "site": "normalize._drop_bare", "where": "…片段…",
 "size": 412, "route": "ledger"}
```

**种类是一张登记表**（`data/issue_kinds.json`）：每个 `kind` 写死它的去处——`code`、
`todo:<文件>`、`human`、`check`、`ledger`——以及是否进闸门（`gate: hard/soft/none`）。

两条规矩让“新冒出来的问题”必然有去处：

1. **未登记的 `kind` 不许出现**：写账本时遇到表里没有的 `kind`，自测失败。新放过点要么登记、要么不存在。
2. **每个放过点都要发一条**：第 3 节的每一行在代码里都对应一个调用点；自测用最小输入逐个触发，
   断言账本里出现对应的 `kind`（与现在 `probe_*.py` 的写法一致）。

`run_paper.sh` 的汇总按 `kind` 打印数量；`run_corpus` 跨篇聚合；`route: todo:*` 的条目与对应
`*_todo.json` 的条目数相等（这就是把 I2 推广到所有表）。

---

## 5. 输出检查：兜住代码不自知的错

新增 `verify_output.py`，**读真实产物**（不重新跑 normalize），每个命中写进账本（`kind: check:Cn`）。
22 篇样本上的实测：

| # | 判据 | 命中 | 抽查 | 闸门 |
|:--|:--|--:|:--|:--|
| C1 | 孤儿 display 的前一句不以 `.!?:` 结尾 | 784 组 | 抽 6 条全是引导句被切 | **硬** |
| C2 | 句子（去掉占位符后）里有控制字 `\[A-Za-z]+` | 37 句、5 篇 | 全是真阳（`\step` 16、`\bqed` 4、`\frenchspacing`、`\selectlanguage`、`\textnumero`、`{S_\bullet}`） | **硬** |
| C3 | 句子里有 `{` 或 `}` | 59 句、8 篇 | 抽 15 条全是真阳；goldbach 致谢里的字面 `&` 是唯一误报 | **硬** |
| C4 | 句子 ≤2 个词元且无句末标点 | 约 110 | “Then”“Let”“We write”是真阳；`Proof.`/`Remark.` 这类小标题以“无句末标点”排除 | 软 |
| C5 | 句子以 `MATH`/`REF` 开头且前一句无句末标点 | 27 | 多数真阳（C1 的副作用） | 软 |
| C6 | 公式 `expanded` 里有 `$` 或 `\intertext` | 3 | 3/3 | **硬** |
| C7 | 当时仅查行内公式括号 | 35 | 原因随后复核，见 `OPEN.md` §1.8 | 当时为软，11 月 23 日对行内与行间公式均为硬检查 |
| C8 | `section`/`title` 里有 `$` | 53 个标题 | 全是真阳 | 软 |
| C9 | 公式 `expanded`（去掉命令与 `<TXT>`）里有 ≥4 个连续英文词 | 0 | 无误报，留作未闭合 `$` 的报警 | **硬** |
| C10 | `expanded` 里有 `grid`/`diagram` 以外的 `\begin{..}` | 0 | 同上 | **硬** |
| C11 | `grid{cases}` 的公式角色不是 piecewise | 87 | 100%（J3） | 软（J3 修好后改硬） |
| C12 | 句子里 `REF\d+}`，或 `ref_target` 里有 `{` | 2 | 2/2 | **硬** |
| C13 | `ref_target` 在全文找不到 label | 62 | 混杂（外部引用、D9 删掉的标签） | 软 |
| C14 | `outside_vocabulary` 的名字出现在句子里 | 20 | 全是真阳（R1） | **硬** |

另外两条已经测过、样本里是 0，留着当回归：display 里出现成段正文；非规范的 `\begin`。

---

## 6. 人工表

新增 `OUT/human_todo.json`，收**没有正确答案或机器不该定**的：

| 来源 | 例子 |
|:--|:--|
| 策略 | 脚注保留还是删（D1）；未答完是否拦（D11 / P14）；抽检比例（P17） |
| 源文件错 | 作者多写的 `}`（C7 的 33 条）、未闭合 `$$`（M9）、缺失的 `\input` 文件（D5） |
| 模型答不了 | 角色 `unsure`、两次返回空（Q2）；名字答 `unknown`、`rejected`（Q1） |
| 编号样式 | `labels_unrendered`（R4） |

它和 `*_todo.json` 一样写完回读、计入汇总，但**不被 asker 读**。

---

## 7. 上游全链脚本的退出码设计

`run_paper.sh` 的最终状态由三件事决定：

| 退出码 | 含义 |
|:--|:--|
| 0 | 干净：账本里没有 `gate: hard` 的条目，所有 `*_todo.json`、`human_todo.json` 为空，`verify_formulas` 与 `verify_output` 的硬检查通过 |
| 3 | 有待答：只剩 `*_todo.json` 或 `human_todo.json`（是否当失败由 D11 决定） |
| 1 | 失败：硬检查不过，或 `verify_formulas` 失败，或出现未登记的 `kind` |

`|| true` 改成把各步返回码收进这个状态；汇总第一行打印状态与原因。`run_corpus` 用同一套规则判 `ok`。

---

## 8. 顺序

| 序 | 做什么 | 为什么先做 |
|:--|:--|:--|
| 1 | **账本 + `verify_output.py` + 闸门**（第 4、5、7 节） | 不改任何判定，只让所有放过点可见；之后每一步都能用账本量前后 |
| 2 | **M1 句中 display**、**D6 `\iffalse`**、**M2 名字表判错（`classify` + 覆盖表）** | 规模最大、改的是内容本身 |
| 3 | **R1 名字题三源合并**、**Q1/Q2 闭环**、**人工表** | 让“开放的”和“归人的”真的有去处 |
| 4 | **删内容的 D 组**：先记账（D1、D2 的删除量），再按人工表的决定改 | 先看见删了多少，再决定 |
| 5 | **死分支 J3/J6**、**J1/J2 条件**、**M3 原语操作数**、**M8 宏表** | 封闭问题，逐条修 |
| 6 | 其余小项 | — |

**验收**：

- 第 1 步之后，22 篇样本的账本按 `kind` 列出，C1–C14 的命中数与第 5 节一致（作为基线）；
- 自测：第 3 节每一行用最小输入触发，账本里出现对应 `kind`；写一个未登记的 `kind` 时自测失败；
- 第 2 步之后：C1 从 784 降到接近 0；C2 里 `\frenchspacing`/`\selectlanguage`/`\textnumero` 消失；
  `\iffalse` 的小例子只剩两句；
- 每一步都比较账本的按类计数，只许下降，或者上升且逐条解释。

---

## 9. 与其它文档的关系

| 文档 | 关系 |
|:--|:--|
| `OPEN-PROBLEMS.md` | 本轮实测的问题清单；本文的 R1 即其 P22，M2 即其 P4/P5，Q1–Q2 与 P12、P23 对应 |
| `QUESTION-ROUTING.md` | “什么时候成题”；本文把它从构造推广到所有放过点（账本 + 登记表） |
| `LLM-SCOPE.md` | 去处的判据（权威 → 代码；开放 → 模型；策略 → 人） |
| `SPLITTING.md` | 未知环境的五处修法；本文 R3 引用它 |
| `DECISIONS.md` D11 | 待答是否拦（第 7 节的退出码 3 由它定） |

---

## 10. 第 1 步已实现（2026-11-18）

账本、输出检查、闸门三件已落地，判定逻辑一行未改。

| 件 | 落点 | 说明 |
|:--|:--|:--|
| 登记表 | `data/issue_kinds.json` | 23 个 kind，每个写死 `route`（code / todo:* / human / check / ledger）与 `gate`（hard/soft/none） |
| 账本 | `scripts/issues.py` | `record(kind, site, where, size)`；**未登记的 kind 直接抛异常**（§4 规矩 1）；`OUT/issues.json` 记 counts/sizes/hard/entries |
| 输出检查 | `scripts/verify_output.py` | C1–C14，读真实产物（`sentences.jsonl`/`formulas.json`/`normalized.tex`/`normalize-stats.json`），不重跑管线；`--strict` 硬检查命中即 rc 1 |
| 闸门 | `run_paper.sh` | `verify_formulas` + `verify_output` + 待答文件 → 退出码 **0 干净 / 3 有待答 / 1 失败**；汇总首行打印 `status` |
| 入口 | `nlprep.sh verify OUT` | 单跑检查 |
| 探针 | `tests/probe_gaps.py` | 7 条：未登记 kind 被拒、每个 kind 有去处、C1 抓到被切断的引导句、跑出 rc 1、账本落盘、汇总打印状态、有待答时 rc 3。`run_all` 19/19 |

### 首次实测（`danmark.tex`，`--no-ask`）

```
check  gate  hits
C1     hard    320     非末句没有句末标点（引导句被切断）
C2     hard     17     句中有控制字（\frenchspacing 1 + \step 16）
C4     soft     48     两词以内且无标点
C5     soft      3     以占位符开头且前句无标点
C13    soft    149     引用目标找不到 label
C14    hard     16     outside_vocabulary 的名字出现在句子里
→ hard 353，status failed，rc 1
```

与 §2、§5 的判定一致：C2 的 17 = 1 条 `\frenchspacing` + 16 条 `\step`，C14 的 16 全是 `\step`。

### 还没做的（第 1 步范围内）

- **代码侧的去处还没接线**：目前账本只有 `verify_output` 这一个生产者；§3.1–§3.4 的 D/M/J/R 各组
  要在代码里逐点 `record()`（第一遍建议只接高频 10 点：D1、D2、D7、M1 修复点、M2、M9、J1、R1、R5、Q1）。
- **22 篇基线**：验收要求"22 篇样本的账本按 kind 列出"。需要一次 `run_paper.sh --no-ask` 的批量跑
  （或 `run_corpus` 接上 `verify_output`）才能给出跨篇基线；现在只有 danmark 一篇的数。
- **人工表**：`human_todo.json` 的登记与产出还没接（kind 已登记：`drop:footnote`、`human:source_error`、
  `human:model_refused`、`human:label_style`）。

### 第 2 步已实现（2026-11-18）

| 项 | 改在 | 结果 |
|:--|:--|:--|
| **M1** 句中 display | `latex2nl._join_display_breaks`：display 前的空行只在**前文有句末标点**时才算段落边界；display 后紧跟小写继续时并回 | `danmark` 的 C1 **320 → 1**；20 篇句子 12,234 → **12,014**（220 条被切断的引导句并回去了） |
| **D6** `\iffalse` | `resolve_conditionals`：`\iffalse` 取第二支（`\else` 后），`\iftrue` 取第一支 | 注释掉的草稿不再回到正文 |
| **M2** 名字表判错 | 新增 `scripts/reclassify_names.py`：按宏体判 `ignore`（间距/罚金/字体，无参数）与 `unwrap`（单组内容），对已有 `tex_names.json` 离线重算 | 34 条改为 `ignore`、4 条改为 `unwrap`；`danmark` 的 `\frenchspacing` 消失（C2 17 → 16） |

新基线（M1 之后）：**20 篇 12,014 句 / 33,932 公式 / 59 花括号句**；`danmark` 1,366 句 / 2,860 公式。
`verify_output` 的 C1 判据同时收紧：非末句没有句末标点**且下一句以小写开头**才算切断（原来把"display 结尾无标点"也算进去，`danmark` 因此虚高到 320）。

探针：`tests/probe_m1d6.py` 7 条（display 留在句内、孤儿消失、真正分段不被合并、`\iffalse` 消失、`\iftrue` 保留）；`run_all` 20/20。

### 第 3 步已实现（2026-11-18）

| 项 | 改在 | 结果 |
|:--|:--|:--|
| **R1** 名字题三源合并 | `run_paper.sh` 组装 `names_todo.json` 时取并集：`paper_lexicon.unknown_commands`（scope `math`）、`normalize-stats.outside_vocabulary`（scope `text`）、`unresolved.log` 的 `commands not understood`（scope `math`），每项带 `scope` | 正文里的未知名（`\step` 那类）第一次进得了队列 |
| **Q1** 名字题闭环 | 合并后**重跑管线**，只有未知名真的消失才删 `names_todo.json`；模型的 `unknown`/空答案写进 `human_todo.json`（`name_undecided`） | 队列不再被无条件 `rm`；拒答有去处 |
| **Q2** 角色题闭环 | `ask_roles` 每次给未答项记 `asked += 1`；两次仍未答 → `human_todo.json`（`model_refused`）；`unsure` → `human_todo.json`（`role_unsure`） | 静默拒答可见 |
| **人工表** | 新增 `scripts/human.py`：`add/merge/write/load` + `--from-names` / `--from-roles`，按 `(reason, detail)` 去重，写 `OUT/human_todo.json` 并计数 | 三类来源（策略/源码错/模型答不了）有了统一出口 |
| 闸门修正 | 空的 `human_todo.json` 不再算"有待答" | 干净的一篇退出码能回到 0 |

实测：`\step` 之类只出现在正文的名字被问到（`papers.<id>` 作答，`name_ignore` 生效）；空人工表被删除后 `run_paper.sh` 返回 0；`probe_human.py` 6 条、`run_all` **21/21**。

仍留待第 4–6 步：D 组的删除量记账、死分支（J3/J6）、条件式（J1/J2）、宏表（M8）。

### 第 4 步已实现（2026-11-18）

删内容的每一处现在**先记账**，再交给人决定。

| 项 | 改在 | 记录了什么 |
|:--|:--|:--|
| **D1** `\footnote` | `normalize._drop_bare` | `drop:footnote`，`size` = 删掉的字符数；策略问题进 `human_todo.json`（`footnote_strategy`） |
| **D2** float 环境 | `normalize._envs` | `drop:float`，`where` = 环境名，`size` = 整块长度 |
| **D7** 空句 | `latex2nl._process_unit` | `drop:empty_sentence`，`where` = 该句前 80 字 |
| 账本落盘 | `normalize --issues`、`latex2nl --issues` | 各自写 `OUT/issues_normalize.json` / `issues_latex2nl.json`；`verify_output` 在写 `issues.json` 前合并 |
| 人工表 | `human.py --from-issues` | 策略类（`drop:footnote`）与源文件错误进 `human_todo.json` |

实测（最小输入：一句带脚注 + 一个 table）：

```
issues_normalize.json  counts {drop:footnote: 1, drop:float: 1}
                       sizes  {drop:footnote: 39, drop:float: 78}
issues.json            合并后同上
human_todo.json        {footnote_strategy: 1}
run_paper.sh           退出码 1（verify_formulas 报了丢内容，正是 D2 的代价）
```

`probe_dgroup.py` 7 条：脚注计数与体量、float 计数、账本合并、策略进人工表、运行报待答、无删除时不记。

**下一步（第 5 步前的决定）**：`human_todo.json` 里现在有 `footnote_strategy`，
需要人回答"脚注保留为单独单元还是接受删除"，回答后再改 D1；D2 的 `tabular`/`minipage`
按 §3.1 的建议移到 `text_block`/`grid`。

### 第 5 步已实现（2026-11-18）

封闭问题，逐条修掉。

| 项 | 改在 | 结果 |
|:--|:--|:--|
| **J3** 死分支 | `classify_role` 认 canonical 写法 `\begin{grid}{cases}` / `{dcases}` | 分段函数终于判成 `piecewise`（原来 22 篇 87 条 cases，0 条命中） |
| **J6** 死分支 | `env_classes.json` 去掉 `restatable`，它自己的分支才可达 | `\begin{restatable}{thm}{key}` → `statement{thm}` |
| **J1** 条件式 | `read_switches()` 读 `\newif\ifX` 与最后一次 `\Xtrue`/`\Xfalse`；`normalize` 与 `latex2nl` 都从**整个源码**读开关（开关声明在导言区，body 里没有） | `\ifdraft`（无 `\drafttrue`）取 `\else` 支；`\showtrue` 取第一支 |
| **J2** 首个记号 | 扫描时跳过孤立的 `\fi`/`\else`，继续找真正的开头；`\ifthenelse`/`\ifstrequal` 这类**宏**不算开头（否则会误判它们的参数） | 孤立 `\fi` 不再让整段失效 |
| **M3** 原语操作数 | `PRIMITIVE_EAT`/`PRIMITIVE_UNWRAP`：`\penalty10000`、`\kern3pt`、`\setbox0=\hbox{..}` 吃掉操作数；`\uppercase{abc}`、`\mathchoice{..}` 保留组内容 | `\penalty10000` 不再留下 `10000`，`\uppercase{abc}` 变 `abc` |

**没做**（同一步里点名、留给下一轮）：**M8**（`\def\7#1{..}` 控制符号宏、`\def\X(#1,#2)` 分隔参数、`\let\x=a`）——它在宏表里，改动面比这几个大，单独一轮。

实测：`probe_j.py` 5 条（cases 判 piecewise、`\ifdraft` 取 else 支、`\showtrue` 取第一支、孤立 `\fi` 不致命、`\fi` 标记消失）；`run_all` **23/23**。

### 第 6 步（其余小项）已实现（2026-11-18）

| 项 | 改在 | 结果 |
|:--|:--|:--|
| **M12** `\%` | `strip_comments` 数反斜杠的奇偶 | `5\%` 保留成 `5%`，`% 注释` 仍被删 |
| **M5** 带花括号的 label | `scan_refs()` 用 `find_group_end` 取整组 | `\ref{a{b}}` 的 `ref_target` 是 `a{b}`，句子里不再漏 `}` |
| **D3** `\proof{标题}` | 改成 `_unwrap_first_arg`，保留标题文字 | `\proof{Of Lemma 2}` → `Of Lemma 2` 进句子 |
| **D5** 缺失的 `\input` | `read_source` 记进 `MISSING_INPUTS`，CLI 写 `human:source_error` | 最小输入：`\input{chapters/missing}` → 账本 1 条，进人工表 |
| **R2** 统计文件名 | `run_corpus`/`env_growth`/`rules_table` 统一成 `normalize-stats.json` | 按名读的下游不再漏单篇产物 |

`probe_small.py` 7 条；`run_all` **24/24**。

**仍未做**（按 §8 结尾的"其余小项"里更大或需决策的）：M4（`\mbox{$..$}` 里的 `$` 扫描）、
M6（标题里的 `$` 建占位符）、M7（`\intertext`）、M8（宏表三类）、M9/M10/M11、
D4（裸 `\thebibliography`）、D8/D9/D10/D11、G2/G3/G5、以及跨篇基线。

---

## 11. 从 `PLAN.md` 接过来的两个遗留（2026-11-20）

`PLAN.md` 的 D1–D8、SA、SB 都已做完并各自提交，文件已删；两件**没做完**的事记在这里：

| # | 事 | 现状 | 下一步 |
|--:|:--|:--|:--|
| 1 | 人写的 60 条角色答案要**重新导出** | `reports/roles_sheet_human.md` 仍是 2026-11-17 17:51 的版本，早于 D5 的括号修复 | 在 SA/SB 之后重导一次，避免答案套到旧公式上 |
| 2 | S5 的花括号集合（约 191 条） | 没有留下"这批到底怎么判"的记录；括号现在计入深度 | 若还要按花括号判，重新出一份清单；否则把结论写进 `RULES.md` |
