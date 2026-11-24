#!/usr/bin/env python3
"""lexicon.py - two-layer lexicon for the formulas produced by latex2nl.py.

LaTeX layer  (latex_lexicon.json, shared by every paper)
    what a token is: relation, operator, delimiter, letter, ...  and which
    juxtapositions LaTeX conventions already decide (2n, \\dim U, (a+b)c).

Paper layer  (out/paper_lexicon.json, built per paper)
    the symbols this paper uses and the juxtapositions the LaTeX layer cannot
    decide (p(y), \\pi x, xy).  Entries the paper itself settles (f: A \\to B,
    tuple arguments) are filled in; the rest keep "reading": null and are the
    only thing a person or an LLM has to answer.  Answers written into the file
    survive re-runs.

Usage
    python3 lexicon.py out/formulas.json            # -> out/paper_lexicon.json
    python3 lexicon.py --self-test
"""

import argparse
import collections
import hashlib
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)              # pipeline/
DATA = os.path.join(ROOT, "data")                 # pipeline/data: answer tables
SHARED = os.path.join(_PKG_ROOT, "data")          # the package's rule tables

if HERE not in sys.path:
    sys.path.insert(0, HERE)


import latex2nl as L                      # noqa: E402  (the answer tables)


def first_existing(*paths):
    """First path that exists, else the last one (so errors name a real place)."""
    for path in paths:
        if os.path.exists(path):
            return path
    return paths[-1]


#: shared, so it lives in data/; the copy next to the script is a fallback for
#: anyone who moved the file out on its own
DEFAULT_LEXICON = first_existing(os.path.join(SHARED, "latex_lexicon.json"),
                                 os.path.join(DATA, "latex_lexicon.json"),
                                 os.path.join(HERE, "latex_lexicon.json"))

TOKEN_RE = re.compile(
    r"<TXT\d+>|\\operatorname\*|\\[A-Za-z]+|\\.|\d+(?:\.\d+)?|\s+|.", re.S)

#: a formula that is nothing but letters: "xy", "Cf", "ra"
BARE_WORD_RE = re.compile(r"[A-Za-z]{2,}\Z")

#: token kinds that end adjacency and carry no operand of their own
BREAK_KINDS = {"relation", "arrow", "logic", "binop", "punct", "colon",
               "fence", "sep", "ellipsis", "separator", "text", "other"}

#: separator commands whose argument is prose, a label or a length: it is
#: consumed, never parsed as mathematics
RAW_ARG_SEPARATORS = {"hspace", "vspace", "tag", "text", "textrm", "textup",
                      "textsl", "textsf", "texttt", "textnormal", "textbf",
                      "textit", "textsc", "mbox"}

#: the text variants; they take one argument but are not all in the arity table
TEXT_VARIANTS = {"text", "textrm", "textup", "textsl", "textsf", "texttt",
                 "textnormal", "textbf", "textit", "textsc", "mbox", "ldotp"}

#: prose-taking commands the word table does not know (they appear inside
#: displays as interruptions); the argument is consumed raw, never parsed
PROSE_ARG_UNKNOWN = {"intertext", "shortintertext", "textintertext"}

#: names TeX, or a person, has already classified.  The word table is the LaTeX
#: layer, and a name it lacks is not automatically a question: \Im, \Re, \mho,
#: \ncong all have a decision on file.
ANSWERED_ELSEWHERE = L.ANSWERED_COMMANDS


class Lexicon:
    """The LaTeX layer, loaded from JSON."""

    def __init__(self, data):
        self.cat = {}
        for cat, names in data["commands"].items():
            for name in names:
                if name in self.cat:
                    raise ValueError("%r is in both %r and %r"
                                     % (name, self.cat[name], cat))
                self.cat[name] = cat
        self.arity = data.get("arity", {})
        self.optional = set(data.get("optional_arg", []))
        self.type_arrows = set(data.get("type_arrows", []))
        self.chars = data["chars"]
        self.csym = data["control_symbols"]
        self.rules = data["juxtaposition"]
        self.readings = data["readings"]

    @classmethod
    def load(cls, path=DEFAULT_LEXICON):
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def rule(self, left, right):
        """(reading, default) for two adjacent factor kinds."""
        for row in self.rules:
            if row["left"] in ("*", left) and row["right"] in ("*", right):
                return row["reading"], row.get("default")
        return None, None


def _factor(kind, sig, pos, **extra):
    item = {"t": "factor", "k": kind, "sig": sig, "pos": pos}
    item.update(extra)
    return item


#: environments whose body is a table (rows and cells, ``&`` and ``\\``).
#: Reading their interior invents factors: ``\begin{grid}{bmatrix}0 & 1 \\ 0 & 0``
#: produced the construction ``x · 0`` out of a row separator, and every cell
#: contributed a symbol.  One atom, no interior.
GRID_ENVS = ("grid", "diagram", "cases", "dcases", "rcases", "array", "subarray",
             "matrix", "pmatrix",
             "bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "ytableau",
             "ytableaushort", "pNiceArray", "bNiceArray", "NiceArray",
             "VNiceArray", "pNiceMatrix", "bNiceMatrix", "NiceMatrix",
             "NiceTabular")


class FormulaScan:
    """Scan one formula: factors, juxtaposition sites, declarations."""

    def __init__(self, lex, body, slot_operators=None):
        self.slot_operators = slot_operators or {}
        self.lex = lex
        self.body = body
        self.toks = [(m.group(), m.start()) for m in TOKEN_RE.finditer(body)
                     if not m.group().isspace()]
        self.i = 0
        self.sites = []            # {"L": item, "R": item, "ctx": str}
        self.idents = []           # every identifier factor
        self.decided = collections.Counter()
        self.operators = collections.Counter()
        self.unknown = set()
        self.negate_next = False
        self.top = self._seq("top", "top")

    # -- token helpers -------------------------------------------------

    def _kind(self, tok):
        if tok.startswith("<TXT"):
            return "slot_operator" if tok in self.slot_operators else "text"
        if tok.startswith("\\"):
            name = tok[1:]
            if name[:1].isalpha():
                return self.lex.cat.get(name.rstrip("*"), "unknown")
            if name.isspace():
                return "thin"
            return self.lex.csym.get(name, "unknown_symbol")
        if tok[0].isdigit():
            return "number"
        if tok in "{}_^":
            return tok
        if tok.isalpha():
            return "char_letter"
        return self.lex.chars.get(tok, "other")

    def _peek(self, offset=0):
        j = self.i + offset
        if j < len(self.toks):
            return self.toks[j][0]
        return None

    def _raw_arg_name(self, offset=0):
        """The environment name at ``offset`` tokens ahead, without consuming.

        The tokenizer keeps letters apart (``{grid}`` is ``{`` ``g`` ``r``
        ``i`` ``d`` ``}``), so the name has to be joined, not taken as one token.
        """
        j = self.i + offset
        if j >= len(self.toks) or self.toks[j][0] != "{":
            return ""
        out = []
        for k in range(j + 1, len(self.toks)):
            tok = self.toks[k][0]
            if tok == "}":
                break
            out.append(tok)
        return "".join(out)

    def _raw_arg(self):
        """Source text of the next argument, without parsing it."""
        tok = self._peek()
        if tok is None:
            return ""
        if tok != "{":
            self.i += 1
            return tok
        depth, out = 0, []
        while self.i < len(self.toks):
            tok = self.toks[self.i][0]
            self.i += 1
            if tok == "{":
                depth += 1
                if depth == 1:
                    continue
            elif tok == "}":
                depth -= 1
                if depth == 0:
                    break
            out.append(tok)
        return "".join(out)

    def _skip_optional(self):
        if self._peek() != "[":
            return
        depth = braces = 0
        while self.i < len(self.toks):
            tok = self.toks[self.i][0]
            self.i += 1
            if tok == "{":
                braces += 1
            elif tok == "}":
                braces = max(0, braces - 1)
            elif not braces and tok == "[":
                depth += 1
            elif not braces and tok == "]":
                depth -= 1
                if depth == 0:
                    return

    def _arg(self, ctx):
        """Parse one argument: a brace group or a single token."""
        tok = self._peek()
        if tok is None:
            return []
        if tok == "{":
            self.i += 1
            inner = self._seq(ctx, "brace")
            if self._peek() == "}":
                self.i += 1
            return inner
        if tok[0].isdigit() and len(tok) > 1:
            # TeX takes one digit: \frac12 is 1 over 2
            text, pos = self.toks[self.i]
            self.toks[self.i] = (text[0], pos)
            self.toks.insert(self.i + 1, (text[1:], pos + 1))
        items = []
        self._step(items, ctx)
        return items

    # -- sequence parsing ----------------------------------------------

    def _closer(self, tok):
        """'delim' / 'env' / 'brace' if tok closes something, else None."""
        if tok == "}":
            return "brace"
        kind = self._kind(tok)
        if kind in ("close", "size_close"):
            return "delim"
        if kind == "size_any":
            nxt = self._peek(1)
            if nxt is not None and self._kind(nxt) == "close":
                return "delim"
        if kind == "env_end":
            return "env"
        return None

    def _seq(self, ctx, stop):
        items = []
        while self.i < len(self.toks):
            closer = self._closer(self.toks[self.i][0])
            # A group ends at its own closer.  It also ends, without consuming
            # anything, at a `}` or `\end` that belongs to an outer level, so
            # unbalanced input degrades instead of swallowing the rest.
            if stop != "top" and closer is not None \
                    and (closer == stop or closer in ("brace", "env")):
                return items
            self._step(items, ctx)
        return items

    def _eat_close(self):
        tok = self._peek()
        if tok is None:
            return
        kind = self._kind(tok)
        if kind == "close":
            self.i += 1
        elif kind in ("size_close", "size_any"):
            self.i += 2

    def _group(self, ctx, delim, pos):
        inner = self._seq(ctx, "delim")
        self._eat_close()
        factors = [it for it in inner if it["t"] == "factor"]
        commas = sum(1 for it in inner
                     if it["t"] == "break" and it["text"] == ",")
        return _factor("paren", delim, pos, delim=delim,
                       arity=(commas + 1) if factors else 0)

    def _push(self, items, item, ctx):
        if item["t"] == "factor" and items:
            prev = items[-1]
            if prev["t"] == "factor":
                self.sites.append({"L": prev, "R": item, "ctx": ctx})
            elif prev["t"] == "prefix":
                self.decided["prefix application"] += 1
        elif item["t"] == "prefix" and items and items[-1]["t"] == "factor":
            self.decided["factor before operator"] += 1
        items.append(item)

    def _step(self, items, ctx):
        tok, pos = self.toks[self.i]
        kind = self._kind(tok)
        name = tok[1:].rstrip("*") if tok.startswith("\\") else tok

        if name in ("includegraphics", "includesvg", "includestandalone") and tok.startswith("\\"):
            self.i += 1
            if self._peek() == "*":
                self.i += 1
            while self._peek() == "[":
                self._skip_optional()
            if self._peek() == "{":
                self._raw_arg()
            items.append({"t": "barrier"})
            return

        if kind == "size_any":
            nxt = self._peek(1)
            nk = self._kind(nxt) if nxt is not None else "other"
            kind = {"open": "size_open", "close": "size_close"}.get(nk, "size_fence")

        if kind == "{":
            self.i += 1
            inner = self._seq(ctx, "brace")
            if self._peek() == "}":
                self.i += 1
            for n, item in enumerate(inner):
                if n == 0:
                    self._push(items, item, ctx)
                else:
                    items.append(item)
            return
        if kind == "}":
            self.i += 1
            return
        if kind in ("_", "^"):
            self.i += 1
            self._arg("subscript" if kind == "_" else "superscript")
            if kind == "_" and items and items[-1]["t"] == "factor" \
                    and items[-1]["k"] == "ident" \
                    and not items[-1]["sig"].endswith("_\u00b7"):
                items[-1]["sig"] += "_\u00b7"
            return
        if kind == "prime":
            self.i += 1
            if items and items[-1]["t"] == "factor" and items[-1]["k"] == "ident":
                items[-1]["sig"] += "'"
            return
        if kind in ("thin", "ignore", "postfix"):
            self.i += 1
            if name in PROSE_ARG_UNKNOWN and self._peek() == "{":
                # \intertext is "ignore" in the table, so it never reaches the
                # unknown branch: its prose argument was parsed as mathematics
                self._raw_arg()
                self._push(items, {"t": "break", "k": kind, "text": tok}, ctx)
                return
            if kind == "ignore" and name == "not":
                # \not negates the relation that follows: consuming it silently
                # turned "x \not\in H" into "x \in H"
                self.negate_next = True
            for _ in range(self.lex.arity.get(name, 0)):
                self._raw_arg()
            return

        if kind == "open":
            self.i += 1
            self._push(items, self._group(ctx, tok, pos), ctx)
            return
        if kind == "size_open":
            delim = self._peek(1) or ""
            self.i += 2
            self._push(items, self._group(ctx, delim, pos), ctx)
            return
        if kind in ("close", "size_close", "size_fence"):
            self.i += 1 if kind == "close" else 2
            self._push(items, {"t": "break", "k": "fence", "text": tok}, ctx)
            return
        if kind == "env_begin" and self._raw_arg_name(1) in GRID_ENVS:
            # A table is one object: its kind argument is not content (reading
            # ``{bmatrix}`` as letters gave ``b · m`` ... and ``x · 0``), and a
            # row or cell boundary is not a juxtaposition.  The cells themselves
            # are still maths and still scanned.
            env = self._raw_arg_name(1)
            self.i += 1                       # \begin
            self._raw_arg()                   # {grid}
            if env in ("grid", "diagram") and self._peek() == "{":
                env = self._raw_arg()
            if self._peek() == "[" and (env == "array" or "Nice" in env or env.endswith("*")):
                self._skip_optional()
            if env in ("array", "subarray", "NiceTabular") or env.endswith("NiceArray"):
                if self._peek() == "{":
                    self._raw_arg()
            # Scan each cell independently, including groups and nested grids.
            cell = []
            while self.i < len(self.toks):
                tok = self._peek()
                if self._kind(tok) == "env_end":
                    self.i += 1
                    self._raw_arg()
                    break
                if tok in ("&", "\\\\"):
                    self.i += 1
                    if tok == "\\\\":
                        self._skip_optional()
                    cell = []
                else:
                    self._step(cell, ctx)
            items.append({"t": "barrier"})
            return
        if kind == "env_begin":
            self.i += 1
            env = self._raw_arg()
            inner = self._seq(ctx, "env")
            if self._peek() == "\\end":
                self.i += 1
                self._raw_arg()
            factors = [it for it in inner if it["t"] == "factor"]
            self._push(items, _factor("paren", "env:" + env, pos,
                                      delim="env:" + env,
                                      arity=1 if factors else 0), ctx)
            return
        if kind == "env_end":
            self.i += 1
            self._raw_arg()
            return

        if kind in BREAK_KINDS:
            self.i += 1
            if self._peek() == "*":
                # \tag* / \hspace* : the star is a token of its own, and
                # leaving it made _raw_arg consume the star instead of the
                # argument, so {WPIC} was parsed as mathematics again
                self.i += 1
            if name in self.lex.optional:
                self._skip_optional()
            if name in RAW_ARG_SEPARATORS:
                # \tag{WPIC} and \hspace{0.5cm} carry a label and a length, not
                # mathematics: parsing them as formulas turned the letters of a
                # tag into symbols and "W · P" into a juxtaposition question
                if name in self.lex.arity or name in TEXT_VARIANTS:
                    self._raw_arg()
            else:
                for _ in range(self.lex.arity.get(name, 0)):
                    self._arg(ctx)
            if getattr(self, "negate_next", False) and kind in (
                    "relation", "arrow", "logic"):
                # \not is not spacing: it flips the relation that follows
                self.negate_next = False
                tok = "\\not" + tok
            self._push(items, {"t": "break", "k": kind, "text": tok}, ctx)
            return

        if kind in ("function", "bigop"):
            self.i += 1
            self._push(items, {"t": "prefix", "k": kind, "sig": tok}, ctx)
            return
        if kind in ("named_operator", "slot_operator"):
            self.i += 1
            word = self._raw_arg() if kind == "named_operator" else self.slot_operators[tok]
            self.operators[word] += 1
            self._push(items, {"t": "prefix", "k": "function",
                               "sig": "\\operatorname{%s}" % word}, ctx)
            return
        if kind == "structure":
            self.i += 1
            if name in self.lex.optional:
                self._skip_optional()
            for _ in range(self.lex.arity.get(name, 1)):
                self._arg(ctx)
            self._push(items, _factor("struct", tok, pos), ctx)
            return
        if kind == "font":
            self.i += 1
            raw = self._raw_arg()
            sig = "%s %s" % (tok, raw) if len(raw) == 1 else "%s{%s}" % (tok, raw)
            self._ident(items, sig, pos, ctx)
            return
        if kind == "accent":
            self.i += 1
            inner = self._arg(ctx)
            if len(inner) == 1 and inner[0]["t"] == "factor" \
                    and inner[0]["k"] == "ident":
                inner[0]["absorbed"] = True
                self._ident(items, "%s %s" % (tok, inner[0]["sig"]), pos, ctx)
            else:
                self._push(items, _factor("group", tok + "{\u2026}", pos), ctx)
            return
        if kind in ("letter", "char_letter", "literal"):
            self.i += 1
            self._ident(items, tok, pos, ctx)
            return
        if kind == "const":
            self.i += 1
            self._push(items, _factor("const", tok, pos), ctx)
            return
        if kind == "number":
            self.i += 1
            self._push(items, _factor("number", tok, pos), ctx)
            return

        if kind == "unknown_symbol":
            self.i += 1
            self.unknown.add(tok)
            self._push(items, {"t": "break", "k": "unknown", "text": tok}, ctx)
            return

        # unknown control word: report it, never guess -- but do not read a
        # prose argument as mathematics either
        self.i += 1
        self.unknown.add(tok)
        if name in PROSE_ARG_UNKNOWN and self._peek() == "{":
            self._raw_arg()
        self._push(items, {"t": "break", "k": "unknown", "text": tok}, ctx)

    def _ident(self, items, sig, pos, ctx):
        item = _factor("ident", sig, pos)
        self.idents.append(item)
        self._push(items, item, ctx)

    # -- results -------------------------------------------------------

    def declared_functions(self):
        """Signatures declared by a top-level ``f, g : A \\to B``."""
        heads = []
        for n, item in enumerate(self.top):
            if item["t"] == "break" and (item["k"] == "colon"
                                         or item["text"] == "\\colon"):
                rest = self.top[n + 1:]
                if any(it["t"] == "break" and it["k"] == "arrow"
                       and it["text"].lstrip("\\") in self.lex.type_arrows
                       for it in rest):
                    return heads
                return []
            if item["t"] == "factor" and item["k"] == "ident":
                heads.append(item["sig"])
            elif item["t"] == "break" and item["k"] in ("punct", "ellipsis"):
                continue
            else:
                return []
        return []

    def snippet(self, pos, width=44):
        start = max(0, pos - 14)
        return re.sub(r"\s+", " ", self.body[start:start + width]).strip()


def construction_key(site):
    left, right, ctx = site["L"], site["R"], site["ctx"]
    if right["k"] == "paren":
        return "%s %s\u2026" % (left["sig"], right["sig"])
    key = "%s \u00b7 %s" % (left["sig"], right["sig"])
    if ctx != "top":
        key += "  [%s]" % ctx
    return key


#: names answered for one paper (``papers`` in data/name_answers.json).  A name
#: the pipeline already knows for this paper is not an unknown command.
PAPER_ID = ""


def _paper_answered(paper):
    if not paper:
        return set()
    # the per-paper name answers live with the other rule tables, in the
    # package's data/, not in pipeline/data/ (which holds this layer's answers)
    path = os.path.join(SHARED, "name_answers.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return set()
    out = set()
    for name, rec in ((doc.get("papers") or {}).get(paper) or {}).items():
        if (rec or {}).get("action") not in (None, "unknown"):
            out.add(name)
    return out


import rule_reviews


PAPER_EVIDENCE = {"arrow_decl", "tuple_arg", "product_decl", "function_value"}  # tuple_arg occurs in older caches
PRODUCT_DECL = re.compile(
    r"^(?:(?:Throughout|In this paper)\s*,?\s+)?(?P<id>MATH\d+)\s+"
    r"(?:denotes|is defined as)\s+(?:the|an?)\s+(?:(?:ordinary|usual)\s+)?"
    r"product\s+of\b", re.I)


# Only use prose whose grammatical subject/object is this exact formula.
PRODUCT_USE = re.compile(
    r"\bthe (?:pointwise )?product\s+(MATH\d+)\s+is\b|"
    r"\b(?:write|use)\s+(MATH\d+)\s+for (?:its|the|their) (?:pointwise )?product\b|"
    r"\b(MATH\d+)\s+denotes scalar multiplication\b|"
    r"\b(MATH\d+)\s+(?:means|denotes|represents|is)\s+(?:their|the|its)\s+"
    r"(?:(?:ordinary|scalar|pointwise)\s+)?product\b", re.I)

FUNCTION_VALUE = re.compile(
    r"\bevaluat(?:e|ing) the function at [^,.;]+,\s*(?:obtaining|giving|yielding)\s+(MATH\d+)\b|"
    r"\b(MATH\d+)\s+is (?:its|the function(?:'s|’s)) value at\b", re.I)



def asserted_reading(text):
    """Only an unqualified statement can supply a binding prose rule."""
    return not re.search(r"\?|\b(?:not|never|whether|if|unless|suppose|assuming|"
                         r"might|may|could|sometimes|only when)\b", text, re.I)


def product_sites(sentences):
    sites, definitions, other_definitions = set(), set(), set()
    for sent in sentences:
        text = sent.get("text", "")
        ids = set(sent.get("formulas", ()))
        declaration = PRODUCT_DECL.match(text)
        asserted = asserted_reading(text)
        if asserted and declaration and declaration["id"] in ids:
            sites.add(declaration["id"])
            definitions.add(declaration["id"])
        else:
            for match in re.finditer(r"\b(MATH\d+)\s+(?:denotes|is defined as|represents)\b", text, re.I):
                if match[1] in ids:
                    other_definitions.add(match[1])
        if not asserted:
            continue
        for match in PRODUCT_USE.finditer(text):
            sites.update(fid for fid in match.groups() if fid in ids)
    return sites, definitions, other_definitions


def build_paper_lexicon(formulas, lex, previous=None, paper="", sentences=()):
    """Scan every formula and assemble the paper layer."""
    symbols = {}
    constructions = {}
    decided = collections.Counter()
    operators = collections.Counter()
    unknown = collections.defaultdict(list)
    declared = {}                       # sig -> formula id

    bodies = []
    slot_operators = {}
    side_bodies = 0
    raw_only = 0
    for f in formulas:
        if f.get("kind") == "ref":
            continue
        if f.get("role") == "opaque":
            # an unknown command kept the whole formula intact; its inside was
            # never normalised, so every juxtaposition here would be a guess
            # about text the splitter could not read (roles.py skips it too)
            continue
        body = f.get("expanded") or ""
        if not body:
            # a record with no expanded form is scanned as empty and looks
            # harmless; say so instead
            raw_only += 1
        bodies.append((f["id"], body))
        slot_operators[f["id"]] = {"<TXT%d>" % n: slot["raw"].strip()
                                   for n, slot in enumerate(f.get("text_slots", []), 1)
                                   if slot.get("role") == "operator"}
        for n, side in enumerate(f.get("side_conditions") or [], 1):
            bodies.append(("%s.side%d" % (f["id"], n), side))
            side_bodies += 1

    products, definitions, other_definitions = product_sites(sentences)
    body_by_id = dict(bodies)
    order = {fid: i for i, (fid, _) in enumerate(bodies)}
    contexts = {fid: sent.get("text", "") for sent in sentences for fid in sent.get("formulas", [])}
    local_apply = {fid for text in set(contexts.values()) if asserted_reading(text)
                   for match in FUNCTION_VALUE.finditer(text)
                   for fid in match.groups() if fid}
    entries = collections.defaultdict(list)
    declarations = collections.defaultdict(list)
    for fid, body in bodies:
        scan = FormulaScan(lex, body, slot_operators.get(fid))
        decided.update(scan.decided)
        operators.update(scan.operators)
        for cmd in scan.unknown:
            unknown[cmd].append(fid)
        for sig in scan.declared_functions():
            declared.setdefault(sig, fid)
            declarations[sig].append(fid)
        for item in scan.idents:
            if item.get("absorbed"):
                continue
            entry = symbols.setdefault(item["sig"], {"count": 0, "first": fid})
            entry["count"] += 1
        for site in scan.sites:
            reading, default = lex.rule(site["L"]["k"], site["R"]["k"])
            if reading is not None:
                decided["%s %s" % (site["L"]["k"], site["R"]["k"])] += 1
                continue
            key = construction_key(site)
            left, right = site["L"], site["R"]
            entries[key].append({
                "form": left["k"] + " " + right["k"], "left": left["sig"], "right": right["sig"],
                "context": site["ctx"], "count": 1, "sites": [fid], "offset": left["pos"],
                "example": scan.snippet(left["pos"]), "reading": None, "source": None,
                "whole": len(scan.top) == 2 and left is scan.top[0] and right is scan.top[1],
                "default": "index" if site["ctx"] == "subscript" and right["k"] == "ident" else default,
            })

    # a formula that is one run of letters ("xy", "Cf", "ra") is the same
    # question asked of a single token: one name, or a product of letters?
    for fid, body in bodies:
        word = body.strip()
        if not BARE_WORD_RE.match(word):
            continue
        entries[word].append({"form": "bare word", "left": word, "right": "", "context": "bare",
                              "count": 1, "sites": [fid], "offset": body.index(word), "example": word,
                              "whole": len(word) == 2, "reading": None, "source": None, "default": "label"})

    scalar_declarations = collections.defaultdict(list)
    for sent in sentences:
        for match in re.finditer(r"\b(MATH\d+(?:\s+and\s+MATH\d+)?)\s+(?:be|is|are)\s+(?:a\s+)?scalars?\b",
                                 sent.get("text", ""), re.I):
            for declared_id in re.findall(r"MATH\d+", match[1]):
                for sig in body_by_id.get(declared_id, "").split(","):
                    if re.fullmatch(r"[A-Za-z]", sig.strip()):
                        scalar_declarations[sig.strip()].append(order[declared_id])

    for shape, occurrences in entries.items():
        occurrences.sort(key=lambda e: (order[e["sites"][0]], e["offset"]))
        definition = None
        latest_product = -1
        for entry in occurrences:
            fid = entry["sites"][0]
            parent = fid.split(".")[0]
            body = body_by_id[fid]
            sentence = contexts.get(parent, "")
            whole = entry.pop("whole")
            if fid in other_definitions:
                definition = None
            if whole and fid in products:
                latest_product = order[fid]
                entry.update(reading="multiply", source="product_decl:" + fid)
                if fid in definitions:
                    definition = fid
            elif whole and fid in local_apply and entry["right"] == "(":
                entry.update(reading="apply", source="function_value:" + fid)
                definition = None
            elif definition and (body.strip() == body_by_id[definition].strip()
                                 or re.fullmatch(r"[A-Za-z]{2}", body_by_id[definition].strip())):
                entry.update(reading="multiply", source="product_decl:" + definition)
            elif entry["right"] == "(":
                # A declaration must precede the use. Local rebinding or an
                # explicit product elsewhere prevents a paper-wide inference.
                earlier = [d for d in declarations.get(entry["left"], []) if order[d] < order[fid]]
                overrides = [idx for idx in scalar_declarations[entry["left"]] if idx <= order[fid]]
                if earlier and order[earlier[-1]] > max([latest_product] + overrides):
                    entry.update(reading="apply", source="arrow_decl:" + earlier[-1])
            location = "%s@%d" % (fid, entry["offset"])
            entry.update(shape=shape, location=location)
            entry["anchor"] = hashlib.sha256(json.dumps(
                [paper, shape, location, body, sentence], ensure_ascii=False).encode()).hexdigest()
            key = shape if len(occurrences) == 1 else shape + " @ " + location
            constructions[key] = entry

    # answers given earlier (by hand or by an LLM) are kept, unless the paper
    # itself now settles the construction: a declaration overwrites a stale
    # guess, and the disagreement is reported instead of being buried
    overridden = []
    for key, old in ((previous or {}).get("constructions") or {}).items():
        new = constructions.get(key)
        if new is None or not old.get("reading") or old.get("from_library"):
            continue
        if old.get("count", 1) != 1 or (old.get("anchor") and new["anchor"] != old["anchor"]):
            continue
        if not old.get("anchor") and rule_reviews.from_model(old.get("source")):
            continue
        source = old.get("source") or "manual"
        if source.split(":")[0] in PAPER_EVIDENCE:
            review = old.get("model_review")
            if review and new.get("reading") and new.get("source"):
                new["model_review"] = rule_reviews.compare(new["source"], new["reading"],
                                                         review["model_answer"], review["model"])
            continue
        if old["reading"] not in lex.readings:
            raise ValueError("%r: reading %r is not one of %s"
                             % (key, old["reading"], lex.readings))
        owner = (new.get("source") or "").split(":")[0]
        if owner in PAPER_EVIDENCE:
            if rule_reviews.from_model(source):
                new["model_review"] = rule_reviews.compare(new["source"], new["reading"], old["reading"], source)
            if old["reading"] != new["reading"]:
                overridden.append((key, old["reading"], new["source"], new["reading"]))
            continue
        new["reading"], new["source"] = old["reading"], source
        if old.get("note"):
            new["note"] = old["note"]

    for sig, fid in declared.items():
        if sig in symbols:
            symbols[sig]["declared"] = "function@" + fid

    if raw_only:
        print("  %d records have no expanded form and were scanned as empty"
              % raw_only)
    if overridden:
        print("  %d answers overridden by the paper's own declaration:"
              % len(overridden))
        for key, was, why, now in overridden[:5]:
            print("    %-24s %r -> %r (%s)" % (key, was, now, why))
    result = {
        "paper": paper,
        "readings": lex.readings,
        "summary": {
            "formulas": len(bodies) - side_bodies,
            "side_conditions": side_bodies,
            "symbols": len(symbols),
            "decided_by_latex_layer": dict(sorted(decided.items())),
            "constructions": len(constructions),
            "settled_by_paper": sum(1 for c in constructions.values()
                                    if (c["source"] or "").split(":")[0]
                                    in PAPER_EVIDENCE),
            "settled": sum(1 for c in constructions.values() if c["reading"]),
            "open": sum(1 for c in constructions.values() if not c["reading"]),
        },
        "constructions": _ordered(constructions),
        "operators": dict(operators.most_common()),
        # a name the word table lacks but TeX, or a person, has classified is
        # not an open question: it is a gap in the table, and it is reported as
        # its own list rather than mixed in with the names nobody knows
        "unknown_commands": {k: v for k, v in sorted(unknown.items())
                             if k.lstrip("\\") not in _paper_answered(paper)
                             if k.lstrip("\\") not in ANSWERED_ELSEWHERE},
        "known_outside_the_table": {k: v for k, v in sorted(unknown.items())
                                    if k.lstrip("\\") in ANSWERED_ELSEWHERE},
        "symbols": {k: symbols[k] for k in sorted(
            symbols, key=lambda s: (-symbols[s]["count"], s))},
        "overridden_by_paper": [
            {"key": key, "was": was, "why": why, "now": now}
            for key, was, why, now in overridden],
    }
    return _assign_status(result)


def _ordered(constructions):
    """Settled entries first, then by how often the construction occurs."""
    order = sorted(constructions, key=lambda k: (
        constructions[k]["reading"] is None, -constructions[k]["count"], k))
    return {k: constructions[k] for k in order}


def _status_of(entry) -> str:
    """Where a construction's reading comes from, in the words of the note."""
    owner = (entry.get("source") or "").split(":")[0]
    if entry.get("reading"):
        if owner in PAPER_EVIDENCE:
            return "settled_by_paper"
        return "settled_by_answer"
    return "open" if entry.get("default") else "missing"


def _assign_status(result) -> dict:
    """Put ``status`` on every construction, with the four values of the note.

    ``settled_by_table`` is deliberately absent: a site the table decides never
    becomes a construction, so a construction can only be settled by the paper,
    by an answer, or be waiting on a default.
    """
    for entry in (result.get("constructions") or {}).values():
        owner = (entry.get("source") or "").split(":")[0]
        if entry.get("reading"):
            entry["status"] = ("settled_by_paper"
                               if owner in PAPER_EVIDENCE
                               else "settled_by_answer")
        else:
            # "defaulted" read like a decision; D11 asks for the state to be
            # visible: still open, and the fallback is named as a fallback
            if entry.get("default"):
                entry["status"] = "open"
                entry["provisional_reading"] = entry["default"]
            else:
                entry["status"] = "missing"
    return result


def check_word_readings(result):
    constructions = result["constructions"]
    by_position = {(e.get("shape", k), e.get("location")): e for k,e in constructions.items()}
    for bare in constructions.values():
        word = bare["left"]
        if bare["form"] != "bare word" or len(word) != 2:
            continue
        pair = by_position.get((word[0] + " · " + word[1], bare["location"]))
        if not pair:
            continue
        readings = (bare["reading"], pair["reading"])
        if all(readings) and readings[0] != readings[1]:
            decisive = [e for e in (bare, pair) if (e.get("source") or "").split(":")[0] in PAPER_EVIDENCE]
            if len(decisive) == 1:
                rule = decisive[0]
                other = pair if rule is bare else bare
                if rule_reviews.from_model(other.get("source")):
                    other["model_review"] = rule_reviews.compare(rule["source"], rule["reading"],
                                                                other["reading"], other["source"])
                other.update(reading=rule["reading"], source=rule["source"])
                continue
            for entry in (bare, pair):
                entry["conflict"] = {"word": word, "bare": readings[0], "pair": readings[1]}
                entry.update(reading=None, source=None)
    _resummarise(result)
    _assign_status(result)


def _resummarise(result):
    c = result["constructions"]
    result["summary"]["constructions"] = len(c)
    result["summary"]["settled"] = sum(1 for v in c.values() if v["reading"])
    result["summary"]["settled_by_paper"] = sum(
        1 for v in c.values()
        if (v.get("source") or "").split(":")[0] in PAPER_EVIDENCE)
    result["summary"]["open"] = sum(1 for v in c.values() if not v["reading"])
    result["constructions"] = _ordered(c)


def _question(entry):
    """One sentence an LLM (or a person) can answer."""
    if entry["context"] == "bare":
        return ("The formula is the single run of letters %r. Is that one name "
                "for a single object (label), a product of its letters "
                "(multiply), or one letter applied to the next (apply)?"
                % entry["left"])
    if entry["right"].startswith("(") or entry["form"].endswith("paren"):
        head = "%s(...)" % entry["left"]
        tail = ("Is %r a function applied to its argument (apply), a product "
                "(multiply), or one name for a single object (label)?"
                % head)
    elif entry["context"] == "subscript":
        tail = ("In a subscript, do %r and %r form one multi-index (index) or "
                "a product (multiply)?" % (entry["left"], entry["right"]))
    else:
        tail = ("%r and %r stand side by side with no operator. Does the paper "
                "read that as one applied to the other (apply), a product "
                "(multiply), or one name for a single object (label)?"
                % (entry["left"], entry["right"]))
    return tail


def export_pending(result, formulas, path, sentences=(), review_rules=False):
    """Write the unanswered constructions as a minimal, answerable list."""
    by_id = {f["id"]: f for f in formulas}
    contexts = {fid: sent.get("text", "") for sent in sentences for fid in sent.get("formulas", [])}
    def source_sentence(text, target):
        def restore(match):
            fid = match[0]
            formula = by_id.get(fid, {})
            raw = formula.get("raw") or formula.get("expanded")
            return ("<<" + raw + ">>" if fid == target else "$" + raw + "$") if raw else fid
        return re.sub(r"MATH\d+", restore, text)
    items = []
    for key, entry in result["constructions"].items():
        if entry["reading"] and not (review_rules and not entry.get("model_review") and
                                     (entry.get("source") or "").split(":")[0] in PAPER_EVIDENCE):
            continue
        examples = []
        for sid in entry["sites"][:3]:
            fid, _, side = sid.partition(".side")
            rec = by_id.get(fid, {})
            body = rec["side_conditions"][int(side)-1] if side else rec.get("expanded", "")
            example = {"id": sid, "formula": body, "offset": entry["offset"]}
            context = contexts.get(sid.split(".")[0])
            if context:
                example["sentence"] = source_sentence(context, fid)
            examples.append(example)
        items.append({
            "key": key, "anchor": entry["anchor"], "location": entry["location"],
            "question": _question(entry),
            **({"conflict": entry["conflict"]} if entry.get("conflict") else {}),
            "count": entry["count"],
            "default_if_unanswered": entry["default"],
            "examples": examples,
            "answer": None,
            "note": None,
        })
    payload = {
        "instructions": (
            "Fill 'answer' for every item with one of the allowed readings, or "
            "leave it null. Use 'note' to record the evidence (a line number, a "
            "definition, a sentence). Then run: "
            "python3 lexicon.py --merge-answers <this file> <formulas.json>"),
        "allowed_readings": result["readings"],
        "meta": {"paper": result.get("paper"), "source_file": result.get("source_file"),
                 "open": len(items)},
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return payload


def scope_matches(scope, current):
    return all(not scope.get(key) or scope[key] == current.get(key)
               for key in ("paper", "source_file"))


def answer_digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def merge_answers(result, answers_path, only_unset=False, library=False, fallback_scope=None):
    """Apply answers in place; return (applied, skipped, rejected).

    ``only_unset`` fills gaps without touching entries that already have a
    reading; used when loading the committed judgements file, so answers given
    later in ``out/paper_lexicon.json`` keep precedence.
    """
    with open(answers_path, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("%s: expected a list under 'items'" % answers_path)
    allowed = set(result["readings"])
    applied, skipped, rejected = 0, 0, []
    #: the sigs of the answers this paper actually used; run_corpus looks for
    #: committed answers that no paper ever applied (D11's I3)
    applied_keys = result.setdefault("answers_applied", [])
    #: answers whose construction is not in this paper: if construction_key
    #: ever changes, every committed answer lands here, and "skipped" would
    #: look like "already answered"
    unknown_key = []
    scope = dict(fallback_scope or {})
    if isinstance(data, dict):
        scope.update({k: v for k, v in (data.get("meta") or {}).items() if v})
    for item in items:
        key, answer = item.get("key"), item.get("answer")
        paper = item.get("paper") or scope.get("paper")
        source_file = item.get("source_file") or scope.get("source_file")
        if library and not paper:
            # The original bundled table attributed most items in this exact form.
            credit = re.fullmatch(r"judged on ([\w.-]+)", item.get("note") or "")
            paper = credit[1] if credit else None
        if (not scope_matches({"paper": paper, "source_file": source_file}, result) or (library and not paper)):
            skipped += 1
            continue
        entry = result["constructions"].get(key)
        if entry is None:
            if answer:
                unknown_key.append((key, answer))
            skipped += 1
            continue
        if not answer:
            skipped += 1
            continue
        if answer not in allowed:
            rejected.append((key, answer))
            continue
        source = item.get("source") or item.get("by") or "manual"
        if (item.get("anchor") and item["anchor"] != entry["anchor"]) or (
                rule_reviews.from_model(source) and not item.get("anchor")):
            skipped += 1
            continue
        owner = (entry.get("source") or "").split(":")[0]
        if owner in PAPER_EVIDENCE:
            if rule_reviews.from_model(source):
                entry["model_review"] = rule_reviews.compare(entry["source"], entry["reading"],
                                                          answer, source, item.get("model"))
            skipped += 1
            continue
        if only_unset and entry.get("reading"):
            skipped += 1
            continue
        entry["reading"] = answer
        entry["source"] = source
        if library:
            entry["from_library"] = True
        else:
            entry.pop("from_library", None)
        entry.pop("provisional_reading", None)
        applied_keys.append(item.get("key") or key)
        if item.get("note"):
            entry["note"] = item["note"]
        entry.pop("conflict", None)
        applied += 1
    _resummarise(result)
    _assign_status(result)
    if unknown_key:
        print("  %d answers name a construction this paper does not have "
              "(first: %r)" % (len(unknown_key), unknown_key[0][0]))
    return applied, skipped, rejected, unknown_key


def _self_test():
    lex = Lexicon.load()
    failures = []

    def check(label, ok, detail=""):
        print("  %s %s" % ("ok  " if ok else "FAIL", label))
        if not ok:
            failures.append("%s %s" % (label, detail))

    def run(*bodies, previous=None):
        formulas = [{"id": "F%d" % n, "kind": "inline", "expanded": b}
                    for n, b in enumerate(bodies, 1)]
        return build_paper_lexicon(formulas, lex, previous)

    print("[1] the word table is the only command list")
    try:
        sys.path.insert(0, HERE)
        import latex2nl
        # latex2nl.py reads the same file this script does, so the two sets
        # must be equal; a difference means one of the loaders is broken
        check("latex2nl.py loaded the same commands",
              latex2nl.LEXICON_COMMANDS == set(lex.cat),
              "only in latex2nl: %s; only here: %s" % (
                  sorted(latex2nl.LEXICON_COMMANDS - set(lex.cat))[:8],
                  sorted(set(lex.cat) - latex2nl.LEXICON_COMMANDS)[:8]))
        check("no second command list in latex2nl.py",
              not hasattr(latex2nl, "KNOWN_MATH_COMMANDS")
              and not hasattr(latex2nl, "GREEK"),
              "a hardcoded list is back: it will drift from the table")
    except ImportError:
        print("  skip (latex2nl.py not importable)")

    print("[2] decided by the LaTeX layer")
    r = run("2n", "\\dim U\\le i", "(a+b)c", "\\frac12", "\\binom Ki",
            "\\left(\\frac{\\pi}{2}+o\\right)\\sqrt n",
            "\\operatorname{conv}\\{u_0,\\ldots,u_i\\}\\cap H_i^+\\ne\\varnothing")
    check("nothing left open", r["summary"]["open"] == 0,
          str(list(r["constructions"])))
    check("conv recorded as operator", r["operators"].get("conv") == 1,
          str(r["operators"]))

    print("[3] left to the paper layer")
    r = run("xy", "p(y)=a", "u=\\sum_{s=0}^i t_su_s", "\\bar x\\bar y",
            "a_{ij}")
    keys = list(r["constructions"])
    check("x \u00b7 y", "x \u00b7 y" in keys, str(keys))
    check("bare word xy", "xy" in keys, str(keys))
    check("p (\u2026", "p (\u2026" in keys, str(keys))
    check("t_\u00b7 \u00b7 u_\u00b7", "t_\u00b7 \u00b7 u_\u00b7" in keys, str(keys))
    check("\\bar x \u00b7 \\bar y", "\\bar x \u00b7 \\bar y" in keys, str(keys))
    check("subscript context kept", "i \u00b7 j  [subscript]" in keys, str(keys))
    check("subscript default is index",
          r["constructions"]["i \u00b7 j  [subscript]"]["default"] == "index")
    check("bare word default is label",
          r["constructions"]["xy"]["default"] == "label")
    check("all six open", r["summary"]["open"] == 6, str(r["summary"]))

    print("[4] settled by the paper itself")
    r = run("\\pi:\\{0,1\\}^K\\to\\{0,1\\}^n", "q(x)=(p(\\pi x),p(\\overline{\\pi x}))",
            "\\ell_1,\\ldots,\\ell_d:W\\to\\mathbb R", "\\ell_{i+1}(u)=0",
            "\\alpha_i(u_0,\\ldots,u_i)=1", "u\\mapsto(a,b)")
    c = r["constructions"]
    pi_sites = [e for e in c.values() if e["shape"] == "\\pi · x"]
    check("two separate open pi x occurrences", len(pi_sites) == 2
          and all(e["reading"] is None and e["count"] == 1 for e in pi_sites))
    check("\\ell family declared",
          c["\\ell_\u00b7 (\u2026"]["source"] == "arrow_decl:F3",
          str(c["\\ell_\u00b7 (\u2026"]))
    check("tuple without a declared function stays open", c["\\alpha_\u00b7 (\u2026"]["reading"] is None,
          str(c["\\alpha_\u00b7 (\u2026"]))
    check("p and q stay open", all(e["reading"] is None for e in c.values() if e["shape"] == "p (…")
          and c["q (\u2026"]["reading"] is None)
    check("mapsto is not a declaration", "u" not in
          [s for s, v in r["symbols"].items() if v.get("declared")])

    print("[5] answers survive a re-run")
    first = run("xy", "p(y)=a")
    first["constructions"]["x \u00b7 y"].update(reading="label", source="llm")
    again = run("xy", "p(y)=a", previous=first)
    check("kept", again["constructions"]["x \u00b7 y"]["reading"] == "label"
          and again["constructions"]["x \u00b7 y"]["source"] == "llm")
    # "xy" and "p (…" are still open; only the answered one is settled
    check("other entry still open", again["summary"]["open"] == 2,
          str(again["summary"]))
    first["constructions"]["x \u00b7 y"]["reading"] = "product"
    try:
        run("xy", previous=first)
        check("reading outside the closed set rejected", False)
    except ValueError:
        check("reading outside the closed set rejected", True)

    print("[6] robustness")
    r = run("\\weirdop x", "[0,1)", "\\left.\\frac{a}{b}\\right|_{x=0}",
            "f(x", "\\begin{cases}1,&x>0,\\\\0,&<TXT1>.\\end{cases}", "")
    check("unknown command reported", "\\weirdop" in r["unknown_commands"],
          str(r["unknown_commands"]))
    # a name TeX has classified is a gap in the word table, not an open
    # question: \apprge is a relation in tex_names.json and is not in the table
    r2 = run("a \\apprge b")
    check("a name classified elsewhere is not called unknown",
          "\\apprge" not in r2["unknown_commands"]
          and "\\apprge" in r2["known_outside_the_table"],
          str((r2["unknown_commands"], r2["known_outside_the_table"])))
    check("unbalanced input does not crash", True)

    print("[7] export and merge")
    import tempfile
    formulas = [{"id": "F1", "kind": "inline", "expanded": "xy"},
                {"id": "F2", "kind": "inline", "expanded": "p(y)=a"}]
    r = build_paper_lexicon(formulas, lex)
    with tempfile.TemporaryDirectory() as tmp:
        pending = os.path.join(tmp, "pending.json")
        payload = export_pending(r, formulas, pending)
        # "xy" is the bare-word question, plus "x · y" and "p (…"
        check("one question per open construction", len(payload["items"]) == 3,
              str(len(payload["items"])))
        check("question is a sentence", payload["items"][0]["question"].endswith("?"),
              payload["items"][0]["question"])
        check("examples carried", payload["items"][0]["examples"][0]["formula"],
              str(payload["items"][0]["examples"]))
        for item in payload["items"]:
            item["answer"] = "apply" if item["key"].startswith("p") else "label"
        with open(pending, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        applied, skipped, rejected, _unknown = merge_answers(r, pending)
        check("all three applied", applied == 3 and not rejected,
              "%d %s" % (applied, rejected))
        check("summary updated", r["summary"]["open"] == 0
              and r["summary"]["settled"] == 3, str(r["summary"]))
        check("settled entries first",
              next(iter(r["constructions"])) in ("x \u00b7 y", "p (\u2026"))
        check("answer recorded",
              r["constructions"]["x \u00b7 y"]["reading"] == "label"
              and r["constructions"]["x \u00b7 y"]["source"] == "manual",
              str(r["constructions"]["x \u00b7 y"]))
        payload["items"][0]["answer"] = "product"
        with open(pending, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        applied, skipped, rejected, _unknown = merge_answers(r, pending)
        check("bad reading rejected", rejected and rejected[0][1] == "product",
              str(rejected))

    print()
    if failures:
        print("%d check(s) failed" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("formulas", nargs="?", help="formulas.json from latex2nl.py")
    ap.add_argument("-o", "--output", help="default: paper_lexicon.json next "
                                           "to the input")
    ap.add_argument("--lexicon", default=DEFAULT_LEXICON)
    ap.add_argument("--judgements", help="committed answers, default "
                                        "data/judgements.json")
    ap.add_argument("--paper-id", metavar="ID", default="",
                    help="use this paper's name answers (data/name_answers.json)")
    ap.add_argument("--no-judgements", action="store_true",
                    help="ignore the committed answers; use for a new paper, "
                         "whose constructions only look alike")
    ap.add_argument("--export-pending", metavar="PATH",
                    help="write the unanswered constructions as a question "
                         "list for a person or an LLM")
    ap.add_argument("--merge-answers", metavar="PATH",
                    help="apply answers from such a list back into the output")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    global PAPER_ID
    PAPER_ID = args.paper_id

    if args.self_test:
        return _self_test()
    if not args.formulas:
        ap.error("formulas.json is required")

    lex = Lexicon.load(args.lexicon)
    with open(args.formulas, encoding="utf-8") as fh:
        formulas = json.load(fh)
    if isinstance(formulas, dict):
        # a formulas.json may be a list or {"formulas": [...]}: iterating the
        # dict gave "formulas" as the first record and crashed
        formulas = formulas.get("formulas") or formulas.get("items") or []
    out = args.output or os.path.join(os.path.dirname(args.formulas) or ".",
                                      "paper_lexicon.json")
    previous = None
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            previous = json.load(fh)
    source_file = None
    stats_path = os.path.join(os.path.dirname(args.formulas), "normalize-stats.json")
    if os.path.isfile(stats_path):
        with open(stats_path, encoding="utf-8") as fh:
            source = json.load(fh).get("input")
        if source:
            source_file = os.path.normcase(os.path.abspath(source))
    local_scope = (previous or {}).get("local_answer_scope", {})
    current_scope = {"paper": PAPER_ID, "source_file": source_file}
    if previous is not None and (previous.get("paper") != PAPER_ID or
                                 not scope_matches(previous, current_scope)):
        if "paper" not in previous and not PAPER_ID.startswith("sha256-"):
            # Old caches mixed local edits with guesses imported from unrelated
            # papers. Retire the identifiable library entries, retaining local edits.
            library_path = os.path.join(DATA, "judgements.json")
            if os.path.isfile(library_path):
                with open(library_path, encoding="utf-8") as fh:
                    legacy = {(row["key"], row.get("answer"), row.get("note"))
                              for row in json.load(fh)["items"]}
                for key, entry in previous.get("constructions", {}).items():
                    if (key, entry.get("reading"), entry.get("note")) in legacy:
                        entry["from_library"] = True
        else:
            previous = None
    sentences = []
    sentence_path = os.path.join(os.path.dirname(args.formulas), "sentences.jsonl")
    if os.path.isfile(sentence_path):
        with open(sentence_path, encoding="utf-8") as fh:
            sentences = [json.loads(line) for line in fh if line.strip()]
    result = build_paper_lexicon(formulas, lex, previous, paper=PAPER_ID, sentences=sentences)

    result["source_file"] = source_file
    if local_scope:
        result["local_answer_scope"] = local_scope

    # Shared readings only apply within their paper of origin.
    if args.no_judgements:
        judgements = ""
    else:
        judgements = args.judgements or first_existing(
            os.path.join(DATA, "judgements.json"),
            os.path.join(HERE, "judgements.json"))
    if judgements and os.path.exists(judgements):
        applied, _skipped, rejected, _unknown = merge_answers(
            result, judgements, only_unset=True, library=True)
        if applied:
            print("from judgements.json  : %d" % applied)
        for key, answer in rejected:
            print("rejected %r in judgements.json: %r is not one of %s"
                  % (key, answer, lex.readings))

    if args.merge_answers:
        # Bind a legacy untagged answer file to the first original that used it.
        digest = answer_digest(args.merge_answers)
        if digest != local_scope.get("sha256"):
            local_scope = {**current_scope, "sha256": digest}
        applied, skipped, rejected, unknown_key = merge_answers(
            result, args.merge_answers, fallback_scope=local_scope)
        if applied:
            result["local_answer_scope"] = local_scope
        for key, answer in rejected:
            print("rejected %r: %r is not one of %s"
                  % (key, answer, lex.readings))
        print("answers applied       : %d (skipped %d, rejected %d, "
              "unknown construction %d)"
              % (applied, skipped, len(rejected), len(unknown_key)))

    check_word_readings(result)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    result = _assign_status(result)
    result["answers_applied"] = sorted(set(result.get("answers_applied") or []))

    todo = os.path.join(os.path.dirname(out) or ".", "construction_todo.json")
    open_keys = {k for k, e in result["constructions"].items()
                 if not e["reading"]}
    questions = os.path.join(os.path.dirname(out) or ".", "construction_questions.json")
    export_pending(result, formulas, questions, sentences, review_rules=True)
    if open_keys:
        export_pending(result, formulas, todo, sentences)
        with open(todo, encoding="utf-8") as fh:
            written = {i["key"] for i in json.load(fh)["items"]}
        # D11's I2, against what is on disk: a construction with no reading and
        # no question is exactly the failure this is meant to prevent
        assert written == open_keys, (len(written), len(open_keys))
        print("open constructions    : %d -> %s" % (len(open_keys), todo))
    elif os.path.exists(todo):
        os.remove(todo)
    if result.get("unknown_commands"):
        # not a question for the model: a command TeX or the paper itself can
        # answer belongs to the name layer, which is pending_names.py
        print("unknown commands      : %d -> name layer (pending_names.py)"
              % len(result["unknown_commands"]))
    if args.export_pending:
        payload = export_pending(result, formulas, args.export_pending, sentences)
        print("pending questions     : %d -> %s"
              % (len(payload["items"]), args.export_pending))

    s = result["summary"]
    print("formulas scanned      : %d" % s["formulas"])
    print("symbols               : %d" % s["symbols"])
    print("decided by LaTeX layer: %d" % sum(s["decided_by_latex_layer"].values()))
    print("left to paper layer   : %d constructions" % s["constructions"])
    print("  settled by the paper: %d" % s["settled_by_paper"])
    print("  settled by answers  : %d" % (s["settled"] - s["settled_by_paper"]))
    print("  open (unanswered)   : %d" % s["open"])
    if result["known_outside_the_table"]:
        print("classified elsewhere  : %s (TeX or an answer knows them, the word "
              "table does not)" % ", ".join(result["known_outside_the_table"]))
    if result["unknown_commands"]:
        print("unknown commands      : %s" % ", ".join(result["unknown_commands"]))
    print("written               : %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
