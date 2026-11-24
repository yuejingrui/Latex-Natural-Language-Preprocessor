#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ask a model about the role questions, one block at a time.

    # 1. what is open, with a window for each item
    python3 ask_roles.py --export /tmp/roles_todo.json out/*/formulas.json
    # 2. let the model answer (one request per block)
    python3 ask_roles.py --answer /tmp/roles_todo.json --batch 1
    # 3. how well did it do against answers a person gave
    python3 ask_roles.py --compare /tmp/roles_todo.json \
        --judgements data/judgements_roles.json

The window is cut by ``roles.py`` (previous sentence, the sentence with the
formula marked, next sentence) -- no model decides what to look at.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import os
import sys as _sys
# pipeline/ is a directory inside the splitting package: the splitter's modules
# live one level up in ../scripts and the shared tables in ../data.  Both have to
# be importable whether this file runs from the package root or from its own
# directory, so the paths are put on sys.path before the local imports below.
_PKG_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_PKG_ROOT, "scripts"),
           os.path.dirname(os.path.abspath(__file__))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import pending_names as P     # the model client lives there
import roles as R


def load_pairs(paths):
    """(formulas_path, sentences_path) for each formulas.json given."""
    out = []
    for f in paths:
        d = os.path.dirname(os.path.abspath(f))
        s = os.path.join(d, "sentences.jsonl")
        if os.path.exists(s):
            out.append((f, s))
        else:
            print("skip %s: no sentences.jsonl next to it" % f)
    return out


def do_export(a) -> int:
    items, papers = [], []
    for fpath, spath in load_pairs(a.formulas):
        formulas = json.load(open(fpath, encoding="utf-8"))
        if isinstance(formulas, dict):
            formulas = formulas.get("formulas") or []
        sentences = [json.loads(l) for l in open(spath, encoding="utf-8")
                     if l.strip()]
        got = R.build_todo(formulas, sentences)
        for it in got:
            it["paper"] = os.path.basename(os.path.dirname(fpath))
        items.extend(got)
        papers.append((os.path.basename(os.path.dirname(fpath)), len(got)))
    blocks = collections.defaultdict(list)
    for it in items:
        blocks[(it["paper"], it.get("block"))].append(it["fid"])
    payload = {
        "instructions": R.INSTRUCTIONS,
        "allowed_answers": list(R.ALLOWED),
        "meta": {"items": len(items), "blocks": len(blocks),
                 "papers": dict(papers),
                 "items_per_block": round(len(items) / max(1, len(blocks)), 1)},
        "blocks": {"%s|%s" % k: v for k, v in blocks.items()},
        "items": items,
    }
    with open(a.export, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("items=%d blocks=%d -> %s" % (len(items), len(blocks), a.export))
    return 0


def make_prompt(items):
    lines = []
    for k, it in enumerate(items, 1):
        lines.append("%d. fid %s   formula: %s" % (k, it["fid"],
                                                  (it.get("formula") or "")[:140]))
        for w in it.get("window") or [it.get("sentence") or ""]:
            lines.append("   context: " + " ".join(str(w).split())[:300])
    return ("Decide for each numbered item whether its formula is a *constraint* "
            "(a condition on something the sentence chooses or assumes) or a "
            "*proposition* (an assertion the sentence makes), or *unsure*. "
            "Answer with JSON only:\n"
            '{"answers": [{"fid": "MATH12", "answer": '
            '"constraint|proposition|unsure", "introducer": "..."}]}\n\n'
            + "\n".join(lines))


def do_answer(a) -> int:
    cfg = P.backend_config(a.backend)
    doc = json.load(open(a.answer, encoding="utf-8"))
    todo = [it for it in doc["items"] if not it.get("answer")]
    if a.limit:
        todo = todo[:a.limit]
    by_block = collections.OrderedDict()
    for it in todo:
        by_block.setdefault((it["paper"], it.get("block")), []).append(it)
    print("backend %s model %s | %d items in %d blocks"
          % (cfg["backend"], cfg["model"], len(todo), len(by_block)))
    asked = filled = 0
    for key, items in by_block.items():
        asked += 1
        try:
            text = P.call_model(cfg, make_prompt(items), timeout=240)
        except Exception as exc:                        # noqa: BLE001
            print("  block %s failed: %r" % (key[0], exc))
            continue
        got = P._json_from(text) or {}
        answers = got.get("answers") if isinstance(got, dict) else None
        if not isinstance(answers, list):
            print("  block %s: no usable answer (%r)" % (key[0], text[:80]))
            continue
        want = {it["fid"] for it in items}
        seen_fid = set()
        for ans in answers:
            if not isinstance(ans, dict):
                continue
            fid = str(ans.get("fid") or "").strip()
            act = str(ans.get("answer") or "").strip()
            if fid not in want or act not in R.ALLOWED:
                continue
            if fid in seen_fid:
                print("  duplicate answer for %s ignored" % fid)
                continue
            seen_fid.add(fid)
            for it in items:
                if it["fid"] == fid:
                    it["answer"] = act
                    it["introducer"] = ans.get("introducer")
                    it["by"] = "model:%s:%s" % (cfg["backend"], cfg["model"])
                    filled += 1
        print("  block %-24s %d items, %d answered"
              % ("%s|%s" % key, len(items),
                 sum(1 for ans in answers
                     if isinstance(ans, dict)
                     and str(ans.get("fid") or "").strip() in want)))
    # a question the model answered nothing for is counted, not forgotten: two
    # strikes send it to the human table (GAPS.md Q2)
    for it in todo:
        if not it.get("answer"):
            it["asked"] = int(it.get("asked") or 0) + 1

    # write beside the file and rename: a Ctrl-C must not truncate the answers
    # that were paid for in this run
    tmp = a.answer + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, a.answer)
    print("requests=%d answers written=%d" % (asked, filled))
    return 0


def do_compare(a) -> int:
    doc = json.load(open(a.compare, encoding="utf-8"))
    human = json.load(open(a.judgements, encoding="utf-8"))
    items = human.get("items") if isinstance(human, dict) else human
    # a fid is only unique inside a paper: the file has 7 fids answered by
    # two papers each, two of them with *conflicting* answers.  Keying by fid
    # alone let one paper's answer overwrite the other's.
    truth = {(it.get("paper"), it["fid"]): it["answer"] for it in items
             if it.get("answer") and it["answer"] in R.ALLOWED}
    print("human answers available: %d" % len(truth))
    rows = [it for it in doc["items"]
            if (it.get("paper"), it["fid"]) in truth]
    print("items we asked that a person also answered: %d" % len(rows))
    if not rows:
        return 1
    table = collections.Counter()
    model_ok = mech_ok = 0
    for it in rows:
        h = truth[(it.get("paper"), it["fid"])]
        m = it.get("answer")
        mech = it.get("role_now")
        table[(h, m)] += 1
        model_ok += (m == h)
        mech_ok += (mech == h)
    n = len(rows)
    print("\n%-12s %-12s %-6s" % ("human", "model", "count"))
    for (h, m), c in table.most_common():
        print("%-12s %-12s %-6d" % (h, m, c))
    print("\nmodel agrees with the person: %d/%d (%.0f%%)"
          % (model_ok, n, 100.0 * model_ok / n))
    print("mechanical role agrees     : %d/%d (%.0f%%)"
          % (mech_ok, n, 100.0 * mech_ok / n))
    # an unanswered item is not a disagreement
    bad = [(it["fid"], it.get("role_now"), it.get("answer"),
            truth[(it.get("paper"), it["fid"])])
           for it in rows
           if it.get("answer") is not None
           and it["answer"] != truth[(it.get("paper"), it["fid"])]]
    for fid, mech, m, h in bad[:10]:
        print("  %-8s mechanical=%-12s model=%-12s human=%s" % (fid, mech, m, h))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", metavar="FILE")
    ap.add_argument("--answer", metavar="FILE")
    ap.add_argument("--compare", metavar="FILE")
    ap.add_argument("--judgements", default=os.path.join(
        os.path.dirname(HERE), "data", "judgements_roles.json"))
    ap.add_argument("--backend", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("formulas", nargs="*")
    a = ap.parse_args(argv)
    if a.export:
        return do_export(a)
    if a.answer:
        return do_answer(a)
    if a.compare:
        return do_compare(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
