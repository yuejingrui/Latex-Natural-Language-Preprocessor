# 处理规则总表

## 钩子、分组与公式边界

1. 文档开始、导言结束、文档结束、环境开始和环境结束的已支持钩子，在对应位置插入其生效内容。环境的持久钩子每次进入或退出时执行，一次性钩子执行一次。自定义环境的字面开始、结束文字及其参数都属于论文内容。按行处理 `comment` 宏包声明的排除与恢复区段，排除区段里的公式和文件调用都不参与切分。
2. 普通宏跟随调用点的作用域。对支持的 `\edef` 和 `\xdef`，在定义点展开可静态求值的操作数，`\noexpand` 保护的标记留到调用时处理。`\aftergroup` 队列按登记顺序在当前分组结束后执行。`\globaldefs` 决定后续赋值的作用域。字面整数按源码处理，受支持的计数寄存器操作数可以从实际构建中读取，无法判定作用域时进入待办。
3. `\begin{math}…\end{math}` 是行内公式。数学边界内的末尾感叹号保留在公式中，包括 `n!`、`n!!`；公式外的标点按正文规则切句。`\mathchoice` 连同四个分支完整保存。计数器出现在公式中时保留原操作数，执行模式可将单独放在花括号里的受支持计数器替换为当次观测到的数值。
4. 固定参数数目的计数、长度和目录命令只消耗实际参数。例中 `\setcounter{section}{2}{Keep $x$.}` 的第三组生成正文和公式。`\IfFileExists` 可按 TeX 实际执行的分支切分，静态模式保留未定分支。

## 真实构建中的正文选择

1. 启用 `--tex-engine` 时，先对同一份项目分别进行原样构建和带执行标记的构建，再归一化。两次构建使用相同引擎、同名主文件、独立的辅助文件和关闭 shell escape 的参数。出现常见交叉引用重跑提示时各自最多运行三遍。最终两份 PDF 的提取文本、项目内输入文件列表和编译遍数必须一致。
2. 在可定位的正文或导言区读取字面条件。支持已在扫描到的项目 TeX 源中声明的 `\newif` 开关、`\iftrue`、`\iffalse`、控制词操作数的 `\ifdefined` 和 `\ifx`，包括前置 `\unless`。每次执行分别读取所选分支的标记，回放该分支的原始源码。嵌套条件只回放实际选中的外层范围。
3. 对字面 `\input{路径}` 和 `\include{路径}`，匹配调用、进入目标文件和返回事件。排除章节须收到内核的 `include/excluded` 事件。重复输入同一文件时按每次调用分别处理。`.fls` 的文件清单只用于核对依赖。生效的 `\endinput` 会截断当前输入文件，其后同一物理行有正文时保留待办。
4. 文首和文末钩子按实际注册与执行次数核对，在对应位置放回回调原文。普通公式沿用既有切分规则。对于明确数学边界内可单独定位的字面条件，按每次执行的分支标记选择公式原文。条件位于公式参数组内或使用未支持的分支结构时，保留原式和待办。一次调用中无法配对的事件仍阻止验收通过。
5. 能定位边界的未观测正文条件可整体保留为待处理块，前后句子继续切分。编译失败、执行事件不配对、观测改变 PDF 文本，以及无法局部隔离的控制流均保留主文件及已映射子文件源码，阻止严格验收通过。重新运行时从原稿重新构建并生成报告。支持范围与复现命令见 [修复说明](REPAIR-2026-11-23.md) 和 [README](README.md)。

## 2026-11-23 更新：切分验收与公式理解分离

正文起止识别跳过宏声明、注释和逐字材料中的文档标记。普通宏定义中保存的文末命令只有在调用展开后才影响静态切分；未调用的定义不得截断后文。

以下规则优先于本文历史表格和先前修复说明中的“未知命令进入待办”要求。

1. 切分负责确定文本/公式边界、保留内容、建立占位符和记录关联；不要求解释数学符号、公式角色或公式内部文字的逻辑作用。
2. 命令表是已知转换与可选注释的依据，不是公式准入名单。明确数学边界内的未知命令不触发切分失败或 pending，不通过不断补白名单取得通过。
3. 对含未知命令的公式，保留整个公式体及参数分组，作为 opaque 公式输出；不猜未知命令的参数个数，不拆其内部 qquad，不提取或改写其内部文字槽，不推断其数学角色。归一化阶段同样保护它，已完成的宏展开/条件处理不因此被声称无损。
4. 未知命令清单、未定公式文字角色进入单独的下游诊断，不计入切分 pending。结构检查仍执行：公式边界不闭合、括号不平衡、占位符失联、token 丢失继续阻止通过。
5. 正文中的未知命令、未知宏隐藏的数学环境、未定条件会影响边界或有效内容，仍保留原文并报警/待处理。不能将“公式内部未知命令可保留”推广成所有未知 TeX 都可忽略。
6. strict=0 只表示切分检查通过；下游诊断可以非空。表格正文未切分、句界未定、条件未定、历史删除记录仍为 pending。旧版未分类 unresolved 保守保留，重新生成输出才应用新分类。

普通正文宏按调用处生效的定义和参数展开，展开后再识别数学边界。缺少定义时，保存命令及相邻可选组和花括号组，用 REF 指向待处理块；相邻组仅表示保存范围，不推断宏的参数个数。补齐定义后从原始输入重跑。

条件按源码顺序求值，普通分组和数学分组内的局部赋值均随分组退出。选支前须读完测试操作数，保留参与文字比较的空格，并计入条件前的取反指令。查询控制序列是否已定义时，优先使用执行到该处的赋值或声明。缺少定义状态、完整操作数或有效作用域时，保留整个条件和各分支，严格验收返回待处理。

例：`$\mySpecialOperator{x}{y}$` 无需注册命令即可输出 MATH 占位符，完整保存 `\mySpecialOperator{x}{y}`。`$\accentset{\ast}{W}$` 也采用这一通用规则，不为星号补数学解释。

当日各轮变更、严格退出码和反例对照见 [11 月 23 日修复汇总](REPAIR-2026-11-23.md)。


这张表回答一个问题：**遇到一种 LaTeX 构造，管线怎么判**。
它不按 TeX 的 atom 类（mathord/mathrel/…）判，按**它在论文里是内容还是排版**判。

第 2 节的命令表取自本包 `Normalizer`。第 3、4 节列环境及答案库，上游历史抽样数字列在第 8 节。

---

## 一、六个动作

| # | 动作 | 判据 | 代表 | 代码 |
|:--|:--|:--|:--|:--|
| 1 | **读声明**：名字是寄存器/环境，不是词 | 论文自己声明过 | `\newlength\bshft`、`\newsavebox\myboxA`、`\newtheorem{rmk}{Remark}` | `normalize()` 里扫 `newlength/newdimen/newskip/newcount/newtoks/newsavebox/newbox/newif/chardef/mathchardef`；`read_theorem_decls()` |
| 2 | **保内容、丢外壳** | 外壳只说明怎么画 | `\abs{x}`→`|x|`、`\scalerel*{(}{x}`→`(` | `_unwrap_one`、`_unwrap_last_arg`、`_unwrap_first_arg`、`_wrap_arg`、`UNWRAP_ONE` |
| 3 | **清理排版** | 只消耗该命令的操作数 | 间距 `\medspace`、尺寸、计数器更新。`\marginpar` 等注释内容另存，`\tag` 可保留为公式标签 | `drop_metadata`、`_drop_bare`、`_preserve_notes` |
| 4 | **改写为等价** | 同义，换个写法 | `\nicefrac`/`\mfrac`→`\frac`、`\colonequals`→`:=`、`\dots`→`…` | `_drop_bare` 里的 `re.sub` 段 |
| 5 | **环境标记**：容器按十类处理 | 环境语义 | `display`/`grid`/`list`/`statement`/`diagram`/`drop`… | `_envs()`、`data/env_classes.json` |
| 6 | **影响切分且未定 → 队列** | 正文命令、隐藏边界或条件分支未定；公式内部未知命令只保留并记录下游诊断 | 正文里的未定义宏；公式里的 `\vb` 等可原样保留 | `unresolved.log` / `content_todo.json`；公式信息见 `formula_diagnostics.json` |

**先判 1，再判 2/3/4，最后才是 5**：声明过的名字在展开时就已经消失，轮不到后面几类。

## 二、命令：各表里都有什么（11 月 23 日实现）

| 表 | 条数 | 内容 |
|:--|--:|:--|
| `BARE_ONE`（最多一组参数） | 39 | `\vspace`, `\hspace`, `\rule`, `\phantom`, `\hphantom`, `\vphantom`, `\footnote`, `\thanks`, `\color`, `\index`, `\glossary`, `\thispagestyle`, `\pagestyle`, `\bibliography`, `\bibliographystyle`, `\refstepcounter`, `\stepcounter`, `\ytableausetup`, `\setunitscale`, `\usetikzlibrary`, `\hypersetup`, `\geometry`, `\definecolor`, `\ctikzset`, `\tikzset`, `\pgfplotsset`, `\xymatrixrowsep`, `\xymatrixcolsep`, `\symbolfootnote`, `\Needspace`, `\footnotetext`, `\dedicatory`, `\special`, `\selectlanguage`, `\originalleft`, `\originalright`, `\printbibliography`, `\bibliographystyle`, `\nocite` |
| `BARE_DROP`（只丢名） | 80 | `\maketitle`, `\tableofcontents`, `\appendix`, `\frontmatter`, `\mainmatter`, `\printindex`, `\listoffigures`, `\listoftables`, `\linebreak`, `\nolinebreak`, `\null`, `\strut`, `\bigskip`, `\medskip`, `\smallskip`, `\noindent`, `\indent`, `\centering`, `\raggedright`, `\raggedleft`, `\selectfont`, `\normalsize`, `\small`, `\footnotesize`, `\scriptsize`, `\tiny`, `\large`, `\Large`, `\LARGE`, `\huge`, `\Huge`, `\bfseries`, `\itshape`, `\rmfamily`, `\sffamily`, `\ttfamily`, `\mdseries`, `\upshape`, `\scshape`, `\slshape`, `\rm`, `\it`, `\bf`, `\em`, `\sf`, `\tt`, `\sc`, `\sl`, `\cal`, `\bold`, `\hfil`, `\vfil`, `\normalfont`, `\global`, `\relax`, `\and`, `\quad`, `\qquad`, `\endgraf`, `\appendices`, `\newpage`, `\clearpage`, `\samepage`, `\protect`, `\nonumber`, `\notag`, `\allowbreak`, `\ignorespaces`, `\unskip`, `\qed`, `\qedhere`, `\endproof`, `\endproofqed`, `\boldmath`, `\unboldmath`, `\fussy`, `\sloppy`, `\raggedbottom`, `\flushbottom`, `\xspace` |
| `UNWRAP_ONE`（保留参数） | 6 | `\enquote`, `\emph`, `\textnormal`, `\reflectbox`, `\ThisStyle`, `\ooalign` |
| `PLUMBING`（内部命令） | 14 | `\m@th`, `\m@the`, `\if@compatibility`, `\if@tempswa`, `\tf@size`, `\sf@size`, `\ssf@size`, `\tagform@`, `\y@ng`, `\y@@optargI`, `\to@size`, `\p@`, `\ex@`, `\@tempskipa` |
| `DEF_CMDS`（声明） | 38 | `\newcommand`, `\renewcommand`, `\providecommand`, `\DeclareMathOperator`, `\DeclareRobustCommand`, `\newtheorem`, `\newenvironment`, `\renewenvironment`, `\newif`, `\newlength`, `\newcount`, `\newcounter`, `\newdimen`, `\newbox`, `\newsavebox`, `\newtoks`, `\DeclareMathSymbol`, `\DeclareMathAlphabet`, `\DeclareSymbolFont`, `\DeclareSymbolFontAlphabet`, `\DeclareMathDelimiter`, `\DeclareMathAccent`, `\newfont`, `\NewDocumentCommand`, `\RenewDocumentCommand`, `\DeclareDocumentCommand`, `\ProvideDocumentCommand`, `\DeclarePairedDelimiter`, `\newtheoremstyle`, `\theoremstyle`, `\NewDocumentEnvironment`, `\RenewDocumentEnvironment`, `\newtcolorbox`, `\newmdenv`, `\lstnewenvironment`, `\newfloat`, `\newlist`, `\newcases` |
| `TITLE_CMDS`（标题元数据） | 17 | `\title`, `\author`, `\date`, `\thanks`, `\address`, `\email`, `\affiliation`, `\affil`, `\orcid`, `\keywords`, `\subjclass`, `\subjectclass`, `\preprint`, `\dedication`, `\pacs`, `\msc`, `\AMSclass` |

布局清理还会处理下列形式。执行条件和公式保护先于布局清理。

| 规则 | 处理 |
|:--|:--|
| `\@… (not \@startsection)` | 见表头动作 |
| `\SavedStyle* (mathstyle placeholder)` | 见表头动作 |
| `\m@th glued to a letter (\m@thV)` | 见表头动作 |
| `\ifnum\mathgroup` 字体条件 | 条件执行阶段处理，无法确定时保存各分支 |
| `\the<counter>` | 数学边界内保留操作数，支持的独立花括号操作数可以读取实际数值 |
| `declared register names` | 见表头动作 |
| `\big/\Big/\bigg/\Bigg (+l/m/r)` | 见表头动作 |
| `\ae` | 见表头动作 |
| `\oe` | 见表头动作 |
| `\aa` | 见表头动作 |
| `\dots / \ldots / \cdots` | 见表头动作 |
| `\vdots` | 见表头动作 |
| `\ddots` | 见表头动作 |
| `\nicefrac -> \frac` | 见表头动作 |
| `\mfrac -> \frac` | 见表头动作 |
| `\colonequals / \coloneqq` | 见表头动作 |
| `\eqqcolon` | 见表头动作 |
| `\SREF -> section sign` | 见表头动作 |
| `brace group with a unit is a length` | 见表头动作 |

## 三、环境：十类

| 类 | 成员 |
|:--|:--|
| `display` | `\equation`, `\equation*`, `\align`, `\align*`, `\gather`, `\gather*`, `\multline`, `\multline*`, `\flalign`, `\flalign*`, `\eqnarray`, `\eqnarray*`, `\displaymath`, `\subequations`, `\alignat`, `\alignat*`, `\equ`, `\equs`, `\eqn`, `\eqs` |
| `inner_display` | `\split`, `\aligned`, `\gathered`, `\multlined`, `\alignedat`, `\subarray` |
| `grid` | `\cases`, `\dcases`, `\rcases`, `\array`, `\matrix`, `\pmatrix`, `\bmatrix`, `\vmatrix`, `\Vmatrix`, `\smallmatrix`, `\ytableau`, `\ytableaushort`, `\psmallmatrix`, `\psmallmatrix*`, `\pmatrix*`, `\bmatrix*`, `\vmatrix*`, `\Vmatrix*`, `\matrix*`, `\bsmallmatrix`, `\bsmallmatrix*`, `\Bmatrix*`, `\cases*`, `\dcases*`, `\rcases*` |
| `list` | `\enumerate`, `\enumerate*`, `\itemize`, `\itemize*`, `\description`, `\trivlist` |
| `text_block` | `\abstract`, `\center`, `\flushleft`, `\flushright`, `\quote`, `\quotation`, `\dedication`, `\verse`, `\NoHyper`, `\small`, `\smalltext`, `\footnotesize`, `\scriptsize`, `\tiny`, `\large`, `\Large`, `\LARGE`, `\huge`, `\Huge`, `\normalsize`, `\rm`, `\bf`, `\it`, `\tt`, `\sf`, `\sl`, `\sc`, `\em` |
| `float` | `\figure`, `\figure*`, `\table`, `\table*`, `\tabular`, `\tabular*`, `\minipage`, `\longtable`, `\wrapfigure`, `\subfigure` |
| `bibliography` | `\thebibliography`, `\bibdiv`, `\biblist`, `\bibliography` |
| `diagram` | `\tikzpicture`, `\tikzcd`, `\xymatrix`, `\picture`, `\pspicture`, `\tikzcd*`, `\pspicture*`, `\xy`, `\xygraph`, `\pgfpicture`, `\pgfonlayer`, `\tikz`, `\feynman`, `\feynmp`, `\asy`, `\circuitikz`, `\CD`, `\CD*` |
| `drop` | `\comment`, `\verbatim`, `\filecontents`, `\filecontents*`, `\tikzmath`, `\pgfmathparse` |
| `document` | `\document` |

命题的 kind（`statement_kinds`，共 25 种）：theorem(4)、lemma(2)、proposition(3)、corollary(3)、claim(1)、conjecture(3)、definition(3)、example(2)、remark(3)、question(1)、proof(3)、assumption(3)、observation(1)、fact(1)、notation(1)、construction(1)、warning(1)、thought(1)、plan(1)、guess(1)、algorithm(1)、counterexample(1)、exercise(1)、problem(1)、homework(2)

## 四、答案库

| 范围 | 动作 | 条数 | 例子 |
|:--|:--|--:|:--|
| 全局 | `allow` | 48 | A, Aut, B, C, End, F, Hom, IVN |
| 全局 | `expand` | 1 | dif |
| 全局 | `ignore` | 8 | citelist, citep, citet, clip, curraddr, draw, node, urladdr |
| 全局 | `unwrap` | 5 | ensurestackMath, on, stackon, vb, young |
| 按篇 | `allow` | 218 | 1507.06903:dashedrightarrow, 1606.05133:AGL, 1606.05133:Fr, 1606.05133:GL, 1606.05133:GO, 1606.05133:Id |
| 按篇 | `expand` | 5 | 1706.00750:lfloorj, 1811.02461:lRa, 1811.02461:op, 2106.00631:bigcupdot, 2106.05334:lfloorZ |
| 按篇 | `font` | 17 | 1701.08152:scriptscriptstyleaff, 1809.03803:delimsize, 1904.09434:mathfontb, 2102.08797:itshapeProof, 2103.10137:sffamilyH, 2103.10137:sffamilyIndCoh |
| 按篇 | `ignore` | 48 | 1309.3267:needspace, 1405.2608:psfrag, 1409.0501:example, 1505.06548:boxrwidth, 1505.06548:boxwidth, 1505.06548:sembox |
| 按篇 | `unwrap` | 8 | 1811.02461:setP, 1912.12721:cyrs, 2010.02884:stackinset, 2103.04368:widering, 2103.10137:sffamilyE, 2103.10137:sffamilyEis |

## 五、公式里的三个结构性坑（已修，容易复发）

| 坑 | 现象 | 规则 |
|:--|:--|:--|
| 只含数字的花括号组 | `x^{2}`→`x^`、`\Phi_d^{-1}`→`\Phi_d^` | **带单位**的组（`{2.5ex}`）才是长度；纯数字是内容 |
| display 内的空行 | texdraw 图被切成两半，整块图变正文 | display/diagram 内部空行不当段落分隔（`_tighten_math_blocks`） |
| 未闭合的 `$`/`\(`/`\[` | 后面整块当正文，字体命令进句子 | 一直延伸到块尾，作为公式 |

---

## 六、队列（判不了的东西去哪）

| 队列 | 单位 | 接口 | 现状（1,123 篇抽样） |
|:--|:--|:--|--:|
| 无定义的名字 | 名字 | `pending_names.py --export/--answer-with-model/--merge` | 292 名待答 |
| 并置读法 | 形状签名 | `lexicon.py` + `data/judgements.json` | 82 万次未判，**暂无消费方** |
| `\text{}` 槽角色 | 短语 | `data/slot_roles.json` | 264 条 |
| 公式角色 | 公式 | `data/judgements_roles.json` | 145 条已答 |

---

## 七、源码选择和切分顺序

1. 原始文件走静态 `read_source` 或 `tex_execution.observe`。后者在临时副本中独立构建，再按已验证事件放回源码。`--canonical` 直接读取已保存的规范化产物。
2. `Normalizer.execute_document` 与 `conditionals.Execution` 维护正文起止、调用点定义、组作用域和延迟动作。局部无法判定的正文条件带原文保存，影响整个文档的未定状态可以保留整稿。
3. 在清理布局、名称和环境前，先保存附属内容、未知正文调用和需原样保留的公式。元数据命令按参数数目消费。规范化结果可跨进程重读。
4. `latex2nl` 按单元定位公式并建立占位符和记录，切句、分词、挂接附属内容。`verify_output` 检查双向关联、原文、词元、账本与待办，输出严格状态。

## 八、上游 1,123 篇历史抽样

| 指标 | 11 月 16 日起点 | 当轮修复后 |
|:--|--:|--:|
| 词表外命令 | 51,694 次 | **34,481** |
| 未知环境 | 12,066 次 | **623** |
| 句内残留 LaTeX | 44,847 次 | **32,607** |
| 待答名字 | 627 名 | 292 名 |

起点是 2026-11-16 晚，修完 `{2}`、下划线、容器标记、mathstyle、寄存器、环境分类、
包命令、四类散文命令之后。
