#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OUT/human_todo.json: the things no machine should decide.

GAPS.md section 6.  Three kinds of entry land here and nowhere else:

  * a strategy question (keep or drop footnotes; whether to fail on pending work);
  * a source error (an unbalanced brace, an unclosed $$, a missing \\input);
  * something the model could not answer -- 'unknown', two empty replies, 'unsure'.

It is written and read back like every other question file, but no asker reads it.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List
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


_ITEMS: List[dict] = []


def reset() -> None:
    _ITEMS.clear()


def add(reason: str, detail: str, source: str = "", kind: str = "") -> dict:
    """One entry; the same (reason, detail) pair is recorded once."""
    detail = detail[:200]
    for it in _ITEMS:
        if it["reason"] == reason and it["detail"] == detail:
            return it
    item = {"reason": reason, "detail": detail, "source": source,
            "kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _ITEMS.append(item)
    return item


def items() -> List[dict]:
    return list(_ITEMS)


def write(path: str) -> dict:
    payload = {"_about": "Questions for a person: no right answer, a broken source, "
                         "or an answer the model could not give (GAPS.md section 6).",
               "counts": {}, "items": _ITEMS}
    for it in _ITEMS:
        payload["counts"][it["reason"]] = payload["counts"].get(it["reason"], 0) + 1
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return payload


def load(path: str) -> Dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for it in doc.get("items") or []:
        out[(it.get("reason"), it.get("detail"))] = it
    return out


def merge_into(path: str) -> int:
    """Fold the file at ``path`` into the current list; returns how many added."""
    added = 0
    for (_r, _d), it in load(path).items():
        before = len(_ITEMS)
        add(it.get("reason", ""), it.get("detail", ""),
            it.get("source", ""), it.get("kind", ""))
        if len(_ITEMS) > before:
            added += 1
    return added

def refresh(path, source):
    previous = load(path)
    reset()
    _ITEMS.extend(it for it in previous.values() if it.get("source") != source)
    return set(previous)


def finish_refresh(path, previous):
    if _ITEMS:
        write(path)
    elif os.path.isfile(path):
        os.remove(path)
    return len({(it["reason"], it["detail"]) for it in _ITEMS} - previous)


def read_current(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {}


def from_names(names_todo: str, out_path: str) -> int:
    doc = read_current(names_todo)
    previous = refresh(out_path, "names_todo")
    for it in doc.get("items") or []:
        action = it.get("action")
        if action in (None, "", "unknown"):
            add("name_undecided", "\\\\" + str(it.get("name")),
                source="names_todo", kind="name")
    return finish_refresh(out_path, previous)


def from_roles(role_todo: str, out_path: str) -> int:
    doc = read_current(role_todo)
    previous = refresh(out_path, "role_todo")
    for it in doc.get("items") or []:
        if it.get("answer") == "unsure":
            add("role_unsure", it["fid"], source="role_todo", kind="role")
        elif not it.get("answer") and int(it.get("asked") or 0) >= 2:
            add("model_refused", it["fid"], source="role_todo", kind="role")
    return finish_refresh(out_path, previous)


#: kinds from a ledger that need a person: a strategy is undecided, or the
#: source itself is broken  (GAPS.md section 6)
#: Notes are preserved now (normalize packs every \footnote, braced or not,
#: into a content block), so there is no footnote strategy left to decide.  A
#: ledger entry that says otherwise is a stale file, not a question.
STRATEGY_KINDS = {}
ERROR_KINDS = {
    "human:source_error": ("source_error", ""),
    "check:C7": ("source_error", "unbalanced braces in an inline formula"),
}


def from_issues(issues_path: str, out_path: str) -> int:
    doc = read_current(issues_path)
    previous = refresh(out_path, "issues.json")
    blocks = read_current(os.path.join(os.path.dirname(issues_path), "content_blocks.json"))
    if isinstance(blocks, dict):
        blocks = blocks.get("content_blocks", [])
    for block in blocks:
        reason = block.get("reason", "")
        if (block.get("kind") == "tex_execution" and block.get("status") == "pending"
                and "not found" in reason.lower()):
            add("source_error", reason, source="issues.json", kind="human:source_error")
    for e in doc.get("entries") or []:
        kind = e.get("kind") or ""
        spec = STRATEGY_KINDS.get(kind) or ERROR_KINDS.get(kind)
        if not spec:
            continue
        add(spec[0], "%s: %s" % (kind, e.get("where") or e.get("site") or ""),
            source="issues.json", kind=kind)
    return finish_refresh(out_path, previous)


def _cli_from_issues(todo, out):
    n = from_issues(todo, out)
    print("human table: %d strategy/source item(s) -> %s" % (n, out))
    return 0

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-names", nargs=2, metavar=("TODO", "OUT"))
    ap.add_argument("--from-roles", nargs=2, metavar=("TODO", "OUT"))
    ap.add_argument("--from-issues", nargs=2, metavar=("ISSUES", "OUT"))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.from_names:
        n = from_names(*a.from_names)
        print("human table: %d name(s) -> %s" % (n, a.from_names[1]))
        return 0
    if a.from_roles:
        n = from_roles(*a.from_roles)
        print("human table: %d role(s) -> %s" % (n, a.from_roles[1]))
        return 0
    if a.from_issues:
        return _cli_from_issues(*a.from_issues)
    ap.print_help()
    return 2


def self_test() -> int:
    import tempfile
    failures = []

    def check(label, ok, detail=""):
        print("  %s %s %s" % ("ok  " if ok else "FAIL", label,
                              "" if ok else detail))
        if not ok:
            failures.append(label)

    tmp = tempfile.mkdtemp()
    names = os.path.join(tmp, "names_todo.json")
    json.dump({"items": [
        {"name": "foo", "action": "allow"},
        {"name": "bar", "action": "unknown"},
        {"name": "baz", "action": None},
    ]}, open(names, "w"))
    out = os.path.join(tmp, "human_todo.json")
    n = from_names(names, out)
    check("names: two undecided go to the table", n == 2, str(n))
    roles = os.path.join(tmp, "role_todo.json")
    json.dump({"items": [
        {"fid": "MATH1", "answer": "unsure"},
        {"fid": "MATH2", "answer": None, "asked": 2},
        {"fid": "MATH3", "answer": None, "asked": 1},
        {"fid": "MATH4", "answer": "constraint"},
    ]}, open(roles, "w"))
    n = from_roles(roles, out)
    check("roles: unsure and twice-refused go in", n == 2, str(n))
    doc = json.load(open(out, encoding="utf-8"))
    check("the table counts by reason",
          doc["counts"].get("name_undecided") == 2
          and doc["counts"].get("role_unsure") == 1
          and doc["counts"].get("model_refused") == 1, str(doc["counts"]))
    check("re-reading merges instead of duplicating",
          from_names(names, out) == 0, "duplicates added")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
