#!/usr/bin/env python3
"""roles.py - second opinion on formula roles in definition/assumption sentences.

`latex2nl.py` decides a formula's role from the word just before it plus the
shape of the formula.  That is a cheap prior and it is wrong where the
introducing phrase is far away: in "Choose linear maps A such that, for every
tuple B from C, with D, det(...) != 0" the last formula is a condition, but no
rule that looks one word back can see the "such that".

So the formulas whose role actually matters (they sit in a definition or an
assumption sentence and are currently a proposition or a constraint) are
exported as a short question list.  A person or a small model answers them;
the answers are frozen in roles.json and reused.

    python3 roles.py out/formulas.json out/sentences.jsonl
    python3 roles.py out/formulas.json out/sentences.jsonl --merge-answers a.json

Output:
    out/role_todo.json   unanswered questions only
    out/roles.json       role_now / role_final per formula + summary
"""

import argparse
import collections
import json
import os
import re
import sys
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


import rule_reviews


CANDIDATE_ACTS = ("definition", "assumption")
CANDIDATE_ROLES = ("proposition", "constraint")
ALLOWED = ("constraint", "proposition", "unsure")

INSTRUCTIONS = (
    "For each item, decide what the target formula does in its sentence. The "
    "distinction is the one a reader of the paper makes:\n"
    "- constraint: the formula RESTRICTS the range of something the sentence "
    "has already introduced ('Let n >= 2', 'with f continuous', 'such that "
    "det(A) != 0').\n"
    "- proposition: the formula NAMES or DEFINES an object, or the sentence "
    "asserts something about it. A definition by assignment is a proposition, "
    "not a constraint ('and put W = ...', 'and set d = ...', 'define H_i = "
    "...', 'we have MATH4').\n"
    "- unsure: the sentence does not settle it.\n"
    "If the entire sentence says Let followed by an equality between two "
    "symbols, treat it as an assumption (constraint) unless the text introduces "
    "a new name or explicitly assigns a definition. An equals sign alone is "
    "insufficient evidence of a definition.\n"
    "In short: a formula used to *bound* a variable is a constraint, a formula "
    "used to *know what a symbol stands for* is a proposition. Give "
    "'introducer' as the words that decided it, then 'answer'.\n"
)


def _find(text, pid):
    # placeholders are MATH/REF plus digits, so an id can never be the suffix of
    # another id; the only neighbour to guard against is a digit, and the old
    # \w lookbehind missed ``CYMATH1729`` (REVIEW.md 二.6)
    return re.search(r"(?<![0-9])%s(?!\d)" % re.escape(pid), text)


def mark_sentence(text, pid):
    m = _find(text, pid)
    if not m:
        return text
    return text[:m.start()] + "<<" + pid + ">>" + text[m.end():]


def preceding_words(text, pid, limit=8):
    m = _find(text, pid)
    if not m:
        return []
    head = re.sub(r"(?:MATH|REF)\d+\b", " ", text[:m.start()])
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", head)
    return [w.lower() for w in words[-limit:]]


def shape(formula):
    return (formula or "").replace("\n", " ").strip()


def group_key(sentence, formula, fid):
    words = preceding_words(sentence.get("text") or "", fid)
    # the last two content words are a stable mechanical label, not a decision
    tail = " ".join(words[-2:]) if words else "(start)"
    symbol = (formula.get("top_relation") or {}).get("symbol") or "atomic"
    # the rest of the sentence, with every formula id masked: two formulas from
    # different sentences used to share a group whenever their last two words and
    # their relation matched, and the conflict report then named formulas that
    # were never in the same context
    context = re.sub(r"MATH\d+|REF\d+", "<>", sentence.get("text") or "")
    context = re.sub(r"\s+", " ", context).strip()[:40]
    return "%s | %s | %s" % (tail, symbol, context)


#: a formula that *defines* something: ``Z := \{..\}``, ``x \coloneqq ..``.
#: Its role is the object being named, which is neither a condition nor an
#: assertion, so it must never become a candidate.  Eleven formulas of one
#: block in 0708.2512 ("We put Z := ..., and |x| = ...") were asked this way and
#: both answers were wrong: the mechanical layer said ``proposition``, the model
#: said ``constraint``, and neither is what a definition by assignment is.
DEFINES_RE = re.compile(r":=|\\coloneqq|\\eqqcolon|\\overset\s*\{\s*(?:def|"
                        r"\\mathrm\{def\})\s*\}\s*\{=\}")

#: a sentence that stipulates a definition rather than asserting something:
#: "We put MATH1457.", "Set MATH12.", "We write MATH9.".  A formula that follows
#: the verb directly (nothing but the placeholder between them) is what is being
#: *defined*, so it is not a condition and not an assertion.  The ``=`` cannot be
#: looked for in the sentence -- the formula has already been replaced by its id
#: there -- which is why an earlier attempt at this test caught only one of the
#: eleven formulas of block eq.6.1.1/item5 in 0708.2512.
#: the list after the verb: "MATH1", "MATH1, MATH2", "MATH1, MATH2, and MATH3",
#: "MATH1 and MATH2, with MATH3".  The old pattern demanded a trailing end of
#: sentence right after the ids, so an Oxford comma or a "with" clause made the
#: whole sentence look like an assertion again.
STIPULATES_RE = re.compile(
    r"^\s*(?:we\s+|one\s+)?(?:put|set|write|define)\s+"
    r"(?P<ids>MATH\d+(?:\s*(?:,\s*(?:and\s+|with\s+)?|and\s+|with\s+)"
    r"(?:MATH\d+|[a-z][a-z \-]*?))*)\s*[.,;:]?\s*$", re.I)
#: ``let`` is deliberately absent: "Let MATH10." is an assumption, and the
#: person who answered the four sample papers called it a constraint.


def defined_here(sentence_text: str):
    """The formula ids a stipulating sentence is defining.

    ``We put MATH1457.`` defines one; ``We put MATH1504, MATH1505, ... and
    MATH1513.`` defines all eleven.  Handling only the single-formula form left
    ten of the eleven still being asked as if they were conditions.
    """
    m = STIPULATES_RE.match(sentence_text or "")
    if not m:
        return set()
    # only the ids that follow the verb directly belong to the list; a later
    # clause ("with MATH3") is part of the stipulation too
    return set(re.findall(r"MATH\d+", m.group("ids")))


def collect_candidates(formulas, sentences):
    by_id = {f["id"]: f for f in formulas}
    out = []
    for sent in sentences:
        if sent.get("speech_act") not in CANDIDATE_ACTS:
            continue
        defined = defined_here(sent.get("text"))
        for fid in sent.get("formulas") or []:
            f = by_id.get(fid)
            if f is None or f.get("role") in ("opaque", None):
                # the splitter kept an unreadable formula whole: nothing to
                # ask about, and it must not be counted as a candidate
                continue
            if f.get("role") not in CANDIDATE_ROLES:
                continue
            body = f.get("expanded") or f.get("text") or ""
            if DEFINES_RE.search(body):
                continue        # the formula is what is being defined
            if fid in defined:
                continue        # "We put MATHn": MATHn is the definition
            out.append((sent, f))
    return out


def anchor_window(sentences, index, width=1):
    """The sentence holding the formula, plus its neighbours.

    Every boundary here is a sentence boundary the pipeline already computed;
    nothing in this function judges what a sentence means.  A model shown a
    300-word statement block cannot tell what one formula is doing inside it;
    shown three consecutive sentences it can.  This is the window that gets
    asked about -- cut by code, not by the model.
    """
    lo = max(0, index - width)
    hi = min(len(sentences), index + width + 1)
    return [sentences[k] for k in range(lo, hi)]


def explicit_role(sentence, formula):
    """A directly attached quantifier or condition governing a relation."""
    target = _find(sentence, formula["id"])
    rel = formula.get("top_relation")
    if not target or not rel:
        return None
    prefix = sentence[:target.start()]
    m = re.search(r"\b(for (?:each|every|all|any))\s*$", prefix, re.I)
    if m and rel.get("symbol") in ("∈", r"\in"):
        return {"name": "quantified_membership", "answer": "constraint", "introducer": m[1]}
    m = re.search(r"\b(such that|provided that|subject to)\s*$", prefix, re.I)
    if m:
        return {"name": "explicit_condition", "answer": "constraint", "introducer": m[1]}
    return None


def build_todo(formulas, sentences, paper=""):
    items = []
    # a sid can repeat when two papers' sentence files are concatenated: the map
    # used to keep only the last one, so the window was cut around the wrong
    # sentence and the formula's own sentence could be missing from it
    order = {}
    for k, sent in enumerate(sentences):
        order.setdefault(sent["sid"], []).append(k)
    for sent, f in collect_candidates(formulas, sentences):
        positions = order.get(sent["sid"]) or []
        k = next((p for p in positions
                  if sentences[p] is sent), positions[0] if positions else 0)
        window = []
        for s in anchor_window(sentences, k):
            window.append(mark_sentence(s["text"], f["id"])
                          if s is sent else s["text"])
        items.append({
            "group": group_key(sent, f, f["id"]),
            "block": sent.get("unit"),
            # a formula id is unique inside one paper, not between papers:
            # ``MATH65`` is a different formula in the next paper.  The paper is
            # part of the key or the answer file cannot be shared.
            "paper": paper,
            "fid": f["id"],
            "sid": sent["sid"],
            "unit": sent.get("unit"),
            "speech_act": sent.get("speech_act"),
            "sentence": mark_sentence(sent["text"], f["id"]),
            "window": window,
            "formula": shape(f["expanded"]),
            "role_now": f["role"],
            "rule": explicit_role(sent["text"], f),
            "introducer": None,
            "answer": None,
        })
    return items


def export_todo(items, path):
    blocks = {}
    for it in items:
        blocks.setdefault(it.get("block") or "?", []).append(it["fid"])
    payload = {
        "instructions": INSTRUCTIONS,
        "allowed_answers": list(ALLOWED),
        "answer_meaning": {
            "constraint": "a condition on something the sentence chooses/assumes",
            "proposition": "an assertion the sentence makes",
            "unsure": "cannot tell; keep the mechanical role and flag it",
        },
        "meta": {"items": len(items),
                 "sentences": len({i["sid"] for i in items}),
                 "blocks": len(blocks),
                 "items_per_block": round(len(items) / max(1, len(blocks)), 1)},
        "blocks": blocks,
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return payload


def apply_answers(roles, items, only_unset=False, paper="", current=None):
    """Fill `roles` from answered items; return (applied, skipped, rejected).

    An answer is about one formula of one paper.  When both the answer and the
    caller say which paper that is, they must agree: ``MATH65`` of one paper is
    not ``MATH65`` of another, and applying the answer across papers is how a
    frozen table silently becomes wrong.  An item without a paper is still
    applied -- the first 35 answers were recorded before the field existed.

    ``current`` maps formula id -> the formula text this run produced.  A
    formula id is positional, so a change upstream renumbers everything after
    it; when the text at that id is no longer the text the answer was written
    for, the answer is skipped instead of landing on a different formula.  That
    is what re-running a frozen paper looks like (Norin: 305 formulas today
    against 313 when the answers were written).
    """
    if not paper:
        # a formula id is unique inside one paper only: without the paper there
        # is nothing to check the answer against, and applying it would put one
        # paper's reading on another's formula
        raise ValueError("apply_answers needs the paper id")
    applied, skipped, rejected, stale = 0, 0, [], []
    conflicts = []
    seen = set()
    for item in items:
        fid, answer = item.get("fid"), item.get("answer")
        if not fid or not answer:
            skipped += 1
            continue
        if answer not in ALLOWED:
            rejected.append((fid, answer))
            continue
        if not item.get("paper"):
            # an answer without a paper is from a run-local file; it cannot be
            # matched to this paper, so it is skipped instead of applied by id
            skipped += 1
            stale.append((fid, "<no paper>", paper))
            continue
        if item["paper"] != paper:
            skipped += 1
            continue
        if current is not None:
            if fid not in current:
                # the id is gone: the upstream change removed or renumbered it
                stale.append((fid, (item.get("formula") or "")[:60],
                              "<no such id>"))
                continue
            if not (item.get("formula") or "").strip():
                # no text to compare with: this answer cannot be verified
                stale.append((fid, "<no formula text>", "<cannot check>"))
                continue
            stored = re.sub(r"\s+", " ", (item.get("formula") or "")).strip()
            now = re.sub(r"\s+", " ", (current.get(fid) or "")).strip()
            if stored and now and stored != now:
                stale.append((fid, stored[:60], now[:60]))
                continue
        if only_unset and fid in roles and (roles[fid].get("final")
                                            or roles[fid].get("unsure")):
            skipped += 1
            continue
        if fid in seen:
            # only an answer that survived every check counts as applied, so a
            # stale first copy cannot make the correct second one a duplicate
            conflicts.append((fid, answer))
            skipped += 1
            continue
        seen.add(fid)
        roles[fid] = {
            "role_now": roles.get(fid, {}).get("role_now") or item.get("role_now"),
            "final": None if answer == "unsure" else answer,
            "source": item.get("source") or item.get("by") or "manual",
            "introducer": item.get("introducer"),
        }
        if current is not None:
            # the text this answer was checked against, so that reusing an
            # earlier roles.json can verify it instead of trusting the id
            roles[fid]["formula"] = (current.get(fid) or "")
        if item.get("note"):
            roles[fid]["note"] = item["note"]
        if answer == "unsure":
            roles[fid]["unsure"] = True
        applied += 1
    return applied, skipped, rejected, stale


def _load_formulas(path):
    """The formula list of a formulas.json, whichever shape it was written in."""
    with open(path, encoding="utf-8") as stream:
        doc = json.load(stream)
    if isinstance(doc, dict):
        doc = doc.get("formulas") or doc.get("items") or []
    return doc


def _load_items(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("%s: expected a list under 'items'" % path)
    return items


def summarise(items, roles, formulas):
    by_id = {f["id"]: f for f in formulas}
    changed, unsure = [], []
    for fid, rec in sorted(roles.items()):
        now = by_id.get(fid, {}).get("role")
        if rec.get("unsure"):
            unsure.append((fid, now))
        elif rec.get("final") and rec["final"] != now:
            changed.append((fid, now, rec["final"], rec["source"]))
    groups = collections.defaultdict(set)
    for item in items:
        rec = roles.get(item["fid"]) or {}
        if rec.get("final"):
            groups[item["group"]].add(rec["final"])
    inconsistent = {g: sorted(v) for g, v in groups.items() if len(v) > 1}
    return {
        "candidates": len(items),
        "sentences": len({i["sid"] for i in items}),
        "answered": sum(1 for r in roles.values() if r.get("final")),
        "changed": len(changed),
        "unsure": len(unsure),
        "groups_with_conflicting_answers": inconsistent,
    }, changed, unsure


def _self_test():
    failures = []
    PAPER = "0708.2512"

    def check(label, ok, detail=""):
        print("  %s %s" % ("ok  " if ok else "FAIL", label))
        if not ok:
            failures.append("%s %s" % (label, detail))

    formulas = [
        {"id": "MATH1", "role": "sequence", "expanded": "\\ell_1,\\ldots,\\ell_d",
         "top_relation": None, "kind": "inline", "flags": []},
        {"id": "MATH2", "role": "proposition", "expanded": "\\det(\\ell_a(u_b))\\ne0",
         "top_relation": {"symbol": "\u2260"}, "kind": "inline", "flags": []},
        {"id": "MATH3", "role": "constraint", "expanded": "1\\le h\\le d",
         "top_relation": {"symbol": "\u2264"}, "kind": "inline", "flags": []},
        {"id": "MATH4", "role": "proposition", "expanded": "x>0",
         "top_relation": {"symbol": ">"}, "kind": "inline", "flags": []},
    ]
    sentences = [
        {"sid": "s1", "speech_act": "definition", "unit": "u1",
         "text": "Choose linear maps MATH1 such that, for every h, with MATH3, "
                 "MATH2.", "formulas": ["MATH1", "MATH2", "MATH3"]},
        {"sid": "s2", "speech_act": "assertion", "unit": "u2",
         "text": "Note that MATH4 holds.", "formulas": ["MATH4"]},
    ]

    print("[1] candidate selection")
    items = build_todo(formulas, sentences, paper=PAPER)
    check("only definition/assumption sentences", len(items) == 2,
          str([i["fid"] for i in items]))
    check("sequence role skipped", "MATH1" not in [i["fid"] for i in items])
    check("assertion sentence skipped", "MATH4" not in [i["fid"] for i in items])

    print("[2] question shape")
    item = items[0]
    check("target marked", "<<MATH2>>" in item["sentence"], item["sentence"])
    check("other placeholders untouched", "MATH3" in item["sentence"]
          and "<<MATH3>>" not in item["sentence"])
    check("formula carried", item["formula"].startswith("\\det"), item["formula"])
    check("role_now carried", item["role_now"] == "proposition")
    check("group is mechanical", "with" in item["group"] or "that" in item["group"],
          item["group"])

    print("[3] merge")
    import tempfile
    roles = {}
    items[0].update(answer="constraint", introducer="such that")
    items[1].update(answer="unsure")
    applied, skipped, rejected, stale = apply_answers(roles, items,
                                                     paper=PAPER)
    check("both applied", applied == 2 and not rejected,
          "%d %s" % (applied, rejected))
    check("changed recorded", roles["MATH2"]["final"] == "constraint")
    check("unsure keeps no final", roles["MATH3"]["final"] is None
          and roles["MATH3"]["unsure"] is True)
    summary, changed, unsure = summarise(items, roles, formulas)
    check("summary counts", summary["changed"] == 1 and summary["unsure"] == 1,
          str(summary))
    check("changed lists the formula", changed[0][0] == "MATH2", str(changed))

    print("[4] validation and precedence")
    roles = {}
    bad = [{"fid": "MATH2", "answer": "condition"}]
    applied, skipped, rejected, stale = apply_answers(roles, bad, paper=PAPER)
    check("bad answer rejected", rejected and not roles, str(rejected))
    roles = {"MATH2": {"role_now": "proposition", "final": "constraint",
                       "source": "manual"}}
    applied, skipped, rejected, stale = apply_answers(
        roles, [{"fid": "MATH2", "answer": "proposition"}], paper=PAPER,
        only_unset=True)
    check("only_unset keeps the earlier answer",
          applied == 0 and roles["MATH2"]["final"] == "constraint")

    print("[5] grouping conflicts surface")
    f2 = [{"id": "MATH5", "role": "proposition", "expanded": "a=b",
           "top_relation": {"symbol": "="}, "kind": "inline", "flags": []},
          {"id": "MATH6", "role": "proposition", "expanded": "c=d",
           "top_relation": {"symbol": "="}, "kind": "inline", "flags": []}]
    s2 = [{"sid": "s3", "speech_act": "definition", "unit": "u3",
           "text": "Choose MATH7 such that MATH5, MATH6.", "formulas":
           ["MATH5", "MATH6"]}]
    items2 = build_todo(f2, s2, paper=PAPER)
    check("same group for identical constructions",
          items2[0]["group"] == items2[1]["group"],
          str([i["group"] for i in items2]))
    items2[0].update(answer="constraint")
    items2[1].update(answer="proposition")
    roles = {}
    apply_answers(roles, items2, paper=PAPER)
    summary, _, _ = summarise(items2, roles, f2)
    check("conflict visible when one group gets two answers",
          summary["groups_with_conflicting_answers"], str(summary))

    print("[6] an answer belongs to one paper")
    items3 = build_todo(f2, s2, paper="0708.2512")
    check("the paper is part of the item",
          all(i.get("paper") == "0708.2512" for i in items3),
          str([i.get("paper") for i in items3]))
    answers = [dict(fid="MATH5", answer="constraint", paper="0708.2512"),
               dict(fid="MATH6", answer="constraint", paper="1512.07573")]
    roles = {}
    applied, skipped, rejected, stale = apply_answers(
        roles, answers, paper="0708.2512")
    check("only this paper's answer is applied",
          applied == 1 and "MATH5" in roles and "MATH6" not in roles,
          "%d %s %s" % (applied, sorted(roles), rejected))
    try:
        apply_answers({}, answers)
        check("no paper id at all is an error", False, "no exception")
    except ValueError:
        check("no paper id at all is an error", True)
    roles = {}
    applied, skipped, _, stale = apply_answers(
        roles, [dict(fid="MATH5", answer="constraint")], paper="0708.2512")
    check("an answer with no paper is skipped and reported",
          applied == 0 and skipped == 1 and stale, "%d %s %s"
          % (applied, skipped, stale))

    print("[6b] an answer without the formula text or with a duplicate")
    roles = {}
    applied, _, _, stale = apply_answers(
        roles, [dict(fid="MATH5", answer="constraint", paper=PAPER)],
        paper=PAPER, current={"MATH5": "a=b"})
    check("no formula text: skipped, not applied",
          applied == 0 and stale and "<no formula text>" in str(stale),
          "%d %s" % (applied, stale))
    roles = {}
    applied, _, _, _ = apply_answers(
        roles, [dict(fid="MATH5", answer="constraint", paper=PAPER,
                     formula="a=b"),
                dict(fid="MATH5", answer="proposition", paper=PAPER,
                     formula="a=b")], paper=PAPER,
        current={"MATH5": "a=b"})
    check("a duplicate answer for one fid applies once, first wins",
          applied == 1 and roles["MATH5"]["final"] == "constraint",
          "%d %s" % (applied, roles.get("MATH5")))

    print("[8] an answer that no longer names its formula is skipped")
    # Norin: 313 formulas when the answers were written, 305 after later fixes,
    # and the ids after the first change no longer mean the same formula
    roles = {}
    applied, _, _, stale = apply_answers(
        roles, [dict(fid="MATH5", answer="constraint", formula="a=b",
                     paper=PAPER)],
        paper=PAPER, current={"MATH5": "a+c=d"})
    check("a shifted formula is not answered",
          applied == 0 and len(stale) == 1 and not roles,
          "%d %s %s" % (applied, stale, sorted(roles)))
    roles = {}
    applied, _, _, stale = apply_answers(
        roles, [dict(fid="MATH5", answer="constraint", formula="a=b",
                     paper=PAPER)],
        paper=PAPER, current={"MATH5": "a=b"})
    check("the same formula is still answered",
          applied == 1 and not stale, "%d %s" % (applied, stale))

    print("[7] an answer that no longer names its formula is reported")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        paper_dir = os.path.join(td, "0708.2512")
        os.makedirs(paper_dir)
        with open(os.path.join(paper_dir, "normalize-stats.json"), "w") as stream:
            json.dump({"paper_id": PAPER}, stream)
        json.dump([{"id": "MATH5", "expanded": "a=b", "role": "proposition"},
                   {"id": "MATH6", "expanded": "c=d", "role": "proposition"}],
                  open(os.path.join(paper_dir, "formulas.json"), "w"))
        good = os.path.join(td, "good.json")
        json.dump({"items": [dict(fid="MATH5", paper="0708.2512",
                                  formula="a=b", answer="constraint")]},
                  open(good, "w"))
        stale = os.path.join(td, "stale.json")
        json.dump({"items": [dict(fid="MATH5", paper="0708.2512",
                                  formula="x>0", answer="constraint")]},
                  open(stale, "w"))
        rc_ok = check_answers(good, [os.path.join(paper_dir, "formulas.json")])
        rc_bad = check_answers(stale, [os.path.join(paper_dir, "formulas.json")])
    check("matching answers pass", rc_ok == 0, str(rc_ok))
    check("a shifted answer fails", rc_bad == 1, str(rc_bad))

    print()
    if failures:
        print("%d check(s) failed" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


def _default_judgements():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "data", "judgements_roles.json")


PLACEHOLDER = re.compile(r"(MATH|REF)(\d+)")


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def skeleton(sentence: str) -> str:
    """The sentence with every formula id replaced by a placeholder.

    A formula id is positional, so "Let <<MATH10>>." today may be
    "Let <<MATH12>>." -- the words are what identifies the sentence.
    """
    # the marker <<...>> is only in the stored sentence: drop the bookkeeping
    # before comparing, or every sentence looks different from its own record
    text = (sentence or "").replace("<<", "").replace(">>", "")
    return PLACEHOLDER.sub(r"\1#", text)


def _target_at_marker(old_sentence: str, new_text: str):
    """The placeholder in ``new_text`` that stands where ``<<...>>`` stood."""
    m = re.search(r"<<(MATH\d+|REF\d+)>>", old_sentence or "")
    if not m:
        return None
    before = len(PLACEHOLDER.findall((old_sentence or "")[:m.start()]))
    hits = list(PLACEHOLDER.finditer(new_text or ""))
    if before >= len(hits):
        return None
    return hits[before].group(0)


def _run_index(run_dir: str) -> dict:
    """paper -> sentences by skeleton, formulas by id."""
    index: dict = {}
    for d in sorted(os.listdir(run_dir)):
        path = os.path.join(run_dir, d)
        fp = os.path.join(path, "formulas.json")
        sp = os.path.join(path, "sentences.jsonl")
        if not (os.path.isfile(fp) and os.path.isfile(sp)):
            continue
        recs = _load_formulas(fp)
        by_skel: dict = {}
        for line in open(sp, encoding="utf-8"):
            if not line.strip():
                continue
            sent = json.loads(line)
            by_skel.setdefault(skeleton(sent["text"]), []).append(sent)
        index[d] = {"formulas": {f["id"]: f for f in recs},
                    "sentences": by_skel}
    return index


def reanchor(judgements_path: str, run_dir: str, report_path: str = "",
             dry_run: bool = False, allow_missing: bool = False) -> int:
    """Move each answer to the formula it was written for, or take it out.

    The rule was fixed in advance: the sentence words and the formula text must
    both match, and exactly one place must match.  Anything else is dropped --
    and written to the report with its full content, so a label is redone
    instead of silently lost.

    A paper the run has no table for is *not* an answer that went stale: there
    is nothing to compare it with.  Those items are kept and counted as
    unverified, and the run refuses to write unless --allow-missing-papers says
    the missing papers are intended.  The same holds for an item that carries no
    sentence text: the published table has none, and an answer without an anchor
    cannot be re-anchored, only kept or thrown away.  Keying the old behaviour on "the id moved"
    deleted every answer of a paper outside the run directory.
    """
    doc = json.load(open(judgements_path, encoding="utf-8"))
    items = doc.get("items") or []
    index = _run_index(run_dir)

    kept, dropped, moved, unverified, marker_fixed = [], [], [], [], 0
    for it in items:
        if it.get("by") == "person":
            kept.append(it)                      # the human answers are not ours
            continue
        paper, fid = it.get("paper"), it.get("fid")
        data = index.get(paper or "")
        if data is None:
            kept.append(it)
            unverified.append((it, "no formulas.json/sentences.jsonl for this "
                                   "paper in %s" % run_dir))
            continue
        if not (it.get("sentence") or "").strip():
            # The published table has no sentence text (it would quote the
            # papers), so an answer written before the field was removed cannot
            # be located by re-anchoring.  Keep it and say it is unverified --
            # dropping it would silently delete an answer that is still usable
            # by fid for the paper it names.
            kept.append(it)
            unverified.append((it, "no sentence text to anchor on"))
            continue
        cands = data["sentences"].get(skeleton(it.get("sentence") or ""), [])
        exact = []
        for sent in cands:
            target = _target_at_marker(it.get("sentence") or "", sent["text"])
            if not target:
                continue
            rec = data["formulas"].get(target)
            if rec is None:
                continue
            if _ws(rec.get("expanded")) == _ws(it.get("formula")):
                exact.append((sent, target))
        if len(exact) == 1:
            sent, target = exact[0]
            if target == fid:
                marked = re.search(r"<<([^>]+)>>", it.get("sentence") or "")
                if marked and marked.group(1) != target:
                    # the id is right but the marker in the sentence is not: the
                    # sentence was rewritten upstream and never re-marked
                    new = dict(it)
                    new["sentence"] = re.sub(
                        r"<<[^>]+>>", "<<" + target + ">>",
                        it.get("sentence") or "", count=1)
                    kept.append(new)
                    marker_fixed += 1
                else:
                    kept.append(it)
                continue
            new = dict(it)
            new["fid"] = target
            new["sid"] = sent.get("sid")
            new["unit"] = sent.get("unit")
            if "block" in it:
                new["block"] = sent.get("unit")
            new["sentence"] = re.sub(
                r"<<[^>]+>>", "<<" + target + ">>", it.get("sentence") or "",
                count=1)
            new["reanchored_from"] = it.get("reanchored_from") or fid
            kept.append(new)
            moved.append((it.get("paper"), fid, target, it.get("sentence")))
        elif len(exact) > 1:
            dropped.append((it, "%d places match" % len(exact)))
        elif cands:
            dropped.append((it, "the sentence matches but the formula text does "
                                 "not"))
        else:
            dropped.append((it, "no sentence with the same words"))

    by_reason = collections.Counter(why for _it, why in dropped)
    print("answers: %d | re-anchored: %d | markers fixed: %d | dropped: %d | "
          "unverified: %d" % (len(items), len(moved), marker_fixed,
                              len(dropped), len(unverified)))
    for paper, old, new, sent in moved[:5]:
        print("   %-12s %-8s -> %-8s  %s" % (paper, old, new, (sent or "")[:60]))
    for why, n in by_reason.most_common():
        print("   dropped %3d  %s" % (n, why))
    if unverified:
        papers = sorted({(it.get("paper") or "?") for it, _w in unverified})
        print("   unverified %3d  (papers not in %s: %s)"
              % (len(unverified), run_dir, ", ".join(papers[:6])))
    if dry_run:
        print("   dry run: nothing written")

    doc["items"] = kept
    meta = doc.setdefault("meta", {})
    meta["items"] = len(kept)
    meta["papers"] = dict(sorted(collections.Counter(
        it.get("paper") or "?" for it in kept).items()))
    meta["by"] = dict(sorted(collections.Counter(
        it.get("by") or "?" for it in kept).items()))
    meta["answers"] = dict(sorted(collections.Counter(
        it.get("answer") for it in kept if it.get("answer")).items()))
    meta["reanchored"] = ("moved to the formula they were written for; the "
                          "sentence words and the formula text both matched, "
                          "and exactly one place matched")
    meta["unverified_papers"] = sorted({(it.get("paper") or "?")
                                        for it, _w in unverified})

    report = {
        "run": run_dir,
        "moved": [{"paper": paper, "from": old, "to": new, "sentence": sent}
                  for paper, old, new, sent in moved],
        "dropped": [{"item": it, "why": why} for it, why in dropped],
        "unverified": [{"item": it, "why": why} for it, why in unverified],
        "markers_fixed": marker_fixed,
        "dry_run": dry_run,
    }
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print("report: %s" % report_path)
    if dry_run:
        return 0
    if unverified and not allow_missing:
        print("refusing to write: %d answers belong to papers this run does not "
              "contain; pass --allow-missing-papers if that is intended"
              % len(unverified))
        return 2
    tmp = judgements_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, judgements_path)
    return 0


from paper_metadata import run_paper_id


def check_answers(judgements_path, formulas_paths, allow_missing=False):
    """Every stored answer must still name the formula it was written for.

    The key is ``paper + fid`` and a formula id is positional: one formula added
    upstream renumbers everything after it, and the frozen answer would then be
    applied to a different formula without any sign of it.  The stored
    ``formula`` is what makes that visible, so it is compared with the current
    table.

        python3 roles.py --check-answers out/*/formulas.json
    """
    import collections
    items = _load_items(judgements_path)
    tables = {}
    for path in formulas_paths:
        paper = run_paper_id(os.path.dirname(path))
        tables[paper] = {f["id"]: f for f in _load_formulas(path)}

    def norm(text):
        return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()

    ok, bad, absent = 0, [], collections.Counter()
    for it in items:
        paper, fid = it.get("paper"), it.get("fid")
        # the <<...>> marker in the stored sentence is the readable record of
        # which formula the answer is about; when it names another id the
        # sentence was rewritten and the marker never followed
        marked = re.search(r"<<([^>]+)>>", it.get("sentence") or "")
        if marked and marked.group(1) != fid:
            bad.append((paper, fid, "sentence marks %s" % marked.group(1),
                        (it.get("sentence") or "")[:60]))
            continue
        table = tables.get(paper)
        if table is None:
            absent[paper] += 1
            continue
        f = table.get(fid)
        if f is None:
            bad.append((paper, fid, "no formula with this id any more",
                        it.get("formula")))
        elif norm(f.get("expanded")) != norm(it.get("formula")):
            bad.append((paper, fid, norm(f.get("expanded"))[:60],
                        norm(it.get("formula"))[:60]))
        else:
            ok += 1
    print("answers %d | formula still identical %d | mismatched %d | papers "
          "without a table %d" % (len(items), ok, len(bad), sum(absent.values())))
    for paper, fid, now, was in bad[:20]:
        print("  %-14s %-8s now=%r was=%r" % (paper, fid, now, was))
    if absent:
        print("  no formulas.json given for: %s"
              % ", ".join(sorted(k or "?" for k in absent)))
        print("  %d answers could not be compared; they are not a pass"
              % sum(absent.values()))
    if bad:
        return 1
    if absent and not allow_missing:
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("formulas", nargs="?", help="out/formulas.json")
    ap.add_argument("sentences", nargs="?", help="out/sentences.jsonl")
    ap.add_argument("-o", "--output", default=None,
                    help="default: roles.json next to formulas.json")
    ap.add_argument("--todo", default=None,
                    help="default: role_todo.json next to formulas.json")
    ap.add_argument("--judgements", default=None,
                    help="default: data/judgements_roles.json")
    ap.add_argument("--no-judgements", action="store_true",
                    help="ignore the committed answers; formula ids are "
                         "paper-specific, so they must not leak across papers")
    ap.add_argument("--merge-answers", metavar="PATH")
    ap.add_argument("--check-answers", nargs="+", metavar="FORMULAS",
                    help="verify that every answer still names the same formula; "
                         "the values are the formulas.json files to check it "
                         "against, e.g. --check-answers out/*/formulas.json")
    ap.add_argument("--paper-id", default=None,
                    help="which paper this formula table belongs to; defaults "
                         "to the paper identity recorded by normalization. "
                         "An answer recorded for another paper is skipped, "
                         "because MATH65 means a different formula there")
    ap.add_argument("--reanchor", action="store_true",
                    help="move each answer to the formula it was written for, "
                         "or take it out and write a report")
    ap.add_argument("--run", metavar="DIR",
                    help="the run whose formulas.json/sentences.jsonl are the "
                         "current output (required with --reanchor)")
    ap.add_argument("--report", metavar="PATH",
                    help="where --reanchor writes the dropped answers")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-missing-papers", action="store_true",
                    help="with --reanchor: write even though some answers "
                         "belong to papers this run does not contain")
    ap.add_argument("--allow-missing", action="store_true",
                    help="with --check-answers: exit 0 even though some answers "
                         "had no formula table to be compared with")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.reanchor:
        if not args.run:
            ap.error("--reanchor needs --run DIR")
        return reanchor(args.judgements or _default_judgements(), args.run,
                        args.report or "", args.dry_run,
                        args.allow_missing_papers)
    if args.check_answers:
        judgements = args.judgements or _default_judgements()
        return check_answers(judgements, args.check_answers,
                             args.allow_missing)
    if not args.formulas or not args.sentences:
        ap.error("formulas.json and sentences.jsonl are required")

    base = os.path.dirname(args.formulas) or "."
    out = args.output or os.path.join(base, "roles.json")
    todo_path = args.todo or os.path.join(base, "role_todo.json")
    paper = args.paper_id if args.paper_id is not None else run_paper_id(base)
    if not paper:
        ap.error("the paper id is empty: an answer is only valid inside one "
                 "paper, so roles.py has to know which one it is reading")

    formulas = _load_formulas(args.formulas)
    with open(args.sentences, encoding="utf-8") as stream:
        sentences = [json.loads(line) for line in stream if line.strip()]
    items = build_todo(formulas, sentences, paper=paper)

    # the formula at every id, not only at the ids that are candidates: an id
    # that moved onto a non-candidate formula used to skip the comparison
    current = {f["id"]: (f.get("expanded") or "") for f in formulas}

    anchors = {item["fid"]: {k: item[k] for k in ("sid", "unit", "sentence", "window", "formula")}
               for item in items}
    roles = {}
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            previous = json.load(fh)
        if previous.get("paper") == paper:
            for fid, rec in previous.get("roles", {}).items():
                if fid in anchors and rec.get("anchor") == anchors[fid]:
                    roles[fid] = rec

    # formula ids are paper-specific (MATH65 is a different formula in another
    # paper), so answers must not leak across papers
    if args.no_judgements:
        judgements = ""
    else:
        judgements = args.judgements or _default_judgements()
    if judgements and os.path.exists(judgements):
        # the formula at each id in this run: an answer written for a different
        # formula at the same id is stale, and is reported, not applied
        applied, _skipped, rejected, stale = apply_answers(
            roles, _load_items(judgements), only_unset=True, paper=paper,
            current=current)
        print("from judgements_roles.json: %d applied, %d skipped, %d stale "
              "(paper %s)" % (applied, _skipped, len(stale), paper))
        if stale:
            print("stale answers skipped : %d (the formula at that id changed; "
                  "re-anchor or drop them)" % len(stale))
            for fid, was, is_now in stale[:3]:
                print("    %-8s was %r now %r" % (fid, was, is_now))
        for fid, answer in rejected:
            print("rejected %r in judgements: %r is not one of %s"
                  % (fid, answer, ALLOWED))

    # The queue doubles as the editable answer sheet. Read it before rebuilding
    # it, including on an offline rerun. An old positional id alone is insufficient.
    saved = {}
    question_path = os.path.join(base, "role_questions.json")
    # A stopped run may have written model answers but not yet merged them.
    for sheet in dict.fromkeys((question_path, todo_path)):
        if not os.path.isfile(sheet):
            continue
        matched = []
        for row in _load_items(sheet):
            fid = row.get("fid")
            if (row.get("paper") == paper and fid in anchors and
                    all(row.get(k) == v for k, v in anchors[fid].items())):
                matched.append(row)
                if row.get("asked"):
                    saved[fid] = max(saved.get(fid, 0), row["asked"])
        apply_answers(roles, matched, paper=paper, current=current)
    if args.merge_answers and os.path.abspath(args.merge_answers) not in (
            os.path.abspath(todo_path), os.path.abspath(question_path)):
        entries = [row for row in _load_items(args.merge_answers)
                   if row.get("fid") in anchors and
                   all(k not in row or row[k] == v for k, v in anchors[row["fid"]].items())]
        apply_answers(roles, entries, paper=paper, current=current)
        for row in entries:
            if row.get("paper") == paper and row.get("asked"):
                saved[row["fid"]] = max(saved.get(row["fid"], 0), row["asked"])

    for item in items:
        rule = item["rule"]
        fid = item["fid"]
        if not rule:
            # An earlier version's rule (or a rule since removed) is not an answer.
            if roles.get(fid, {}).get("source") == "rule":
                roles.pop(fid)
            continue
        old = roles.get(fid, {})
        rec = {"role_now": item["role_now"], "final": rule["answer"], "source": "rule",
               "rule": rule["name"], "introducer": rule["introducer"], "formula": current[fid]}
        review = old.get("model_review")
        if rule_reviews.from_model(old.get("source")):
            rec["model_review"] = rule_reviews.compare(rule["name"], rule["answer"],
                old.get("final") or "unsure", old["source"])
        elif review:
            rec["model_review"] = rule_reviews.compare(rule["name"], rule["answer"],
                review["model_answer"], review["model"])
        roles[fid] = rec
    roles = {fid: dict(rec, anchor=anchors[fid]) for fid, rec in roles.items() if fid in anchors}

    summary, changed, unsure = summarise(items, roles, formulas)
    payload = {"paper": paper, "summary": summary, "allowed_answers": list(ALLOWED),
               "roles": roles}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    pending = []
    questions = []
    for item in items:
        fid = item["fid"]
        role = roles.get(fid, {})
        if role.get("final"):
            if item["rule"] and not role.get("model_review"):
                questions.append(item)
            continue
        if role.get("unsure"):
            item.update(answer="unsure", introducer=role.get("introducer"), source=role["source"])
        if saved.get(fid):
            item["asked"] = saved[fid]
        pending.append(item)
        questions.append(item)
    if questions:
        export_todo(questions, question_path)
    elif os.path.exists(question_path):
        os.remove(question_path)
    if pending:
        export_todo(pending, todo_path)
    elif os.path.exists(todo_path):
        os.remove(todo_path)

    print("candidates            : %d formulas in %d sentences"
          % (summary["candidates"], summary["sentences"]))
    print("answered              : %d" % summary["answered"])
    print("pending               : %d%s" % (len(pending),
          "" if not pending else " -> %s" % todo_path))
    print("role changed          : %d" % summary["changed"])
    if summary["groups_with_conflicting_answers"]:
        print("conflicting groups    : %s"
              % summary["groups_with_conflicting_answers"])
    for fid, now, final, source in changed:
        sent = next((i["sid"] for i in items if i["fid"] == fid), "?")
        print("    %-9s %-12s -> %-12s %s  (%s)"
              % (fid, now, final, source, sent))
    print("written               : %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
