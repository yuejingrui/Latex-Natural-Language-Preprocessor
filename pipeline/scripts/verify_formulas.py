#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check the formula table itself, not just how many rows it has.

Every other check counts: fixed point, sentence count, formula count, the prose
word multiset.  A transform can pass all of them and still destroy content --
that is exactly what the rule that deleted ``{2}`` did: ``\\Phi_d^{-1}`` became
``\\Phi_d^``, the count did not move, and the fixed point held.

What it compares, for every record that is not an opaque drawing:

  numbers        every number in the raw formula appears in what the record kept
                 -- the expanded body, its text slots, and the side condition
                 that was lifted out of it
  commands       every control word appears there too, case-sensitively, unless
                 the handling table says the name is rewritten or dropped
  control symbols \\{ \\} \\% \\& \\# \\_ \\$ \\| are compared by count; the spacing
                 commands (\\, \\; \\: \\! and backslash-space) are not
  references     every MATH/REF in a sentence exists in the table
  anchors        every record is either referenced by a sentence or marked
                 orphan by the normaliser (an unmarked one is a bug)

Exit status: 1 when anything was lost or an anchor is missing, 2 when there was
nothing to check, 0 otherwise.  It has to be able to fail: the previous version
printed the same numbers and returned 0 always, so nothing could gate on it.

    python3 verify_formulas.py RUN_DIR      # or one paper directory, or several
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
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

sys.path.insert(0, HERE)

from verify_output import formula_error

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
CMD_RE = re.compile(r"\\([A-Za-z@]+)")
CSYM_RE = re.compile(r"\\([{}%&#_$|])")
SITE_RE = re.compile(r"(MATH\d+|REF\d+)")
#: spacing commands are typesetting, not content
SPACING = {"\\,", "\\;", "\\:", "\\!", "\\ "}

#: actions that make a name disappear on purpose.  ``allow`` and ``font`` keep
#: it, so a name with those answers must still be in the formula.
REWRITING_ACTIONS = {"expand", "ignore", "unwrap", "drop"}


def rewritten_names(paper: str = "") -> set:
    """Names that may vanish from a formula, per the handling table."""
    import latex2nl as L
    import normalize as N
    names = set()
    for attr in ("BARE_ONE", "BARE_DROP", "UNWRAP_ONE", "PLUMBING", "DEF_CMDS"):
        names |= set(getattr(N.Normalizer, attr))
    with open(N.ANSWERS_PATH, encoding="utf-8") as fh:
        answers = json.load(fh)
    for name, rec in (answers.get("names") or {}).items():
        if (rec or {}).get("action") in REWRITING_ACTIONS:
            names.add(name)
    # only this paper's answers: one paper's decision must not excuse the same
    # command in another paper
    for name, rec in ((answers.get("papers") or {}).get(paper) or {}).items():
        if (rec or {}).get("action") in REWRITING_ACTIONS:
            names.add(name)
    # formatting commands are rewritten on purpose: \textit{pr} becomes
    # \operatorname{pr}, and the letters are what carries the meaning
    names |= set(L.FORMAT_CMDS)
    # \text and its relatives become <TXT n> slots on purpose
    names |= {"text", "textrm", "textup", "textsl", "textsf", "texttt",
              "textnormal", "textbf", "textit", "textsc", "mbox"}
    return names


from paper_metadata import run_paper_id


def tables(paths):
    """Find formula tables under run directories or in the files supplied."""
    for path in paths:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            yield path
        elif os.path.isfile(os.path.join(path, "formulas.json")):
            yield os.path.join(path, "formulas.json")
        elif os.path.isdir(path):
            for d in sorted(os.listdir(path)):
                fp = os.path.join(path, d, "formulas.json")
                if os.path.isfile(fp):
                    yield fp


def kept_text(rec: dict) -> str:
    """Everything the record still holds of the formula, as one string."""
    parts = [rec.get("expanded") or ""]
    for slot in rec.get("text_slots") or []:
        parts.append(slot.get("raw") or "")
    # a range condition is lifted out of the body; it is still content
    for side in rec.get("side_conditions") or []:
        parts.append(side if isinstance(side, str) else str(side))
    return " ".join(parts)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: verify_formulas.py DIR [DIR ...]")
        return 2
    args = [a for a in argv if not a.startswith("--")]

    formula_errors = 0
    lost_num = lost_cmd = lost_csym = dangling = 0
    content_refs = 0
    orphan_marked = orphan_unmarked = dups = records = papers = 0
    examples = []
    for fpath in tables(args):
        papers += 1
        with open(fpath, encoding="utf-8") as fh:
            doc = json.load(fh)
        recs = doc["formulas"] if isinstance(doc, dict) else doc
        if not isinstance(recs, list):
            raise ValueError("formulas must be a list")
        records += len(recs)
        paper = run_paper_id(os.path.dirname(fpath))
        known = rewritten_names(paper)
        table = {}
        for rec in recs:
            if rec.get("id") in table:
                dups += 1
            table[rec.get("id")] = rec
        for rec in recs:
            raw = rec.get("raw") or ""
            exp = rec.get("expanded") or ""
            if rec.get("ref_kind") == "content":
                # a preserved note/float: raw is the original block and the
                # text lives in content_blocks.json, not in expanded
                content_refs += 1
                continue
            problem = formula_error(rec)
            if problem:
                formula_errors += 1
                if len(examples) < 18:
                    examples.append((paper, rec["id"], problem, raw[:70], exp[:70]))
            if "diagram" in (rec.get("flags") or []):
                continue
            kept = kept_text(rec)
            have = collections.Counter(NUM_RE.findall(kept))
            for n in NUM_RE.findall(raw):
                if have[n]:
                    have[n] -= 1
                else:
                    lost_num += 1
                    if len(examples) < 6:
                        examples.append((paper, rec["id"], "number %s" % n,
                                         raw[:70], kept[:70]))
            havec = collections.Counter(CMD_RE.findall(kept))
            for c in CMD_RE.findall(raw):
                if c in known:
                    continue
                if havec[c]:
                    havec[c] -= 1
                else:
                    lost_cmd += 1
                    if len(examples) < 12:
                        examples.append((paper, rec["id"], "\\" + c,
                                         raw[:70], kept[:70]))
            haves = collections.Counter(CSYM_RE.findall(kept))
            for c in CSYM_RE.findall(raw):
                sym = "\\" + c
                if sym in SPACING:
                    continue
                if haves[c]:
                    haves[c] -= 1
                else:
                    lost_csym += 1
                    if len(examples) < 18:
                        examples.append((paper, rec["id"], sym + " (symbol)",
                                         raw[:70], kept[:70]))
        used = set()
        with open(os.path.join(os.path.dirname(fpath), "sentences.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                for s in SITE_RE.findall(json.loads(line)["text"]):
                    used.add(s)
                    if s not in table:
                        dangling += 1
        for fid in set(table) - used:
            if table[fid].get("orphan"):
                orphan_marked += 1
            else:
                orphan_unmarked += 1

    print("papers=%d records=%d" % (papers, records))
    print("numbers lost in a formula : %d" % lost_num)
    print("commands lost in a formula: %d" % lost_cmd)
    print("control symbols lost      : %d" % lost_csym)
    print("MATH/REF not in the table : %d" % dangling)
    print("records no sentence uses  : %d (marked orphan %d, unmarked %d)"
          % (orphan_marked + orphan_unmarked, orphan_marked, orphan_unmarked))
    if dups:
        print("duplicate record ids      : %d" % dups)
    for pid, fid, what, raw, exp in examples:
        print("   %s %s  lost %s\n      raw: %s\n      exp: %s"
              % (pid, fid, what, raw, exp))

    if formula_errors:
        print("inconsistent formula bodies: %d" % formula_errors)
    failed = (formula_errors or lost_num or lost_cmd or lost_csym or dangling or orphan_unmarked
              or dups)
    if failed:
        print("FAILED: content was lost, or a record has no anchor")
        return 1
    if not papers:
        print("checked nothing: no formulas.json under %s"
              % ", ".join(args))
        return 2
    print("ok: %d papers, %d records" % (papers, records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
