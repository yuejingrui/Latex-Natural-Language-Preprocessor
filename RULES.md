# 处理规则总表

## 2026-11-23 更新：切分验收与公式理解分离

以下规则优先于本文历史表格和先前修复说明中的“未知命令进入待办”要求。

1. 切分负责确定文本/公式边界、保留内容、建立占位符和记录关联；不要求解释数学符号、公式角色或公式内部文字的逻辑作用。
2. 命令表是已知转换与可选注释的依据，不是公式准入名单。明确数学边界内的未知命令不触发切分失败或 pending，不通过不断补白名单取得通过。
3. 对含未知命令的公式，保留整个公式体及参数分组，作为 opaque 公式输出；不猜未知命令的参数个数，不拆其内部 qquad，不提取或改写其内部文字槽，不推断其数学角色。归一化阶段同样保护它，已完成的宏展开/条件处理不因此被声称无损。
4. 未知命令清单、未定公式文字角色进入单独的下游诊断，不计入切分 pending。结构检查仍执行：公式边界不闭合、括号不平衡、占位符失联、token 丢失继续阻止通过。
5. 正文中的未知命令、未知宏隐藏的数学环境、未定条件会影响边界或有效内容，仍保留原文并报警/待处理。不能将“公式内部未知命令可保留”推广成所有未知 TeX 都可忽略。
6. strict=0 只表示切分检查通过；下游诊断可以非空。表格正文未切分、句界未定、条件未定、历史删除记录仍为 pending。旧版未分类 unresolved 保守保留，重新生成输出才应用新分类。

例：`$\mySpecialOperator{x}{y}$` 无需注册命令即可输出 MATH 占位符，完整保存 `\mySpecialOperator{x}{y}`。`$\accentset{\ast}{W}$` 也采用这一通用规则，不为星号补数学解释。

2026-11-23 修复后的行为、数据接口与验收结果见 [修复说明](REPAIR-2026-11-23.md)。本文原有的较早测试数字和未修状态保留为历史记录；涉及脚注/浮动块删除、纯公式过滤、条件默认取第一支及 strict 退出码的旧描述，以修复说明为准。


这张表回答一个问题：**遇到一种 LaTeX 构造，管线怎么判**。
它不按 TeX 的 atom 类（mathord/mathrel/…）判，按**它在论文里是内容还是排版**判。

第二节到第四节由 `scripts/rules_table.py` 从代码生成，**不要手改**：
`python3 scripts/rules_table.py --check` 会在表和代码不一致时失败，
`--write` 重新生成。第五到第八节是散文，改动请直接改。

---

## 一、六个动作

| # | 动作 | 判据 | 代表 | 代码 |
|:--|:--|:--|:--|:--|
| 1 | **读声明**：名字是寄存器/环境，不是词 | 论文自己声明过 | `\newlength\bshft`、`\newsavebox\myboxA`、`\newtheorem{rmk}{Remark}` | `normalize()` 里扫 `newlength/newdimen/newskip/newcount/newtoks/newsavebox/newbox/newif/chardef/mathchardef`；`read_theorem_decls()` |
| 2 | **保内容、丢外壳** | 外壳只说明怎么画 | `\abs{x}`→`|x|`、`\scalerel*{(}{x}`→`(` | `_unwrap_one`、`_unwrap_last_arg`、`_unwrap_first_arg`、`_wrap_arg`、`UNWRAP_ONE` |
| 3 | **整条丢** | 不印任何内容 | 间距 `\medspace`、`\special{…}`、`\vrule`、`\marginpar{…}`、`\tag{…}`、`\proof[…]` | `BARE_ONE`（连参数）、`BARE_DROP`（只丢名）、`PLUMBING` |
| 4 | **改写为等价** | 同义，换个写法 | `\nicefrac`/`\mfrac`→`\frac`、`\colonequals`→`:=`、`\dots`→`…` | `_drop_bare` 里的 `re.sub` 段 |
| 5 | **环境标记**：容器按十类处理 | 环境语义 | `display`/`grid`/`list`/`statement`/`diagram`/`drop`… | `_envs()`、`data/env_classes.json` |
| 6 | **影响切分且未定 → 队列** | 正文命令、隐藏边界或条件分支未定；公式内部未知命令只保留并记录下游诊断 | 正文里的未定义宏；公式里的 `\vb` 等可原样保留 | `unresolved.log` / `content_todo.json`；公式信息见 `formula_diagnostics.json` |

**先判 1，再判 2/3/4，最后才是 5**：声明过的名字在展开时就已经消失，轮不到后面几类。

<!-- BEGIN GENERATED -->
## 二、命令：各表里都有什么（本节目动生成，不要手改）

| 表 | 条数 | 内容 |
|:--|--:|:--|
| `BARE_ONE`（丢名 + 最多 3 组参数） | 37 | `\vspace`, `\hspace`, `\rule`, `\phantom`, `\hphantom`, `\vphantom`, `\footnote`, `\thanks`, `\color`, `\index`, `\glossary`, `\thispagestyle`, `\pagestyle`, `\bibliography`, `\bibliographystyle`, `\refstepcounter`, `\stepcounter`, `\ytableausetup`, `\setunitscale`, `\usetikzlibrary`, `\hypersetup`, `\geometry`, `\definecolor`, `\ctikzset`, `\tikzset`, `\pgfplotsset`, `\xymatrixrowsep`, `\xymatrixcolsep`, `\symbolfootnote`, `\Needspace`, `\dedicatory`, `\special`, `\originalleft`, `\originalright`, `\printbibliography`, `\bibliographystyle`, `\nocite` |
| `BARE_DROP`（只丢名） | 80 | `\maketitle`, `\tableofcontents`, `\appendix`, `\frontmatter`, `\mainmatter`, `\printindex`, `\listoffigures`, `\listoftables`, `\linebreak`, `\nolinebreak`, `\null`, `\strut`, `\bigskip`, `\medskip`, `\smallskip`, `\noindent`, `\indent`, `\centering`, `\raggedright`, `\raggedleft`, `\selectfont`, `\normalsize`, `\small`, `\footnotesize`, `\scriptsize`, `\tiny`, `\large`, `\Large`, `\LARGE`, `\huge`, `\Huge`, `\bfseries`, `\itshape`, `\rmfamily`, `\sffamily`, `\ttfamily`, `\mdseries`, `\upshape`, `\scshape`, `\slshape`, `\rm`, `\it`, `\bf`, `\em`, `\sf`, `\tt`, `\sc`, `\sl`, `\cal`, `\bold`, `\hfil`, `\vfil`, `\normalfont`, `\global`, `\relax`, `\and`, `\quad`, `\qquad`, `\endgraf`, `\appendices`, `\newpage`, `\clearpage`, `\samepage`, `\protect`, `\nonumber`, `\notag`, `\allowbreak`, `\ignorespaces`, `\unskip`, `\qed`, `\qedhere`, `\endproof`, `\endproofqed`, `\boldmath`, `\unboldmath`, `\fussy`, `\sloppy`, `\raggedbottom`, `\flushbottom`, `\xspace` |
| `UNWRAP_ONE`（留参数） | 7 | `\ensuremath`, `\enquote`, `\emph`, `\textnormal`, `\reflectbox`, `\ThisStyle`, `\ooalign` |
| `PLUMBING`（`@` 内部名） | 14 | `\m@th`, `\m@the`, `\if@compatibility`, `\if@tempswa`, `\tf@size`, `\sf@size`, `\ssf@size`, `\tagform@`, `\y@ng`, `\y@@optargI`, `\to@size`, `\p@`, `\ex@`, `\@tempskipa` |
| `DEF_CMDS`（读定义后丢） | 38 | `\newcommand`, `\renewcommand`, `\providecommand`, `\DeclareMathOperator`, `\DeclareRobustCommand`, `\newtheorem`, `\newenvironment`, `\renewenvironment`, `\newif`, `\newlength`, `\newcount`, `\newcounter`, `\newdimen`, `\newbox`, `\newsavebox`, `\newtoks`, `\DeclareMathSymbol`, `\DeclareMathAlphabet`, `\DeclareSymbolFont`, `\DeclareSymbolFontAlphabet`, `\DeclareMathDelimiter`, `\DeclareMathAccent`, `\newfont`, `\NewDocumentCommand`, `\RenewDocumentCommand`, `\DeclareDocumentCommand`, `\ProvideDocumentCommand`, `\DeclarePairedDelimiter`, `\newtheoremstyle`, `\theoremstyle`, `\NewDocumentEnvironment`, `\RenewDocumentEnvironment`, `\newtcolorbox`, `\newmdenv`, `\lstnewenvironment`, `\newfloat`, `\newlist`, `\newcases` |
| `TITLE_CMDS`（连参数丢） | 17 | `\title`, `\author`, `\date`, `\thanks`, `\address`, `\email`, `\affiliation`, `\affil`, `\orcid`, `\keywords`, `\subjclass`, `\subjectclass`, `\preprint`, `\dedication`, `\pacs`, `\msc`, `\AMSclass` |

正则类（`_drop_bare` 里，不占表；`--check` 会确认每条正则还在代码里）：

| 规则 | 处理 |
|:--|:--|
| `\@… (not \@startsection)` | 见表头动作 |
| `\SavedStyle* (mathstyle placeholder)` | 见表头动作 |
| `\m@th glued to a letter (\m@thV)` | 见表头动作 |
| `\ifnum\mathgroup=\X (font test)` | 见表头动作 |
| `\the<counter> (head)` | 见表头动作 |
| `\the<counter> (tail)` | 见表头动作 |
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

## 三、环境：十类（本节目动生成）

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

## 四、答案库（本节目动生成）

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
<!-- END GENERATED -->

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

## 七、流程顺序（改代码时按这个顺序找位置）

```
read_source        内联 \input/\include
macros.parse       读宏定义（含 \def/\let/声明别名）
丢弃定义            DEF_CMDS，保留宏表
展开                macros.expand
_META_ / TITLE_     连参数丢
_drop_bare          动作 2/3/4 全在这里
环境                _envs → 十类
清理                section 机械、行内 tikz、bibliography、thin space
```

`latex2nl` 侧再接：`find_math_spans`（含未闭合）→ `_clean_segment`（容器标记、间距）→
句子切分 → tokenize（**无损**，`_` 也是 token）→ 公式记录。

---

## 八、当前数字（1,123 篇抽样，起点 → 现在）

| 指标 | 起点 | 现在 |
|:--|--:|--:|
| 词表外命令 | 51,694 次 | **34,481** |
| 未知环境 | 12,066 次 | **623** |
| 句内残留 LaTeX | 44,847 次 | **32,607** |
| 待答名字 | 627 名 | 292 名 |

起点是 2026-11-16 晚，修完 `{2}`、下划线、容器标记、mathstyle、寄存器、环境分类、
包命令、四类散文命令之后。
