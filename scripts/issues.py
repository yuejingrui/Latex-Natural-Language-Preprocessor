#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ledger: one entry per thing the pipeline let through, with its destination.

RULES.md describes issue routing.  A site that drops
content, misplaces it, guesses a default, or leaves a question unanswered calls
``record()``.  The destination of each kind is fixed in ``data/issue_kinds.json``:

    code        a defect: fix the code or the table
    todo:<file> open, answerable from the text: goes into that question file
    human       no right answer, or the source itself is wrong
    check       caught by verify_output.py rather than by the code
    ledger      deliberate, but the amount must be visible

An unregistered kind raises, so a new gap cannot appear without being registered
(RULES.md).  Counts are written to ``OUT/issues.json``.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS_PATH = os.path.join(os.path.dirname(HERE), "data", "issue_kinds.json")

_ENTRIES: List[dict] = []
_KINDS: Optional[Dict[str, dict]] = None


def registry() -> Dict[str, dict]:
    """kind -> {route, gate, about}.  Cached."""
    global _KINDS
    if _KINDS is None:
        with open(KINDS_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        _KINDS = {k: v for k, v in (doc.get("kinds") or {}).items()}
    return _KINDS


def known(kind: str) -> bool:
    return kind in registry()


def reset() -> None:
    _ENTRIES.clear()


def record(kind: str, site: str, where: str = "", size: int = 0,
           **extra) -> dict:
    """Log one let-through.  Raises on a kind that is not in the table."""
    reg = registry()
    if kind not in reg:
        raise ValueError(
            "unregistered issue kind %r at %s: add it to data/issue_kinds.json "
            "(route + gate) before recording it" % (kind, site))
    entry = {"kind": kind, "site": site, "route": reg[kind].get("route", ""),
             "gate": reg[kind].get("gate", "none"),
             "where": where[:200], "size": int(size)}
    entry.update({k: v for k, v in extra.items() if v is not None})
    _ENTRIES.append(entry)
    return entry


def entries() -> List[dict]:
    return list(_ENTRIES)


def counts() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for e in _ENTRIES:
        out[e["kind"]] = out.get(e["kind"], 0) + 1
    return out


def sizes() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for e in _ENTRIES:
        out[e["kind"]] = out.get(e["kind"], 0) + int(e.get("size") or 0)
    return out


def hard() -> List[dict]:
    return [e for e in _ENTRIES if e.get("gate") == "hard"]


def by_route(route_prefix: str) -> List[dict]:
    return [e for e in _ENTRIES if str(e.get("route", "")).startswith(route_prefix)]


def write(path: str, extra: Optional[dict] = None) -> dict:
    """Write OUT/issues.json; returns the payload that was written."""
    payload = {
        "_about": "Every place the pipeline let something through, one entry per "
                  "site, with the destination fixed in data/issue_kinds.json.",
        "counts": counts(),
        "sizes": sizes(),
        "hard": len(hard()),
        "kinds": {k: registry()[k] for k in sorted(counts())},
        "entries": _ENTRIES,
    }
    if extra:
        payload.update(extra)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return payload


def merge_file(path: str) -> int:
    """Fold entries written by another process (normalize, latex2nl) in."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise ValueError("invalid issue ledger: " + path)
    added = 0
    reg = registry()
    for e in doc["entries"]:
        if not isinstance(e, dict):
            raise ValueError("invalid issue entry: " + path)
        kind = e.get("kind")
        if kind not in reg:
            raise ValueError("unregistered issue kind %r in %s" % (kind, path))
        entry = {"kind": kind, "site": e.get("site", ""),
                 "route": reg[kind].get("route", ""),
                 "gate": reg[kind].get("gate", "none"),
                 "where": (e.get("where") or "")[:200],
                 "size": int(e.get("size") or 0)}
        for k, v in e.items():
            if k not in entry and k not in ("route", "gate"):
                entry[k] = v
        _ENTRIES.append(entry)
        added += 1
    return added


def summary_line() -> str:
    c = counts()
    if not c:
        return "issues: none"
    parts = ", ".join("%s %d" % (k, v) for k, v in sorted(c.items()))
    return "issues: %s | hard %d" % (parts, len(hard()))
