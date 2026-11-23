# 未修清单（本项目）

2026-11-23 修复后的行为、数据接口与验收结果见 [修复说明](REPAIR-2026-11-23.md)。本文原有的较早测试数字和未修状态保留为历史记录；涉及脚注/浮动块删除、纯公式过滤、条件默认取第一支及 strict 退出码的旧描述，以修复说明为准。


2026-11-20。**只列 `nlprep/` 这个仓库里还没修的东西**，按"是不是真缺陷"分组，每条带
证据/数字、落点和修法出处。`GTED/`、`dsr/` 属于别的项目，见文末 §6。

已修的部分不在这里（`AUDIT.md` §1–§2、`DECISIONS.md` D1–D11、`REVIEW.md` 本仓库两条、
`SPLITTING.md`、`out/corpus-audit/report.md`）。

---

## 0. 复核更正（2026-11-21）

`2005.01702` 的逐句复核提出的五类问题已在切分层修掉：`.)` 句界、列表标签与只有公式的条目、
冒号后的 display、引用占位符粘字、图注丢失。规则写进 `SPLITTING.md` §2.3；
该篇现在 357 句 / 779 公式、硬检查 0，20 篇抽样 `drop:empty_sentence` 236 → **57**。

## 0. 复核更正（2026-11-20）

上一版有 5 处要改，依据是当天在 HEAD 上的重跑与最小复现：

| 条目 | 上一版 | 实测 |
|:--|:--|:--|
| 1.3 的 1803.07553（56 条） | "`\begin{equation}` 头被吃掉一半" | 论文自己写了 `\def\[#1]#2{ \begin{#1}#2\end{#1}}`，用了 **106 次** `\[equation]{…}`。这是**分隔参数宏**，归 §1.2，不是新问题 |
| 1.3 的 1908.00378（37 条） | "`\item` 列表套在 display 里" | normalized 里出现 `$b\in$\mathcal{B}$$`：这篇把 `\cB` 定义成 `\ensuremath{\mathcal B}`，`\ensuremath` 修改**又插了 `$`**、把奇偶翻转了。最小输入复现不出，说明是这篇前面某处先把数学模式判错。**D10 的根因没有完全关掉** |
| 1.4 `\{` 残留 2 句 | 未修 | 当前 HEAD 上 1901.04848 带花括号的句子是 **0**，本条可关闭 |
| 1.5 账本"只有 `verify_output` 一个生产者" | — | normalize 有 3 处 `record()`（`drop:float`/`drop:footnote`、`human:source_error`、`conditional:unknown`），latex2nl 有 2 处。后半句（GAPS 第 1 步的 10 个高频点没逐点记账、22 篇基线没跑）仍然成立 |
| 4.5 "约 140 MB" | — | 实测 **36.2 MB**（float32）。`DEPCCG-ISSUES.md` §4 已更正过 |

另：2.1 与 2.2 是同一处代码，可合并。

---

## 1. 真缺陷（有数据在丢或错，未修）

### 1.0 颜色命令与保留宏把字粘进正文——**已修（2026-11-20）**

**现象**：两种写法都会把额外的字粘进正文，改动的是**句子里的词**，比 1.1 影响面大。

| 输入 | 现在的句子 |
|:--|:--|
| `\textcolor{blue}{Let it be.}` | `blueLet it be.` |
| `\newcommand{\rev}[1]{{\color{blue}#1}}` 后写 `\rev{Let it be.}` | `\revLet it be.` |

- **第一行**：`textcolor` 在名字表里的动作是 `ignore`（只删命令名），**颜色参数 `blue` 留下**，和后面的词粘在一起。全语料 **1,137 篇**用到 `\textcolor`。
- **第二行**：`color` **不在** `tex_names` 里。所以 `2432509` 新增的"展开后含未知名字就保留宏名"规则把 `\rev` 原样保留，而到了句子层它的参数花括号被剥掉，宏名粘在正文上。宏体里有 `\color` 的论文 **1,251 篇**。

**规模**（201 篇抽样）：句中出现"控制字或颜色词粘着单词"**97 处 / 11 篇**。这批是混合的——
`\mcite` 这类保留宏（2009.03492 的 `\mciteMkz:GTGA`）、颜色词（`blueMATH1349`）、
以及 2201.10462 里没识别出来的 xymatrix 图漏出的 `\operatornameHom`。

**修法**：

| # | 做什么 | 位置 |
|--:|:--|:--|
| 1 | `\textcolor` 改成"丢掉第一个参数、展开第二个参数"；`\color` 加进名字表，作为"在当前组内生效"的格式命令 | `tex_names.json` / `normalize.py` |
| 2 | 保留宏（`kept_unknown`）出现在正文时，latex2nl 不能把它的参数花括号剥掉：要么整体当一个不透明单元，要么在宏名与参数之间留一个空格 | `latex2nl.py` 的组处理 |
| 3 | 新增一条检查：句子里出现"控制字后面直接跟字母"（如 `\revLet`）即报出 | `verify_output.py` |
| 4 | 验收：上面两个最小输入得到 `Let it be.`；201 篇那 97 处归零；现有检查不新增误报 | — |

**已做**：① `\textcolor{colour}{content}` 只留 content（`normalize._drop_bare`）；
② `\color` 进已知名字集合——`_known_name` 原来只读 latex2nl 的全局表，漏了 Normalizer 自己的
`BARE_ONE` 等表，`\color` 因此被当未知、整个宏被保留；
③ 未知/保留宏带参数组时，去括号但**留一个空格**（`latex2nl._strip_text_groups`）。
两条最小输入现在都得到 `Let it be.`，`probe_audit` 加了三条检查。④ 的 201 篇 97 处尚未重测。

### 1.1 名字层的**静默丢弃**

**现象**：`pending_names.py` 对"论文定义过的名字"有两处 `rec.pop`，直接删掉，**不问、不记账**。

**HEAD 复现**（2026-11-20）：

```
\DeclareMathSymbol{\R}{\mathbin}{AMSb}{"52}
$\R^2$               -> 修前：pending_names 输出 0 道题（\R 被 rec.pop）
                         修后：1 道，reason=definition_not_parsed，附定义体
```

（`\DeclarePairedDelimiter\norm` 这一例在本机被名字表当作已知，不落在这条；
`norm` 在 1809.03803 的 65 次是另一回事，见 §1.1 的注。）

**已做（2026-11-20，只做修法第 1 步，不改输出）**：两处 `rec.pop` 改成单列一类，
`reason` 分 `definition_not_parsed` / `defined_but_unexpanded` / `kept_macro` /
`cyclic_definition`，附定义体与一个用例；`--export` 打印 `by reason: …`；
账本新增软类别 `name:defined_unexpanded`。

**已做**：第 2 步（`\DeclarePairedDelimiter`、`\DeclareMathSymbol` 的解析）——见 §1.2。
**未做**：第 3 步（宏表内未展开的 210 次）；201 篇重放的 641 次现在**进了待办**，其中 431 次这一轮已能展开。

**规模**（201 篇抽样重放）：被丢 **641 次 / 48 个名字**，同批留在待办的只有 346 次。

| 被丢原因 | 次数 | 例 |
|:--|--:|:--|
| 源码里有定义，但宏表没解析出来 | **431** | `\DeclarePairedDelimiter\norm{…}`（1809.03803，106 次）、`\DeclareMathSymbol{\R}{…}{AMSb}{"52}`（1605.06481，75 次） |
| 在宏表里，但正文里没展开 | **210** | `\sJ`、`\raisepunct`、`\res`、`\endpf` |

**注意**：2026-11-20 的保留宏工作只让「normalize 故意保留的宏」进了待办（`kept_unknown`），
**没有**让"定义没解析出来"的名字进待办。

**修法**（`OPEN-PROBLEMS.md` §7.1 修法表，按顺序）：

| # | 做什么 | 改不改输出 |
|--:|:--|:--|
| 1 | 两处 `rec.pop` 改成单列一类，按"定义没解析出来"/"在宏表里但没展开"分因，附定义体与用例，计数进账本（新增软检查 `name:defined_unexpanded`） | 不改 |
| 2 | 补宏表缺的定义形式：`\DeclarePairedDelimiter(X)` 按 mathtools 语义展开；`\DeclareMathSymbol` 按字体编码位换字符（AMSb `"52` → `\mathbb{R}`） | 改 |
| 3 | "在宏表里但没展开"：记下未展开的宏名与原因；属"展开后含未知名字"的保留宏名、单列一类回待办，答案强制带 `paper` | 改 |
| 4 | 验收：`\norm{x}`/`\R^2` 不进待办；201 篇被静默丢弃 **641 → 0** | — |

**落点**：`nlprep/scripts/pending_names.py:620-635`（两处 `rec.pop`）、`normalize.py`（补定义形式）。

### 1.2 宏表缺的定义形式（GAPS M8）——**已修（2026-11-20）**

| 形式 | 例子 | 现状 |
|:--|:--|:--|
| 控制符号宏 | `\def\7#1{…}` | 不解析 |
| 分隔参数宏 | `\def\X(#1,#2){…}` | 不解析 |
| `\let` 别名 | `\let\x=a` | 只把 `\let\a=a` 整条丢弃；别名不解析 |

`\let\a=a` 的"字符留在正文"已修（AUDIT 小项）；`\let\x=\y` 的别名解析仍未做。

**已修**：`\def\7#1`（控制符号名）、`\def\X(#1,#2)`（分隔参数）、`\DeclarePairedDelimiter(X)`（按 mathtools 语义）、`\DeclareMathSymbol`（按字体编码位换字符，AMSb → `\mathbb`）现在都能解析；四条最小输入在 `probe_audit` 里。

**已修**：`\let\x=a`（字符目标）现在解析成 0 参宏（`MacroTable._lets_char`）；
`\def\[#1]#2{…}` 这种**非圆括号定界**的宏，normalize 在 `\def` 存在时把
`\[equation]{…}` 改写成 `\begin{equation}…\end{equation}`（`_expand_bracket_display`）。
1803.07553 的"像散文的 display"由 **70 → 9**（原来记的 56 是这一类）。

**仍未修**：`\let\x=\y` 中**目标为另一个控制序列**且两者都在宏表里的情形（`_resolve_lets`
已有保护逻辑，未覆盖的只是"别名与目标互相定义"的绕圈写法，见 `tangled` 分支）。

**修法出处**：`GAPS.md` M8；`OPEN-PROBLEMS.md` §3。

### 1.3 D10 的尾巴：171 条 display 像散文——**已修（2026-11-20）**

| 论文 | 条数 | 形状 |
|:--|--:|:--|
| 1803.07553 | 56 | 论文自定义 `\def\[#1]#2{ \begin{#1}#2\end{#1}}`（106 次 `\[equation]{…}`）→ **归 §1.2 分隔参数宏**，不是独立问题 |
| 1908.00378 | 37 | `\cB` = `\ensuremath{\mathcal B}`，`\ensuremath` 又插了 `$` 导致奇偶翻转（见 §0）→ **D10 的残留** |
| 其余 77 篇 | 78 | 每篇 1–2 条（长 `\text{…}`、未闭合 `\left(`） |

全语料 11,171,537 个公式里 171 条（0.0015%）。清单：`out/corpus-audit/displays.jsonl`；
报告：`out/corpus-audit/report.md`。D10 的**主根因**已修，但 1908.00378 的 37 条说明
`\ensuremath` 的处理还有残留：

**已做**：normalize 不再把 `\ensuremath{…}` 改写成 `$…$`（`_ensuremath_to_math` 原样返回），latex2nl 把它当行内数学区起点，公式体里的 `\ensuremath` 外壳被剥掉。
1908.00378 的散文 display **37 → 0**；1803.07553 **70 → 2**；20 篇抽样 C16 **0**、C6 **0**、hard 0。

### 1.4 ~~`\{` 残留 2 句~~（已关闭）

1901.04848 的 `\Set` 宏曾把 `\{…\}` 写在非数学模式。**2026-11-20 复测：0 句**带花括号，
`a5b7565` 之后已修。本条关闭，保留记录。

### 1.5 账本没接线，22 篇基线没出——**已做（2026-11-20）**

| # | 改什么 | 落点 |
|--:|:--|:--|
| 1 | `normalize.py` 与 `latex2nl.py` 的 CLI 都写 `issues_*.json`；**修掉一个真缺陷**：`latex2nl` 原来在 `convert()` **之前**写账本，写出来永远是空的 | `latex2nl.py` main |
| 2 | `run_corpus` 给两个阶段都传 `--issues`，`verify_output` 合并成 `issues.json` | `run_corpus.py` |
| 3 | 新接线四类：`display:orphan`、`math:unclosed`、`unresolved:line`、`env:unknown`；原有的 `drop:footnote`/`drop:float`/`drop:empty_sentence`/`conditional:unknown` 保留 | `latex2nl._note`/`_process_unit`/`find_math_spans`、`normalize._envs` |
| 4 | `scripts/ledger_baseline.py`：扫 run 目录，按 kind 汇总成 markdown | 新脚本 |

**22 篇基线**：`out/ledger-baseline.md`（`--step 280 --limit 22` 抽样 21 篇 + danmark = 22）。
重跑：

```sh
python3 nlprep/scripts/run_corpus.py --step 280 --limit 22 --out out/baseline-runs
sh nlprep/nlprep.sh paper danmark.tex out/baseline-runs/danmark --no-ask
python3 nlprep/scripts/ledger_baseline.py 'out/baseline-runs/*' --verify -o out/ledger-baseline.md
```

| kind | papers | total |
|:--|--:|--:|
| `check:C13` | 22 | 1,443 |
| `unresolved:line` | 19 | 671 |
| `drop:empty_sentence` | 20 | 265 |
| `display:orphan` | 20 | 179 |
| `check:C7` | 3 | 57 |
| `drop:float` | 6 | 43 |
| `check:C4` | 9 | 25 |
| `check:C5` | 4 | 16 |
| `drop:footnote` | 6 | 6 |
| `human:source_error` | 3 | 3 |

**hard 合计 0**，有 hard 的篇数 0。

**已做（2026-11-20 补）**：R1 —— 名字题现在带 `scope: text`（`outside_vocabulary` 那条）；
Q1 —— `--merge` 有 rejected 或未定答案时返回 **1**，并打印未定个数。
**已做（2026-11-20 补）**：M2 —— 新增 `data/name_overrides.json`，人工更正可以覆盖 TeX 的判读（`NameTable._add` 的 `origin == "override"` 例外），探针锁住。
`math:unclosed` 与 `env:unknown` 在 22 篇里是 0，未被真实数据触发。

### 1.6 条件式 J1：`\ifx` 与未知 `\if*`——**已接线（2026-11-20）**

`\ifcase` 已修（只留选中支）。仍开放：

| 项 | 数量 | 现状 |
|:--|--:|:--|
| 未知 `\if*` | **132 处 / 26 篇** | 取第一支 |
| `\ifx` | 16 处 | 取第一支 |

**已做**：`read_switches` 读 `\newif` + `\Xtrue/\Xfalse`（原有）；未知条件式现在把**两支文本**
写进 `conditional_todo.json`（`COND_TODOS`、`_branch_pair`），答案 `branch1`/`branch2`/`both`。
未答的仍取第一支，但不再静默：账本 `conditional:unknown` + 待办文件。

### 1.7 死分支与删除量记账——**J3/J6 已确认**

**J6**：`restatable` 早已从 `env_classes.json` 移除（文件里留了日期注记）。
**J3**：`classify_role` 现在认 canonical 的 `\begin{grid}{cases}` 与 `dcases`，最小输入均判 `piecewise`（2026-11-20 复核）。
**仍未做**：D 组的删除量记账（删了多少只有部分计数）。

### 1.8 行内公式在花括号处被切断（C7，**57 → 33**；剩下的 33 是宏展开缺陷，见本节末）

C7 原来按"软检查"处理，取 20 篇抽样逐条看 raw 后确认是**真缺陷**：行内公式的 span 在
`{`/`}` 处被切开，公式体以 `{` 开头或以 `}` 结尾。

| 论文 | 条数 | 例（`raw`） |
|:--|--:|:--|
| 1808.01468 | 33 | `\iota(\psi,y) = \iota(\pi,\iota(\eta,y))\iota(\eta,y)}`（缺开括号） |
| 1512.07573 | 22 | `{`、`}\mathcal A: …`、`\Decbot{({`（在花括号中间被切） |
| 1607.08880 | 2 | `\mbox{` / `-module}` |

20 篇合计 **57** 处。规律：`\text{…}`、`\mbox{…}`、`\Decbot{…}` 或指数里的组
（`{…}^{-1}`）让 `find_math_spans` 的括号深度算错，span 从组中间开始/结束。

**已做**：`$` 的扫描现在跳过**组内**的 `$`（`\mbox{$x$}` 这类嵌套不再把 span 切开），57 → **33**；三个最小复现都平衡。
~~**剩下的 33 全在 1808.01468**：`\sls` 宏把 `subarray` 环境放进上标，收尾的 `}` 悬在公式外，属源文件结构问题；要修得先处理"上标里套环境"的组结构。~~

**2026-11-20 复核：上一句判断不对。** 33 条与 `\sls`、`subarray` 无关，原文是干净的
（`$\io{\psi}{y} = \io{\pi}{\io{\eta}{y}}$`，`\io` 定义为 `\newcommand{\io}[2]{\iota(#1,#2)}`），
根因是**宏展开器在参数里还有同名宏时，把内层再输出一遍并多出一个 `}`**。

#### 1.8.1 嵌套宏重复输出（真根因，一行修）

| 输入 | 现在的输出 |
|:--|:--|
| `\newcommand{\gg}[1]{(#1)}` + `$\gg{\gg{b}}$` | `$((b))(b)}$` |
| `\newcommand{\ff}[2]{(#1+#2)}` + `$\ff{a}{\ff{b}{c}}$` | `$(a+(b+c))(b+c)}$` |
| `\newcommand{\io}[2]{\iota(#1,#2)}` + `$\io{\pi}{\io{\eta}{y}}$` | `$\iota(\pi,\iota(\eta,y))\iota(\eta,y)}$` |

**位置**：`latex2nl.py` 的 `MacroTable._expand_one`（`def` 在 1331 行）。它用
`for m in pat.finditer(text)` 逐个处理匹配；外层宏吃掉参数后把 `pos` 设到参数末尾，但
`finditer` 仍会给出**落在刚吃掉的参数里面**的内层匹配。这时 `text[pos:m.start()]` 是空串，
内层宏被再展开、追加一次，`pos` 又退回到内层末尾，于是外层参数的收尾 `}` 被当成正文追加。
**不是新问题**：从 `abd3e9b`（11-20 00:49）到 HEAD 的每个提交都一样。

**修法**：循环开头加一句——匹配落在已消耗的范围里就跳过：

```python
for m in pat.finditer(text):
    if m.start() < pos:
        continue          # inside the argument just consumed
```

内层宏留在外层的替换文本里，由 `expand()` 的下一轮（共 8 轮）展开，嵌套深度不受影响。

**实测**（在 `/tmp` 副本里只加这两行）：

| 样本 | 改动的公式 | C7 | 深度不平衡的行内公式 | 深度不平衡的 display | 句子数 |
|:--|--:|:--|:--|:--|:--|
| 21 篇（20 篇抽样 + danmark） | **39 / 31,702**，2 篇 | 33 → **0** | 33 → 0 | 7 → 1 | 13,336 不变 |
| 201 篇抽样（`--step 28`） | **86 / 364,494**，9 篇 | 69 → 30 | 41 → **2** | 28 → 8 | 148,298 → 148,305 |

改动集中在 1808.01468（37）、1309.3267（29）、2403.18879（5）等；改后例：
`F_1(x)=x(2- \frac{1}{(x+1)^\beta})(x+1)^\beta}}` → `F_1(x)=x(2- \frac{1}{(x+1)^\beta})`。
**display 也受影响**，而 C7 只查行内，所以 display 里的这类损坏此前没有任何检查报出。

**守卫**：宏体与参数在 TeX 里都是平衡的，展开结果也必然平衡。在 `_expand_one` 出口断言
"替换文本花括号平衡（跳过 `\{`/`\}`）"，不平衡就保留原文并记账，不输出半个公式。

**探针**（修前应红）：上表三个输入，期望 `$((b))$`、`$(a+(b+c))$`、`$\iota(\pi,\iota(\eta,y))$`；
另加 `$\gg{x}\gg{y}$` → `$(x)(y)$`（相邻、不嵌套，修前修后都应正确）。

#### 1.8.2 C7 把转义括号算成不平衡（检查误报）

`check_c7` 直接比 `raw.count("{") != raw.count("}")`，不跳过 `\{`/`\}`：`$a\}$` 会被报成 C7，
按 TeX 语义它是平衡的。201 篇里 1.8.1 修完后 C7 还剩 30，而按深度算真正不平衡的行内公式只有
**2**——**28 条是这个误报**（如 1911.01242 的 `\widehat{P}_{…} = \{ \begin{grid}…`）。

**修法**：C7 改成数深度，遇 `\` 跳过下一个字符；深度中途 < 0 或结尾 ≠ 0 才报。同样的判据
可以把 display 也纳入（目前 C7 只查行内）。

#### 1.8.3 `subarray` 被拆壳，列格式 `{c}` 漏出，并可能在行内数学里留下空行（次要）

`subarray` 在 `env_classes.json` 里归 `inner_display`，被**拆壳**而不是像 `array`/`smallmatrix`
那样转成 `grid`：

| 输入 | 现在的 normalized |
|:--|:--|
| `$x_{\begin{subarray}{c} a \\ b \end{subarray}}y$` | `$x_{\n{c} a \\ b\n}y$`（`{c}` 漏进公式） |
| `$x_{\begin{array}{c} a \\ b \end{array}}y$` | `$x_{\begin{grid}{array}a \\ b\end{grid}}y$`（正确） |

1808.01468 里 `subarray` 被拆壳 **57 次**。拆壳时补的换行如果和宏体里 `%` 行留下的换行相邻，
就成了空行；行内数学里的空行会被 `_prose_units` 当段落分隔，把一条公式切成几段（最小复现：
`\sls` 写在 `$…$` 里时，`subarray` 的内容变成一句正文）。1808.01468 正文里 `\sls` 只用了 3 次、
大多在 display 里（display 的空行已由 `_tighten_math_blocks` 去掉），所以这篇**目前没有**这样的句子。

**修法**：

1. `subarray` 归入 `grid`，第一个参数按 `array` 的列格式处理（amsmath 里它就是带对齐参数的 array）；
2. 数学环境内的拆壳不补换行；
3. 守卫：`_prose_units` 切完段落后，若某单元里未转义的 `$` 是奇数个，记一条硬问题。

**规模待量**：上一轮报的"201 篇里 9 篇 32 处行内数学含空行"是用粗略的 `$` 计数得到的，
会被 display 块和转义打乱，**数字不可靠，撤回**；修 1、2 之后用上面第 3 条守卫重新计数。

#### 1.8 的顺序

| # | 做什么 | 理由 |
|--:|:--|:--|
| 1 | 1.8.1 跳过已消耗范围内的匹配 + 出口平衡守卫 | 真根因；一行修；display 里的同类损坏也一并修掉 |
| 2 | 1.8.2 C7 按深度计 | 去掉 28 条误报，C7 才能当闸门 |
| 3 | 1.8.3 `subarray` 归 `grid`、数学里拆壳不补换行、奇数 `$` 守卫 | 影响小，但防同类静默损坏 |


---

## 2. 平台相关（本机不可复现）

| # | 项 | 说明 |
|--:|:--|:--|
| 2.1 | `pending_names.py` 的 `CORPUS` 只在 fork 下可见 | 默认 spawn 的平台（macOS/Windows）下全部变 `__error__`；Linux 是 fork，测不出来 |
| 2.2 | AUDIT 第 46 条 | 同上，同一处 |
| 2.3 | `audit_displays.py` 的 `_root_tex` 只找顶层 `*.tex` | 2202.12876 的根在一层子目录里，审计时被记为 "no .tex"；已改为先读 `manifest.tsv`（2026-11-20） |

---

## 3. 软检查里的残留（不是缺陷，只列不卡）

| 检查 | 内容 | 现状 |
|:--|:--|:--|
| C13 | 引用目标不在文内 | 每篇 30–185 条，外部引用，正常 |
| C4/C5/C7 | 短句、括号、行内公式 | 少量，未逐条看 |
| C16 | display 像散文 | 与 §1.3 同一批，171 条 |

---

## 4. DepCCG 侧（解析层，`DEPCCG-ISSUES.md`）

| # | 项 | 现状 |
|--:|:--|:--|
| 4.1 | `run_corpus` 的解析三列（解析率、叶子一致、根分布） | 没接 |
| 4.2 | `usage` 字段（从句 / 名词 / 修饰） | 只有 S/P/O 三个 span，没有"这个公式在句中干什么" |
| 4.3 | 金标准 100 句 | 没有；S/P/O 的三个已知错误类（expletive 主宾倒置、系表/被动补语当宾语、从句宾语不猜）只记录了，未系统评估 |
| 4.4 | `spans` 的两处细化 | 8b（系表/被动补语）、8c（补语字段）没做 |
| 4.5 | 内存分块 | 实测 **36.2 MB**/1,366 句（float32），按比例一万句约 265 MB；`-p` 不随内存自适应 |

---

## 5. 建议顺序

| 顺序 | 项 | 理由 |
|--:|:--|:--|
| 1 | **1.0 颜色命令与保留宏粘字** | 改动的是正文里的词，影响上千篇 |
| 2 | **1.1 静默丢弃**（只做修法第 1 步：不丢、分因、记账） | "数据在丢且没人知道"，且不改输出 |
| 3 | **1.2 宏表的三种定义**（含 1.1 第 2 步；顺带解决 1803.07553 的 56 条） | 与 1.1 同一个入口，补完 431 次的大头 |
| 4 | **1.3 的 `\ensuremath` 残留**（改由 latex2nl 判数学模式） | D10 没关干净，1908.00378 的 37 条 |
| 5 | **1.5 账本接线 + 22 篇基线** | 之后所有修复都能被账本量化 |
| 6 | **1.8 C7**：先修嵌套宏重复输出（1.8.1，一行），再让 C7 按深度计（1.8.2） | 33 条全是宏展开缺陷；201 篇里 86 条公式受影响，含 display |
| 7 | **1.3 其余 78 条 / 1.6 / 1.7** | 量小，逐篇查 |
| 8 | **§4 DepCCG** | 解析层，独立于上面 |

---

## 6. 不在本项目

`GTED/`、`dsr/` 的批次 4、7–10（含 minif2f 多数投票）。只保留记录，不排期、不验收
（`REVIEW.md` 已按此改写，`1d87b8a`）。
