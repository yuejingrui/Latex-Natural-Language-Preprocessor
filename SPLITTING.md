# 切分层：句子、环境、边界

2026-11-20。本文是 `SPLIT-AUDIT.md`、`ENV-HANDLING.md`、`COMMAND-AS-ENV.md`、
`BRACE-LEAK.md` 四份文档的合并与去重——它们讲的是同一层：`normalize` → `latex2nl`
怎样把 TeX 切成句子、单元和公式，以及这一层里已经修掉、还没修掉的东西。

范围：**不含** DepCCG（见 `DEPCCG.md`）、不含角色判定（见 `RULES.md`/`DECISIONS.md`）。

---

## 1. 这一层的不变量（当前 HEAD 实测）

| 不变量 | 结果 |
|:--|:--|
| 分词无损（`"".join(tokens)` == 句子去空白） | 100% |
| 空 display | 全语料 **0**（`out/corpus-audit/report.md`） |
| 残留 `\[`、`\]` | 0 |
| `$` 不配对 | 0 |
| normalized 里的环境种类 | 只有 `display`、`statement`、`diagram`、`list` 四种 |
| 句子里有裸花括号 | 21 篇合计 **2**（1901.04848 的 `\{`，转义括号） |
| 句子里有控制字 | 硬检查 C2 全语料 0（2026-11-20 清理后） |
| `sid` 唯一 | 是 |
| 孤儿 display | 524 个里 49 个（9.3%），前一句无句末标点的只有 1 个 |
| `verify_output` 硬检查 | 20 篇 + danmark **全 0** |

复验：`python3 nlprep/scripts/verify_output.py OUT`，以及
`out/split-audit-baseline.md`（324 句无句末标点的分类清单）。

---

## 2. 段落与句子边界

### 2.1 注释行不是段落分隔（已修，`abd3e9b`）

`strip_comments` 原来"去掉注释、留下空行"，而空行是段落分隔：TeX 里**只有注释的行不产生
任何输出**，所以这是与 TeX 语义相反的切分。

| 行 | 现在的处理 |
|:--|:--|
| `text % comment` | 去注释，**保留该行** |
| `   % comment`（整行只有注释） | **整行丢弃**，不产生空行 |
| 真空行 | 仍是段落分隔 |

效果（20 篇 + danmark）：句子 13,372 → **13,300**；无句末标点 456 → **324**；
danmark 1,366 → **1,349**（这是当时的数，之后又有变化，见基线清单）。

### 2.2 句中 display 并回（已修，GAPS M1）

display 前的空行只在**前文有句末标点**时才算段落边界；display 后紧跟小写继续时并回。
`danmark` 的 C1（句子被切断）320 → 1，20 篇句子 12,234 → 12,014。

### 2.3 边界规则（2026-11-21 复核补）

| 情形 | 规则 |
|:--|:--|
| 句号在括号内：`(… MATH529.) Then` | `.)`、`?)`、`!)` **结束句子**（句号后紧接右括号） |
| 列表标签：`1.`、`a.`、`(i)` | 标签里的句点**不算句界**（按 item 单元处理，保护首标签） |
| `\item[(ii)]` | 去方括号、保留标签文字 `(ii)`；**只有公式的条目也保留**（否则公式成孤儿） |
| 冒号后跟 display | 冒号是引导词，**不切** display：`… ring: MATH95` 是一句 |
| `flat\cite{…}`（源里没空格） | 占位符与前面的词之间补一个空格：`flat REF58` |
| `figure`/`table` 浮动体 | 图形丢弃，**`\caption` 文本保留**为正文（图注是内容） |

### 2.3 句界猜测的已知取舍

| 情形 | 处理 |
|:--|:--|
| `A. This set is open.` | 单字母首字母**保留句点**只在两个以上连写时（`S.N.`）；`A.` 是句子结束 |
| 列表条目之间 | 每个条目是独立单元，句子不跨条目（**设计取舍**，见 §6） |
| display 以 `,` 结尾 | 不并回（列表/枚举的形状），C1 也据此跳过 |

---

## 3. 环境：未知环境怎么处理

### 3.1 现状与规模

| 位置 | 处理 | 计数 |
|:--|:--|:--|
| `normalize._expand_declared_envs` | 论文的 `\newenvironment`：begin 开了环境 → 展开；否则原样保留 | `declared_env_kept:X` |
| `normalize._envs` | 表里没有、也没声明成定理的环境原样保留 | `env_unknown:X`、`unknown_envs` |
| `latex2nl._unwrap_unknown_envs` | **拆壳**：去掉首尾标记、保留体 | `unwrapped_envs` |

`\newtheorem` 声明的环境走 `read_theorem_decls`，直接成为 `statement`。

201 篇抽样（normalize 之后）：仍未知 **27 名 / 117 次**（改前 25 名 / 215 次）。
头部是论文自定义块：`prooff` 23、`Def` 13、`fct` 11、`ex` 10、`dfn` 9、`rmk` 9、
`customthm` 6、`Th` 6 ——它们要么在 `.sty` 里声明（我们只读源文件），要么用了
`\declaretheorem`。

### 3.2 五条规则（已实现，2026-11-18）

| # | 规则 | 落点 |
|--:|:--|:--|
| 1 | 补数学环境：`math` → 行内公式；`IEEEeqnarray(*)`、`subeqn` → `display`；`pNiceArray` 一族 → `grid` | `env_classes.json` |
| 2 | `\begin{X}` 在 `\X` 是已知命令时改写成 `\X`（TeX 语义）：`section` → 标题；`title`/`author` → 前言丢弃；`math` → `\(…\)` | `normalize._envs` |
| 3 | 补画图环境：`forest`、图外的 `scope` → `diagram` | `env_classes.json` |
| 4 | 规则 3 的声明按**代码文字**归类：begin 含 `Proof` 或 end 含 `\qed/\square/\blacksquare/\Box` → `statement{proof}`；`restatable` → `statement`，种类取第一参数 | `_expand_env_uses` |
| 5 | 拆壳只去标记、**不插空行**；紧跟的参数**只去括号、保留文字** | `latex2nl._unwrap_unknown_envs` |

外加：带星号的名字未知、基名已知时回退基名（`description*` → `description`）。

### 3.3 为什么不交给 TeX，也不交给模型

环境含义都有权威来源：`env_classes.json`、论文自己的 `\newtheorem`/`\newenvironment`、
以及 TeX 的语义 `\begin{X}` = `\X`。按 `LLM-SCOPE.md` 的顺序，有权威来源 → 代码。

### 3.4 剩余

- 27 个"一篇一个"的自定义块（`prooff`、`Def`…）——下一轮的输入，不是模型题；
- 容器边界（§6）与 171 条 display 里的散文（`out/corpus-audit/report.md`）。

---

## 4. 命令当环境：`\step` 这一类

### 4.1 现象与判据

论文写 `\step First, …`（裸命令），而第 78–79 行写着：

```latex
\newcounter{steps}[thm]
\newtheorem{step}[steps]{Step}
```

`\newtheorem{step}{Step}` **同时定义 `\step`** 作为这个单元的开始命令（`pdflatex` 实测
`\meaning\step` = `\@thm{…}{step}{Step}`）。所以它是"开一个 Step 块"，不是排版开关。

### 4.2 处理（A 方案，已实现）

| 位置 | 做什么 |
|:--|:--|
| `normalize._part_markers` | 在名字表**之前**把裸 `\X`（`X ∈ theorem_envs`，不含 `*` 与 `proof`/`document`）换成 `\partmarker{X}`；计数 `part_marker:X` |
| `latex2nl` | 标记换成私有区哨兵，按哨兵切段，句子写 `part: {kind, index}`（每个 unit 各自计数） |

`danmark` 实测：`part_marker:step` **16**，118 句带 `part`，`env` 仍是 `proof`，`unit` 仍是
`proof#N`。**B 方案**（嵌套单元、`env=step`）没做：latex2nl 会把嵌套 `statement` 压平，
要先改单元模型。

### 4.3 为什么不是"名字题"

`\step` 有权威（`\newtheorem` 声明），按 `LLM-SCOPE.md` 不该问模型。之前模型答 `ignore`，
把 16 个单元边界删掉了。

---

## 5. 转换残留：花括号与 `$`

### 5.1 裸花括号（已修，`a5b7565`）

正文里 `{...}` 的四个最小复现与修法：

| 输入 | 现在的输出 | 修法 |
|:--|:--|:--|
| `\selectlanguage{english} The text.` | `The text.` | `selectlanguage` 进 `BARE_ONE` |
| `A title \z@{.5\linespacing}{.5\linespacing} follows.` | `A title {.5 } follows.` → 修后无括号 | 分节命令的论文重定义**不读**（`STANDARD_SECTION_NAMES`） |
| `{\unitlength 0.1in $x$} after.` | `MATH1 after.` | `unitlength` 进 `PRIMITIVE_EAT` |
| `Text {is uniformly bounded.} more.` | `Text is uniformly bounded. more.` | 数学抽取之后去掉纯文字平衡组 |

另：`\ensuremath{X}` 现在是"已在数学模式只去壳、否则加 `$`"（D10 的根因，见
`DECISIONS.md`），`{S_\bullet}` 因此变成公式而不是括号。

实测：21 篇句中有花括号 **54 → 2**（剩 1901.04848 的 `\{` 转义括号）。

### 5.2 那 54 句的来源（逐条追到源码）

`out/split-audit-baseline.md` 里"同时缺句末标点"的只有 14 句；C3（句中含裸花括号）全量是
**54 句 / 7 篇**。按源码分类：

| 真实来源 | 句数 | 例 |
|:--|--:|:--|
| **论文重定义了分节命令**（`\def\subsection{\@startsection{…}}`） | **27** | `{.5 } *{Background and motivation}`、`{Connected components of MATH1337.}` |
| 其它格式命令或宏展开留下的组 | 23 | `{Proof of Theorem REF42:}`、`{S_\bullet}` 的双层组 |
| 作者自己写的纯文字组 | 3 | `{is uniformly bounded.}` |
| `\selectlanguage{english}` | 1 | — |

**一处更正**：曾有判断把这些归为"beamer 的 frame/block 标题"——**不对**。1512.07573 与
2011.07716 里 `\begin{frame}` 各 0 次，来源是两篇各自用 `\@startsection` 重定义了
`\subsection`（1512 的 `main.tex:198`、2011 的 `lib.sty:297`）。全语料有 **93 篇**这样
重定义分节命令，损失不是括号，而是**标题进不了 `section` 字段**——§5.1 的第一条修法就是冲它去的。

### 5.3 `$` 与 display 的奇偶

`$$\mathcal W$$` 曾把行内公式变 display，并使之后所有 `$` 奇偶翻转——那段历史见
`DECISIONS.md` D10。现在的检查：**C6 硬**（公式体里有 `$` 或 `\begin{display}`）、
**C16 软**（display 读起来像散文）。

---

## 6. 设计取舍与基线清单

### 6.1 容器边界（不算缺陷）

`1901.04848` 有 73 句以连词/逗号结尾（`If either`、`… is odd, or`），因为那句话跨了
`itemize` 的多个条目，而**每个条目是独立单元**。要改就得允许句子跨条目，那是容器模型的
取舍。C1 因此跳过 `,`/`;` 结尾和 `item` 单元。

### 6.2 良性

- 句末标点写在公式内部：`\operatorname{rigidity.}` 这类，`trailing_punct` 为空是因为标点在
  命令参数里；
- `Proof of Proposition REF257` 这类证明标题：本来就不是完整句。

### 6.3 基线清单

`out/split-audit-baseline.md`：21 篇、13,300 句里 **324 句无句末标点**，按九类分组

| 类 | 句数 | 性质 |
|:--|--:|:--|
| 标点在公式里 / 括号内 | 31 + 22 | 良性 |
| 容器边界（列表条目/逗号结尾） | 137 | 设计取舍 |
| 标题（`Proof of …`、`Step …`） | 9 | 非句子 |
| 句末是 display / inline 公式、正文无标点 | 72 + 12 | 待核（多为原文如此） |
| 裸花括号残留 | 14 | **已修**（现在 2） |
| 疑似被切断 | **0** | 注释行修复后清零 |
| 原文缺句末标点（需复核） | 27 | 人工看 |

生成：`python3 nlprep/scripts/split_audit.py --run '/tmp/sp20/*' --run test-danmark/danmark -o nlprep/out/split-audit-baseline.md`。

---

## 7. 探针

| 探针 | 覆盖 |
|:--|:--|
| `probe_split.py` | 句界、缩略语、注释行、分段 |
| `probe_envs.py` | 五条环境规则与回归 |
| `probe_brace.py` | 花括号五条、`\ensuremath` 三条 |
| `probe_part.py` | `\partmarker`、`part` 字段、`env` 不变 |
| `probe_audit.py` | AUDIT §2 的 19 条复现（含未闭合环境、`\ifcase`、`\tag`、孤立反斜杠、`\end {env}`） |

`run_all.py` 全部串起来跑。
