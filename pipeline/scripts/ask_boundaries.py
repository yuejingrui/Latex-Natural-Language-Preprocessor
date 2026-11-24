#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ask a model whether each doubtful full stop ends a sentence.

``latex2nl`` splits prose on ``[.!?]`` and then accepts a break only when the
next word starts with a capital; a break before a lower-case word is written to
``sentence_todo.json`` and the sentence stays split until someone answers
``join``.  That someone can be a model: this script reuses the backend and the
HTTP call that ``pending_names.py`` already uses for names.

    # see the questions without spending a call
    python3 ask_boundaries.py OUT/sentence_todo.json --dry-run

    # ask, then let latex2nl apply the answers
    python3 ask_boundaries.py OUT/sentence_todo.json
    python3 latex2nl.py OUT/normalized.tex -o OUT --canonical \\
            --merge-sentence-answers data/sentence_answers.json

The answer file is the shape ``load_boundary_answers`` reads: one item per
``sig`` with ``answer`` in {join, split}, plus ``source`` and ``model`` so a
wrong answer can be traced to the model that gave it.
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

DATA = os.path.join(os.path.dirname(HERE), "data")
sys.path.insert(0, HERE)

BOUNDARY_ANSWERS = ("join", "split")

SYS_PROMPT = """You decide where a sentence ends in mathematics papers.

Each item is one full stop that a splitter treated as a sentence boundary, with
the text on both sides.  Answer:

- split: the full stop ends a sentence.  Common when the next part really is a
  new sentence, a list item, or a heading.
- join: it does not.  The stop belongs to an abbreviation, a symbol, an initial,
  a number, or an ellipsis, so the two halves are one sentence.

Judge only from the text.  A lower-case word after the stop is weak evidence for
join but not proof: authors do start sentences with a symbol or a variable name.
Two cases that repeat in this corpus:

- an author's initials followed by a verb are one sentence: "S.N. was supported
  by ..." -> join.  The stop belongs to the initials, not to the sentence.
- an enumerated item is a new sentence: "..., as desired." then "(2) As in the
  proof of (1), ..." -> split, although the item starts with "(" and a number.

Reply with one JSON object and nothing else, in exactly this shape:

{"answers": [{"n": 1, "answer": "join"}, {"n": 2, "answer": "split"}]}

"n" is the item number, "answer" is "join" or "split".  Answer every item.
"""


def build_prompt(items, offset=0):
    lines = ["Items to decide:", ""]
    for n, it in enumerate(items, 1):
        lines.append("%d. before: %s" % (n, (it.get("before") or "")[-90:]))
        lines.append("   after : %s" % ((it.get("after") or "")[:90]))
        lines.append("")
    lines.append('Answer as JSON: {"answers": [{"n": 1, "answer": "join"},'
                 ' ...]}')
    return "\n".join(lines)


def parse_answers(reply, count):
    """{index: answer} from the model's reply; anything else is ignored.

    Real replies are not always "1: join": they carry markdown, "Item 1", a JSON
    block, or a bare list of answers.  Strip the decoration, then look for the
    number and the word; a line with no number is taken in order.
    """
    text = (reply or "").replace("**", "").replace("`", "")
    found = {}
    order = []
    try:
        import pending_names as P
        doc = P._json_from(text)
    except Exception:
        doc = None
    if isinstance(doc, dict):
        doc = doc.get("answers") or doc.get("items") or []
    for it in doc or []:
        if not isinstance(it, dict):
            continue
        n = it.get("n") or it.get("index") or it.get("item")
        a = (it.get("answer") or "").strip().lower()
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= count and a in ("join", "split"):
            found[n] = a
    if found:
        return found
    for line in text.splitlines():
        # a JSON array or object of {"n": 1, "answer": "join"} is accepted too
        m = re.search(r"\{?\s*\"?\s*(?:n|item|index)?\"?\s*:?\s*(\d+)\s*,?"
                      r"\s*\"?\s*(?:answer)?\"?\s*:?\s*\"?(join|split)\w*",
                      line, re.I)
        if m:
            n, answer = int(m.group(1)), m.group(2).lower()
            if 1 <= n <= count:
                found[n] = answer
            continue
        m = re.match(r"\s*(?:item\s*)?(\d+)\s*[:.)\-]\s*(join|split)\b",
                     line.strip(), re.I)
        if m:
            n = int(m.group(1))
            if 1 <= n <= count:
                found[n] = m.group(2).lower()
            continue
        m = re.match(r"\s*[-*\d.]*\s*(join|split)\s*$", line.strip(), re.I)
        if m:
            order.append(m.group(1).lower())
    if found:
        return found
    for n, answer in enumerate(order, 1):
        if n <= count:
            found[n] = answer
    return found


def load_items(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return doc.get("items") if isinstance(doc, dict) else doc


def existing_answers(path):
    """Answers already written, so a second run does not ask again."""
    if not os.path.exists(path):
        return {}, []
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}, []
    items = doc.get("items") if isinstance(doc, dict) else doc
    answers = {it["sig"]: it["answer"] for it in items or []
               if it.get("sig") and it.get("answer") in BOUNDARY_ANSWERS}
    return answers, list(items or [])


def write_answers(path, old_items, new_items):
    keep = [it for it in old_items
            if it.get("sig") not in {n["sig"] for n in new_items}]
    payload = {
        "instructions": "join: the two halves are one sentence; split: they are two",
        "allowed_answers": list(BOUNDARY_ANSWERS),
        "items": keep + new_items,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def self_test():
    failures = []

    def check(label, ok, detail=""):
        print("  %s %s" % ("ok  " if ok else "FAIL", label))
        if not ok:
            failures.append("%s %s" % (label, detail))

    print("[1] parsing the reply")
    reply = "1: join\n2. SPLIT\n3: split\nnot an answer\n99: join"
    got = parse_answers(reply, 3)
    check("three answers read", got == {1: "join", 2: "split", 3: "split"},
          str(got))
    check("an out-of-range number is ignored", 99 not in parse_answers(reply, 3))
    check("a line without an answer is ignored",
          parse_answers("the map is onto", 1) == {})

    print("[2] the prompt carries both halves")
    items = [{"sig": "a ‖ b", "before": "… prime.", "after": "we know"},
             {"sig": "c ‖ d", "before": "No.", "after": "5 holds"}]
    prompt = build_prompt(items)
    check("both halves are in it",
          "… prime." in prompt and "we know" in prompt and "No." in prompt)
    check("numbered from 1", "1. before:" in prompt and "2. before:" in prompt)

    print("[3] the file shape the reader expects")
    answers = {"a ‖ b": "join"}
    items_out = [{"sig": sig, "answer": ans, "source": "model"}
                 for sig, ans in answers.items()]
    check("only allowed answers", all(i["answer"] in BOUNDARY_ANSWERS
                                      for i in items_out))
    check("sig is the key", items_out[0]["sig"] == "a ‖ b")

    bad = [x for x in failures]
    print("\n%d checks passed%s" % (4 + len(failures) - len(bad),
                                    "" if not bad else ", %d FAILED" % len(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("todo", nargs="?", help="OUT/sentence_todo.json")
    ap.add_argument("-o", "--out",
                    default=os.path.join(DATA, "sentence_answers.json"),
                    help="where the answers are written (default: "
                         "pipeline/data/sentence_answers.json)")
    ap.add_argument("--backend", default=os.environ.get("NL_PREP_BACKEND",
                                                        "api-openai"))
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0,
                    help="ask at most this many items")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompts, call nothing")
    ap.add_argument("--show-reply", action="store_true",
                    help="print the raw reply of every batch")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.todo:
        ap.error("a sentence_todo.json is required (or --self-test)")

    items = load_items(args.todo) or []
    pending = [it for it in items if not it.get("answer")]
    if args.limit:
        pending = pending[:args.limit]
    if not pending:
        print("nothing to ask: every item in %s already has an answer"
              % args.todo)
        return 0

    print("items: %d pending, %d batch" % (len(pending), args.batch))
    if args.dry_run:
        print(build_prompt(pending[:args.batch]))
        return 0

    import pending_names as P
    cfg = P.backend_config(args.backend)
    if not cfg["key"] and args.backend != "local-openai":
        print("no API key: set NL_PREP_API_KEY (or use --backend local-openai)")
        return 2
    print("backend %s (%s) model %s"
          % (cfg["backend"], cfg["protocol"], cfg["model"]))

    old_answers, old_items = existing_answers(args.out)
    written, failed = [], 0
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    for start in range(0, len(pending), args.batch):
        batch = pending[start:start + args.batch]
        try:
            reply = P.call_model(cfg, build_prompt(batch), timeout=180,
                                 system=SYS_PROMPT)
        except Exception as exc:                    # network, rate limit, JSON
            print("  batch at %d failed: %s" % (start, exc))
            failed += 1
            continue
        got = parse_answers(reply, len(batch))
        if args.show_reply or not got:
            print("  raw reply (%d chars): %r" % (len(reply or ""),
                                                  (reply or "")[:400]))
        for n, answer in sorted(got.items()):
            it = batch[n - 1]
            if it["sig"] in old_answers:
                continue
            written.append({
                "sig": it["sig"],
                "answer": answer,
                "before": it.get("before"),
                "after": it.get("after"),
                "source": "model",
                "model": cfg["model"],
                "asked_at": started,
            })
        print("  batch at %d: %d of %d answered"
              % (start, len(got), len(batch)))

    if written:
        write_answers(args.out, old_items, written)
        print("written %d answers to %s" % (len(written), args.out))
        print("now run: latex2nl.py OUT/normalized.tex -o OUT --canonical "
              "--merge-sentence-answers %s" % args.out)
    else:
        print("nothing written")
    return 1 if failed and not written else 0


if __name__ == "__main__":
    sys.exit(main())
