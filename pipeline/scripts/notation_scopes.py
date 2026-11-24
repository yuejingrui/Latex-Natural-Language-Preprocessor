"""Resolve explicit notation declarations in source order and record effective ranges."""
import re
from collections import defaultdict

SYMBOL = r"(?:[A-Za-z]|\\[A-Za-z]+)(?:_(?:\{[^{}]+\}|[A-Za-z0-9]))?"
KINDS = {"function": "function", "map": "function", "homomorphism": "function",
         "scalar": "scalar", "integer": "scalar", "number": "scalar",
         "variable": "variable", "vector": "variable", "matrix": "variable",
         "set": "notation", "group": "notation", "space": "notation", "symbol": "notation"}
TYPED = re.compile(r"\s+(?:to be|denote[s]?|be|is|are|as|stands? for)\s+"
                   r"(?:(?:an?|the|real|complex|positive|negative|linear|continuous|fixed|new)\s+)*"
                   r"(" + "|".join(KINDS) + r")s?\b", re.I)
LOCAL_ENVS = {"example", "theorem", "lemma", "proposition", "corollary", "proof", "claim",
              "counterexample", "exercise", "footnote", "remark"}


def symbol_key(symbol):
    return re.sub(r"_\{([A-Za-z0-9])\}", r"_\1", symbol.strip())


def context_path(sent):
    path = sent.get("section_scope")
    if path is None:  # Older sentence files carry the heading path as text.
        path = [s for s in sent.get("section", "").split(" > ") if s]
    path = tuple(path) + tuple(sent.get("enclosing_units", ()))
    if sent.get("env") == "proof" and (sent.get("title") or "").startswith("proof of "):
        path += (sent["title"][9:],)
    if (sent.get("env") or "").rstrip("*") in LOCAL_ENVS:
        return tuple(path) + (re.sub(r"/item\d+$", "", sent.get("unit") or ""),)
    return tuple(path)


def prose_declarations(fid, body, sent):
    text = sent.get("text", "")
    marker = re.search(r"(?<!\w)" + re.escape(fid) + r"(?!\d)", text)
    if not marker:
        return
    before, after = text[:marker.start()], text[marker.end():]
    # In "let f and g be scalars" both positions have the same type.
    after = re.sub(r"^(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+)MATH\d+)+", "", after)
    typed = TYPED.match(after)
    intro = re.search(r"\b(?:let|take|set|put|define|choose|write|where)\s+"
                      r"(?:MATH\d+(?:,\s*(?:and\s+)?|\s+and\s+))*$", before, re.I)
    if typed:
        for part in body.split(","):
            symbol = part.strip()
            if re.fullmatch(SYMBOL, symbol):
                yield symbol, KINDS[typed[1].lower()]
        return
    function = re.fullmatch(r"(" + SYMBOL + r")\s*\([^()]+\)\s*(:=|\\coloneqq?|=).+", body)
    if function and (function[2] != "=" or re.search(r"\bdefine\s+$", before, re.I)):
        yield function[1], "function"
        return
    assigned = re.compile(r"(" + SYMBOL + r")\s*(:=|\\coloneqq?|=)\s*(.+)")
    clauses = [assigned.fullmatch(clause.strip()) for clause in body.split(",")]
    if all(clauses) and (intro or all(eq[2] != "=" for eq in clauses)):
        for eq in clauses:
            number = re.fullmatch(r"[+-]?\s*(?:\d+(?:\.\d+)?|\d+\s*/\s*\d+)", eq[3].strip())
            yield eq[1], "scalar" if number else "variable"
        return
    if re.match(r"\s+(?:denotes|represents|stands for|is defined as)\b", after, re.I):
        yield body, "notation"
    elif re.search(r"\bdenote\s+by\s+$", before, re.I):
        yield body, "notation"
    elif (re.search(r"\b(?:write|use)\s+$", before, re.I) or
          re.search(r"\b(?:write|use)\s+MATH\d+\s+for\b[^.;!?]*\band\s*$", before, re.I)
          ) and re.match(r"\s+for\b", after, re.I):
        yield body, "notation"


class NotationScopes:
    def __init__(self, formulas, sentences, arrows, product_defs):
        contexts = {fid: s for s in sentences for fid in s.get("formulas", [])}
        self.definitions = []
        self.at = {}
        active = defaultdict(dict)
        previous = {}
        for formula in formulas:
            if formula.get("kind") == "ref":
                continue
            fid = formula["id"]
            sent = contexts.get(fid, {})
            local = context_path(sent)
            path = () if re.search(r"\b(?:throughout (?:this|the) paper\b|throughout\s*,|in this paper\b)",
                                   sent.get("text", ""), re.I) else local
            body = (formula.get("expanded") or "").strip()
            events = {}
            hypothetical = re.search(r"\b(?:not|never|whether|unless|if|might|may|could|"
                                     r"suppose for contradiction|assume for contradiction)\b|\?",
                                     sent.get("text",""), re.I)
            if formula.get("role") != "opaque" and not hypothetical:
                events.update(prose_declarations(fid, body, sent))
                events.update((name, "function") for name in arrows.get(fid, []))
                if fid in product_defs:
                    events[body] = "product"
            for symbol, kind in events.items():
                symbol = symbol_key(symbol)
                definition = dict(symbol=symbol, kind=kind, formula=fid, expression=body,
                                  sentence=sent.get("text", ""), section=sent.get("section", ""),
                                  scope=list(path), ranges=[])
                self.definitions.append(definition)
                active[path][symbol] = definition
            visible = {}
            for depth in range(len(local)+1):
                visible.update(active.get(local[:depth], {}))
            self.at[fid] = visible
            for symbol, definition in visible.items():
                if previous.get(symbol) is definition:
                    definition["ranges"][-1]["end"] = fid
                else:
                    definition["ranges"].append({"start": fid, "end": fid})
            previous = visible
