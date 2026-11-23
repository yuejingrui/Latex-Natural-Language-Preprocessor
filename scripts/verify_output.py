#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Output checks: the errors the code does not know it is making.

``verify_formulas.py`` asks "did a formula lose something"; this asks "does the
output look like a paper".  It reads the artefacts of one run -- sentences.jsonl,
formulas.json, normalized.tex, normalize-stats.json -- and records every hit in the
ledger with the kind from GAPS.md section 5 (C1-C16).

    python3 verify_output.py OUT            # report + write OUT/issues.json
    python3 verify_output.py OUT --strict   # exit 1 if a hard check hits

Strict exit codes: 0 no hard/pending issues, 1 hard failure, 2 invalid artifacts, 3 pending.
Without --strict, valid artifacts return 0 and status is reported in issues.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import issues  # noqa: E402

PLACEHOLDER = re.compile(r"\b(?:MATH|REF|EQ|GRID|DIAGRAM)\d+\b")
CONTROL = re.compile(r"\\[A-Za-z]+")
TERMINAL = ".!?:。！？"
REF_BRACE = re.compile(r"REF\d+\}")


def load(out: str) -> Dict[str, object]:
    doc = {}
    sents_path = os.path.join(out, "sentences.jsonl")
    forms_path = os.path.join(out, "formulas.json")
    if not os.path.exists(sents_path) or not os.path.exists(forms_path):
        raise ValueError("%s has no sentences.jsonl/formulas.json" % out)
    with open(sents_path, encoding="utf-8") as fh:
        doc["sentences"] = [json.loads(l) for l in fh if l.strip()]
    with open(forms_path, encoding="utf-8") as fh:
        data = json.load(fh)
    doc["formulas"] = data["formulas"] if isinstance(data, dict) else data
    stats_path = os.path.join(out, "normalize-stats.json")
    doc["stats"] = {}
    if os.path.exists(stats_path):
        with open(stats_path, encoding="utf-8") as fh:
            doc["stats"] = json.load(fh)
    for field, key in (("sentences", "sid"), ("formulas", "id")):
        if not isinstance(doc[field], list):
            raise ValueError(field + " must be a list")
        for row in doc[field]:
            if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(field + " has an invalid identifier")
    for s in doc['sentences']:
        if (not isinstance(s.get('text'), str) or
            not isinstance(s.get('tokens'), list) or not all(isinstance(t,str) for t in s['tokens']) or
            not isinstance(s.get('formulas'), list) or not all(isinstance(t,str) for t in s['formulas'])):
            raise ValueError('invalid sentence fields: ' + s['sid'])
    doc['content_blocks'] = []
    block_path = os.path.join(out, 'content_blocks.json')
    if os.path.exists(block_path):
        with open(block_path, encoding='utf-8') as fh: doc['content_blocks'] = json.load(fh)
    if not isinstance(doc['content_blocks'], list):
        raise ValueError('content_blocks must be a list')
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
    for sid in missing:
        hits.append(("parse:coverage", "verify_output.check_parses",
                     "%s has no parse" % sid, 1))
    for r in rows:
        if r['sid'] not in {s['sid'] for s in sentences} or sum(x['sid']==r['sid'] for x in rows)>1:
            hits.append(('parse:coverage', 'verify_output.check_parses', 'duplicate or unknown sid '+r['sid'], 1))
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
        if sent.get('unit') != sents[i + 1].get('unit'):
            continue
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
        if f.get("kind") == "ref" or f.get('opaque'):
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
        if f.get("kind") not in ("inline", "display"):
            continue
        raw = f.get("raw") or ""
        depth, broken = 0, False
        for m in re.finditer(r'\\[A-Za-z@]+|\\[^A-Za-z@]|[{}]', raw):
            if m.group() == '{': depth += 1
            elif m.group() == '}':
                depth -= 1
                if depth < 0: broken = True
        if depth != 0 or broken:
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
        if f.get("kind") == "ref" or f.get('opaque'):
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
        if f.get('opaque'):
            continue  # Internal environments are preserved, not interpreted.
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
    from latex2nl import find_group_end
    labels = set()
    for m in re.finditer(r'\\label\s*\{', normalized):
        end = find_group_end(normalized, m.end()-1)
        if end: labels.add(normalized[m.end():end-1])
    hits = []
    for f in doc["formulas"]:
        if f.get('kind') != 'ref' or f.get('ref_kind') in ('cite', 'content'):
            continue
        target = f.get("ref_target")
        if not target or not isinstance(target, str):
            continue
        if target in labels or all(t.strip() in labels for t in target.split(',')) or target in ("", "None"):
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


def check_links(doc, normalized):
    hits = []
    def hit(where): hits.append({'where': where})
    sents, forms, blocks = doc['sentences'], doc['formulas'], doc['content_blocks']
    by_sid = {s['sid']: s for s in sents}
    by_id = {f['id']: f for f in forms}
    for label, ids in [('sentence',[s['sid'] for s in sents]), ('formula',[f['id'] for f in forms]),
                       ('content',[b['id'] for b in blocks])]:
        for key, count in Counter(ids).items():
            if count > 1: hit('duplicate '+label+' '+key)
    used = set()
    # The converter supports placeholders attached to affixes (MATH1th).
    refs = re.compile(r'(?:MATH|REF)\d+')
    for sentence in sents:
        text_ids = refs.findall(sentence['text'])
        used.update(text_ids)
        if set(text_ids) != set(sentence['formulas']):
            hit(sentence['sid']+' text/formulas mismatch')
        for fid in set(text_ids) | set(sentence['formulas']):
            if fid not in by_id: hit(sentence['sid']+' missing '+fid)
    for f in forms:
        if f['id'] not in used: hit('unreachable '+f['id'])
        if f.get('opaque'):
            from latex2nl import strip_trailing_punct
            # Opaque output has a stricter preservation contract instead of the
            # expanded-form conventions (C6/C9/C10) used by known formulas.
            expected, _ = strip_trailing_punct(f.get('raw', ''))
            if (f.get('expanded') != expected or f.get('text_slots') or
                    f.get('side_conditions') or f.get('role') != 'opaque'):
                hit(f['id']+' opaque formula was rewritten')
    block_ids = {b['id'] for b in blocks}
    for f in forms:
        if f.get('ref_kind') == 'content' and f.get('ref_target') not in block_ids:
            hit(f['id']+' missing content block')
    for b in blocks:
        ref = by_id.get(b['anchor_ref'], {})
        if ref.get('ref_target') != b['id'] or ref.get('ref_kind') != 'content':
            hit(b['id']+' invalid anchor reference')
        if b.get('kind') in ('conditional', 'conditional_formula', 'tex_execution'):
            if b.get('status') != 'pending' or b.get('sentence_ids'):
                hit(b['id']+' unresolved conditional emitted as decided text')
            if ref.get('raw') != b.get('raw'):
                hit(b['id']+' conditional reference lost source branches')
        anchors = {s['sid'] for s in sents if b['anchor_ref'] in s['formulas']}
        if not anchors or anchors != set(b.get('anchor_sids', [])):
            hit(b['id']+' invalid anchor sentences')
        for sid in b.get('sentence_ids', []) + b.get('preview_sentence_ids', []):
            if sid not in by_sid or by_sid[sid].get('unit') != b['id']:
                hit(b['id']+' invalid child sentence '+sid)
        if b.get('status') not in ('parsed','pending') or (b.get('status')=='parsed' and not b.get('sentence_ids')):
            hit(b['id']+' invalid content status')
        if not isinstance(b.get('raw'),str) or not b['raw']:
            hit(b['id']+' missing preserved source')
    import preserved
    # Top-level capsules must survive even if both the REF and block are removed.
    source_records = [preserved.unpack(m) for m in preserved.MARKER.finditer(normalized)]
    source_blocks = Counter(b['raw'] for b in source_records)
    output_blocks = Counter(b['raw'] for b in blocks)
    for kind in ('tex_execution', 'conditional_formula'):
        expected = Counter(b['raw'] for b in source_records if b['kind'] == kind)
        actual = Counter(b['raw'] for b in blocks if b['kind'] == kind and b['status'] == 'pending')
        if expected - actual:
            hit(kind + ' lost its pending source')
    for raw, count in source_blocks.items():
        enclosing = sum(b['raw'].count(raw) for b in blocks if b.get('kind') == 'conditional_formula'
                        and raw != b['raw'])
        if output_blocks[raw] + enclosing < count: hit('source capsule missing from output')
    # A source question must remain attached to an unresolved output. Keeping
    # the raw text in an unrelated record cannot justify selecting a branch.
    for raw, count in Counter(b['raw'] for b in source_records if b['kind'] == 'conditional').items():
        retained = 0
        for b in blocks:
            if b.get('status') != 'pending':
                continue
            if b['kind'] == 'conditional' and b['raw'] == raw:
                retained += 1
            elif b['kind'] == 'conditional_formula' and raw in b['raw']:
                components = b.get('components')
                if components is None:  # Also accept the older formula transport.
                    retained += b['raw'].count(raw)
                else:
                    retained += sum(c.get('kind') == 'conditional' and c.get('raw') == raw
                                    and c.get('status') == 'pending' for c in components)
        if retained < count:
            hit('unresolved source conditional lost its pending record')
    return hits


def pending_items(out, doc):
    pending = []
    for block in doc['content_blocks']:
        if block.get('status') == 'pending': pending.append('content '+block['id'])
    for name in sorted(os.listdir(out)):
        if not name.endswith('_todo.json'): continue
        with open(os.path.join(out,name),encoding='utf-8') as fh: data=json.load(fh)
        items = data.get('items') if isinstance(data,dict) else data
        if not isinstance(items,list): raise ValueError('invalid todo: '+name)
        if name == 'slot_todo.json':
            continue  # Downstream role interpretation, not a sentence boundary.
        # Filling an answer is not the same as rebuilding and checking output.
        if items: pending.append(name+' ('+str(len(items))+')')
    state_path = os.path.join(out,'split_status.json')
    if not os.path.exists(state_path) and any('content_kind' in s for s in doc['sentences']):
        raise ValueError('new-format output is missing split_status.json')
    if os.path.exists(state_path):
        with open(state_path,encoding='utf-8') as fh: state=json.load(fh)
        for key,count in state['pending'].items():
            if key == 'slots': continue  # Also understand the old v1 manifest.
            if count: pending.append(key+' ('+str(count)+')')
        for key in ('sentences','formulas','content_blocks'):
            if state['counts'][key] != len(doc[key]):
                issues.record('check:linkage','verify_output.manifest',where=key+' count changed',size=1)
    unresolved_path = os.path.join(out,'unresolved.log')
    if os.path.exists(unresolved_path):
        with open(unresolved_path,encoding='utf-8') as fh:
            if fh.read().strip(): pending.append('unresolved.log')
    names = doc['stats'].get('outside_math_vocabulary')
    if names is None:
        names = doc['stats'].get('outside_vocabulary') or {}
        normalized_path = os.path.join(out, 'normalized.tex')
        if names and os.path.exists(normalized_path):
            # Legacy stats did not distinguish prose from formula interiors.
            # Reclassify against the actual source instead of relabeling a
            # document-wide inventory as a prose defect.
            import latex2nl
            with open(normalized_path, encoding='utf-8') as fh: source = fh.read()
            text_names = set(latex2nl.control_words(latex2nl.strip_math(source)))
            names = {name: count for name, count in names.items() if name in text_names}
        elif names:
            pending.append('legacy_unscoped_vocabulary')
            names = {}
    if names: pending.append('outside_math_vocabulary')
    for entry in issues.entries():
        if (str(entry.get('route','')).startswith('todo:') or
            entry['kind'] in ('drop:float','drop:footnote','math:unclosed','human:source_error','unresolved:line')):
            pending.append('ledger '+entry['kind'])
    return sorted(set(pending))


def downstream_items(out, doc):
    """Report interpretation work without changing the splitting verdict."""
    items = []
    path = os.path.join(out, 'formula_diagnostics.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh: data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ValueError('invalid formula_diagnostics.json')
        if not all(isinstance(item, dict) for item in data['items']):
            raise ValueError('invalid formula diagnostic item')
        items.extend(data['items'])
    for f in doc['formulas']:
        if f.get('unrecognized_commands') and not any(
                item.get('formula') == f['id'] and item.get('kind') == 'unrecognized_commands' for item in items):
            items.append({'formula': f['id'], 'kind': 'unrecognized_commands',
                          'commands': f['unrecognized_commands']})
        for slot in f.get('text_slots', []):
            if slot.get('role') in ('unclassified', None) and not any(
                    item.get('formula') == f['id'] and item.get('kind') == 'unclassified_text_slot'
                    and not item.get('slot') for item in items):
                items.append({'formula': f['id'], 'kind': 'unclassified_text_slot',
                              'slot': slot.get('id'), 'raw': slot.get('raw')})
    return items


def run(out: str, strict: bool) -> int:
    try:
        return _run(out, strict)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        issues.reset()
        issues.record('check:artifact','verify_output.load',where=str(exc),size=1)
        if os.path.isdir(out): issues.write(os.path.join(out,'issues.json'), {'status':'invalid'})
        print('invalid artifact: '+str(exc),file=sys.stderr)
        return 2


def _run(out: str, strict: bool) -> int:
    doc = load(out)
    norm_path = os.path.join(out, "normalized.tex")
    normalized = ""
    if os.path.exists(norm_path):
        with open(norm_path, encoding="utf-8", errors="replace") as fh:
            normalized = fh.read()

    checks = [
        ("C1", "hard", check_c1(doc)),
        ("C2", "hard", check_c2(doc)),
        ("C3", "hard", check_c3(doc)),
        ("C4", "soft", check_c4(doc)),
        ("C5", "soft", check_c5(doc)),
        ("C6", "hard", check_c6(doc)),
        ("C7", "hard", check_c7(doc)),
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
    print("check  gate  hits  example")
    for name, gate, hits in checks:
        if hits:
            example = (hits[0].get("where") or "")[:60]
        else:
            example = ""
        print("%-6s %-5s %5d  %s" % (name, gate, len(hits), example))
        for h in hits:
            issues.record("check:" + name, "verify_output.%s" % name,
                          where=h.get("where", ""), size=1)
    # the tools write their own ledgers; fold them in before writing
    for name in sorted(os.listdir(out)):
        if name.startswith("issues_") and name.endswith(".json"):
            n = issues.merge_file(os.path.join(out, name))
            if n:
                print("merged %d entries from %s" % (n, name))

    for kind, site, where, size in check_parses(out, doc["sentences"]):
        issues.record(kind, site, where, size)

    for h in check_links(doc, normalized):
        issues.record('check:linkage', 'verify_output.links', where=h['where'], size=1)
    for sentence in doc['sentences']:
        if ''.join(sentence['tokens']) != ''.join(sentence['text'].split()):
            issues.record('check:tokens','verify_output.tokens',where=sentence['sid'],size=1)
    pending = pending_items(out, doc)
    downstream = downstream_items(out, doc)
    for item in pending:
        issues.record('check:pending','verify_output.pending',where=item,size=1)
    status = 'hard_failure' if issues.hard() else 'pending' if pending else 'clean'
    payload = issues.write(os.path.join(out, "issues.json"),
                           {"checks": {n: len(h) for n, _g, h in checks},
                            "scope": "splitting", "status": status, "pending": pending,
                            "downstream_diagnostics": downstream})
    print("\nissues written: %s" % os.path.join(out, "issues.json"))
    print(issues.summary_line())
    if strict and payload["hard"]:
        return 1
    if strict and pending:
        return 3
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", nargs="?", help="a run directory")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 for hard issues, 2 for invalid artifacts, 3 for pending work")
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
