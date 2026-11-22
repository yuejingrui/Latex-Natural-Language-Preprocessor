#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render one run's sentences as a Markdown document, one sentence at a time.

    python3 sentences_md.py --run /tmp/pre -o nlprep/out/2005.01702/sentences.md

The document is generated from ``sentences.jsonl`` and ``formulas.json``, so it always
matches the artifacts: the sentence text keeps its ``MATHn`` placeholders, each sentence
also shows the text with the formula bodies inlined, and a small table lists the formulas
that sentence uses.  Nothing is rewritten by hand.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Dict, List


def load(run: str):
    with open(os.path.join(run, "formulas.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    forms = doc["formulas"] if isinstance(doc, dict) else doc
    rows = []
    with open(os.path.join(run, "sentences.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return forms, rows


def cell(text: str, limit: int = 80) -> str:
    """A table-safe cell: pipes escaped, newlines flattened, length capped."""
    text = " ".join((text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def inline(sentence: str, ids: List[str], by_id: Dict[str, dict]) -> str:
    text = sentence
    for fid in ids:
        body = (by_id.get(fid) or {}).get("expanded") or ""
        # code spans keep LaTeX readable and stop Markdown from eating backslashes
        text = text.replace(fid, "`%s`" % body if body else "`%s`" % fid)
    return text


def render(run: str, out: str, title: str) -> dict:
    forms, rows = load(run)
    by_id = {f["id"]: f for f in forms}
    envs = Counter(r["env"] for r in rows)
    kinds = Counter(f["kind"] for f in forms)
    roles = Counter(f.get("role") or "-" for f in forms)

    lines = ["# %s" % title, "",
             "由 `sentences.jsonl` + `formulas.json` 生成，**未手改**。", "",
             "| 项 | 值 |", "|:--|:--|",
             "| 句子 | %d |" % len(rows),
             "| 公式 | %d（%s） |" % (len(forms),
                                     "、".join("%s %d" % (k, v) for k, v in kinds.most_common())),
             "| 角色 | %s |" % "、".join("%s %d" % (k, v) for k, v in roles.most_common()),
             "| 环境 | %s |" % "、".join("%s %d" % (k, v) for k, v in envs.most_common()),
             "",
             "读法：`### sN` 后是句子编号与所属单元；引用块是原句（公式处保留 `MATHn`）；"
             "其下的表给出该句用到的公式；再下面一行是**公式已内联**的可读版本。", "",
             "---", ""]

    for r in rows:
        ids = r.get("formulas") or []
        head = "### %s · %s · %s · %d 词" % (r["sid"], r["unit"], r["env"], r["words"])
        lines.append(head)
        lines.append("")
        if r.get("part"):
            lines.append("> part: `%s %s`" % (r["part"].get("kind"), r["part"].get("index")))
            lines.append("")
        lines.append("> %s" % r["text"])
        lines.append("")
        if ids:
            lines.append("| 公式 | 类型 | 角色 | 展开 |")
            lines.append("|:--|:--|:--|:--|")
            for fid in ids:
                f = by_id.get(fid) or {}
                lines.append("| %s | %s | %s | %s |" % (
                    fid, f.get("kind") or "-", f.get("role") or "-",
                    cell(f.get("expanded") or "")))
            lines.append("")
            lines.append("内联：%s" % inline(r["text"], ids, by_id))
        else:
            lines.append("（无公式）")
        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")
    return {"sentences": len(rows), "formulas": len(forms),
            "with_formulas": sum(1 for r in rows if r.get("formulas")),
            "bytes": os.path.getsize(out)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, help="a run directory with the split artifacts")
    ap.add_argument("-o", "--out", required=True, help="the Markdown file to write")
    ap.add_argument("--title", default="", help="heading of the document")
    a = ap.parse_args(argv)
    title = a.title or ("%s 逐句" % os.path.basename(os.path.normpath(a.run)))
    info = render(a.run, a.out, title)
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
