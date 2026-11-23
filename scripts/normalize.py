#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""normalize.py -- one canonical shape for LaTeX math papers

Pipeline position
-----------------
    paper.tex
        |  [this script]
        v
    paper.norm.tex  +  normalize_stats.json
        |  [latex2nl.py, later]
        v
    sentences / formulas

Why this exists
---------------
A survey of 17 papers (4 in ``out/``, 13 in ``articles/``) found 62 distinct
environment names for 9 functions, 16 spellings of display maths and 29
spellings of lists.  Names are unbounded; functions are not.  This script
collapses the names so the *vocabulary* becomes closed, and reports every
rewrite it made instead of guessing silently.

What it does, in order
----------------------
1. drops comments;
2. reads ``\\newcommand`` / ``\\def`` / ``\\let`` / ``\\DeclareMathOperator``
   from the document and from local ``.sty`` files, then expands them away;
3. decides ``\\ifdefined`` branches (before expansion destroys the test);
4. drops document machinery that carries no propositional content;
5. maps every environment to one of five canonical forms:

       \\begin{statement}{<kind>}      theorem, lemma, definition, proof, ...
       \\begin{display}                every display-math spelling
       \\begin{grid}{cases}            cases / array / matrix
       \\begin{list}{enumerate}        enumerate / itemize / description,
                                       each \\item carrying its rendered label
       \\begin{diagram}{tikzcd}        drawings, body left untouched

   Text blocks are unwrapped, floats and bibliographies are dropped (and
   counted), unknown environments are kept and reported;
6. rewrites spelling variants that render the *same glyph* (``\\le`` ->
   ``\\leq``), strips delimiter sizing (``\\left``, ``\\bigl``, ...) and
   layout commands, and removes thin spaces;
7. checks that the result is a fixed point and that every command left in
   the output is accounted for.

What it does NOT do
-------------------
* It never rewrites mathematics, never evaluates, never reorders arguments.
* Pairs whose glyphs differ are not merged: ``\\epsilon``/``\\varepsilon``,
  ``\\emptyset``/``\\varnothing``, ``\\subset``/``\\subseteq`` stay distinct.
* Ragged list labels that cannot be rendered (``[(Lip)]``, ``\\greek*``) are
  reported in ``labels_unrendered`` rather than replaced by a guess.
* It is not wired into ``latex2nl.py``; it writes a file you can inspect.

Usage
-----
    python3 normalize.py paper.tex -o paper.norm.tex
    python3 normalize.py paper.tex --style local.sty --stats s.json
    python3 normalize.py paper.tex --check        # fixed point + vocabulary
    python3 normalize.py --self-test

Only the Python standard library is used.
"""

from __future__ import annotations

import preserved
import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import issues as _issues
import latex2nl as L  # noqa: E402  (same directory, shares the TeX helpers)

DATA = os.path.join(os.path.dirname(HERE), "data")
ENV_PATH = os.path.join(DATA, "env_classes.json")
ALIAS_PATH = os.path.join(DATA, "format_aliases.json")
LEXICON_PATH = os.path.join(DATA, "latex_lexicon.json")
#: what the installed TeX says each command is (scripts/tex_oracle.py)
TEX_NAMES_PATH = os.path.join(DATA, "tex_names.json")
#: what a person or a model decided about names TeX does not know
ANSWERS_PATH = os.path.join(DATA, "name_answers.json")
#: manual corrections that win over what TeX reported
OVERRIDES_PATH = os.path.join(DATA, "name_overrides.json")

CANONICAL = ("statement", "display", "list", "grid", "diagram")
BEGIN_RE = re.compile(r"\\begin\s*\{([A-Za-z@]+\*?)\}")
#: what may follow ``@`` in a Xy-pic setting: ``@!C``, ``@R=3mm``, ``@1``, ``@+``
XYPIC_DIRECTIVE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789=+-<>!*.,")
LIST_NAMES = {"enumerate", "enumerate*", "itemize", "itemize*", "description"}
COUNTER_CMDS = ("arabic", "alph", "Alph", "roman", "Roman", "greek")
GLYPH_COUNTERS = {"1": "arabic", "i": "roman", "I": "Roman",
                  "a": "alph", "A": "Alph", "*": "arabic"}

#: environments TeX itself provides.  A paper may redefine one of these
#: (``\newenvironment{enumerate}{...}`` to change the spacing); whatever it says,
#: the name still means the same thing, so its body is never expanded.
STANDARD_ENVS = frozenset("""
abstract abstract* align align* alignat alignat* aligned alignedat array
bibliography blockquote bmatrix bmatrix* Bmatrix Bmatrix* cases cases* center
description description* displaymath document enumerate enumerate* enumerate*
eqnarray eqnarray* equation equation* figure figure* filecontents flushleft
flushright gather gather* gathered grid itemize itemize* list longtable math
matrix matrix*
minipage multline multline* pmatrix pmatrix* picture proof quotation quote
quotation scope smallmatrix smallmatrix* split subequations tabular tabular*
table table* tabularx thebibliography theindex titlepage trivlist verbatim
verbatim* verse vmatrix vmatrix* Vmatrix Vmatrix*
""".split())


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_name_map(env_data: dict) -> Dict[str, Tuple[str, Optional[str]]]:
    """environment name -> (class, statement kind)"""
    out: Dict[str, Tuple[str, Optional[str]]] = {}
    for kind, names in env_data["statement_kinds"].items():
        for n in names:
            out[n] = ("statement", kind)
    for cls, names in env_data["classes"].items():
        for n in names:
            out.setdefault(n, (cls, None))
    return out


def build_lexicon() -> set:
    try:
        data = _load(LEXICON_PATH)
    except (OSError, ValueError):
        return set()
    names = set()
    for entries in (data.get("commands") or {}).values():
        names.update(entries)
    return names


INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")


#: \input files that were not found; the CLI records them for a person
MISSING_INPUTS: List[str] = []


#: command names the rule tables themselves handle; a macro body that only
#: contains these (plus the name table) is safe to expand
def _rule_names() -> set:
    names = set()
    for attr in ("BARE_ONE", "BARE_DROP", "NO_ARG_DROP", "TITLE_CMDS",
                 "FORMAT_CMDS", "MATH_FONT_CMDS", "HEADING_CMDS", "ENV_AS_COMMAND"):
        names.update(getattr(L, attr, ()) or ())
    return names


RULE_NAMES = _rule_names() | frozenset("""
cal bf it tt sc sl sf rm mit Bbb bold frak
rule hfill vfill hskip vskip hspace vspace
linebreak newline pagebreak nopagebreak nolinebreak
medskip smallskip bigskip noindent indent par relax
qed qedhere Box square blacksquare diamondsuit
mathfrak mathscr mathds mathsf mathtt mathrm mathbf mathit mathcal mathnormal
overbrace underbrace ensuremath mbox hbox text textrm operatorname mathop mathbin
frac dfrac tfrac sqrt left right big Big bigg Bigg biggg Biggg
EuScript LaTeXoverbrace LaTeXunderbrace longhookrightarrow hookrightarrow
longrightarrow longleftarrow longleftrightarrow mapsto to gets bBigg@
""".split())


def _ensuremath_to_math(text: str) -> str:
    """Keep ``\\ensuremath{...}`` as written.

    normalize used to rewrite it to ``$...$``, which flipped the parity of every
    following dollar and put whole paragraphs inside displays (D10).  latex2nl
    knows whether it is already inside maths, so the decision belongs there: it
    turns the construct into an inline formula in text and leaves it alone inside
    a formula (OPEN.md 1.3).
    """
    return text


def read_source(path: str, seen: Optional[set] = None) -> str:
    """Read a root file and inline every ``\\input``/``\\include`` it reaches."""
    seen = seen if seen is not None else set()
    ap = os.path.abspath(path)
    if ap in seen or not os.path.isfile(ap):
        return ""
    seen.add(ap)
    with open(ap, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    def repl(m):
        name = m.group(1).strip()
        cand = os.path.join(os.path.dirname(ap), name)
        if not cand.endswith(".tex"):
            cand += ".tex"
        if not os.path.isfile(cand) and cand not in MISSING_INPUTS:
            # the paper is incomplete: the text that lives there is lost
            MISSING_INPUTS.append(cand)
        return read_source(cand, seen)

    return INPUT_RE.sub(repl, raw)


def local_styles(root_path: str, text: str) -> List[str]:
    """``.sty`` files named by ``\\usepackage`` that sit next to the paper."""
    base = os.path.dirname(os.path.abspath(root_path))
    out: List[str] = []
    for m in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?"
                         r"\s*\{([^}]+)\}", text):
        for name in m.group(1).split(","):
            path = os.path.join(base, name.strip() + ".sty")
            if os.path.isfile(path) and path not in out:
                out.append(path)
    return out


def augment_macros(macros, text: str) -> None:
    """Extra forms ``MacroTable.parse`` does not read: ``\\providecommand``."""
    spans = L.definition_spans(text, tuple(macros.decl_aliases))
    for m in re.finditer(r"\\providecommand\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?"
                         r"\s*(?:\[(\d)\])?\s*(?:\[([^\]]*)\])?\s*\{", text):
        if L.inside_definition(spans, m.start()):
            continue                     # local to the macro that contains it
        end = L.find_group_end(text, m.end() - 1)
        if end is not None:
            nargs = int(m.group(2) or 0)
            default = m.group(3)
            if default is not None and not nargs:
                nargs = 1
            macros.macros.setdefault(
                m.group(1), (nargs, text[m.end(): end - 1], default))


#: ``(nargs, optional default, begin code, end code)``
EnvDecl = Tuple[int, Optional[str], str, str]


def read_env_decls(text: str) -> Dict[str, EnvDecl]:
    """What the paper declares about its own environments."""
    out: Dict[str, EnvDecl] = {}

    def two_groups(i):
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i >= len(text) or text[i] != "{":
            return None
        j = L.find_group_end(text, i)
        if j is None:
            return None
        k = j
        while k < len(text) and text[k] in " \t\r\n":
            k += 1
        if k >= len(text) or text[k] != "{":
            return None
        e = L.find_group_end(text, k)
        if e is None:
            return None
        return text[i + 1: j - 1], text[k + 1: e - 1]

    spans = L.definition_spans(text)
    for m in re.finditer(r"\\(?:re)?newenvironment\*?\s*\{\s*([A-Za-z@*]+)\s*\}",
                         text):
        if L.inside_definition(spans, m.start()):
            continue
        i = m.end()
        nargs, default = 0, None
        seen = 0
        while True:
            while i < len(text) and text[i] in " \t\r\n":
                i += 1
            if i >= len(text) or text[i] != "[":
                break
            end = L.find_group_end(text, i)
            if end is None:
                break
            inner = text[i + 1: end - 1].strip()
            if seen == 0 and inner.isdigit():
                nargs = int(inner)
            elif seen == 1:
                default = inner
            else:
                break
            seen += 1
            i = end
        g = two_groups(i)
        if g:
            out[m.group(1)] = (nargs, default, g[0], g[1])
    for m in re.finditer(r"\\NewDocumentEnvironment\s*\{\s*([A-Za-z@*]+)\s*\}"
                         r"\s*\{([^{}]*)\}", text):
        g = two_groups(m.end())
        if g:
            out[m.group(1)] = (L.xparse_nargs(m.group(2)), None, g[0], g[1])
    return out


#: how a \newtheorem display name becomes a statement kind.  Anything not here
#: keeps its own name, lowercased: a paper that calls it "Notation" gets the
#: kind "notation" rather than being rounded to something it is not.
THEOREM_KINDS = {
    "theorem": "theorem", "proposition": "proposition", "lemma": "lemma",
    "corollary": "corollary", "definition": "definition", "remark": "remark",
    "remarks": "remark", "example": "example", "examples": "example",
    "claim": "claim", "conjecture": "conjecture", "question": "question",
    "problem": "problem", "assumption": "assumption", "notation": "notation",
    "observation": "observation", "fact": "fact", "exercise": "exercise",
    "hypothesis": "hypothesis", "property": "property", "result": "result",
    "algorithm": "algorithm", "setting": "setting", "recall": "recall",
    "answer": "answer", "solution": "solution", "proof": "proof",
}


def read_theorem_decls(text: str) -> Dict[str, str]:
    """Map each environment the paper declares as theorem-like to its kind.

    ``\\newtheorem{rmk}{Remark}``, ``\\newtheorem{Thm}[theorem]{Theorem}`` and
    ``\\newtheorem*{Claim}{Claim}`` are the three forms; the counter argument is
    optional and carries no content.
    """
    out: Dict[str, str] = {}
    pat = re.compile(r"\\newtheorem\*?\s*\{\s*([A-Za-z@*]+)\s*\}")
    for m in pat.finditer(text):
        i = m.end()
        # an optional [counter] may come before the display name
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i < len(text) and text[i] == "[":
            end = L.find_group_end(text, i)
            if end is not None:
                i = end
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i >= len(text) or text[i] != "{":
            continue
        end = L.find_group_end(text, i)
        if end is None:
            continue
        display = re.sub(r"\\[A-Za-z]+|[{}~]", " ", text[i + 1: end - 1])
        words = re.findall(r"[A-Za-z]+", display)
        kind = THEOREM_KINDS.get((words[0].lower() if words else ""), "")
        if not kind:
            kind = (words[0].lower() if words else m.group(1).lower())
        out[m.group(1)] = kind
    return out


# --------------------------------------------------------------------------
# list label rendering
# --------------------------------------------------------------------------


def _strip_wrapper(text: str, name: str) -> str:
    """``\\textnormal{x}`` -> ``x`` (one level, whole string only)."""
    m = re.match(r"\s*\\" + name + r"\s*\{", text)
    if not m:
        return text
    end = L.find_group_end(text, m.end() - 1)
    if end is None or text[end:].strip():
        return text
    return text[m.end(): end - 1]


def _cut_top_level(value: str) -> str:
    depth = 0
    for i, c in enumerate(value):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "," and depth == 0:
            return value[:i]
    return value


def parse_label_option(opt: Optional[str]) -> Optional[str]:
    """Extract the label template from a list's ``[...]`` argument."""
    if not opt or not opt.startswith("["):
        return None
    inner = opt[1:-1].strip()
    m = re.search(r"(?:^|,)\s*label\s*=\s*", inner)
    if m:
        inner = _cut_top_level(inner[m.end():]).strip()
    elif re.match(r"^[A-Za-z]+\s*=", inner):
        return None                       # leftmargin=..., start=..., etc.
    for junk in ("\\upshape", "\\normalfont", "\\bfseries", "\\itshape"):
        inner = inner.replace(junk, "")
    inner = _strip_wrapper(inner, "textnormal").strip()
    return inner or None


def render_label(template: Optional[str], n: int, depth: int,
                 defaults: dict) -> Tuple[str, bool]:
    """Return ``(label, rendered)``.  ``rendered`` is False when the template
    uses something we refuse to guess at."""
    if template is None:
        template = defaults.get(str(depth), "{arabic}.")
    t = template
    for cmd in COUNTER_CMDS:
        t = t.replace("{" + cmd + "}", "\x00" + cmd + "\x00")
        t = re.sub(r"\\" + cmd + r"\s*\{\s*\*\s*\}", "\x00" + cmd + "\x00", t)
        t = re.sub(r"\\" + cmd + r"\s*\*", "\x00" + cmd + "\x00", t)
    if "\x00" not in t:
        m = re.fullmatch(r"(.*)\(([1iIaA*])\)(.*)", t, re.S)
        if m and "\x00" not in m.group(1) + m.group(3):
            t = m.group(1) + "(\x00" + GLYPH_COUNTERS[m.group(2)] + "\x00)" + m.group(3)
        elif t.strip() == "(*)" or t.strip() == "*":
            t = "(\x00arabic\x00)"
        else:
            return t, False               # e.g. [(Lip)], [(A_1)]
    ok = True
    for cmd in COUNTER_CMDS:
        if "\x00" + cmd + "\x00" not in t:
            continue
        if cmd == "greek":
            ok = False                    # we cannot render greek ordinals
            value = "(%d)" % n            # place the number so the shape stays
        else:
            value = _format_counter(cmd, n)
        t = t.replace("\x00" + cmd + "\x00", value)
    return t, ok


def _format_counter(kind: str, n: int) -> str:
    if kind in ("arabic",):
        return str(n)
    if kind in ("alph", "Alph"):
        if not 1 <= n <= 26:
            return str(n)
        base = chr(ord("a") + n - 1)
        return base if kind == "alph" else base.upper()
    if kind in ("roman", "Roman"):
        vals = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"),
                (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"),
                (5, "v"), (4, "iv"), (1, "i"))
        out, left = [], n
        for v, s in vals:
            while left >= v:
                out.append(s)
                left -= v
        s = "".join(out) or str(n)
        return s if kind == "roman" else s.upper()
    return str(n)


# --------------------------------------------------------------------------
# normaliser
# --------------------------------------------------------------------------


def _load_optional(path: str) -> dict:
    try:
        return _load(path)
    except (OSError, ValueError):
        return {}


#: what a name may be told to do.  ``allow`` keeps it and records the class TeX
#: gives it; ``unwrap`` keeps its argument; ``ignore`` drops the name; ``expand``
#: substitutes a body.
NAME_ACTIONS = ("allow", "font", "unwrap", "ignore", "expand")

#: classes a name may be given.  The first eight are TeX's own math classes,
#: so an answer can always be checked against the class system rather than
#: being free text.
NAME_CLASSES = ("letter", "bigop", "binop", "relation", "open", "close",
                "separator", "const", "font", "named_operator", "fence",
                "accent", "text")


class NameTable:
    """What to do with a command name, from TeX or from an answer.

    Two files feed it: ``data/tex_names.json``, generated by asking the
    installed TeX (``\\meaning`` gives a primitive as itself, a math symbol as
    ``\\mathchar"<class><slot>``, a macro as its body), and
    ``data/name_answers.json``, where a person or a model has answered for names
    TeX does not know.  TeX wins: an answer never overrides it.
    """

    def __init__(self, tex_path: str = TEX_NAMES_PATH,
                 answers_path: str = ANSWERS_PATH,
                 paper: str = "") -> None:
        #: name -> class, for names that stay in the text
        self.allowed: Dict[str, str] = {}
        #: names whose argument is the content
        self.unwrap: set = set()
        #: names that carry nothing
        self.ignore: set = set()
        #: name -> replacement body
        self.expand: Dict[str, str] = {}
        #: name -> where the decision came from: "tex", "answer", "answer:<pid>"
        self.origin: Dict[str, str] = {}
        #: the paper this table was built for, if any
        self.paper = paper
        doc = _load_optional(answers_path)
        #: paper id -> names that paper answered for itself
        self.paper_names: Dict[str, List[str]] = {
            p: sorted(recs or {}) for p, recs in (doc.get("papers") or {}).items()}
        for name, rec in (_load_optional(tex_path).get("names") or {}).items():
            self._add(name, rec, "tex")
        # a manual correction wins over the TeX reading (GAPS M2: --merge
        # refuses to answer for a name TeX decides, so this is its only fix)
        for name, rec in (_load_optional(OVERRIDES_PATH).get("names") or {}).items():
            self._add(name, rec, "override", force=True)
        for name, rec in (doc.get("names") or {}).items():
            self._add(name, rec, "answer")
        # what this paper says about its own notation.  A paper overrides a
        # general answer (``\Z`` may be the integers in one paper and a set in
        # another) but never a decision TeX makes (``\cr`` is a primitive
        # everywhere).
        if paper:
            for name, rec in ((doc.get("papers") or {}).get(paper) or {}).items():
                self._add(name, rec, "answer:" + paper, force=True)

    def _add(self, name: str, rec: dict, origin: str, force: bool = False) -> None:
        prev = self.origin.get(name)
        if prev is not None and (not force or (prev == "tex" and origin != "override")):
            return                                  # TeX decides, not an answer
            # ... unless the decision is in the override table, which exists
            # precisely because TeX can be read wrong (GAPS M2)
        act = (rec or {}).get("action")
        # Validate first: a per-paper entry that is then refused must not have
        # removed the general decision it was going to replace.
        if act in ("allow", "font"):
            pass
        elif act == "unwrap":
            pass
        elif act in ("ignore", "drop"):
            pass
        elif act == "expand":
            body = rec.get("body")
            if not body or re.search(r"\\" + re.escape(name) + r"(?![A-Za-z@])",
                                     body):
                return                              # refuse a self-reference
        else:
            return

        # A paper's own answer replaces the general one: the name must leave
        # every container it was in, or _apply_names applies the general rule
        # last and the paper's decision is silently lost.
        if force and prev is not None:
            self.allowed.pop(name, None)
            self.unwrap.discard(name)
            self.ignore.discard(name)
            self.expand.pop(name, None)

        if act in ("allow", "font"):
            cls = (rec.get("class") or "").strip()
            self.allowed[name] = cls if cls in NAME_CLASSES else "const"
        elif act == "unwrap":
            self.unwrap.add(name)
        elif act in ("ignore", "drop"):
            self.ignore.add(name)
        elif act == "expand":
            self.expand[name] = rec["body"]
        self.origin[name] = origin

    def known(self, name: str) -> bool:
        return (name in self.allowed or name in self.unwrap
                or name in self.ignore or name in self.expand)


#: environments whose name is also a command: TeX's ``\begin{X}`` runs ``\X``,
#: so the pipeline can hand the content to the pass that already knows that
#: command (a section title becomes a heading, a title is dropped).
ENV_AS_COMMAND = ("section", "subsection", "subsubsection", "paragraph",
                  "subparagraph", "chapter", "part")


#: a begin code that prints "Proof" (with or without a full stop or a colon)
PROOF_WORDS = re.compile(r"Proof\b|\\proof\b")
#: an end code that closes a proof
PROOF_END = re.compile(r"\\(?:qed|qedhere|square|blacksquare|Box)\b")


#: a primitive dropped as "ignore" still owns its operand.  Dropping only the
#: name left ``\penalty10000`` as ``10000`` and ``\setbox0=\hbox{..}`` as
#: ``0=`` (GAPS.md M3).
PRIMITIVE_EAT = {
    "penalty": "number", "vskip": "dimen", "hskip": "dimen",
    "kern": "dimen", "vspace": "dimen", "hspace": "dimen",
    "vrule": "dimen", "hrule": "dimen", "mskip": "dimen",
    "setbox": "assign", "sbox": "assign", "savebox": "assign",
    "unitlength": "number",
}
PRIMITIVE_UNWRAP = {"uppercase", "lowercase", "mathchoice",
                    "MakeUppercase", "MakeLowercase"}


def skip_operand(body: str, i: int, dimen: bool = False,
                 assign: bool = False) -> int:
    """Index past a primitive's operand: a number, an optional unit, a group."""
    import re as _re
    n = len(body)
    j = i
    while j < n and body[j] in " \t\n":
        j += 1
    if assign:
        m = _re.match(r"\d+\s*=\s*", body[j:])
        if m:
            j += m.end()
        if j < n and body[j] == "\\":
            mm = _re.match(r"\\[A-Za-z@]+", body[j:])
            if mm:
                j += mm.end()
        while j < n and body[j] in " \t\n":
            j += 1
        if j < n and body[j] == "{":
            close = L.find_group_end(body, j)
            return close or j
        return j
    m = _re.match(r"[-+]?\s*\d+(?:\.\d+)?\s*"
                  r"(?:pt|em|ex|cm|mm|in|bp|pc|sp|dd|cc)?", body[j:])
    if m:
        return j + m.end()
    if dimen:
        m = _re.match(r"\\[A-Za-z@]+", body[j:])
        if m:
            return j + m.end()
    return j


class Normalizer:
    def __init__(self, names: "NameTable" = None, paper: str = "") -> None:
        self.name_to = build_name_map(_load(ENV_PATH))
        # a picture-label list is a drawing, whatever env_classes.json says
        self.name_to.setdefault("labellist", ("diagram", None))
        # drawing packages whose environments are not all in env_classes.json.
        # texdraw alone accounts for 57 commands (\\htext, \\avec, \\clvec,
        # \\setunitscale, \\xymatrixrowsep ...) leaking into the prose of one
        # paper as if they were words.
        for name in ("texdraw", "xypic", "pspicture", "psmatrix", "pspicture*",
                     "asy", "feynman", "circuitikz", "pgfonlayer", "pgfscope"):
            self.name_to.setdefault(name, ("diagram", None))
        self.aliases_doc = _load(ALIAS_PATH)
        self.aliases = self.aliases_doc.get("symbol_aliases", {})
        self.aliases.pop("mathrm", None)          # marker, not an alias
        self.size_cmds = self.aliases_doc.get("size_commands", [])
        self.layout_cmds = self.aliases_doc.get("layout_commands", [])
        self.thin = self.aliases_doc.get("thin_spaces", [])
        self.extra = set(self.aliases_doc.get("extra_commands", [])) | {"nlprepcontent", "accentset", "underaccent", "ifthenelse", "isempty", "equal"}
        self.names = names if names is not None else NameTable(paper=paper)
        self.paper = paper
        self.extra |= set(self.names.allowed) | set(self.names.expand)
        self.defaults = self.aliases_doc.get("list_depth_defaults", {})
        self.lexicon = build_lexicon()
        self.stats: Counter = Counter()
        self.unknown_envs: Counter = Counter()
        self.unknown_cmds: Counter = Counter()
        self.unrendered: Counter = Counter()
        self._diagrams: List[str] = []
        self.env_decls: Dict[str, EnvDecl] = {}
        self._def_cmds: Tuple[str, ...] = self.DEF_CMDS
        self._skip_aliases: set = set()
        #: names the paper declares as a length or a counter with \newlength and
        #: friends: they hold numbers, never words.  One paper's \bshft
        #: (\kern-\bshft) alone accounted for 1,930 off-vocabulary uses.
        self.dimension_names: set = set()

    # -- entry ----------------------------------------------------------
    def normalize(self, tex: str, style_paths: List[str] = ()) -> str:
        """Normalise, then feed the result back until it stops changing.

        Each rewrite can expose work for a later one (a macro expands to an
        environment, an expanded environment inserts a command the cleanup
        passes know).  One pass may therefore not be its own fixed point, and
        the acceptance check requires exactly that.  Iterating here keeps the
        guarantee local instead of leaving it to every caller.  ``self.stats``
        describes the first pass; ``extra_rounds`` says how many more it took.
        """
        out = self._run(tex, style_paths)
        for _ in range(4):
            # the same name table: re-reading the data files per round would let
            # a round disagree with the one before it about the same command
            nxt = Normalizer(self.names)._run(out, style_paths)
            if nxt == out:
                break
            out = nxt
            self.stats["extra_round"] += 1
        else:
            # The cap is a safety net, not an answer.  Text that is still
            # changing after it is wrong somewhere, and the caller must be able
            # to see that without running the whole thing a second time.
            if Normalizer(self.names)._run(out, style_paths) != out:
                self.stats["not_converged"] = 1
        return out

    def _run(self, tex: str, style_paths: List[str] = ()) -> str:
        tex, capsules = preserved.mask(tex)
        tex = L.strip_comments(tex)
        macros = L.MacroTable()
        macros.parse(tex)
        augment_macros(macros, tex)
        macros.parse(tex)                    # picks up declarers named by the paper
        self.env_decls = read_env_decls(tex)
        # the theorem-like environments the paper names for itself:
        # \newtheorem{rmk}{Remark} means \begin{rmk} is a remark, not an
        # unknown container.  These are the largest class of unknown
        # environment in the corpus (rmk alone: 89 papers, 850 uses).
        self.theorem_envs = read_theorem_decls(tex)
        for path in style_paths:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    sty = fh.read()
            except OSError:
                continue
            macros.parse(sty)
            augment_macros(macros, sty)
            macros.parse(sty)
            for name, decl in read_env_decls(sty).items():
                self.env_decls.setdefault(name, decl)
            for name, kind in read_theorem_decls(sty).items():
                self.theorem_envs.setdefault(name, kind)
        self._def_cmds = tuple(self.DEF_CMDS) + tuple(macros.decl_aliases)
        # A declared alphabet is read from its font family, and a family whose
        # name says nothing becomes upright.  Counting them per family keeps
        # that fallback reviewable: it is a guess, not a reading.
        for family, n in getattr(macros, "alphabet_families", {}).items():
            self.stats["alphabet:" + family] += n
        # An alias must not rewrite a name the paper defines itself:
        # lacromay.sty writes
        # ``\newcommand{\mathbb}[1]{\protect\text{$\protect\Bbb{#1}$}}``, so
        # aliasing ``\Bbb`` to ``\mathbb`` recreates the very call that produced
        # it and the text grows by one wrapper per pass.
        self._skip_aliases = {old for old, new in self.aliases.items()
                              if new in macros.macros}
        preamble, body = L.split_document(tex)
        defined = set(macros.macros) | set(macros.decl_aliases) | set(
            re.findall(r"\\(?:def|gdef|edef|xdef|let)\s*\\([A-Za-z@]+)", tex))
        # Start with executed preamble state; body assignments are read in order.
        body = L.resolve_conditionals(body, defined.__contains__,
                                      L.read_switches(preamble))
        # a bibliography written without \begin: one corpus paper writes
        # "\thebibliography{999}" and the whole reference list came out as prose
        body = self._drop_bare_bibliography(body)
        # \let\a=a gives a control sequence a character meaning; the name and the
        # character are both meta, and leaving them put a bare "a" in the text
        body = re.sub(r"\\let\s*\\[A-Za-z@]+\s*=\s*[^\\\s]", " ", body)
        # before the definitions are dropped: the names they declare are needed
        # to know that a later \bshft is a length, not a word
        for m in re.finditer(r"\\(?:newlength|newdimen|newskip|newcount|newtoks|"
                             r"newsavebox|newbox|newif|chardef|mathchardef)"
                             r"\s*\\?\{?([A-Za-z@]+)\}?", tex):
            self.dimension_names.add(m.group(1))
        body = self._drop_definitions(body)
        # a paper macro whose fully expanded body introduces names that nothing
        # knows (no table, no declaration, no rule) is NOT expanded: expanding it
        # hands private font/graphics commands to the text, and the name itself is
        # the only thing downstream can ask about (OPEN-PROBLEMS.md 7.1 item 2)
        macros.kept_unknown = self._macros_with_unknown_names(macros)
        self.kept_macros = {name: (macros.macros[name][1] or "")[:300]
                            for name in macros.kept_unknown}
        for kept in sorted(macros.kept_unknown):
            self.stats["macro_kept_unknown:" + kept] += 1
        body = macros.expand(body)
        body = L.resolve_conditionals(body, defined.__contains__)
        body = self._ifthenelse(body)
        # before the cleanup passes: the code the paper's own environment
        # inserts must go through the same treatment as the rest
        body = self._expand_declared_envs(body, macros)
        # Preserve semantic side content before any layout/name cleanup. Shield
        # newly created capsules in this pass as well as ones from prior passes.
        body = self._preserve_floats(body)
        body = self._preserve_notes(body)
        body, new_capsules = preserved.mask(body, 'NEW')
        body, opaque_math = self._protect_opaque_math(
            body, known=lambda name: self._known_name(name, macros))
        body = L.drop_command_all_args(body, L.META_CMDS)
        body = L.drop_command_all_args(body, self.TITLE_CMDS)
        body = self._drop_bare(body)
        # _drop_bare leaves a marker where a font switch was; this unwraps the
        # group that held it
        body = self._font_groups(body)
        body = self._bracket_displays(body)
        body = self._ifthenelse(body)
        body = self._stash_labellist(body)
        body = self._stash_xymatrix(body)
        body = self._envs(body, 0)
        body = self._expand_bracket_display(body, tex)
        body = self._split_intertext(body)
        body = self._drop_unclosed_diagrams(body)
        # before the name table: a declared class must not be decided by an
        # answer (that is how \step became "ignore")
        body = self._part_markers(body)
        body = self._commands(body)
        body = self._restore_diagrams(body)
        body = preserved.restore(self._tidy(body), opaque_math, 'OPAQUEMATH')
        body = preserved.restore(body, new_capsules, 'NEW')
        return preserved.restore(body, capsules)

    # -- document-level material ---------------------------------------
    # Partial registry of transformations actually implemented below. This is
    # not an acceptance whitelist: all other formula names can stay opaque.
    MATH_REPLACEMENTS = {'nicefrac': r'\frac', 'mfrac': r'\frac',
                         'coloneq': ' :=', 'coloneqq': ' :=', 'colonequals': ' :=',
                         'eqqcolon': ' =:'}
    MATH_FIRST_ARG = ('scalerel',)
    MATH_UNWRAP = ('mathlarger',)
    MATH_FENCES = {'abs': ('|', '|'), 'norm': (r'\|', r'\|')}
    @staticmethod
    def _protect_opaque_math(body: str, offset: int = 0, known=None):
        """Shield whole unknown formulas from name/layout cleanup this pass."""
        records, out, pos = [], [], 0
        for span in L.find_math_spans(body, clean=False):
            unknown = L.unknown_commands(span.body)
            if known:
                unknown = [cmd for cmd in unknown if not known(cmd[1:])]
            if not unknown:
                continue
            # Known drawings have their own lossless transport and previews.
            if re.search(r'\\(?:xymatrix\b|begin\{(?:diagram|tikzcd|tikzpicture|xy)\})', span.body):
                continue
            out.append(body[pos:span.start])
            records.append(body[span.start:span.end])
            out.append('\ue200OPAQUEMATH' + str(offset + len(records)-1) + '\ue201')
            pos = span.end
        out.append(body[pos:])
        return ''.join(out), records

    #: commands that take no argument.  Only the name goes: eating a following
    #: ``{..}`` or ``[..]`` deletes content (``\noindent{\bf Step 1.}`` lost the
    #: heading, ``\quad [x]`` lost the interval).
    BARE_DROP = (
        "maketitle", "tableofcontents", "appendix", "frontmatter",
        "mainmatter", "printindex", "listoffigures", "listoftables",
        "linebreak", "nolinebreak", "null", "strut", "bigskip", "medskip",
        "smallskip", "noindent", "indent", "centering", "raggedright",
        "raggedleft", "selectfont", "normalsize", "small", "footnotesize",
        "scriptsize", "tiny", "large", "Large", "LARGE", "huge", "Huge",
        "bfseries", "itshape", "rmfamily", "sffamily", "ttfamily", "mdseries",
        "upshape", "scshape", "slshape", "rm", "it", "bf", "em", "sf", "tt",
        "sc", "sl", "cal", "bold", "hfil", "vfil", "normalfont", "global",
        "relax", "and", "quad", "qquad", "endgraf", "appendices", "newpage",
        "clearpage", "samepage", "protect", "nonumber", "notag", "allowbreak",
        "ignorespaces", "unskip", "qed", "qedhere", "endproof", "endproofqed",
        "boldmath", "unboldmath", "fussy", "sloppy", "raggedbottom",
        "flushbottom",
        # a spacing macro: it prints nothing, and 79 of them were still sitting
        # in sentence text of the 20-paper sample
        "xspace",
    )

    #: commands whose whole argument list is document machinery
    BARE_ONE = (
        "vspace", "hspace", "rule", "phantom", "hphantom", "vphantom",
        "footnote", "thanks", "color", "index", "glossary", "thispagestyle",
        "pagestyle", "bibliography", "bibliographystyle", "refstepcounter",
        "stepcounter",
        # package configuration: it sets up a package, it says nothing
        "ytableausetup", "setunitscale", "usetikzlibrary", "hypersetup",
        "geometry", "definecolor", "ctikzset", "tikzset", "pgfplotsset",
        # xy-pic spacing inside a drawing, and the typographic leftovers that
        # still reached the prose: \Needspace{3\baselineskip} kept the word
        # "baselineskip", \symbolfootnote[1]{..} kept its argument
        "xymatrixrowsep", "xymatrixcolsep", "symbolfootnote", "Needspace",
        "footnotetext",
        "dedicatory",
        # a LAMS-TeX picture is a run of \special{..} commands; each one is
        # drawing instructions, and leaving them in the text produced sentences
        # that were nothing but coordinates
        "special",
        # a language switch prints nothing of its own; its argument is a name
        "selectlanguage",
        # spacing commands are listed in NO_ARG_DROP, not here: they take no
        # arguments, and eating the group after them lost content (\!{+},
        # original delimiters are delimiters: the name goes, the mark stays
        "originalleft", "originalright",
        # bibliography machinery: an entry is a reference, not a proposition
        "printbibliography", "bibliographystyle", "nocite",
    )
    #: front-matter commands: their arguments are names and addresses, never a
    #: proposition, so the whole argument list goes
    TITLE_CMDS = ("title", "author", "date", "thanks", "address", "email",
                  "affiliation", "affil", "orcid", "keywords", "subjclass",
                  "subjectclass", "preprint", "dedication", "pacs", "msc",
                  "AMSclass")

    #: one-argument commands whose braces hold the content we want to keep
    UNWRAP_ONE = ("enquote", "emph", "textnormal",
                  "reflectbox", "ThisStyle", "ooalign")

    #: declarers whose argument is a *name*, not a group: \newlength\bshft,
    #: \newif\if@submit.  Consuming a following control sequence as a "second
    #: argument" is how \newif\if@submit used to leave "@submit" in the prose.
    NAME_ONLY_DECL = ("newlength", "newdimen", "newskip", "newcount", "newtoks",
                      "newsavebox", "newbox", "newif", "chardef",
                      "mathchardef", "newfont")

    #: commands that take no argument at all: the name goes, the text after it
    #: stays.  ``\!{+}`` used to lose the ``+`` by being treated as a command
    #: with one argument.
    NO_ARG_DROP = ("medspace", "negmedspace", "thickspace", "negthickspace",
                   "enspace", "thinspace", "negthinspace", "!", "nobreak",
                   "allowbreak", "relax", "protect")

    #: TeX's own plumbing that is spelled with an @, listed rather than matched
    #: by pattern because ``\xymatrix@R=5pt`` is a command and an option
    PLUMBING = ("m@th", "m@the", "if@compatibility", "if@tempswa", "tf@size",
                "sf@size", "ssf@size", "tagform@", "y@ng", "y@@optargI",
                "to@size", "p@", "ex@", "@tempskipa")

    def _known_name(self, name: str, macros) -> bool:
        """Known to the word table, the name table, a declaration, or a rule."""
        if name in self.lexicon or name in self.extra or name in self.dimension_names:
            return True
        if (self.names.known(name) or name in self.names.allowed
                or name in self.names.expand or name in self.names.unwrap
                or name in self.names.ignore):
            return True
        if name in macros.macros or name in self.theorem_envs or name in self.name_to:
            return True
        if name in PRIMITIVE_EAT or re.fullmatch(r'SavedStyle[A-Za-z]*', name):
            return True
        # the rule tables on the Normalizer itself: RULE_NAMES was built from
        # latex2nl's globals and missed BARE_ONE and friends, which is why \color
        # looked unknown and its whole macro was kept (OPEN.md 1.0)
        for attr in ("BARE_ONE", "BARE_DROP", "NO_ARG_DROP", "TITLE_CMDS",
                     "FORMAT_CMDS", "MATH_FONT_CMDS", "HEADING_CMDS",
                     "ENV_AS_COMMAND", "UNWRAP_ONE", "PLUMBING",
                     "MATH_REPLACEMENTS", "MATH_FIRST_ARG", "MATH_UNWRAP", "MATH_FENCES"):
            if name in (getattr(self, attr, ()) or ()):
                return True
        return name in RULE_NAMES

    def _macros_with_unknown_names(self, macros) -> set:
        """Macros whose expansion would hand an unknown command to the text."""
        kept: set = set()
        for name, (nargs, body, _default) in macros.macros.items():
            if "@" in name or not body:
                continue
            try:
                expanded = macros.expand(body, rounds=3)
            except Exception:                     # noqa: BLE001 - cyclic bodies
                continue
            names = set(re.findall(r"\\([A-Za-z@]+)", expanded))
            if any(not self._known_name(n, macros) for n in names):
                kept.add(name)
        return kept

    def _preserve_floats(self, body: str) -> str:
        out, pos = [], 0
        for m in L.ENV_TOKEN_RE.finditer(body):
            if m.start() < pos or m.group(1) != "begin":
                continue
            env = m.group(2)
            cls, _ = self.name_to.get(env, self.name_to.get(env.rstrip('*'), (None, None)))
            if cls != "float":
                continue
            end = L.find_env_end(body, m.end(), env)
            stop = end.end() if end else len(body)
            raw = body[m.start():stop]
            captions = []
            for cm in re.finditer(r"\\caption\*?\s*(?:\[[^\]]*\]\s*)?\{", raw):
                ce = L.find_group_end(raw, cm.end()-1)
                if ce:
                    captions.append(raw[cm.end():ce-1])
            out.append(body[pos:m.start()])
            out.append(preserved.pack("float", raw, environment=env,
                       status="pending", reason="layout_not_parsed" if end else "unclosed_layout",
                       previews=captions, source_stage="expanded_body", offset=m.start()))
            self.stats["content_preserved:" + env] += 1
            pos = stop
        out.append(body[pos:])
        return ''.join(out)

    def _preserve_notes(self, body: str) -> str:
        pat = re.compile(r"\\(footnote|footnotetext|symbolfootnote|marginpar)(?![A-Za-z@])\s*(?:\[[^\]]*\]\s*)?\{")
        out, pos = [], 0
        for m in pat.finditer(body):
            if m.start() < pos:
                continue
            end = L.find_group_end(body, m.end()-1)
            if end is None:
                out.append(body[pos:m.start()])
                out.append(preserved.pack(m.group(1), body[m.start():],
                           status='pending', reason='unclosed_note',
                           source_stage='expanded_body', offset=m.start()))
                pos = len(body)
                break
            before = body[pos:m.start()]
            # A note immediately after a full stop belongs to that sentence.
            tail = re.search(r"([.!?。！？])\s*$", before)
            punctuation = tail.group(1) if tail else ""
            out.append(before[:tail.start()] if tail else before)
            out.append(preserved.pack(m.group(1), body[m.start():end],
                       body=body[m.end():end-1], status="ready",
                       source_stage="expanded_body", offset=m.start()))
            out.append(punctuation)
            self.stats["content_preserved:" + m.group(1)] += 1
            pos = end
        out.append(body[pos:])
        return "".join(out)

    def _drop_bare(self, body: str) -> str:
        body = self._preserve_notes(body)
        # TeX's own plumbing: these are package internals and never a word of
        # the paper (11,493 uses of one \if@compatibility).  Matched by name,
        # not by pattern: \xymatrix@R=5pt is a command followed by an option,
        # not a name that contains @.  \@startsection is left alone -- its whole
        # argument list is dropped by _drop_section_machinery, which needs the
        # name still there to find it.
        body = re.sub(r"\\@(?!startsection)[A-Za-z@]*(?![A-Za-z@])", " ", body)
        # \SavedStyleL and friends belong to the mathstyle package: the style is
        # a placeholder for how the maths is drawn, the symbol next to it is the
        # content
        body = re.sub(r"\\SavedStyle[A-Za-z]*(?![A-Za-z@])", " ", body)
        # \m@th followed straight by a letter is the same spacing switch with
        # the letter glued on: TeX reads it as one name, and the paper's macro
        # bodies produce \m@thV, \m@thT, \m@thB ...
        body = re.sub(r"\\m@th[A-Za-z]*(?![A-Za-z@])", " ", body)
        # a length the paper declared: \bshft is a number, not a word
        for name in self.dimension_names:
            body = re.sub(r"\\" + re.escape(name) + r"(?![A-Za-z@])", " ", body)
        for cmd in self.PLUMBING:
            body = re.sub(r"\\" + re.escape(cmd) + r"(?![A-Za-z@])", " ", body)
        for cmd in self.NO_ARG_DROP:
            body = self._drop_arg_cmd(body, cmd, limit=0)
        for cmd in self.BARE_ONE:
            # the name of the list says "bare one": one group, not three.
            # With three, {\color{red}{Main Theorem}} lost "Main Theorem".
            before = len(body)
            body = self._drop_arg_cmd(body, cmd, limit=1)
            gone = before - len(body)
            if gone and cmd == "footnote":
                # content leaves the paper here: the amount has to be visible
                # until a person decides whether to keep it (GAPS.md D1)
                _issues.record("drop:footnote", "normalize._drop_bare",
                               where="", size=gone)
        for cmd in self.BARE_DROP:
            if cmd in self.FONT_SWITCH_SET:
                # the marker carries a one-letter code for which switch it was:
                # _font_groups has to put the right font command back, and by
                # then the name itself is gone.  A letter, never the name, or a
                # group that survives this pass would show "normalsize" in prose.
                body = re.sub(r"\\" + cmd + r"(?![A-Za-z@])",
                              self.FONT_MARK + self.FONT_CODES.get(cmd, "z"),
                              body)
            else:
                body = re.sub(r"\\" + cmd + r"(?![A-Za-z@])", " ", body)
        # \nicefrac{a}{b} is a fraction; \frac is its closest equivalent and
        # both arguments survive
        for name, replacement in self.MATH_REPLACEMENTS.items():
            body = re.sub(r'\\' + re.escape(name) + r'(?![A-Za-z@])',
                          lambda _m, value=replacement: value, body)
        # \scalerel*{delim}{ref}: the delimiter is the content
        for cmd in self.MATH_FIRST_ARG:
            body = self._unwrap_first_arg(body, cmd)
        # absolute value and norm, for a paper that never defined the name
        for name, (left, right) in self.MATH_FENCES.items():
            body = self._wrap_arg(body, name, left, right)
        # a delimiter drawn at a chosen size is the delimiter
        body = self._drop_size_name(body)
        # \ifnum\mathgroup=\symbold: a test of which maths font is current.
        # The whole conditional goes, keeping the first branch -- replacing only
        # the test left ``\else B\fi`` behind, which is worse than not firing.
        body = self._resolve_mathgroup_tests(body)
        # the typeset branch of \texorpdfstring is the content
        body = self._keep_first_of_two(body, "texorpdfstring")
        # \hyperref[label]{text}: the text is the content, the label is the link
        # hyperref takes [label] then {text}: one brace group is enough
        body = self._unwrap_last_arg(body, "hyperref", min_groups=1)
        # a marginal note, an edit tag and a proof heading print text that is
        # not part of the proposition; the tag's number is display metadata
        # a \tag{3} is the equation's number: turn it into a label before the
        # drop below eats it, so the formula record keeps it (AUDIT.md 36)
        body = re.sub(r"\\tag\*?\s*\{([^{}]*)\}", r"\\taglabel{\1}", body)
        for cmd, limit in (("marginpar", 3),):
            body = self._drop_arg_cmd(body, cmd, limit=limit)
        # \proof{Of Lemma 2}: the title says what is being proved, so keep the
        # words and drop only the command (GAPS.md D3)
        body = self._unwrap_first_arg(body, "proof")
        # a bare \proof (no group) is the end-of-proof mark
        body = re.sub(r"\\proof(?![A-Za-z@])\s*(?!\{)", " ", body)
        # \ensuremath{x} prints x in math mode; unwrapping it left math commands
        # as text, and {S_\bullet} then kept its braces in the sentence
        body = _ensuremath_to_math(body)
        # \textcolor{blue}{Let it be.} used to leave "blue" glued to the next
        # word: the name is dropped but the colour argument stayed (OPEN.md 1.0)
        body = re.sub(r"\\textcolor\s*\{[^{}]*\}\s*", " ", body)
        # a bullet left in the text (\Sdot expands to {\ensuremath{S_\bullet}})
        # is a symbol, not a command name
        body = re.sub(r"\\bullet(?![A-Za-z@])", "\u2022", body)
        # \textnumero prints the numero sign; \bqed is a paper's proof end
        body = re.sub(r"\\textnumero(?![A-Za-z@])", "\u2116", body)
        body = re.sub(r"\\bqed(?![A-Za-z@])", " ", body)
        # ellipses are symbols, and they were 400 uses of \dots in prose
        body = re.sub(r"\\[lc]?dots(?![A-Za-z@])", "\u2026", body)
        body = re.sub(r"\\vdots(?![A-Za-z@])", "\u22ee", body)
        body = re.sub(r"\\ddots(?![A-Za-z@])", "\u22f1", body)
        # \theequation, \thesection: a counter's value, which the reference
        # table already carries.  The counter names are listed, because \the is
        # a prefix of \theorem and \thebibliography.
        body = re.sub(r"\\the(?:equation|eqnarray|section|subsection|"
                      r"subsubsection|figure|table|chapter|part|page|"
                      r"appendix|footnote|enumi|enumii|enumiii|enumiv|"
                      r"theorem|lemma|definition|proposition|corollary|"
                      r"remark|example)(?![A-Za-z@])", " ", body)
        # text ligatures that arrive as commands
        body = re.sub(r"\\ae(?![A-Za-z@])", "\u00e6", body)
        body = re.sub(r"\\oe(?![A-Za-z@])", "\u0153", body)
        body = re.sub(r"\\aa(?![A-Za-z@])", "\u00e5", body)
        body = re.sub(r"\\o(?![A-Za-z@])", "\u00f8", body)
        # \SREF34 is a section reference the paper never defines; the section
        # sign is what it prints, and \S is the same sign
        body = re.sub(r"\\SREF(?![A-Za-z@])", "\u00a7", body)
        body = re.sub(r"\\S(?![A-Za-z@])", "\u00a7", body)
        # a vertical rule with its dimensions carries no words
        body = self._drop_arg_cmd(body, "vrule", limit=0)
        body = re.sub(r"\\nobreak(?![A-Za-z@])", " ", body)
        # Mathematical accents carry identity (e.g. W with a star is not W).
        # Keep the constructor and both arguments intact.
        # a bigger version of something is that thing
        for cmd in self.MATH_UNWRAP:
            body = self._unwrap_one(body, cmd)
        # boxes: keep the content, drop the size.  Early, because a drawing
        # inside a box must still be reachable by the environment pass.
        for cmd in ("scalebox", "resizebox", "raisebox", "rotatebox"):
            body = self._unwrap_last_arg(body, cmd)
        return body

    @staticmethod
    def _drop_arg_cmd(body: str, cmd: str, limit: int = 3) -> str:
        """Drop ``\\cmd`` and the groups attached to it, nesting included.

        A regex with ``[^{}]*`` stops at the first nested brace, so
        ``\\footnote{see \\emph{x}}`` used to leave the command name behind.
        """
        pat = re.compile(r"\\" + cmd + r"(\*?)(?![A-Za-z@])\s*")
        pos = 0
        while True:
            m = pat.search(body, pos)
            if not m:
                return body
            i = m.end()
            n = 0
            while n < limit:
                j = i
                while j < len(body) and body[j] in " \t\n":
                    j += 1
                if j < len(body) and body[j] in "{[":
                    end = L.find_group_end(body, j)
                    if end is None:
                        break
                    i = end
                    n += 1
                    continue
                break
            if n == 0:
                i = m.end()                    # nothing to drop: just the name
            body = body[:m.start()] + " " + body[i:]
            pos = m.start()

    #: definition commands.  Their effect is already in the macro table; left in
    #: the body they would be re-expanded on a second pass, which is how the
    #: 1304.6180 corruption happened (``\def\Ctilde{...}`` expanded inside the
    #: definition of ``\Ctilde`` itself).
    DEF_CMDS = ("newcommand", "renewcommand", "providecommand",
                "DeclareMathOperator", "DeclareRobustCommand", "newtheorem",
                "newenvironment", "renewenvironment", "newif", "newlength",
                "newcount", "newcounter", "newdimen", "newbox", "newsavebox",
                "newtoks",
                "DeclareMathSymbol", "DeclareMathAlphabet", "DeclareSymbolFont",
                "DeclareSymbolFontAlphabet",
                "DeclareMathDelimiter", "DeclareMathAccent", "newfont",
                "NewDocumentCommand", "RenewDocumentCommand",
                "DeclareDocumentCommand", "ProvideDocumentCommand",
                "DeclarePairedDelimiter", "newtheoremstyle", "theoremstyle",
                "NewDocumentEnvironment", "RenewDocumentEnvironment",
                "newtcolorbox", "newmdenv", "lstnewenvironment",
                "newfloat", "newlist", "newcases")

    def _drop_bare_bibliography(self, body: str) -> str:
        """Drop a bibliography, complete or opened without ``\\begin``.

        ``\\thebibliography{999}`` is a typo for ``\\begin{thebibliography}{999}``,
        but the missing ``\\begin`` meant the environment table never saw it and
        every ``\\bibitem`` plus the reference text reached the sentence stream.
        The complete form is cut here too: leaving the decision to the
        environment pass let one paper's markers be cleaned as nesting leftovers
        while its entries stayed.
        """
        body, k = re.subn(
            r"\\begin\{thebibliography\}.*?\\end\\s*\\{thebibliography\}",
            " ", body, flags=re.S)
        if k:
            self.stats["bibliography_dropped"] += k
        m = re.search(r"\\thebibliography(?![A-Za-z@])\s*(?:\{[^{}]*\})?", body)
        if not m:
            return body
        if body[max(0, m.start() - 12): m.start()].rstrip().endswith("\\begin{"):
            return body                      # a real environment, handled below
        end = re.search(r"\\end\\s*\\{thebibliography\}", body[m.end():])
        cut = m.end() + end.end() if end else len(body)
        self.stats["bibliography_bare"] += 1
        return body[:m.start()] + " " + body[cut:]

    def _drop_definitions(self, body: str) -> str:
        for _ in range(6):
            before = body
            for cmd in self._def_cmds:
                # ``(?![A-Za-z])``: \newcount must not match inside \newcounter,
                # which used to leave ``er{claim}`` in the text
                pat = re.compile(r"\\" + re.escape(cmd) + r"\*?(?![A-Za-z@])\s*")
                pos = 0
                while True:
                    m = pat.search(body, pos)
                    if not m:
                        break
                    i = m.end()
                    # How many arguments to swallow is a property of the
                    # declarer, not of the text: \newcommand takes two groups,
                    # \newlength takes one control sequence.  Swallowing "up to
                    # six of anything" ate the \section or \begin that
                    # followed a definition.
                    if cmd in self.NAME_ONLY_DECL:
                        j = i
                        while j < len(body) and body[j] in " \t\n":
                            j += 1
                        m2 = re.match(r"\\[A-Za-z@]+", body[j:])
                        if m2:
                            i = j + m2.end()
                    else:
                        # ``\newcommand\name{..}`` names the macro without
                        # braces; without this the definition was kept, and its
                        # body stayed in the text as if it were prose
                        j = i
                        while j < len(body) and body[j] in " \t\n":
                            j += 1
                        m_name = re.match(r"\\[A-Za-z@]+", body[j:])
                        if m_name:
                            i = j + m_name.end()
                        # \newenvironment takes {name}, [nargs], {begin},
                        # {end}: swallowing two left both code halves in the
                        # text, where "{ Proof of #1.}{ }" became a sentence
                        most = 4 if cmd in ("newenvironment", "renewenvironment") \
                            else 3 if cmd.startswith("newtheorem") else 2
                        n = 0
                        while n < most:                # groups at most
                            j = i
                            while j < len(body) and body[j] in " \t\n":
                                j += 1
                            if j < len(body) and body[j] in "{[":
                                end = L.find_group_end(body, j)
                                if end is None:
                                    break
                                i = end
                                n += 1
                                continue
                            break
                    self.stats["definition_dropped:" + cmd] += 1
                    body = body[:m.start()] + " " + body[i:]
                    pos = m.start()
            for cmd in ("def", "gdef", "edef", "xdef"):
                pat = re.compile(r"\\" + cmd + r"(?![A-Za-z@])\s*\\[A-Za-z@]+")
                m = pat.search(body)
                while m:
                    i = m.end()
                    while i < len(body) and body[i] in " \t\n":
                        i += 1
                    if i < len(body) and body[i] == "{":
                        end = L.find_group_end(body, i)
                        i = end if end is not None else i
                    else:                              # \def\X#1{...}
                        j = body.find("{", i, i + 200)
                        end = L.find_group_end(body, j) if j != -1 else None
                        i = end if end is not None else i
                    self.stats["definition_dropped:" + cmd] += 1
                    body = body[:m.start()] + " " + body[i:]
                    m = pat.search(body)
            m = re.search(r"\\let(?![A-Za-z@])\s*\\[A-Za-z@]+\s*=?\s*", body)
            while m:
                i = m.end()
                if i < len(body) and body[i] == "\\":
                    i = L.skip_control_sequence(body, i)
                self.stats["definition_dropped:let"] += 1
                body = body[:m.start()] + " " + body[i:]
                m = re.search(r"\\let(?![A-Za-z@])\s*\\[A-Za-z@]+\s*=?\s*", body)
            if body == before:
                return body
        return body

    # -- environments ---------------------------------------------------
    XYMATRIX_RE = re.compile(r"\\xymatrix\*?(?![A-Za-z])")
    IFTHEN_RE = re.compile(r"\\ifthenelse\s*")

    #: tests whose value is visible in the source, without running TeX
    ISEMPTY_RE = re.compile(r"\\isempty\s*\{(.*)\}\s*\Z", re.S)
    EQUAL_RE = re.compile(r"\\equal\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\Z", re.S)

    def _decide_test(self, test: str) -> Optional[int]:
        """1 or 2 when the test can be evaluated here, else None."""
        t = test.strip()
        m = self.EQUAL_RE.match(t)
        if m:
            return 1 if m.group(1).strip() == m.group(2).strip() else 2
        m = self.ISEMPTY_RE.match(t)
        if m:
            return 1 if not m.group(1).strip() else 2
        return None

    def _ifthenelse(self, body: str) -> str:
        """``\\ifthenelse{test}{yes}{no}`` -> the branch the test selects.

        ``\\isempty{..}`` and ``\\equal{..}{..}`` are read from the source;
        otherwise the full conditional is preserved as pending content.
        Always taking the first branch dropped real content: 358 of 969 calls
        in the sample choose the second one.

        There is no cap on the number of calls.  There used to be one (400 per
        pass).  In 1801.04845 a reference macro expands to a block of two dozen
        at every use, far more than the cap in total, so the tail was left in
        the output and a second run of the normaliser picked it up -- which is
        how the paper failed the fixed-point check.  The loop ends on its own:
        every step either removes one ``\\ifthenelse`` or moves past one.
        """
        pos = 0
        while True:
            m = self.IFTHEN_RE.search(body, pos)
            if not m:
                break
            i = m.end()
            args: List[str] = []
            for _k in range(3):
                while i < len(body) and body[i] in " \t\n":
                    i += 1
                if i >= len(body) or body[i] != "{":
                    break
                end = L.find_group_end(body, i)
                if end is None:
                    break
                args.append(body[i + 1: end - 1])
                i = end
            if len(args) < 3:
                pick = preserved.pack('conditional', body[m.start():], status='pending',
                                      reason='malformed_ifthenelse', conditional=r'\ifthenelse')
                body = body[:m.start()] + pick
                break
            choice = self._decide_test(args[0])
            pick = args[choice] if choice is not None and len(args) > choice \
                else args[1]
            if choice is not None:
                self.stats["ifthenelse_decided"] += 1
            else:
                self.stats["ifthenelse_pending"] += 1
                pick = preserved.pack('conditional', body[m.start():i], status='pending',
                                      reason='undecidable_test', conditional=r'\ifthenelse')
            body = body[:m.start()] + " " + pick + body[i:]
            pos = m.start()                    # the branch may hold another one
        return body

    def _stash_labellist(self, body: str) -> str:
        """``\\labellist \\pinlabel{..} at 61 87 ... \\endlabellist`` draws.

        The ``pinlabel`` package uses commands, not an environment, so the
        environment pass never sees it and 435 ``\\pinlabel`` calls of one paper
        were counted as unknown words in the prose.
        """
        if "\\labellist" not in body:
            return body
        out: List[str] = []
        pos = 0
        for m in re.finditer(r"\\labellist(?![A-Za-z@])", body):
            if m.start() < pos:
                continue
            end = body.find("\\endlabellist", m.end())
            if end < 0:
                continue
            out.append(body[pos: m.start()])
            out.append(self._stash_diagram("pinlabel", body[m.end(): end]))
            pos = end + len("\\endlabellist")
        out.append(body[pos:])
        return "".join(out)

    def _stash_xymatrix(self, body: str) -> str:
        """``\\xymatrix@C=30pt{..}`` is a command, not an environment, but it
        draws.  The optional ``@[A-Za-z]=<value>`` settings sit between the name
        and the body, so they have to be skipped before the brace."""
        out: List[str] = []
        pos = 0
        for m in self.XYMATRIX_RE.finditer(body):
            if m.start() < pos:
                continue
            i = m.end()
            while i < len(body):
                while i < len(body) and body[i] in " \t\n":
                    i += 1
                if i >= len(body) or body[i] != "@":
                    break
                j = i + 1
                if j < len(body) and body[j] in "{(":
                    # ``@(..)`` / ``@{..}``: a setting with its own group
                    if body[j] == "{":
                        g = L.find_group_end(body, j)
                    else:
                        g = body.find(")", j)
                        g = None if g < 0 else g + 1
                    if not g:
                        break
                    i = g
                    continue
                # ``@!C``, ``@R=3mm``, ``@1``, ``@+``, ``@*{}``: a short
                # directive.  ``@1`` (row/column separation, in em) used to stop
                # here, so 307 ``\ar`` of 10 papers were never treated as a
                # drawing and their targets leaked into the text.
                k = j
                while k < len(body) and body[k] in XYPIC_DIRECTIVE:
                    k += 1
                if k == j:
                    break
                i = k
            if i >= len(body) or body[i] != "{":
                continue
            end = L.find_group_end(body, i)
            if end is None:
                continue
            out.append(body[pos: m.start()])
            out.append(self._stash_diagram("xymatrix", body[i + 1: end - 1]))
            pos = end
        out.append(body[pos:])
        return "".join(out)

    def _expand_declared_envs(self, body: str, macros) -> str:
        """Expand a paper-declared environment when its begin code opens one.

        Three rules, decided by counting ``\\begin`` / ``\\end`` in the two code
        halves (see ``L.env_stack``):

        1. the name is standard (``proof``, ``enumerate``...): never expand,
           whatever the paper redefined it to;
        2. the begin code leaves an environment open and the end code closes
           exactly that one: expand, so the structure becomes visible;
        3. anything else: keep the wrapper for a later pass to interpret.
        """
        if not self.env_decls:
            return body
        for _ in range(6):
            before = body
            for name in list(self.env_decls):
                if name in STANDARD_ENVS:
                    continue
                body = self._expand_env_uses(body, name, macros)
            if body == before:
                break
        return body

    def _as_proof(self, body: str, name: str) -> str:
        """Rewrite a proof-like wrapper into the canonical statement block.

        The begin code is not expanded: it prints a heading ("Proof of #1.") and
        that heading is not content.  What the reader needs is the title the
        paper passed in, so the first argument is kept as plain words.
        """
        nargs, default, beg, end_code = self.env_decls[name]
        groups = "".join(r"\s*\{([^{}]*)\}" for _ in range(nargs))
        begin = re.compile(r"\\begin\s*\{" + re.escape(name) + r"\}" + groups)
        end = re.compile(r"\\end\s*\{" + re.escape(name) + r"\}")

        def head(m):
            args = m.groups()
            out = "\\begin{statement}{proof}"
            if args and "#1" in beg:
                out += "  " + (args[0] or "").strip()
            return out

        body = begin.sub(head, body)
        body = end.sub(lambda m: "\\end{statement}", body)
        return body

    def _expand_env_uses(self, body: str, name: str, macros) -> str:
        nargs, default, beg, end = self.env_decls[name]
        b = macros.expand(beg, 4)
        e = macros.expand(end, 4)
        # The end code alone always looks like a stray close (``\end{theorem}``
        # closes what the begin code opened), so the two halves have to be
        # examined together.  Reading them apart made rule 2 fire zero times.
        opened, b_stray = L.env_stack(b)
        whole, w_stray = L.env_stack(b + "\x00" + e)
        if b_stray or w_stray or not opened or whole:
            self.stats["declared_env_kept:" + name] += 1
            # rule 4: the code itself says what the environment is.  A begin
            # that prints "Proof" and an end that closes with \qed (or a
            # square) is a proof, whatever the name is.
            proof = bool(PROOF_WORDS.search(b)) or bool(PROOF_END.search(e))
            if proof:
                self.stats["env_statement:proof"] += 1
                return self._as_proof(body, name)
            return body
        pat = re.compile(r"\\begin\s*\{" + re.escape(name) + r"\}")
        out: List[str] = []
        pos = 0
        for m in pat.finditer(body):
            if m.start() < pos:
                continue
            stop = L.find_env_end(body, m.end(), name)
            if stop is None:
                continue
            i = m.end()
            args: List[str] = []
            if default is not None:
                # ``\newenvironment{x}[n][d]``: the first of the n arguments is
                # optional and its default stands in when the option is absent
                j = i
                while j < len(body) and body[j] in " \t\n":
                    j += 1
                if j < len(body) and body[j] == "[":
                    g = L.find_group_end(body, j)
                    if g is None:
                        continue
                    args.append(body[j + 1: g - 1])
                    i = g
                else:
                    args.append(default)
            while len(args) < nargs:
                k = i
                while k < len(body) and body[k] in " \t\n":
                    k += 1
                if k >= len(body) or body[k] != "{":
                    break
                g = L.find_group_end(body, k)
                if g is None:
                    break
                args.append(body[k + 1: g - 1])
                i = g
            if len(args) < nargs:
                continue

            def sub(code: str) -> str:
                for k, a in enumerate(args, start=1):
                    code = code.replace("#%d" % k, a)
                return code

            out.append(body[pos: m.start()])
            inner = body[i: stop.start()]
            out.append("\n" + sub(b) + inner + sub(e) + "\n")
            pos = stop.end()
            self.stats["declared_env_expanded:" + name] += 1
        out.append(body[pos:])
        return "".join(out)

    def _bracket_displays(self, body: str) -> str:
        """``\\[...\\]`` and ``$$...$$`` are displays, not environments."""
        def repl(m):
            # _hoist returns (label, tag, body) -- unpacking it in this order
            # used to put the formula before \begin{display} and leave the
            # environment empty in more than half of all displays
            label, tag, inner = self._hoist(m.group(1))
            self.stats["display_bracket"] += 1
            return "\n%s%s\\begin{display}%s\\end{display}\n" % (
                label, tag, inner.strip())

        body = re.sub(r"(?<!\\)\\\[(.+?)(?<!\\)\\\]", repl, body, flags=re.S)
        return self._bracket_doubles(body)

    @staticmethod
    def _double_close(body: str, start: int):
        """Where the display that opens at ``start`` closes, or None."""
        i = start
        while True:
            j = body.find("$$", i)
            if j == -1:
                return None
            k = j - 1
            while k >= 0 and body[k] == "\\":
                k -= 1
            if (j - 1 - k) % 2 == 0:          # not escaped
                return j
            i = j + 1

    @staticmethod
    def _skip_command(body: str, i: int) -> int:
        r"""Past ``\cmd`` and its star only: no attached group.

        ``skip_control_sequence`` also swallows one ``[...]``/``{...}`` group,
        which is right for macro detection and wrong inside a display walker:
        ``\in[0,1)`` would jump over the closing ``]`` of something else and
        take every ``$`` in between with it.
        """
        n = len(body)
        j = i + 1
        if j < n and body[j].isalpha():
            while j < n and body[j].isalpha():
                j += 1
            while j < n and body[j] == "*":
                j += 1
            return j
        return min(j + 1, n)

    #: where a dropped font switch was; _font_groups turns it back into nothing
    FONT_MARK = "\x01"

    #: a group whose content starts with a font switch: {\bf X}, {\it X}
    #: the six switches that select a glyph in maths, and the command each one
    #: stands for.  Every other BARE_DROP switch also leaves a marker, with the
    #: code "z": it has no font of its own, only its braces have to go.
    GLYPH_SWITCHES = ("rm", "bf", "it", "sf", "tt", "cal")
    FONT_CODES = {"rm": "a", "bf": "b", "it": "c", "sf": "d", "tt": "e",
                  "cal": "f"}
    CODE_TO_COMMAND = {"a": "mathrm", "b": "mathbf", "c": "mathit",
                       "d": "mathsf", "e": "mathtt", "f": "mathcal"}

    #: commands whose group is an argument: the braces have to survive, or the
    #: pass that drops the command cannot find it (\subsubsection*{\bf Proof}
    #: would become \subsubsection*Proof and leak into the sentence)
    ARG_TAKING = ("section", "subsection", "subsubsection", "paragraph",
                  "subparagraph", "chapter", "part", "text", "mbox", "hbox",
                  "textrm", "textup", "textsl", "textsf", "texttt", "textbf",
                  "textit", "textsc", "mathop", "operatorname", "caption")

    FONT_SWITCHES = ("bf", "it", "em", "sc", "sl", "tt", "rm", "sf", "cal",
                     "bfseries", "itshape", "upshape", "scshape", "slshape",
                     "rmfamily", "sffamily", "ttfamily", "normalfont",
                     "boldmath", "unboldmath", "tiny", "scriptsize",
                     "footnotesize", "small", "normalsize", "large", "Large",
                     "LARGE", "huge", "Huge")

    @property
    def FONT_SWITCH_SET(self) -> set:
        return set(self.FONT_SWITCHES)

    def _font_groups(self, text: str) -> str:
        r"""Put the font back where it selects glyphs, drop it where it does not.

        ``_drop_bare`` removes a switch by name (it must: ``\noindent{\bf
        Step 1.}`` keeps its heading) and leaves a marker with a one-letter code
        for which switch it was.  In maths the marker becomes the matching font
        command, because upright is the only evidence that a run of letters is a
        name; in prose it becomes nothing.

        One pass over the whole text with a mask of the maths positions: a group
        can straddle a formula.  In ``{\bf Proposition $(1+a)$.}`` the closing
        brace sits after the ``$..$``, and a per-segment walk saw the group open
        and never close.

        The scope of a switch is TeX's: the group it sits in, or the rest of the
        formula when it has none.  Each run of letters is wrapped on its own,
        because a font changes the glyphs and not the structure --
        ``{\bf R^2}`` is ``\mathbf{R}^2``, not ``\mathbf{R^2}``.
        """
        if self.FONT_MARK not in text:
            return text
        in_math = bytearray(len(text))
        for span in L.find_math_spans(text):
            for k in range(span.start, min(span.end, len(text))):
                in_math[k] = 1
        mark = re.escape(self.FONT_MARK)
        group_re = re.compile(r"\{\s*" + mark + r"([a-fz])?\s*")
        loose_re = re.compile(mark + r"([a-fz])?")
        out: List[str] = []
        i, n = 0, len(text)
        while i < n:
            if text[i] == "{":
                m = group_re.match(text, i)
                close = L.find_group_end(text, i) if m else None
                if m and close:
                    code = m.group(1) or ""
                    inner = text[m.end():close - 1]
                    tail = "".join(out[-24:])
                    prev = re.search(r"\\([A-Za-z@]+)\*?\s*$", tail)
                    if re.search(r"\\(?:mathop|operatorname)\*?\s*\{?\s*$",
                                 tail):
                        # \mathop{\rm deg}: upright already, and wrapping would
                        # turn the operator key into \mathrm{deg}
                        out.append("{%s}" % inner.strip())
                    elif prev and prev.group(1) in self.ARG_TAKING:
                        # the group is the command's argument: keep the braces
                        out.append("{%s}" % self._font_groups(inner))
                    elif in_math[i] and code in self.CODE_TO_COMMAND:
                        out.append(self._wrap_runs(inner,
                                                   self.CODE_TO_COMMAND[code]))
                    else:
                        # prose, or a switch with no font of its own: the words
                        # stay and the braces go
                        inner = inner.strip()
                        while (inner.startswith("{")
                               and L.find_group_end(inner, 0) == len(inner)):
                            inner = inner[1:-1].strip()
                        out.append(self._font_groups(inner))
                    i = close
                    continue
            if text[i] == self.FONT_MARK:
                m = loose_re.match(text, i)
                code = (m.group(1) if m else "") or ""
                j = m.end() if m else i + 1
                if not in_math[i] or code not in self.CODE_TO_COMMAND:
                    # the marker goes, the space after it stays: a switch at the
                    # end of an argument ("{taller}\bfseries Title") separates
                    # two words, and eating the space glued them together
                    i = j
                    continue
                tail = "".join(out[-24:])
                if re.search(r"\\(?:mathop|operatorname)\*?\s*\{?\s*$", tail):
                    while j < n and text[j] == " ":
                        j += 1
                    i = j
                    continue
                k = j
                depth = 0
                # the scope ends where the formula ends: an unbraced switch
                # inside a display used to wrap the prose and the displays that
                # followed it (1708.05648 lost a whole page that way)
                while k < n and in_math[k]:
                    c = text[k]
                    if c == "\\":
                        cm = re.match(r"\\([A-Za-z]+)", text[k:])
                        k += cm.end() if cm else 2
                        continue
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        if depth == 0:
                            break
                        depth -= 1
                    k += 1
                out.append(self._wrap_runs(text[j:k], self.CODE_TO_COMMAND[code]))
                i = k
                continue
            out.append(text[i])
            i += 1
        return "".join(out)

    #: the argument of these commands is not a run of maths: an environment
    #: name, prose, a reference key
    PROTECTED_ARG = ("begin", "end", "text", "mbox", "hbox", "textrm", "textup",
                     "textsl", "textsf", "texttt", "textbf", "textit", "textsc",
                     "mathop", "operatorname", "label", "tag", "ref", "eqref",
                     "cite")

    def _wrap_runs(self, text: str, command: str) -> str:
        """Wrap every run that holds a letter, leaving the structure alone."""
        out: List[str] = []
        i, n = 0, len(text)
        while i < n:
            if text[i] == "\\":
                # a backslash at the very end of the text (or before a newline)
                # has no control sequence to consume; the regex returned None and
                # three papers in the corpus crashed here
                m = re.match(r"\\(?:[A-Za-z]+|[\s\S])", text[i:])
                if m is None:
                    out.append(text[i])
                    i += 1
                    continue
                name = m.group(0)[1:]
                if name in self.PROTECTED_ARG:
                    k = i + m.end()
                    if k < n and text[k] == "{":
                        close = L.find_group_end(text, k)
                        if close:
                            out.append(text[i:close])
                            i = close
                            continue
                out.append(m.group(0))
                i += m.end()
                continue
            if text[i] == "{":
                close = L.find_group_end(text, i)
                if close:
                    out.append("{")
                    out.append(self._wrap_runs(text[i + 1:close - 1], command))
                    out.append("}")
                    i = close
                    continue
            m = re.match(r"[A-Za-z0-9]+", text[i:])
            if m and any(ch.isalpha() for ch in m.group(0)):
                prev = "".join(out[-8:]).rstrip()[-1:]
                if prev not in ("^", "_"):
                    out.append("\\%s{%s}" % (command, m.group(0)))
                    i += m.end()
                    continue
            out.append(text[i])
            i += 1
        return "".join(out)

    def _bracket_doubles(self, body: str) -> str:
        """``$$...$$`` is a display -- unless the two dollars are two inline ends.

        A regex that pairs ``$$`` blindly turns ``$x$$y$`` into a display that
        starts between the two inline formulas: everything after it is off by
        one, so the prose lands inside displays and the formulas land in the
        prose (43 papers at least).  Walking the dollars and keeping track of
        whether an inline formula is open gives TeX's reading: after ``$x$``
        closes, the next ``$`` opens a new inline formula, it does not pair with
        the preceding one.
        """
        out: List[str] = []
        i, n = 0, len(body)
        in_inline = False
        while i < n:
            c = body[i]
            if c == "\\":
                # skip the command, not its "argument": in maths ``\in[0,1)``
                # the bracket is a bracket, and consuming it here ate the
                # dollars that followed (506 of 4,152 files with ``$$``)
                j = self._skip_command(body, i)
                out.append(body[i:j])
                i = j
                continue
            if c != "$":
                out.append(c)
                i += 1
                continue
            if in_inline:
                in_inline = False
                out.append("$")
                i += 1
                continue
            run = 1
            while i + run < n and body[i + run] == "$":
                run += 1
            if run >= 2:
                close = self._double_close(body, i + 2)
                if close is None:
                    out.append(body[i:])
                    break
                label, tag, inner = self._hoist(body[i + 2: close])
                self.stats["display_bracket"] += 1
                out.append("\n%s%s\\begin{display}%s\\end{display}\n"
                           % (label, tag, inner.strip()))
                i = close + 2
                continue
            in_inline = True
            out.append("$")
            i += 1
        return "".join(out)

    def _expand_bracket_display(self, body: str, tex: str) -> str:
        """A paper may redefine ``\\[`` as ``\\def\\[#1]#2{...}`` and write
        ``\\[equation]{...}``; without this the use looks like display maths and
        the whole environment body leaked as prose (OPEN.md 1.2)."""
        if not re.search(r"\\def\s*\\\[\s*#1\s*\]\s*#2", tex):
            return body
        out, pos = [], 0
        while True:
            i = body.find("\\[", pos)
            if i < 0:
                out.append(body[pos:])
                break
            m = re.match(r"\\\[\s*([^\]]*?)\s*\]\s*\{", body[i:])
            if not m:
                out.append(body[pos:i + 2])
                pos = i + 2
                continue
            start = i + m.end() - 1
            end = L.find_group_end(body, start)
            if end is None:
                out.append(body[pos:])
                break
            env = m.group(1).strip()
            inner = body[start + 1: end - 1]
            out.append(body[pos:i])
            out.append("\\begin{%s}%s\\end{%s}" % (env, inner, env))
            self.stats["bracket_display:" + env] += 1
            pos = end
        return "".join(out)

    def _split_intertext(self, body: str) -> str:
        """``\\intertext{X}`` interrupts a display: close it, write X, reopen.

        Left in place, the command and its prose stayed inside the formula body,
        which is what C6 reported and what M7 listed (AUDIT.md / GAPS.md).
        """
        pat = re.compile(r"\\intertext\s*\{")
        pos = 0
        while True:
            m = pat.search(body, pos)
            if not m:
                break
            end = L.find_group_end(body, m.end() - 1)
            if end is None:
                break
            prose = body[m.end():end - 1]
            body = (body[:m.start()] + "\\end{display} " + prose
                    + " \\begin{display}" + body[end:])
            self.stats["intertext_split"] += 1
            pos = m.start()
        return body

    def _drop_unclosed_diagrams(self, body: str) -> str:
        """Drop a drawing whose \\end never comes, up to the paragraph end.

        Without this the drawing commands leaked into the sentences; dropping to
        the end of the paper lost the rest of the text.  A blank line is where the
        author most likely stopped the drawing (AUDIT.md 27).
        """
        for env in [name for name, (cls, _kind) in self.name_to.items()
                    if cls == "diagram"]:
            pat = re.compile(r"\\begin\{" + re.escape(env) + r"\}.*?(?=\n\s*\n|\Z)",
                             re.S)
            pos = 0
            while True:
                m = pat.search(body, pos)
                if not m:
                    break
                if ("\\end{" + env + "}") in m.group(0):
                    pos = m.end()
                    continue
                self.stats["env_unclosed:" + env] += 1
                body = body[:m.start()] + body[m.end():]
                pos = m.start()
        return body

    def _envs(self, text: str, depth: int) -> str:
        out: List[str] = []
        pos = 0
        while True:
            m = BEGIN_RE.search(text, pos)
            if not m:
                out.append(text[pos:])
                break
            env = m.group(1)
            em = L.find_env_end(text, m.end(), env)
            if em is None:
                # A drawing with unbalanced braces inside it hides its own
                # ``\end``: the pairing test walks the brace structure and gives
                # up.  The closing name is still in the file, so take the last
                # one -- otherwise 556 ``\draw`` and 383 ``\node`` of 16 papers
                # leak into the prose as if they were words.
                if self.name_to.get(env, (None, None))[0] == "diagram":
                    last = text.rfind("\\end{%s}" % env)
                    if last > m.end():
                        out.append(text[pos: m.start()])
                        out.append(self._stash_diagram(env, text[m.end(): last]))
                        pos = last + len("\\end{%s}" % env)
                        self.stats["env_diagram_recovered:" + env] += 1
                        continue
                out.append(text[pos: m.end()])
                pos = m.end()
                self.stats["env_unclosed"] += 1
                continue
            out.append(text[pos: m.start()])
            inner = text[m.end(): em.start()]
            out.append(self._transform(env, inner, depth))
            pos = em.end()
        return "".join(out)

    def _opt_arg(self, inner: str) -> Tuple[Optional[str], str]:
        start = len(inner) - len(inner.lstrip())
        if inner[start:start + 1] != "[":
            return None, inner
        end = L.find_group_end(inner, start)
        if end is None:
            return None, inner
        return inner[start:end], inner[end:]

    def _brace_arg(self, inner: str) -> Tuple[Optional[str], str]:
        stripped = inner.lstrip()
        if not stripped.startswith("{"):
            return None, inner
        start = len(inner) - len(stripped)
        end = L.find_group_end(inner, start)
        if end is None:
            return None, inner
        return inner[start + 1: end - 1], inner[end:]

    def _transform(self, env: str, inner: str, depth: int) -> str:
        # already canonical -> rebuild identically (keeps the pass idempotent)
        if env == "statement":
            kind, rest = self._brace_arg(inner)
            return "\\begin{statement}{%s}%s\\end{statement}" % (
                kind or "theorem", self._envs(rest, depth))
        if env == "display":
            return "\\begin{display}%s\\end{display}" % self._envs(inner, depth)
        if env == "grid":
            name, rest = self._brace_arg(inner)
            return "\\begin{grid}{%s}%s\\end{grid}" % (
                name or "array", self._envs(rest, depth))
        if env == "list":
            name, rest = self._brace_arg(inner)
            return self._list(name or "enumerate", None, rest, depth)
        if env == "diagram":
            name, rest = self._brace_arg(inner)
            return self._stash_diagram(name or env, rest)

        cls, kind = self.name_to.get(env, (None, None))
        if cls is None and env.lower() != env:
            # papers capitalise: \begin{Lemma}, \begin{Theorem}, \begin{Def}
            cls, kind = self.name_to.get(env.lower(), (None, None))
        if cls is None and env.endswith("*"):
            # a starred variant is the same block: \begin{description*} is a
            # list, and leaving it unknown unwrapped it with its argument
            cls, kind = self.name_to.get(env[:-1], (None, None))
        if cls is None:
            # an environment the paper defines around a known one: ``equ`` is
            # \newenvironment{equ}{\begin{equation}}{\end{equation}}, and its
            # declaration body says so
            decl = self.env_decls.get(env) or self.env_decls.get(env.lower())
            if decl:
                inner_m = re.search(r"\\begin\{([A-Za-z@*]+)\}", decl[2])
                if inner_m:
                    cls, kind = self.name_to.get(inner_m.group(1),
                                                 (None, None))
                    if cls is None:
                        kind = (self.theorem_envs.get(inner_m.group(1))
                                or self.theorem_envs.get(inner_m.group(1).lower()))
                        if kind:
                            cls = "statement"
        if cls is None:
            # and they invent their own: \newtheorem{propn}{Proposition}
            kind = self.theorem_envs.get(env) or self.theorem_envs.get(
                env.lower())
            if kind:
                cls = "statement"
        opt, rest = self._opt_arg(inner)

        if cls is None:
            # rule 2: TeX's own semantics -- \begin{X} runs \X.  When X is a
            # command the pipeline already knows, rewrite to that command and
            # let its own pass handle the title (\section) or drop it (\title).
            if env == "math":
                self.stats["env_inline_math"] += 1
                body = self._envs(inner, depth).strip()
                while body.startswith("{") and L.find_group_end(body, 0) == len(body):
                    body = body[1:-1].strip()   # egin{math}{x} means x
                return "\\(" + body + "\\)"
            if env in ("title", "author", "date"):
                self.stats["env_dropped:" + env] += 1
                return "\n"
            if env in ENV_AS_COMMAND:
                self.stats["env_as_command:" + env] += 1
                return "\\" + env + (opt or "") + self._envs(rest, depth)
            if env == "restatable":
                # thmtools: \begin{restatable}{thm}{name} ... the kind is the
                # first argument, the name is a key for \restatable later
                kind, rest2 = self._brace_arg(rest)
                _, rest2 = self._brace_arg(rest2)
                self.stats["env_statement:restatable"] += 1
                return "\\begin{statement}{%s}%s\\end{statement}" % (
                    (kind or "theorem").strip(), self._envs(rest2, depth))

        if cls == "float" or cls == "bibliography" or cls == "drop":
            if cls == "float":
                self.stats["content_preserved:" + env] += 1
                raw = "\\begin{" + env + "}" + inner + "\\end{" + env + "}"
                # Keep the entire block, including table cells and captions.
                # The current splitter does not interpret arbitrary layouts.
                return "\n" + preserved.pack("float", raw, environment=env,
                            status="pending", reason="layout_not_parsed") + "\n"
            self.stats["env_dropped:" + env] += 1
            return "\n"
        if cls == "diagram":
            return self._stash_diagram(env, inner)
        if cls == "text_block" or cls == "inner_display" or cls == "document":
            self.stats["env_unwrapped:" + env] += 1
            return "\n" + self._envs(rest, depth) + "\n"
        if cls == "statement":
            self.stats["env_statement:" + env] += 1
            return "\\begin{statement}{%s}%s%s\\end{statement}" % (
                kind, opt or "", self._envs(rest, depth))
        if cls == "display":
            self.stats["env_display:" + env] += 1
            if env.startswith("IEEEeqnarray") or env.startswith("subeqn"):
                _, rest = self._brace_arg(rest)   # {rCl}: a column spec
            body = self._envs((opt or "") + rest, depth)
            label, tag, body = self._hoist(body)
            return "%s%s\\begin{display}%s\\end{display}" % (label, tag,
                                                             body.strip())
        if cls == "grid":
            self.stats["env_grid:" + env] += 1
            body = self._envs((opt or "") + rest, depth)
            if env == "array" or env.endswith("NiceArray") \
                    or env.endswith("NiceMatrix") or env == "NiceTabular":
                _, body = self._brace_arg(body)     # column spec carries no content
            return "\\begin{grid}{%s}%s\\end{grid}" % (env, body.strip())
        if cls == "list":
            return self._list(env, opt, rest, depth)

        self.stats["env_unknown:" + env] += 1
        _issues.record("env:unknown", "normalize._envs", where=env, size=1)
        self.unknown_envs[env] += 1
        return "\\begin{%s}%s%s\\end{%s}" % (env, opt or "",
                                             self._envs(rest, depth), env)

    def _hoist(self, body: str) -> Tuple[str, str, str]:
        label = tag = ""
        m = re.search(r"\\label\s*\{[^{}]*\}", body)
        if m:
            label = m.group(0) + "\n"
            body = body[:m.start()] + body[m.end():]
        m = re.search(r"\\tag\s*\{[^{}]*\}", body)
        if m:
            tag = m.group(0) + "\n"
            body = body[:m.start()] + body[m.end():]
        return label, tag, body

    # -- lists ----------------------------------------------------------
    def _list(self, env: str, opt: Optional[str], inner: str,
              depth: int) -> str:
        spec = parse_label_option(opt)
        head = "\\begin{list}{%s}" % env.rstrip("*")
        items = self._split_items(inner)
        # a paper's own begin code may have written \begin{list}{label}{settings};
        # that settings group is not an item
        items = [p for p in items
                 if not re.fullmatch(r"\s*\{\s*\\(?:setlength|addtolength|"
                                     r"renewcommand|setcounter)[^{}]*"
                                     r"(?:\{[^{}]*\}[^{}]*)*\}\s*", p)]
        labelled = env.startswith("enumerate") or env == "description"
        pieces: List[str] = []
        n = 0
        start = self._start_counter(inner)
        for body in items:
            explicit, body = self._opt_arg(body)
            if explicit:
                explicit = explicit[1:-1].strip()
            n += 1
            if not labelled:
                head_txt = "\\item[%s]" % explicit if explicit else "\\item"
                pieces.append(head_txt + " " + self._envs(body, depth + 1).strip())
                continue
            if explicit:
                label, ok = explicit, True
            else:
                label, ok = render_label(spec, start + n - 1, depth + 1,
                                         self.defaults)
            if not ok:
                self.unrendered[(env, explicit or spec or "?")] += 1
                self.stats["label_unrendered"] += 1
            pieces.append("\\item[%s] %s" % (label,
                                             self._envs(body, depth + 1).strip()))
        self.stats["env_list:" + env] += 1
        return head + "\n" + "\n".join(pieces) + "\n\\end{list}"

    @staticmethod
    def _start_counter(inner: str) -> int:
        """Number of the first ``\\item``: ``\\setcounter{enumi}{3}`` -> 4."""
        m = re.search(r"\\setcounter\s*\{enumi\}\s*\{\s*(\d+)\s*\}", inner)
        if m:
            return int(m.group(1)) + 1
        m = re.search(r"(?:^|,)\s*start\s*=\s*(\d+)", inner)
        if m:
            return int(m.group(1))
        return 1

    @staticmethod
    def _split_items(inner: str) -> List[str]:
        """Split a list body on ``\\item`` that is not inside a nested list."""
        marks = list(re.finditer(
            r"\\item(?![A-Za-z])|"
            r"\\begin\s*\{(?:enumerate|itemize|description|list)\*?\}|"
            r"\\end\s*\{(?:enumerate|itemize|description|list)\*?\}", inner))
        cuts, pend = [], 0
        nested = 0
        for m in marks:
            tok = m.group(0)
            if tok.startswith("\\begin"):
                nested += 1
            elif tok.startswith("\\end"):
                nested -= 1
            elif nested == 0:
                cuts.append((m.start(), m.end()))
        out, pos = [], 0
        for s, e in cuts:
            out.append(inner[pos:s])
            pos = e
        out.append(inner[pos:])
        return [p for p in out if p.strip()]

    # -- diagrams -------------------------------------------------------
    def _stash_diagram(self, name: str, body: str) -> str:
        self._diagrams.append("\\begin{diagram}{%s}%s\\end{diagram}"
                              % (name, body))
        self.stats["env_diagram:" + name] += 1
        return "\x02DIAG%d\x02" % (len(self._diagrams) - 1)

    def _restore_diagrams(self, body: str) -> str:
        def repl(m):
            i = int(m.group(1))
            return self._diagrams[i] if i < len(self._diagrams) else ""
        return re.sub(r"\x02DIAG(\d+)\x02", repl, body)

    # -- commands -------------------------------------------------------
    def _unwrap_one(self, body: str, name: str) -> str:
        pat = re.compile(r"\\" + name + r"\s*\{")
        while True:
            m = pat.search(body)
            if not m:
                return body
            end = L.find_group_end(body, m.end() - 1)
            if end is None:
                return body
            body = body[:m.start()] + body[m.end(): end - 1] + body[end:]

    def _unwrap_first_arg(self, body: str, name: str) -> str:
        """Keep the first group, drop what follows.

        ``\\scalerel*{(}{\\strut}`` means "draw this ( at the size of a strut":
        the delimiter is the content, the reference is only the size.
        """
        pat = re.compile(r"\\" + name + r"\*?(?![A-Za-z@])\s*(\{)")
        while True:
            m = pat.search(body)
            if not m:
                return body
            first = L.find_group_end(body, m.start(1))
            if first is None:
                return body
            i = first
            while True:                      # the reference argument, if any
                j = i
                while j < len(body) and body[j] in " \t\n":
                    j += 1
                if j < len(body) and body[j] in "{[":
                    end = L.find_group_end(body, j)
                    if end is None:
                        break
                    i = end
                    continue
                break
            body = body[:m.start()] + body[m.start(1) + 1: first - 1] + body[i:]

    def _wrap_arg(self, body: str, name: str, left: str, right: str) -> str:
        """``\\abs{x}`` -> ``|x|`` for a name the paper never defined.

        A paper that defines the name has already had its body expanded, so a
        call still spelled ``\\abs`` here is the package convention: absolute
        value, or norm.
        """
        pat = re.compile(r"\\" + name + r"(?![A-Za-z@])\s*\{")
        while True:
            m = pat.search(body)
            if not m:
                return body
            end = L.find_group_end(body, m.end() - 1)
            if end is None:
                return body
            inner = body[m.end(): end - 1]
            body = body[:m.start()] + left + inner + right + body[end:]

    @staticmethod
    def _resolve_mathgroup_tests(body: str) -> str:
        r"""Drop ``\ifnum\mathgroup=<name> ... \else ... \fi`` whole.

        Keeping the first branch is what a reader of the source sees, and the
        two branches differ only in which font the maths is drawn in.
        """
        pat = re.compile(r"\\ifnum\s*\\mathgroup\s*=\s*\\[A-Za-z@]+")
        out, pos = [], 0
        while True:
            m = pat.search(body, pos)
            if not m:
                out.append(body[pos:])
                return "".join(out)
            out.append(body[pos: m.start()])
            depth, else_at, end_at, end_end = 1, None, None, None
            for t in L.IF_TOKEN_RE.finditer(body, m.end()):
                cmd = t.group(1)
                if cmd in L.IF_OPENERS:
                    depth += 1
                elif cmd == "else":
                    if depth == 1 and else_at is None:
                        else_at = t.start()
                else:                                   # \fi
                    depth -= 1
                    if depth == 0:
                        end_at, end_end = t.start(), t.end()
                        break
            if end_at is None:
                # unbalanced: keep the rest untouched rather than cutting it
                out.append(body[m.end():])
                return "".join(out)
            out.append(body[m.end(): else_at if else_at is not None else end_at])
            pos = end_end

    def _drop_size_name(self, body: str) -> str:
        """``\\big(``, ``\\Bigl[``, ``\\Biggr|``: the delimiter is the content.

        These are sizing wrappers around one delimiter, and all six sizes are
        the same word to a reader.
        """
        return re.sub(r"\\[bB]igg?[lrm]?(?![A-Za-z@])\s*", "", body)

    def _keep_first_of_two(self, body: str, name: str) -> str:
        """``\\texorpdfstring{tex}{pdf}``: the typeset one is the content."""
        pat = re.compile(r"\\" + name + r"(?![A-Za-z@])\s*(\{)")
        while True:
            m = pat.search(body)
            if not m:
                return body
            first = L.find_group_end(body, m.start(1))
            if first is None:
                return body
            second = first
            while second < len(body) and body[second] in " \t\n":
                second += 1
            if second < len(body) and body[second] == "{":
                end = L.find_group_end(body, second)
                if end is None:
                    return body
                second = end
            body = (body[:m.start()] + body[m.start(1) + 1: first - 1]
                    + body[second:])

    def _unwrap_last_arg(self, body: str, name: str,
                         min_groups: int = 2) -> str:
        """``\\scalebox{0.7}{x}`` -> ``x`` (drop the size, keep the content).

        As a META command the whole call went, so a drawing wrapped in
        ``\\scalebox`` vanished and left an empty display behind.

        These calls have one argument that is content and one or more that are
        not: a raise, an angle, a scale, or an optional ``[0ex]`` height and
        depth.  The content is the last brace group, the rest is size.  Only the
        brace groups were counted, so ``\\raisebox{-0.25ex}[0ex][0ex]{x}``
        stopped at the first ``[`` and the call came out as
        ``\\raisebox [0ex][0ex]{x}`` -- a command that means nothing, inside a
        formula.  The optional groups are skipped, not counted.
        """
        pat = re.compile(r"\\" + name + r"(?![A-Za-z@])\s*")
        pos = 0
        while True:
            m = pat.search(body, pos)
            if not m:
                return body
            i = m.end()
            groups: List[Tuple[int, int]] = []
            while True:
                j = i
                while j < len(body) and body[j] in " \t\n":
                    j += 1
                if j < len(body) and body[j] in "{[":
                    end = L.find_group_end(body, j)
                    if end is None:
                        break
                    if body[j] == "{":
                        groups.append((j + 1, end - 1))
                    i = end
                    continue
                break
            if len(groups) < min_groups:
                pos = m.end()
                continue
            s, e = groups[-1]
            body = body[:m.start()] + body[s:e] + body[i:]
            pos = m.start()
            self.stats["unwrap:" + name] += 1

    def _commands(self, body: str) -> str:
        # one reference form: \eqref{x}, \cref{x}, \autoref{x} -> \ref{x}
        for old in ("eqref", "cref", "Cref", "autoref", "cites"):
            new = "ref" if old != "cites" else "cite"
            body, k = re.subn(r"\\" + old + r"\s*\{", "\\\\" + new + "{", body)
            if k:
                self.stats["ref_merged:%s->%s" % (old, new)] += k
        for name in self.UNWRAP_ONE:
            before = body
            body = self._unwrap_one(body, name)
            if body != before:
                self.stats["unwrap:" + name] += 1
        body = re.sub(r"\\(?:left|right|middle)\s*\.", "", body)
        # <dimen> glued to a command: \mkern2mu, \kern 5pt, \vskip .2truein,
        # \vskip\baselineskip.  No \b after the unit: in ``\kern 5ptT`` the unit
        # and the following letter are both word characters.
        body, k = re.subn(
            r"\\(?:mkern|kern|hskip|vskip|mskip)\s*"
            r"(?:"                                        # the value
            r"-?[\d.]+\s*(?:mu|pt|em|ex|cm|mm|in|sp|pc|bp|dd|cc"
            r"|true(?:pt|in|cm|mm))?(?![A-Za-z])"
            r"|\\[A-Za-z@]+"                              # \baselineskip
            r")?"
            r"(?:\s*(?:plus|minus)\s*-?[\d.]+\s*(?:mu|pt|em|ex|cm|mm|in"
            r"|fil+|true(?:pt|in|cm|mm))?)*",
            " ", body)
        if k:
            self.stats["dimen_removed"] += k
        # a row break with a vertical skip: \\[3pt] -> \\ ; the length is layout,
        # and left in place it becomes text inside the display
        body, k = re.subn(r"\\\\\s*\[-?[\d.]+\s*(?:pt|em|ex|cm|mm|in|mu|sp|pc"
                          r"|bp|dd|cc)\]", r"\\\\", body)
        if k:
            self.stats["rowskip_removed"] += k
        for old, new in self.aliases.items():
            if old in self._skip_aliases:
                continue
            body, k = re.subn(r"\\" + re.escape(old) + r"(?![A-Za-z@])",
                              "\\\\" + new, body)
            if k:
                self.stats["alias:%s->%s" % (old, new)] += k
        size_re = re.compile(r"\\(" + "|".join(self.size_cmds) + r")(?![A-Za-z])")
        body, k = size_re.subn("", body)
        if k:
            self.stats["size_stripped"] += k
        for thin in self.thin:
            body, k = self._drop_thin(body, thin)
            if k:
                self.stats["thin_removed"] += k
        # control space, but never the second half of a row break ``\\ ``
        body, k = self._drop_thin(body, "\\ ", control_space=True)
        if k:
            self.stats["thin_removed"] += k
        for cmd in self.layout_cmds:
            body, k = re.subn(r"\\" + cmd + r"(?![A-Za-z])", "", body)
            if k:
                self.stats["layout_removed:" + cmd] += k
        body = self._drop_section_machinery(body)
        body = self._drop_inline_tikz(body)
        return self._apply_names(body)

    @staticmethod
    def _drop_thin(body: str, thin: str, control_space: bool = False
                   ) -> Tuple[str, int]:
        """Remove a spacing command, but never let it fuse two tokens.

        A thin space is spacing, not a separator, so it goes.  Removed blindly
        it joins the two names around it: ``\\e^{\\b\\,n}`` is
        ``\\varepsilon^{\\beta n}`` in TeX, and deleting the ``\\,`` produced
        ``\\varepsilon^{\\betan}`` -- a command that exists nowhere.  The
        sample's remaining "unknown names" were largely of this kind
        (``\\betap``, ``\\lambdau``, ``\\sigmau``, ``\\inftyr``,
        ``\\varepsilonV``).

        One space is left behind in exactly the case that would change the
        token: a letter on both sides, or a closing group and a letter.
        """
        if control_space:
            pat = re.compile(r"(?<!\\)\\ ")
        else:
            pat = re.compile(re.escape(thin))
        out: List[str] = []
        pos = 0
        removed = 0
        for m in pat.finditer(body):
            before = body[m.start() - 1:m.start()]
            after = body[m.end():m.end() + 1]
            out.append(body[pos:m.start()])
            if after.isalpha() and (before.isalpha() or before == "}"):
                out.append(" ")
            pos = m.end()
            removed += 1
        if not removed:
            return body, 0
        out.append(body[pos:])
        return "".join(out), removed

    def _drop_section_machinery(self, body: str) -> str:
        """Residue of ``\\@startsection`` and the TeX arithmetic around it.

        ``\\@startsection{section}{1}\\z@{.5\\linespacing\\@plus .7\\linespacing}``
        is what a sectioning command expands to.  The command itself is dropped
        earlier, the machinery it left behind was not: ``\\linespacing`` (36 uses
        in one paper), ``\\z@`` (12), ``\\@plus`` (12) all ended up in the prose.
        """
        body, k1 = re.subn(r"\\@startsection\b(?:\s*\{[^{}]*\}){0,6}", " ", body)
        # ``\z@{.5\linespacing\@plus .7\linespacing}`` is a length that has
        # already lost its meaning here; the whole group goes, otherwise the
        # word ``\linespacing`` stays between the braces.
        body, k2 = re.subn(r"\\z@(?![A-Za-z@])\s*(?:\{[^{}]*\})?", " ", body)
        body, k3 = re.subn(r"\\@(?:plus|minus)(?![A-Za-z@])", " ", body)
        body, k4 = re.subn(r"\\@tempskipa(?![A-Za-z@])", " ", body)
        # what is left of a length: the name of the length, and the group that
        # held the number measured in it (``{.5\linespacing}``).  The group is
        # consumed here, with the name.  It used to be a rule of its own that
        # matched *any* brace group holding nothing but a number, and that
        # silently deleted every ``{2}`` in the corpus: ``x^{2}`` became ``x^``,
        # ``\frac{1}{2}`` became ``\frac``, ``\theta_{1}`` became ``\theta_``.
        # Nothing caught it, because the checks count formulas, not their
        # contents.
        body, k5 = re.subn(r"\\(?:linespacing|baselineskip|parindent|"
                           r"parskip|topsep|parsep)(?![A-Za-z@])\s*"
                           r"(?:\{\s*[-+.\d]*(?:\s*(?:pt|em|ex|bp|cm|mm|in|sp))?"
                           r"\s*\})?", " ", body)
        # a group that carries a *unit* is a length, wherever it sits; a group
        # that is only a number is content (``x^{2}``).  The old rule could not
        # tell them apart and deleted both.
        body, k6 = re.subn(r"\{\s*[-+]?\d*\.?\d+\s*(?:pt|em|ex|bp|cm|mm|in|sp)"
                           r"\s*\}", " ", body)
        if k1 + k2 + k3 + k4 + k5 + k6:
            self.stats["section_machinery"] += k1 + k2 + k3 + k4 + k5 + k6
        return body

    def _drop_inline_tikz(self, body: str) -> str:
        """``\\mathrel{\\tikz[baseline]\\draw[->] (0ex,.65ex) -- (3ex,.65ex);}``

        An inline TikZ picture that draws an arrow as a relation.  It is not an
        environment, so the diagram pass never sees it and its ``\\draw`` --
        556 of them in 9 papers -- was counted as an unknown word.  The picture
        itself is a drawing: the paired command ends at ``;``, and the arrow it
        draws is decoration, not text.
        """
        if "\\tikz" not in body:
            return body
        out: List[str] = []
        pos = 0
        for m in re.finditer(r"\\tikz(?![A-Za-z@])", body):
            if m.start() < pos:
                continue
            i = m.end()
            while True:                      # skip ``[...]`` settings
                j = i
                while j < len(body) and body[j] in " \t\n":
                    j += 1
                if j < len(body) and body[j] == "[":
                    end = L.find_group_end(body, j)
                    if end is None:
                        break
                    i = end
                    continue
                break
            semi = body.find(";", i)
            brace = body.find("{", i)
            if semi >= 0 and (brace < 0 or semi < brace) and semi - i < 600:
                stop = semi + 1
            elif brace >= 0 and brace - i < 40:
                end = L.find_group_end(body, brace)
                stop = end if end else None
            else:
                stop = None
            if not stop or "\\end{" in body[i:stop]:
                continue
            out.append(body[pos: m.start()])
            pos = stop
            self.stats["inline_tikz_dropped"] += 1
        out.append(body[pos:])
        return "".join(out)

    def _part_markers(self, body: str) -> str:
        r"""Turn a bare ``\X`` into a part marker when X is a declared class.

        ``\newtheorem{step}{Step}`` defines ``\step`` as the begin command, so a
        paper that writes ``\step …`` starts a Step block (SPLITTING.md).
        The block is *not* made a nested unit -- latex2nl would flatten it -- it
        is marked as a part of the unit that contains it, so the sentence keeps
        "this is step 3" without changing env or unit.
        """
        for name in sorted(self.theorem_envs):
            if "*" in name or name in ("proof", "document"):
                continue
            pat = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")
            body, k = pat.subn(lambda _m, n=name: "\\partmarker{" + n + "}", body)
            if k:
                self.stats["part_marker:" + name] += k
        return body

    def _apply_names(self, body: str) -> str:
        """Do to each name what TeX (or an answer) said to do with it."""
        for name, repl in self.names.expand.items():
            if name in ("accentset", "underaccent"):
                continue
            body, k = re.subn(r"\\" + re.escape(name) + r"(?![A-Za-z@])",
                              lambda _m, r=repl: r, body)
            if k:
                self.stats["name_expand:" + name] += k
        for name in self.names.unwrap:
            if name in ("accentset", "underaccent"):
                continue
            # ``\hbox to 3cm{..}``: the size sits between name and group
            body = re.sub(r"\\" + re.escape(name) + r"(?![A-Za-z@])\s*"
                          r"(?:to|spread)\s*[-+.\d]+\s*"
                          r"(?:pt|em|ex|cm|mm|in|sp|pc|bp|dd|cc)"
                          r"(?![A-Za-z])", "\\\\" + name, body)
            before = body
            body = self._unwrap_one(body, re.escape(name))
            if body != before:
                self.stats["name_unwrap:" + name] += 1
        for name in self.names.ignore:
            if name in ("accentset", "underaccent"):
                continue
            pat = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")
            out, pos, count = [], 0, 0
            while True:
                m = pat.search(body, pos)
                if not m:
                    break
                count += 1
                out.append(body[pos:m.start()])
                end = m.end()
                kind = PRIMITIVE_EAT.get(name)
                if kind in ("number", "dimen"):
                    end = skip_operand(body, end, dimen=(kind == "dimen"))
                elif kind == "assign":
                    end = skip_operand(body, end, assign=True)
                elif name in PRIMITIVE_UNWRAP:
                    j = end
                    while j < len(body) and body[j] in " \t\n":
                        j += 1
                    if j < len(body) and body[j] == "{":
                        close = L.find_group_end(body, j)
                        if close:
                            out.append(body[j + 1:close - 1])
                            end = close
                out.append(" ")
                pos = end
            out.append(body[pos:])
            body = "".join(out)
            # one count per match: len(out) counts output pieces, which is twice
            # the matches (17 pieces for 8 matches)
            if count:
                self.stats["name_ignore:" + name] += count
        return body

    @staticmethod
    def _tidy(body: str) -> str:
        body = body.replace("\x00", "")
        body = re.sub(r"[ \t]+\n", "\n", body)
        body = re.sub(r"\n{3,}", "\n\n", body)
        body = re.sub(r"[ \t]{2,}", " ", body)
        return body.strip() + "\n"

    # -- checks ---------------------------------------------------------
    DIAGRAM_RE = re.compile(r"\\begin\{diagram\}\{[^{}]*\}.*?\\end\\s*\\{diagram\}",
                            re.S)

    def vocabulary(self, body: str,
                   count_allowed_names: bool = True) -> Dict[str, int]:
        # a diagram body is opaque: what it contains is the drawing's business,
        # not commands the pipeline has to know
        body = self.DIAGRAM_RE.sub(" ", body)
        # a diagram whose \end never arrived (unbalanced braces in a drawing
        # are common) still holds a drawing, not prose: ``\draw``/``\node`` in
        # it are the drawing's, and counting them as unknown names is misleading
        body = re.sub(r"\\begin\{diagram\}\{[^{}]*\}.*\Z", " ", body, flags=re.S)
        extra = self.extra
        if not count_allowed_names:
            # what the count would be without the names an entry allowed, so a
            # report can say how many of them it accounts for
            extra = extra - set(self.names.allowed) - set(self.names.expand)
        else:
            # a name TeX says to unwrap or to drop has been decided just as
            # much as one it says to keep: counting it as outside the
            # vocabulary reports a gap that does not exist
            extra = extra | set(self.names.unwrap) | set(self.names.ignore)
        extra = extra | self.dimension_names
        allowed = (self.lexicon | extra |
                   {"begin", "end", "item", "label", "tag", "ref", "cite",
                    "section", "subsection", "subsubsection", "paragraph",
                    "statement", "display", "list", "grid", "diagram"})
        out = Counter()
        for name in L.control_words(body):
            if name not in allowed:
                out[name] += 1
        return dict(out)


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------


def _sample() -> str:
    return r"""\documentclass{article}
\newcommand{\R}{\mathbb R}
\begin{document}
Let $x\in\R$ and $a\le b$, see \ref{thm:x}.
\begin{lemma}\label{lem:a}
We have
\begin{equation}\label{eq:a}
\left( \frac{a}{b} \right) = c
\end{equation}
\begin{enumerate}[(i)]
\item first
\item second, $\alpha \to \beta$
\end{enumerate}
\end{lemma}
\begin{center}centred text\end{center}
\begin{figure}\caption{a picture}\end{figure}
\[ x \quad y \]
\end{document}
"""


def _toy_cases() -> List[Tuple[str, str, str, "callable"]]:
    """Regression cases: ``(name, preamble, body, predicate on the output)``.

    Each one is a defect found by reading the code and running it, not by the
    fixed-point or vocabulary checks -- neither of those can see content that
    moves to the wrong place or disappears.
    """
    return [
        ("dyadic display keeps its content",
         "", "Text $$a=b$$ more.",
         lambda o: "\\begin{display}a=b\\end{display}" in o),
        ("\\[..\\] keeps its content",
         "", "Text \\[x=y\\] more.",
         lambda o: "\\begin{display}x=y\\end{display}" in o),
        ("no empty display", "",
         "$$a$$ and $$b$$ and \\[c\\]",
         lambda o: "\\begin{display}\\end{display}" not in o),
        ("no-arg command keeps a following group",
         "", "\\noindent{\\bf Step 1.} We show the claim.",
         lambda o: "Step 1." in o and "claim" in o),
        ("no-arg command keeps a following bracket",
         "", "$x \\in A, \\quad [x] = [y]$.",
         lambda o: "[x] = [y]" in o),
        ("\\qquad keeps a braced group",
         "", "$$f(x)=0 \\qquad {\\rm if}\\ x<0$$",
         lambda o: "if" in o and "x<0" in o),
        ("argument-taking command drops its argument",
         "", "Text \\vspace{3mm} more.",
         lambda o: "3mm" not in o and "more" in o),
        ("unbraced dimension goes away",
         "", "$a \\hskip 3pt b \\kern-0.4ex c$.",
         lambda o: "3pt" not in o and "0.4ex" not in o),
        ("declarer alias does not cut a longer command",
         "\\newcommand{\\nc}{\\newcommand}",
         "\\nc{\\Hom}{\\operatorname{Hom}} $A \\ncong B$ and $\\Hom(A,B)$.",
         lambda o: "\\ncong" in o and "\\operatorname{Hom}" in o),
        ("declarer alias for \\renewcommand does not cut \\rceil",
         "\\newcommand{\\rc}{\\renewcommand}",
         "$\\lceil x \\rceil = 3$.",
         lambda o: "\\lceil x \\rceil = 3" in o),
        ("declarer alias still defines the macro",
         "\\newcommand{\\nc}{\\newcommand}",
         "\\nc{\\R}{\\mathbb{R}} $\\R$.",
         lambda o: "\\mathbb{R}" in o and "\\R" not in o and "\\nc" not in o),
        ("\\newcount does not cut \\newcounter",
         "", "\\newcounter{claim} Text after.",
         lambda o: "Text after." in o and "er{claim}" not in o),
        ("\\let swap does not merge phi and varphi",
         "\\let\\temp\\phi \\let\\phi\\varphi \\let\\varphi\\temp",
         "$\\phi + \\varphi$.",
         lambda o: "\\phi" in o and "\\varphi" in o),
        ("\\let of an undefined target still expands",
         "\\let\\R\\mathbb", "$\\R$.",
         lambda o: "\\mathbb" in o),
        ("a name is not a prefix of a longer name ending in @",
         "\\newcommand{\\foo}{A}\\newcommand{\\foo@}{B}",
         "$\\foo@ x \\foo$.",
         lambda o: "\\foo@" in o and "A" in o),
        ("an alias does not rewrite a name the paper defines",
         "\\newcommand{\\mathbb}[1]{\\mathrm{#1}}\\let\\Bbb\\mathbb",
         "$\\mathbb{n}$.", lambda o: "\\mathrm{n}" in o),
        ("a definition inside another macro's body is not global",
         "\\newcommand{\\warn}[1]{\\begingroup\\def\\item{BREAK}#1\\endgroup}",
         "\\begin{itemize}\\item first\\end{itemize}",
         lambda o: "BREAK" not in o and "first" in o),
        ("every \\ifthenelse is decided, however many there are",
         "", "Start " + "\\ifthenelse{\\equal{a}{b}}{Y}{N} " * 450 + "end.",
         lambda o: "\\ifthenelse" not in o and o.count("N") >= 450),
        ("an \\ifthenelse inside a chosen branch is decided too",
         "", "\\ifthenelse{\\equal{a}{a}}{\\ifthenelse{\\equal{a}{b}}{P}{Q}}{R}.",
         lambda o: "Q" in o and "P" not in o and "\\ifthenelse" not in o),
        ("a self-referential definition does not grow",
         "\\def\\text#1{\\text{$#1$}}", "$\\text{n}$.",
         lambda o: o.count("\\text") <= 1),
        ("declared environment: begin code opens one",
         "\\newenvironment{thm}{\\begin{theorem}}{\\end{theorem}}",
         "\\begin{thm} Let $x$. \\end{thm}",
         lambda o: "\\begin{statement}{theorem}" in o and "{thm}" not in o),
        ("declared environment: optional argument given",
         "\\newenvironment{pf}[1][Proof]{\\begin{proof}[#1]}{\\end{proof}}",
         "\\begin{pf}[Proof of A] Let $x$. \\end{pf}",
         lambda o: "Proof of A" in o and "{pf}" not in o),
        ("declared environment: optional argument omitted",
         "\\newenvironment{pf}[1][Proof]{\\begin{proof}[#1]}{\\end{proof}}",
         "\\begin{pf} Let $x$. \\end{pf}",
         lambda o: "{pf}" not in o and "Let" in o),
        ("declared environment: begin code opens nothing stays",
         "\\newenvironment{note}[1]{#1.}{}",
         "\\begin{note}{Careful} Text. \\end{note}",
         lambda o: "\\begin{note}" in o and "Careful" in o and "Text." in o),
        ("standard name is never expanded",
         "\\renewenvironment{itemize}{\\begin{list}{$\\bullet$}{}"
         "}{\\end{list}}",
         "\\begin{itemize}\\item a\\end{itemize}",
         lambda o: "\\begin{list}{itemize}" in o and "bullet" not in o),
        ("\\ifthenelse with \\isempty: argument present",
         "\\newcommand{\\norm}[2][]{\\ifthenelse{\\isempty{#1}}"
         "{\\|#2\\|}{\\|#2\\|_{#1}}}",
         "$\\norm[p]{x}$.", lambda o: "_{p}" in o),
        ("\\ifthenelse with \\isempty: argument absent",
         "\\newcommand{\\norm}[2][]{\\ifthenelse{\\isempty{#1}}"
         "{\\|#2\\|}{\\|#2\\|_{#1}}}",
         "$\\norm{x}$.", lambda o: "_{" not in o),
        ("\\ifthenelse with \\equal",
         "\\newcommand{\\k}[1]{\\ifthenelse{\\equal{#1}{a}}{one}{other}}",
         "$\\k{a} \\k{b}$.", lambda o: "one" in o and "other" in o),
        ("\\xymatrix with a @! option",
         "", "$$\\xymatrix@!C{A \\ar[r] & B}$$",
         lambda o: "\\begin{diagram}{xymatrix}" in o),
        ("\\xymatrix with a @R= dimension",
         "", "$$\\xymatrix@R=3mm{A \\ar[r] & B}$$",
         lambda o: "\\begin{diagram}{xymatrix}" in o),
        ("\\scalebox around a drawing keeps the drawing",
         "", "\\[\\scalebox{0.7}{\\begin{tikzpicture}\\draw (0,0);"
         "\\end{tikzpicture}}\\]",
         lambda o: "\\begin{diagram}{tikzpicture}" in o
         and "\\begin{display}\\end{display}" not in o),
        ("front matter is dropped whole",
         "", "\\author{A. Person}\\title{On \\emph{X}} Text.",
         lambda o: "Person" not in o and "Text." in o),
        # these four are typesetting, not mathematics: the command and its
        # argument both go, because the argument is a length or a credit line
        ("\\Needspace and its length are dropped",
         "", "\\Needspace*{3\\baselineskip}Text.",
         lambda o: "Needspace" not in o and "baselineskip" not in o
         and "Text." in o),
        ("\\symbolfootnote keeps neither the name nor its argument",
         "", "Text.\\symbolfootnote[1]{\\normalsize More.}",
         lambda o: "symbolfootnote" not in o and "normalsize" not in o),
        ("\\dedicatory is dropped whole",
         "", "\\dedicatory{\\em Dedicated to X.}Body.",
         lambda o: "Dedicated" not in o and "Body." in o),
        ("\\xymatrixrowsep inside a drawing is dropped",
         "", "$$\\xymatrixrowsep{8pt}\\xymatrix{A \\ar[r] & B}$$",
         lambda o: "xymatrixrowsep" not in o),
        ("a LAMS-TeX picture is dropped",
         "", "Text. \\special{pn 8} \\special{pa 2000 3200} \\special{fp} End.",
         lambda o: "special" not in o and "Text." in o and "End." in o),
        # the off-vocabulary triage: package plumbing, and package commands
        # whose meaning is known, taken from how they are used in the corpus
        ("plumbing goes but an unclosed conditional is preserved",
         "", "Text \\m@th still \\if@compatibility here.",
         lambda o: "still" in o and any(
             preserved.unpack(m)['raw'].strip() == r"\if@compatibility here."
             for m in preserved.MARKER.finditer(o))),
        # the corpus names its own environments: rmk, Lem, Prop, defi.  A
        # declared one is a statement of that kind, a capitalised standard one
        # is the standard kind, and a diagram package's CD is a diagram.
        ("a declared theorem environment becomes a statement",
         "\\newtheorem{rmk}{Remark}",
         "\\begin{rmk}\nOne point.\\end{rmk}",
         lambda o: "\\begin{statement}{remark}" in o and "rmk" not in o),
        ("a capitalised standard environment is that kind",
         "", "\\begin{Lemma}\nOne point.\\end{Lemma}",
         lambda o: "\\begin{statement}{lemma}" in o and "Lemma" not in o),
        ("CD is a diagram",
         "", "$$\\begin{CD} A @>f>> B \\end{CD}$$",
         lambda o: "\\begin{diagram}" in o),
        # package conventions the corpus uses, all taken from how the papers
        # spell them rather than from a list of names
        ("a scaled delimiter keeps the delimiter",
         "", "Open $\\scalerel*{(}{\\strut} x$ here.",
         lambda o: "(" in o and "scalerel" not in o and "strut" not in o),
        (":= keeps its meaning",
         "", "Set $A\\colonequals B$ and $C\\eqqcolon D$.",
         lambda o: ":=" in o and "=:" in o and "coloneq" not in o),
        ("absolute value and norm survive",
         "", "We have $\\abs{x}$ and $\\norm{y}$.",
         lambda o: "|x|" in o and "\\|y\\|" in o and "abs" not in o),
        ("a sized delimiter keeps the delimiter",
         "", "Take $\\big(A\\Bigr)B$ here.",
         lambda o: "(" in o and ")" in o and "big" not in o.lower()),
        ("the typeset branch of texorpdfstring survives",
         "", "We study \\texorpdfstring{$G$}{G}-bundles.",
         lambda o: "$G$" in o and "texorpdfstring" not in o),
        ("a counter value goes, a theorem name stays",
         "", "By (\\theequation) and \\theorem{} here.",
         lambda o: "theequation" not in o and "\\theorem" in o),
        ("link text, note content and tag label survive",
         "\\marginpar{x}",
         "See \\hyperref[lbl]{the text} and \\tag{3} \\marginpar{note} here.",
         lambda o: "the text" in o and "hyperref" not in o
         and "\\taglabel{3}" in o
         and any(preserved.unpack(m).get('body') == 'note' for m in preserved.MARKER.finditer(o))),
        ("ellipses are symbols",
         "", "We write \\dots and \\ldots and \\cdots.",
         lambda o: "\\dots" not in o and o.count("\u2026") == 3),
        # a number inside braces is content, not a length: this rule deleted
        # every {2} in the corpus for a while
        ("a number in braces survives",
         "", "We have $x^{2}$, $\\frac{1}{2}$, $a_{0}$ and $\\sqrt{2}$.",
         lambda o: "x^{2}" in o and "\\frac{1}{2}" in o and "a_{0}" in o
         and "\\sqrt{2}" in o),
        # mathstyle's placeholders, and a length the paper declares: neither is
        # a word, and between them they were 9,650 uses in one paper
        ("mathstyle placeholders and a declared length go",
         "\\newlength\\bshft",
         "Set $\\ThisStyle{\\SavedStyle A}$ and $\\kern-\\bshft x$.",
         lambda o: "ThisStyle" not in o and "SavedStyle" not in o
         and "bshft" not in o and "A" in o and "x" in o),
        ("an accent over a base keeps both arguments",
         "", "The value $\\accentset{\\ast}{W}$ is finite.",
         lambda o: r"\accentset{\ast}{W}" in o),
        ("a nice fraction becomes a fraction",
         "", "We get $\\nicefrac{1}{2}$ of it.",
         lambda o: "\\frac{1}{2}" in o and "nicefrac" not in o),
        ("a larger version of a symbol is the symbol",
         "", "Let $\\mathlarger{\\mathcal{S}}$ be the set.",
         lambda o: "\\mathcal{S}" in o and "mathlarger" not in o),
        # and these two are symbols: they must survive, with a class that says
        # what they mean, not the atom class TeX happens to use for spacing
        ("\\Re and \\Bbbk stay in the formula",
         "", "$\\Re z$ and $\\Bbbk$.",
         lambda o: "\\Re z" in o and "\\Bbbk" in o),
        # these three put a size between the name and the content; the content
        # is the point, the size is not, and the optional groups must not be
        # mistaken for content
        ("\\raisebox keeps its content and drops raise, height and depth",
         "", "$A^{\\raisebox{-0.25ex}[0ex][0ex]{x}}B$",
         lambda o: "raisebox" not in o and "0ex" not in o and "A^{x}B" in o),
        ("\\rotatebox keeps its content and drops the angle",
         "", "$\\rotatebox[origin=c]{180}{x}$",
         lambda o: "rotatebox" not in o and "180" not in o and "$x$" in o),
        ("\\reflectbox keeps its content",
         "", "$\\reflectbox{x}$",
         lambda o: "reflectbox" not in o and "$x$" in o),
    ]


def self_test() -> int:
    n = Normalizer()
    out = n.normalize(_sample())
    checks = [
        ("macro expanded", "\\R" not in out),
        ("alias", "\\leq" in out and "\\le " not in out),
        ("size stripped", "\\left" not in out and "( \\frac{a}{b} )" in out),
        ("display canonical", out.count("\\begin{display}") == 2),
        ("statement canonical", "\\begin{statement}{lemma}" in out),
        ("label hoisted", "\\label{eq:a}\n\\begin{display}" in out),
        ("list canonical", "\\begin{list}{enumerate}" in out),
        ("label rendered", "\\item[(i)] first" in out
         and "\\item[(ii)] second" in out),
        ("text block unwrapped", "centred text" in out and "center" not in out),
        ("float preserved with caption", any(
            preserved.unpack(m).get('kind') == 'float' and
            r'\caption{a picture}' in preserved.unpack(m)['raw']
            for m in preserved.MARKER.finditer(out))),
        ("document stripped", "documentclass" not in out),
    ]

    def wrap(pre: str, body: str) -> str:
        return ("\\documentclass{article}\n%s\n\\begin{document}\n%s\n"
                "\\end{document}\n" % (pre, body))

    for name, pre, body, ok in _toy_cases():
        try:
            got = Normalizer().normalize(wrap(pre, body))
            verdict = bool(ok(got))
        except Exception as exc:                  # noqa: BLE001
            got, verdict = "EXCEPTION %r" % exc, False
        checks.append((name, verdict))
        if not verdict:
            print("  %-46s FAIL\n      in : %s\n      out: %s"
                  % (name, body, got.replace("\n", " / ")))

    again = Normalizer().normalize(out)
    checks.append(("idempotent", again == out))

    # -- what TeX says about a name must reach the text ------------------
    # Injected directly: the self-test must not depend on the generated data
    # file, but it must still prove that each action is applied.
    def judged(pre: str, body: str, recs: dict, ok) -> None:
        table = NameTable(tex_path="", answers_path="")
        for name, rec in recs.items():
            table._add(name, rec, "tex")
        got = Normalizer(table).normalize(wrap(pre, body))
        verdict = bool(ok(got))
        checks.append(("name judgement", verdict))
        if not verdict:
            print("  %-46s FAIL\n      in : %s\n      out: %s"
                  % ("name judgement", body, got.replace("\n", " / ")))

    judged("", "$\\rtimes$ and $\\gtrsim$.", {
        "rtimes": {"action": "allow", "class": "binop"},
        "gtrsim": {"action": "allow", "class": "relation"},
    }, lambda o: "\\rtimes" in o and "\\gtrsim" in o)
    judged("", "\\hbox{boxed text}.", {
        "hbox": {"action": "unwrap"}}, lambda o: "boxed text" in o
        and "\\hbox" not in o)
    judged("", "\\hbox to 3cm{spread text}.", {
        "hbox": {"action": "unwrap"}}, lambda o: "spread text" in o
        and "\\hbox" not in o and "3cm" not in o)
    judged("", "one\\cr two.", {"cr": {"action": "ignore"}},
           lambda o: "\\cr" not in o and "one" in o and "two" in o)
    judged("", "$\\sph$ x.", {"sph": {"action": "expand",
                                      "body": "\\mathcal{S}"}},
           lambda o: "\\mathcal{S}" in o and "\\sph" not in o)
    judged("", "$\\loop$ x.", {"loop": {"action": "expand",
                                        "body": "\\loop"}},
           lambda o: "\\loop" in o)          # a self-referential body is refused

    # -- a paper may use a name in its own sense -------------------------
    table = NameTable(tex_path="", answers_path="")
    table._add("Zz", {"action": "allow", "class": "letter"}, "answer")
    checks.append(("global answer applies", table.allowed.get("Zz") == "letter"))
    table._add("Zz", {"action": "allow", "class": "const"}, "answer:p", True)
    checks.append(("a paper overrides a general answer",
                   table.allowed.get("Zz") == "const"))
    table._add("cr", {"action": "ignore"}, "tex")
    table._add("cr", {"action": "allow", "class": "letter"}, "answer:p", True)
    checks.append(("a paper cannot override TeX",
                   "cr" in table.ignore and "cr" not in table.allowed))

    bad = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print("  %-46s %s" % (name, "ok" if ok else "FAIL"))
    if bad:
        print("\n--- output was ---\n" + out)
        print("FAILED: " + ", ".join(bad))
        return 1
    print("\n%d checks passed" % len(checks))
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("tex", nargs="?", help="root .tex file")
    ap.add_argument("-o", "--out", help="write normalised source here")
    ap.add_argument("--style", action="append", default=[],
                    help="local .sty to read macros from (repeatable)")
    ap.add_argument("--no-inputs", action="store_true",
                    help="do not inline \\input / \\include")
    ap.add_argument("--stats", help="write a JSON report here")
    ap.add_argument("--issues", metavar="PATH", default="",
                    help="write the ledger of let-throughs here")
    ap.add_argument("--check", action="store_true",
                    help="assert fixed point and closed vocabulary")
    ap.add_argument("--paper-id",
                    help="select the per-paper answers in data/name_answers.json"
                         " (a paper may give its own meaning to a name)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.tex:
        ap.error("give a .tex file or --self-test")
    n = Normalizer(paper=args.paper_id or "")

    with open(args.tex, encoding="utf-8", errors="replace") as fh:
        tex = fh.read() if args.no_inputs else read_source(args.tex)
    styles = list(args.style)
    if not args.no_inputs:
        styles += local_styles(args.tex, tex)
    out = n.normalize(tex, styles)
    vocab = n.vocabulary(out)
    # Whole-document name inventory is informational. Only names outside known
    # mathematical boundaries can prevent text/formula splitting.
    text_parts, cursor = [], 0
    for span in L.find_math_spans(out, clean=False):
        text_parts.append(out[cursor:span.start])
        cursor = span.end
    text_parts.append(out[cursor:])
    text_vocab = n.vocabulary(''.join(text_parts))

    report = {
        "kept_macros": dict(n.kept_macros),
        "input": args.tex,
        "input_chars": len(tex),
        "output_chars": len(out),
        "stats": dict(sorted(n.stats.items())),
        "unknown_envs": dict(n.unknown_envs.most_common()),
        "outside_vocabulary": dict(sorted(vocab.items(),
                                          key=lambda kv: -kv[1])),
        "outside_math_vocabulary": text_vocab,
        "vocabulary_scope_version": 2,
        "labels_unrendered": {"%s %s" % k: v
                              for k, v in n.unrendered.most_common()},
    }
    if args.check:
        again = Normalizer(n.names).normalize(out)
        report["idempotent"] = again == out
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
    for missing in MISSING_INPUTS:
        _issues.record("human:source_error", "normalize.read_source",
                       where=os.path.basename(missing), size=0)
    if args.issues:
        _issues.write(args.issues)
        print("issues written: %s" % args.issues)

    if args.stats:
        with open(args.stats, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in
                      ("input_chars", "output_chars", "unknown_envs",
                       "outside_vocabulary") if k in report},
                     ensure_ascii=False, indent=2))
    if args.out:
        print("\nwrote " + args.out, file=sys.stderr)
    if args.check and not report["idempotent"]:
        print("NOT a fixed point", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
