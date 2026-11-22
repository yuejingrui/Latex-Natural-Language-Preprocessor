#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Output checks: the errors the code does not know it is making.

``verify_formulas.py`` asks "did a formula lose something"; this asks "does the
output look like a paper".  It reads the artefacts of one run -- sentences.jsonl,
formulas.json, normalized.tex, normalize-stats.json -- and records every hit in the
ledger with the kind from GAPS.md section 5 (C1-C16).

    python3 verify_output.py OUT            # report + write OUT/issues.json
    python3 verify_output.py OUT --strict   # exit 1 if a hard check hits

Exit codes: 0 clean, 1 a hard check hit, 2 nothing to check.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import issues  # noqa: E402

PLACEHOLDER = re.compile(r"\b(?:MATH|REF|EQ|GRID|DIAGRAM)\d+\b")
CONTROL = re.compile(r"\\[A-Za-z]+")
TERMINAL = ".!?:"
REF_BRACE = re.compile(r"REF\d+\}")


def load(out: str) -> Dict[str, object]:
    doc = {}
    sents_path = os.path.join(out, "sentences.jsonl")
    forms_path = os.path.join(out, "formulas.json")
    if not os.path.exists(sents_path) or not os.path.exists(forms_path):
        raise SystemExit("verify_output: %s has no sentences.jsonl/formulas.json" % out)
    doc["sentences"] = [json.loads(l) for l in open(sents_path, encoding="utf-8")
                        if l.strip()]
    data = json.load(open(forms_path, encoding="utf-8"))
    doc["formulas"] = data["formulas"] if isinstance(data, dict) else data
    stats_path = os.path.join(out, "normalize-stats.json")
    doc["stats"] = {}
    if os.path.exists(stats_path):
        try:
            doc["stats"] = json.load(open(stats_path, encoding="utf-8"))
        except ValueError:
            pass
    return doc


SENTENCE_ROOTS = {"S[dcl]", "S[wq]", "S[q]", "S[qem]", "NP"}


def _parses(out: str):
    path = os.path.join(out, "parses.jsonl")
    if not os.path.exists(path):
        return None
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _leaves(tree):
    if "word" in tree:
        yield tree["word"]
        return
    for ch in tree.get("children") or []:
        yield from _leaves(ch)


def check_parses(out: str, sentences) -> List[dict]:
    """The parse file, when the run has one: coverage, roots, leaf fidelity."""
    rows = _parses(out)
    if rows is None:
        return []
    hits = []
    by_sid = {r["sid"]: r for r in rows}
    missing = [s["sid"] for s in sentences if s["sid"] not in by_sid]
    for sid in missing[:50]:
        hits.append(("parse:coverage", "verify_output.check_parses",
                     "%s has no parse" % sid, 1))
    for r in rows:
        tree = r.get("tree")
        if not tree:
            hits.append(("parse:failed", "verify_output.check_parses",
                         "%s has no tree" % r["sid"], 1))
            continue
        if tree.get("cat") not in SENTENCE_ROOTS:
            hits.append(("parse:root", "verify_output.check_parses",
                         "%s root %s" % (r["sid"], tree.get("cat")), 1))
        leaves = list(_leaves(tree))
        if leaves == ["FAILED"]:
            hits.append(("parse:failed", "verify_output.check_parses",
                         "%s is a FAILED tree" % r["sid"], 1))
        sent = next((s for s in sentences if s["sid"] == r["sid"]), None)
        if sent is not None and leaves != list(sent.get("tokens") or []):
            hits.append(("parse:leaf_mismatch", "verify_output.check_parses",
                         "%s leaves %d vs tokens %d"
                         % (r["sid"], len(leaves), len(sent.get("tokens") or [])), 1))
    return hits


def check_c1(doc) -> List[dict]:
    """A sentence cut in the middle: no terminal punctuation, and the next
    sentence starts in lower case.

    The first half alone is not evidence -- a display can end a sentence without
    a full stop ("Define MATH81"), and that is a different note.  What marks a
    cut (GAPS.md M1) is the continuation: the next sentence begins as if the
    sentence had not ended.  The record's ``context_before`` is the second
    signal for the same defect, for displays whose lead-in was kept.
    """
    hits = []
    sents = doc["sentences"]
    for i, sent in enumerate(sents[:-1]):
        text = (sent.get("text") or "").rstrip()
        if not text or text[-1] in TERMINAL:
            continue
        # a list item or a display that ends on a comma is split by design
        # (SPLITTING.md section 4.1), not cut in the middle
        if text[-1] in ",;" or "/item" in (sent.get("unit") or ""):
            continue
        nxt = (sents[i + 1].get("text") or "").strip()
        if "/item" in (sents[i + 1].get("unit") or ""):
            continue
        if not nxt:
            continue
        first = next((ch for ch in nxt if ch.isalpha()), "")
        if first and first.islower():
            hits.append({"where": "%s %r -> %r"
                                 % (sent.get("sid"), text[-30:], nxt[:30])})
    for f in doc["formulas"]:
        if not f.get("orphan") or f.get("kind") != "display":
            continue
        before = (f.get("context_before") or "").rstrip()
        if not before or before[-1] in TERMINAL:
            continue
        if before[-1] in ",;":
            continue          # a list ends on a comma by design
        if re.search(r"(?:MATH|REF)\d+$", before):
            continue          # the display before this one, not a cut lead-in
        # a context that is only placeholders is the display before this one, not
        # a lead-in sentence: two displays in a row are not a cut
        if not re.search(r"[A-Za-z]{2,}", PLACEHOLDER.sub(" ", before)):
            continue
        hits.append({"where": "%s after %r" % (f["id"], before[-40:])})
    return hits


def check_c2(doc) -> List[dict]:
    hits = []
    for s in doc["sentences"]:
        text = PLACEHOLDER.sub(" ", s["text"])
        m = CONTROL.search(text)
        if m:
            hits.append({"where": "%s %r" % (s["sid"], m.group(0))})
    return hits


def check_c3(doc) -> List[dict]:
    return [{"where": s["sid"]} for s in doc["sentences"]
            if "{" in s["text"] or "}" in s["text"]]


def check_c4(doc) -> List[dict]:
    return [{"where": "%s %r" % (s["sid"], s["text"][:40])}
            for s in doc["sentences"]
            if len(s.get("tokens") or []) <= 2
            and (not s["text"] or s["text"].rstrip()[-1] not in TERMINAL)]


def check_c5(doc) -> List[dict]:
    hits = []
    sents = doc["sentences"]
    for i in range(1, len(sents)):
        text = sents[i]["text"].lstrip()
        prev = sents[i - 1]["text"].rstrip()
        if (text.startswith(("MATH", "REF")) and prev
                and prev[-1] not in TERMINAL):
            hits.append({"where": "%s after %r" % (sents[i]["sid"], prev[-40:])})
    return hits


def check_c6(doc) -> List[dict]:
    """A formula body that still holds a $ or a display: maths inside maths.

    An unescaped $ there is the signature of the \\ensuremath parity flip
    (DECISIONS.md D10), so this is a hard signal, not a heuristic.
    """
    hits = []
    for f in doc["formulas"]:
        if f.get("kind") == "ref":
            # a citation's optional note may hold maths ("\cite[for $p=2$]{..}"),
            # and the record is a reference, not a formula body
            continue
        body = f.get("expanded") or ""
        if "$" in body or "\\intertext" in body or "\\begin{display}" in body:
            hits.append({"where": f["id"]})
    return hits


def check_c16(doc) -> List[dict]:
    """Soft: a display that reads like prose.  Kept to watch D10's tail."""
    hits = []
    for f in doc["formulas"]:
        if f.get("kind") != "display":
            continue
        body = f.get("expanded") or ""
        if re.search(r"(?:[A-Za-z]{2,}\s+){3,}", body):
            hits.append({"where": f["id"]})
    return hits


def check_c7(doc) -> List[dict]:
    hits = []
    for f in doc["formulas"]:
        if f.get("kind") != "inline":
            continue
        raw = f.get("raw") or ""
        if raw.count("{") != raw.count("}"):
            hits.append({"where": f["id"]})
    return hits


def check_c8(doc) -> List[dict]:
    hits = []
    for s in doc["sentences"]:
        for field in ("section", "title"):
            v = s.get(field)
            if isinstance(v, str) and "$" in v:
                hits.append({"where": "%s %s" % (s["sid"], field)})
    return hits


WORD_RUN = re.compile(r"\b[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){3,}\b")


def check_c9(doc) -> List[dict]:
    hits = []
    for f in doc["formulas"]:
        if f.get("kind") == "ref":
            # a reference record's body is the label ("eq:two moduli are
            # birational"); labels are words by design, not prose that leaked in
            continue
        body = CONTROL.sub(" ", f.get("expanded") or "")
        body = re.sub(r"<TXT\d*>", " ", body)
        if WORD_RUN.search(body):
            hits.append({"where": f["id"]})
    return hits


def check_c10(doc) -> List[dict]:
    hits = []
    for f in doc["formulas"]:
        for m in re.finditer(r"\\begin\{([A-Za-z@*]+)\}", f.get("expanded") or ""):
            if m.group(1) not in ("grid", "diagram"):
                hits.append({"where": "%s %s" % (f["id"], m.group(1))})
                break
    return hits


def check_c11(doc) -> List[dict]:
    return [{"where": f["id"]} for f in doc["formulas"]
            if "\\begin{grid}{cases}" in (f.get("expanded") or "")
            and f.get("role") != "piecewise"]


def check_c12(doc) -> List[dict]:
    hits = []
    for s in doc["sentences"]:
        if REF_BRACE.search(s["text"]):
            hits.append({"where": s["sid"]})
    for f in doc["formulas"]:
        # a label such as \ref{a{b}} is legal; only an unbalanced target is a bug
        target = f.get("ref_target")
        if isinstance(target, str) and target.count("{") != target.count("}"):
            hits.append({"where": "%s ref_target" % f["id"]})
    return hits


def check_c13(doc, normalized: str) -> List[dict]:
    labels = set(re.findall(r"\\label\{([^{}]*)\}", normalized))
    hits = []
    for f in doc["formulas"]:
        target = f.get("ref_target")
        if not target or not isinstance(target, str):
            continue
        if target in labels or target in ("", "None"):
            continue
        hits.append({"where": "%s -> %s" % (f["id"], target)})
    return hits


def check_c14(doc) -> List[dict]:
    names = ((doc["stats"] or {}).get("outside_vocabulary") or {})
    if not names:
        return []
    want = {"\\" + n for n in names}
    hits = []
    for s in doc["sentences"]:
        for m in CONTROL.finditer(s["text"]):
            if m.group(0) in want:
                hits.append({"where": "%s %s" % (s["sid"], m.group(0))})
                break
    return hits


def run(out: str, strict: bool) -> int:
    doc = load(out)
    norm_path = os.path.join(out, "normalized.tex")
    normalized = ""
    if os.path.exists(norm_path):
        normalized = open(norm_path, encoding="utf-8", errors="replace").read()

    checks = [
        ("C1", "hard", check_c1(doc)),
        ("C2", "hard", check_c2(doc)),
        ("C3", "hard", check_c3(doc)),
        ("C4", "soft", check_c4(doc)),
        ("C5", "soft", check_c5(doc)),
        ("C6", "hard", check_c6(doc)),
        ("C7", "soft", check_c7(doc)),
        ("C8", "soft", check_c8(doc)),
        ("C9", "hard", check_c9(doc)),
        ("C10", "hard", check_c10(doc)),
        ("C11", "soft", check_c11(doc)),
        ("C12", "hard", check_c12(doc)),
        ("C13", "soft", check_c13(doc, normalized)),
        ("C14", "hard", check_c14(doc)),
        ("C16", "soft", check_c16(doc)),
    ]
    issues.reset()
    hard_hits = 0
    print("check  gate  hits  example")
    for name, gate, hits in checks:
        if hits:
            example = (hits[0].get("where") or "")[:60]
        else:
            example = ""
        print("%-6s %-5s %5d  %s" % (name, gate, len(hits), example))
        for h in hits[:400]:
            issues.record("check:" + name, "verify_output.%s" % name,
                          where=h.get("where", ""), size=1)
        if gate == "hard":
            hard_hits += len(hits)
    # the tools write their own ledgers; fold them in before writing
    for name in sorted(os.listdir(out)):
        if name.startswith("issues_") and name.endswith(".json"):
            n = issues.merge_file(os.path.join(out, name))
            if n:
                print("merged %d entries from %s" % (n, name))

    for kind, site, where, size in check_parses(out, doc["sentences"]):
        issues.record(kind, site, where, size)

    payload = issues.write(os.path.join(out, "issues.json"),
                           {"checks": {n: len(h) for n, _g, h in checks}})
    print("\nissues written: %s" % os.path.join(out, "issues.json"))
    print(issues.summary_line())
    if strict and hard_hits:
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", nargs="?", help="a run directory")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when a hard check hits")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.out:
        ap.error("a run directory is required")
    return run(args.out, args.strict)


def self_test() -> int:
    failures = []

    def check(label, ok, detail=""):
        print("  %s %s %s" % ("ok  " if ok else "FAIL", label,
                              "" if ok else detail))
        if not ok:
            failures.append(label)

    # an unknown kind must be refused
    try:
        issues.record("not:a:kind", "self_test")
        check("an unregistered kind is refused", False, "record() accepted it")
    except ValueError:
        check("an unregistered kind is refused", True)
    # a registered one must be accepted and counted
    issues.reset()
    issues.record("check:C1", "self_test", where="x", size=1)
    check("a registered kind is recorded", issues.counts() == {"check:C1": 1},
          str(issues.counts()))
    check("a hard kind is counted as hard", len(issues.hard()) == 1)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
