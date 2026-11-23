# 示例运行的来源

## 论文

| 项 | 值 |
|:--|:--|
| 标题 | Quiver gauge theories and symplectic singularities |
| 作者 | Alex Weekes |
| arXiv | [2005.01702](https://arxiv.org/abs/2005.01702) |
| DOI | [10.1016/j.aim.2022.108185](https://doi.org/10.1016/j.aim.2022.108185) |
| 期刊 | Advances in Mathematics（2022） |

## 这里的文件是什么

本目录是切分层对上述论文的 TeX 源码跑一遍得到的产物，用于演示 §4 的每个字段：

| 文件 | 内容 |
|:--|:--|
| `normalized.tex` | 归一化后的全文 |
| `normalize-stats.json` | 改写计数 |
| `sentences.jsonl`、`sentences.txt`、`sentences.tok.txt` | 句子流（362 句） |
| `formulas.json` | 公式表（795 条） |
| `content_blocks.json`、`content_todo.json` | 保留下来的脚注等内容块 |
| `split_status.json`、`formula_diagnostics.json` | 计数、未决项、下游诊断 |
| `unresolved.log` | 本轮切分的疑点 |
| `issues.json`、`issues_normalize.json`、`issues_latex2nl.json` | 三段账本 |
| `sentences.md` | `sentences_md.py` 渲染的可读版本 |

## 版权与许可

**本目录里的正文（`sentences*.txt`、`sentences.md`、`normalized.tex` 等）是 Alex Weekes 那篇论文的文字**，
版权归原作者所有，**不在本仓库的 MIT 许可范围内**。它在这里只作"工具输出长什么样"的示例，
篇幅约占原文的很小一部分；引用他人作品时请引用原论文，不要引用本目录。

代码本身（`scripts/`、`tests/` 与 `data/` 的表）见仓库根目录的 [LICENSE](../../LICENSE)。

如果你要再分发这个示例，请自行判断所在法域对引用的要求，或改用你自己论文的产物。
