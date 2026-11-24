#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ask a model for the reading of each open construction.

``lexicon.py`` writes ``construction_todo.json`` with one item per construction the
tables could not decide: a question, an example formula, and the default that is
currently used instead of a decision.  This sends those questions to the model the
other askers already use, and writes the answer file ``--merge-answers`` reads.

    python3 ask_lexicon.py OUT/construction_todo.json --dry-run
    python3 ask_lexicon.py OUT/construction_todo.json
    python3 lexicon.py OUT/formulas.json -o OUT/paper_lexicon.json \\
            --merge-answers OUT/construction_answers.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

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

import pending_names as P


SYS_PROMPT = """You decide how a product written without an operator sign is meant.

Each item is a piece of LaTeX in which two things sit next to each other.  Decide
which of the allowed readings it has:

- multiply: the two are multiplied (or juxtaposed) -- the default in maths.
- apply: the left one is a function or operator applied to the group on the right.
- label: the run is one name for one object (a group, a space, a map), not a product.
- index: the two form one multi-index inside a subscript or superscript.

Use the example formula and the question.  Judge only from the text.

Reply with one JSON object and nothing else:

{"answers": [{"n": 1, "answer": "multiply"}, {"n": 2, "answer": "label"}]}
"""




def load_answers(path, scope):
    """Answers already in the file, by {id}; a missing file is an empty set."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    from lexicon import answer_digest, scope_matches
    owner = (data.get("meta") or {}) if isinstance(data, dict) else {}
    if not owner.get("source_file"):
        lexicon_path = os.path.join(os.path.dirname(path), "paper_lexicon.json")
        if os.path.isfile(lexicon_path):
            with open(lexicon_path, encoding="utf-8") as fh:
                binding = json.load(fh).get("local_answer_scope") or {}
            if binding.get("sha256") == answer_digest(path):
                owner = {**binding, **{k: v for k, v in owner.items() if v}}
    if not scope_matches(owner, scope):
        return {}
    items = data.get("items") if isinstance(data, dict) else data
    out = {}
    for it in items or []:
        ident = it.get("key")
        if ident and it.get("answer") and scope_matches(it, scope):
            out[ident] = it
    return out


def merge_items(old, new):
    """Old answers plus new ones; a new answer replaces the old for the same id."""
    by = dict(old)
    for it in new:
        ident = it.get("key")
        if ident:
            by[ident] = it
    return list(by.values())


def save_answers(path, items, scope):
    """Write atomically, so a killed run leaves the previous file intact."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"meta": scope, "items": items}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def build_prompt(items, allowed):
    lines = ["Allowed answers: " + ", ".join(allowed), ""]
    for n, it in enumerate(items, 1):
        lines.append("%d. %s" % (n, it.get("question")))
        if it.get("scope_definition"):
            definition = it["scope_definition"]
            lines.append("   Active definition of %s: %s at %s (%s)" %
                         (definition["symbol"], definition["kind"], definition["formula"], definition["sentence"]))
        if it.get("conflict"):
            lines.append("   Conflicting readings for the same occurrence: %s" % it["conflict"])
        for ex in (it.get("examples") or [])[:2]:
            formula = ex.get("formula") or ""
            if "offset" in ex:
                pos = ex["offset"]
                formula = formula[:pos] + "[[target]]" + formula[pos:]
            lines.append("   e.g. %s: %s" % (ex.get("id"), formula))
            if ex.get("sentence"):
                lines.append("   sentence: " + ex["sentence"])
        lines.append("")
    lines.append('Answer with one JSON object: {"answers": [{"n": 1, "answer":'
                 ' "..."}]}')
    return "\n".join(lines)


def parse_answers(reply, count, allowed):
    """{index: answer}; anything outside the allowed set is ignored."""
    text = (reply or "").replace("**", "").replace("`", "")
    numbers = lambda s: re.sub(r"\s+", "", s).lower()
    try:
        doc = P._json_from(text)
    except Exception:
        doc = None
    if isinstance(doc, dict):
        doc = doc.get("answers") or doc.get("items") or []
    found = {}
    for it in doc or []:
        if not isinstance(it, dict):
            continue
        n = it.get("n") or it.get("index") or it.get("key")
        a = str(it.get("answer") or "").strip().lower()
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= count and a in allowed:
            found[n] = a
    if found:
        return found
    # a plain "1: multiply" reply, in case the endpoint ignored the JSON mode
    for line in text.splitlines():
        m = re.match(r"\s*(?:item\s*)?(\d+)\s*[:.)\-]\s*([a-z_]+)\s*$",
                     line.strip(), re.I)
        if m and 1 <= int(m.group(1)) <= count:
            a = numbers(m.group(2))
            if a in allowed:
                found[int(m.group(1))] = a
    return found


def load_todo(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    items = doc.get("items") if isinstance(doc, dict) else doc
    allowed = (doc.get("allowed_readings") if isinstance(doc, dict) else None) \
        or ["multiply", "apply", "label", "index"]
    pending = [it for it in items or [] if it.get("key") and not it.get("answer")]
    meta = (doc.get("meta") or {}) if isinstance(doc, dict) else {}
    scope = {k: meta[k] for k in ("paper", "source_file") if meta.get(k)}
    return pending, allowed, scope


def self_test():
    failures = []

    def check(label, ok, detail=""):
        print("  %s %s" % ("ok  " if ok else "FAIL", label))
        if not ok:
            failures.append("%s %s" % (label, detail))

    allowed = ["multiply", "apply", "label", "index"]
    print("[1] parsing the reply")
    got = parse_answers('{"answers": [{"n": 1, "answer": "label"}, '
                        '{"n": 2, "answer": "MULTIPLY"}]}', 2, allowed)
    check("two answers read", got == {1: "label", 2: "multiply"}, str(got))
    check("an answer outside the set is ignored",
          parse_answers('{"answers": [{"n": 1, "answer": "math"}]}', 1,
                        allowed) == {})
    check("a plain line reply still works",
          parse_answers("1: apply\n2: index", 2, allowed)
          == {1: "apply", 2: "index"})
    check("an out-of-range number is ignored",
          3 not in parse_answers("3: label", 2, allowed))

    print("[2] the prompt")
    items = [{"question": "Is 'D · o' one multi-index or a product?",
              "examples": [{"id": "MATH13", "formula": "\\mathbb{M}_{Dol}"}]}]
    prompt = build_prompt(items, allowed)
    check("the question and the example are in it",
          "multi-index" in prompt and "MATH13" in prompt)
    check("the allowed answers are listed", "label" in prompt)
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("todo", nargs="?", help="OUT/construction_todo.json")
    ap.add_argument("-o", "--out", help="answer file (default: next to the todo)")
    ap.add_argument("--backend", default="")
    ap.add_argument("--batch", type=P.positive_count, default=15)
    ap.add_argument("--limit", type=P.positive_count, default=0)
    ap.add_argument("--restart", action="store_true",
                    help="ignore an existing answer file and ask everything again")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show-reply", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.todo:
        ap.error("a construction_todo.json is required (or --self-test)")

    pending, allowed, scope = load_todo(args.todo)


    out = args.out or os.path.join(os.path.dirname(args.todo) or ".",
                                   "construction_answers.json")
    existing = {} if args.restart else load_answers(out, scope)
    existing = {key: row for key, row in existing.items() if row.get("answer") in allowed}
    if existing:
        pending = [it for it in pending
                   if it.get("conflict") or it.get("key") not in existing
                   or existing[it["key"]].get("anchor") != it.get("anchor")]
        print("resuming: %d already answered in %s" % (len(existing), out))
    if args.limit:
        pending = pending[:args.limit]
    if not pending:
        print("nothing to ask: every item already has an answer")
        return 0

    print("items: %d pending, %d batch, allowed: %s"
          % (len(pending), args.batch, ", ".join(allowed)))
    if args.dry_run:
        print(build_prompt(pending[:args.batch], allowed))
        return 0

    cfg = P.backend_config(args.backend)
    print("backend %s (%s) model %s"
          % (cfg["backend"], cfg["protocol"], cfg["model"]))

    written = []
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    for start in range(0, len(pending), args.batch):
        batch = pending[start:start + args.batch]
        try:
            reply = P.call_model(cfg, build_prompt(batch, allowed), timeout=180,
                                 system=SYS_PROMPT)
        except Exception as exc:
            print("  batch at %d failed: %s" % (start, exc))
            continue
        got = parse_answers(reply, len(batch), set(allowed))
        if args.show_reply or not got:
            print("  raw reply (%d chars): %r" % (len(reply or ""),
                                                  (reply or "")[:400]))
        for n, answer in sorted(got.items()):
            it = batch[n - 1]
            written.append({"key": it["key"], "anchor": it.get("anchor"), "answer": answer,
                            "source": "model", "model": cfg["model"],
                            "asked_at": started,
                            "note": "default was %s"
                                    % it.get("default_if_unanswered")})
        print("  batch at %d: %d of %d answered" % (start, len(got), len(batch)))
        if written:
            save_answers(out, merge_items(existing, written), scope)

    if written:
        print("written %d answers to %s (file holds %d)"
              % (len(written), out, len(merge_items(existing, written))))
        print("now run: lexicon.py FORMULAS -o OUT --merge-answers %s" % out)
    else:
        print("nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
