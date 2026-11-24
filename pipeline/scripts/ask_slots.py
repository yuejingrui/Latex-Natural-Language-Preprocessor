#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ask a model for the role of each pending phrase inside a formula.

``latex2nl`` writes ``slot_todo.json`` with one item per phrase that has no role in
``data/slot_roles.json``.  This sends those phrases to the model the other askers
use and writes the answer file ``--merge-slot-answers`` reads.

    python3 ask_slots.py OUT/slot_todo.json --dry-run
    python3 ask_slots.py OUT/slot_todo.json
    python3 latex2nl.py OUT/normalized.tex -o OUT --canonical \\
            --merge-slot-answers OUT/slot_answers.json
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

SYS_PROMPT = """You name the role a phrase plays inside a formula.

Each item is a phrase that was lifted out of a formula into a text slot.  The
context shows where it sat: the text is replaced by <TXT>.  Choose exactly one of
the allowed answers (their meaning is given in the prompt) that fits the phrase in
that context.

Judge only from the phrase and its context.

Reply with one JSON object and nothing else:

{"answers": [{"n": 1, "answer": "predicate"}]}
"""




def load_answers(path):
    """Answers already in the file, by {id}; a missing file is an empty set."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    items = data.get("items") if isinstance(data, dict) else data
    out = {}
    for it in items or []:
        ident = it.get("sig")
        if ident and it.get("answer"):
            out[ident] = it
    return out


def merge_items(old, new):
    """Old answers plus new ones; a new answer replaces the old for the same id."""
    by = dict(old)
    for it in new:
        ident = it.get("sig")
        if ident:
            by[ident] = it
    return list(by.values())


def save_answers(path, items):
    """Write atomically, so a killed run leaves the previous file intact."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"items": items}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def build_prompt(items, allowed, meaning):
    lines = ["Allowed answers:"]
    for key in allowed:
        lines.append("- %s: %s" % (key, (meaning or {}).get(key, "")))
    lines.append("")
    for n, it in enumerate(items, 1):
        lines.append("%d. phrase: %r" % (n, it.get("raw") or it.get("sig")))
        if it.get("context"):
            lines.append("   context: %s" % it["context"])
        if it.get("count"):
            lines.append("   seen %d time(s)" % it["count"])
        lines.append("")
    lines.append('Answer with one JSON object: {"answers": [{"n": 1, "answer":'
                 ' "..."}]}')
    return "\n".join(lines)


def parse_answers(reply, count, allowed):
    """{index: answer}; anything outside the allowed set is ignored."""
    text = (reply or "").replace("**", "").replace("`", "")
    try:
        import pending_names as P
        doc = P._json_from(text)
    except Exception:
        doc = None
    if isinstance(doc, dict):
        doc = doc.get("answers") or doc.get("items") or []
    found = {}
    for it in doc or []:
        if not isinstance(it, dict):
            continue
        n = it.get("n") or it.get("index")
        a = str(it.get("answer") or "").strip().lower()
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= count and a in allowed:
            found[n] = a
    if found:
        return found
    for line in text.splitlines():
        m = re.match(r"\s*(?:item\s*)?(\d+)\s*[:.)\-]\s*([a-z_]+)\s*$",
                     line.strip(), re.I)
        if m and 1 <= int(m.group(1)) <= count:
            a = m.group(2).lower()
            if a in allowed:
                found[int(m.group(1))] = a
    return found


def load_todo(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    items = doc.get("items") if isinstance(doc, dict) else doc
    allowed = (doc.get("allowed_answers") if isinstance(doc, dict) else None) or []
    meaning = (doc.get("answer_meaning") if isinstance(doc, dict) else None) or {}
    pending = [it for it in items or [] if it.get("sig") and not it.get("answer")]
    return pending, allowed, meaning


def self_test():
    failures = []

    def check(label, ok, detail=""):
        print("  %s %s" % ("ok  " if ok else "FAIL", label))
        if not ok:
            failures.append("%s %s" % (label, detail))

    allowed = ["condition", "proposition", "predicate", "index"]
    print("[1] parsing")
    got = parse_answers('{"answers": [{"n": 1, "answer": "predicate"}]}', 1,
                        allowed)
    check("one answer read", got == {1: "predicate"}, str(got))
    check("an answer outside the set is ignored",
          parse_answers('{"answers": [{"n": 1, "answer": "verb"}]}', 1,
                        allowed) == {})
    check("a plain line reply works", parse_answers("2: index", 2, allowed)
          == {2: "index"})

    print("[2] the prompt")
    items = [{"sig": "Iso", "raw": "Iso", "context": "\\operatorname{<TXT>}(X)",
              "count": 10}]
    prompt = build_prompt(items, allowed, {"predicate": "a verb or relation"})
    check("phrase, context and meaning are in it",
          "'Iso'" in prompt and "<TXT>" in prompt and "a verb or relation" in prompt)
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("todo", nargs="?", help="OUT/slot_todo.json")
    ap.add_argument("-o", "--out", help="answer file (default: next to the todo)")
    ap.add_argument("--backend", default=os.environ.get("NL_PREP_BACKEND",
                                                        "api-openai"))
    ap.add_argument("--batch", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--restart", action="store_true",
                    help="ignore an existing answer file and ask everything again")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show-reply", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.todo:
        ap.error("a slot_todo.json is required (or --self-test)")

    pending, allowed, meaning = load_todo(args.todo)


    out = args.out or os.path.join(os.path.dirname(args.todo) or ".",
                                   "slot_answers.json")
    existing = {} if args.restart else load_answers(out)
    if existing:
        pending = [it for it in pending
                   if it.get("sig") not in existing]
        print("resuming: %d already answered in %s" % (len(existing), out))
    if not allowed:
        print("the todo file lists no allowed answers: nothing to ask")
        return 2
    if args.limit:
        pending = pending[:args.limit]
    if not pending:
        print("nothing to ask: every phrase already has an answer")
        return 0

    print("phrases: %d pending, %d batch, %d allowed answers"
          % (len(pending), args.batch, len(allowed)))
    if args.dry_run:
        print(build_prompt(pending[:args.batch], allowed, meaning))
        return 0

    import pending_names as P
    cfg = P.backend_config(args.backend)
    if not cfg["key"] and args.backend != "local-openai":
        print("no API key: set NL_PREP_API_KEY (or --backend local-openai)")
        return 2
    print("backend %s (%s) model %s"
          % (cfg["backend"], cfg["protocol"], cfg["model"]))

    written = []
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    for start in range(0, len(pending), args.batch):
        batch = pending[start:start + args.batch]
        try:
            reply = P.call_model(cfg, build_prompt(batch, allowed, meaning),
                                 timeout=180, system=SYS_PROMPT)
        except Exception as exc:
            print("  batch at %d failed: %s" % (start, exc))
            continue
        got = parse_answers(reply, len(batch), set(allowed))
        if args.show_reply or not got:
            print("  raw reply (%d chars): %r" % (len(reply or ""),
                                                  (reply or "")[:400]))
        for n, answer in sorted(got.items()):
            it = batch[n - 1]
            written.append({"sig": it["sig"], "answer": answer,
                            "raw": it.get("raw"), "context": it.get("context"),
                            "source": "model", "model": cfg["model"],
                            "asked_at": started})
        print("  batch at %d: %d of %d answered" % (start, len(got), len(batch)))
        if written:
            save_answers(out, merge_items(existing, written))

    if written:
        print("written %d answers to %s (file holds %d)"
              % (len(written), out, len(merge_items(existing, written))))
        print("now run: latex2nl.py OUT/normalized.tex -o OUT --canonical "
              "--merge-slot-answers %s" % out)
    else:
        print("nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
