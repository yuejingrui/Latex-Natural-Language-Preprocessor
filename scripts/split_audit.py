#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify the sentences that end without a terminal mark, per paper.

SPLITTING.md section 5 asks for this list: after the comment-line fix, about 324
of 13,300 sentences still end without . ! ? : ; and they are not one phenomenon.
This script reads `sentences.jsonl` and `formulas.json` from one or more run
directories and writes a Markdown report that sorts every such sentence into

    punctuation-in-formula   the mark sits inside the formula (benign)
    container-boundary       the sentence is a list item, cut by design
    heading                  a proof/step heading, not a sentence
    other                    needs a look

    python3 scripts/split_audit.py --run OUT -o OUT/split-audit.md
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import Counter, defaultdict
from typing import Dict, List

TERMINAL = ".!?:;"
PLACEHOLDER_TAIL = re.compile(r"(?:MATH|REF)\d+$")
HEADING = re.compile(r"^(?:Proof|Step|Claim|Remark|Note|Definition|Lemma|Theorem|"
                     r"Proposition|Corollary|Example|Construction|Proof of)\b")
LOWER = re.compile(r"^[a-z(]")


def load_run(run: str):
    rows = []
    with open(os.path.join(run, "sentences.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    forms = {}
    with open(os.path.join(run, "formulas.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    for f in (doc["formulas"] if isinstance(doc, dict) else doc):
        forms[f["id"]] = f
    return rows, forms


def classify(row, rows, i, forms) -> str:
    """One of the classes the report lists, in a deliberate order."""
    text = (row.get("text") or "").rstrip()
    if not text:
        return "other"
    # a mark inside a bracket is still a mark: "(... categories.)"
    if text[-1] in ")]}" and any(ch in TERMINAL for ch in text[-3:]):
        return "mark-in-bracket"
    # a bare brace group left over from a box/overlay command
    if "{" in text or "}" in text:
        return "brace-leak"
    tail = PLACEHOLDER_TAIL.search(text)
    if tail:
        f = forms.get(tail.group(0)) or {}
        exp = (f.get("expanded") or "").rstrip()
        # the mark can sit deep inside the formula: \text{otherwise.}\end{grid}
        if (f.get("trailing_punct") or "") or any(
                ch in TERMINAL for ch in exp[-80:]):
            return "punctuation-in-formula"
        if f.get("kind") == "display":
            return "display-final"
        return "formula-final"
    if "/item" in (row.get("unit") or "") or row.get("env") == "item":
        return "container-boundary"
    if text[-1] == ",":
        return "container-boundary"
    nxt = rows[i + 1]["text"] if i + 1 < len(rows) else ""
    if nxt and LOWER.match(nxt) and "/item" not in (rows[i + 1].get("unit") or ""):
        return "cut-sentence"
    if len(text.split()) <= 6 or HEADING.match(text):
        return "heading"
    return "source-missing-mark"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", action="append", required=True,
                    help="run directory (repeatable, globs allowed)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)

    runs: List[str] = []
    for pattern in a.run:
        runs.extend(sorted(glob.glob(pattern)))
    runs = [r for r in runs if os.path.isdir(r)]

    buckets = defaultdict(list)
    per_paper = []
    totals = Counter()
    for run in runs:
        try:
            rows, forms = load_run(run)
        except (OSError, ValueError):
            continue
        paper = os.path.basename(os.path.normpath(run))
        bad = 0
        for i, row in enumerate(rows):
            text = (row.get("text") or "").rstrip()
            if not text or text[-1] in TERMINAL:
                continue
            bad += 1
            cls = classify(row, rows, i, forms)
            totals[cls] += 1
            buckets[cls].append((paper, row["sid"], text))
        per_paper.append((paper, len(rows), bad))
        totals["sentences"] += len(rows)
        totals["bad"] += bad

    lines = ["# 分割审计基线：无句末标点的句子", "",
             "由 `split_audit.py` 生成，未手改。分类规则见脚本；`other` 类需要人工看。", "",
             "## 汇总", "",
             "| 项 | 值 |", "|:--|--:|",
             "| 论文 | %d |" % len(per_paper),
             "| 句子 | %d |" % totals["sentences"],
             "| 无句末标点 | %d |" % totals["bad"]]
    names = [("punctuation-in-formula", "标点在公式里（良性）"),
             ("mark-in-bracket", "标点在括号内（良性）"),
             ("container-boundary", "容器边界：列表条目/逗号结尾（设计取舍）"),
             ("heading", "标题：证明/步骤等（非句子）"),
             ("display-final", "句末是 display、正文无标点"),
             ("formula-final", "句末是 inline 公式、正文无标点"),
             ("brace-leak", "裸花括号残留（缺陷）"),
             ("cut-sentence", "疑似被切断（缺陷）"),
             ("source-missing-mark", "原文缺句末标点（需复核）"),
             ("other", "其它（需人工）")]
    for key, label in names:
        lines.append("| %s | %d |" % (label, totals[key]))
    lines += ["", "## 逐篇", "", "| 论文 | 句子 | 无句末标点 |", "|:--|--:|--:|"]
    for paper, total, bad in sorted(per_paper):
        lines.append("| %s | %d | %d |" % (paper, total, bad))

    for key, label in names:
        items = buckets[key]
        lines += ["", "## %s（%d）" % (label, len(items)), ""]
        if not items:
            lines.append("（无）")
            continue
        for paper, sid, text in items:
            lines.append("- `%s` %s — %s" % (paper, sid, text[:160]))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")
    print(json.dumps({"papers": len(per_paper), "sentences": totals["sentences"],
                      "without_terminal": totals["bad"],
                      "classes": {k: totals[k] for k, _ in names}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
