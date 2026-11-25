#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""latex2nl.py -- LaTeX 数学论文 -> 解析器可读的纯文本 + 公式表

Pipeline position
-----------------
    paper.tex
        |  [this script]
        v
    sentences.txt / sentences.jsonl / formulas.json / unresolved.log
        |
        +--> DepCCG   (CCG derivation, optional ccg2lambda)
        +--> SPRING   (AMR graph)
        |
        +--> formula parser (separate stage, reads formulas.json)
                then splice formula trees back at the MATH placeholders

What this script does
---------------------
1. Removes LaTeX markup that carries no propositional content (preamble,
   bibliography, cross-reference machinery, typographic accents, ...).
2. Lifts every formula out of the prose into a table and leaves a placeholder
   token (``MATH1``) at the original position, so the sentence still parses.
3. Records the *role* of each formula (term / proposition / constraint /
   sequence / modifier / piecewise) because the formula parser and the
   splice-back step need it, and because DepCCG's CLI in this checkout cannot
   accept per-token category constraints.
4. Writes every judgement it could not make to ``unresolved.log`` instead of
   guessing silently.

The script does not attempt to understand the mathematics: it never rewrites
a formula, never evaluates anything, never reorders arguments. Anything that
needs a symbol table or a type checker is flagged for a later stage.

Usage
-----
    python3 latex2nl.py paper.tex -o out/
    python3 latex2nl.py --self-test

Outputs (in the directory given by -o)
--------------------------------------
    sentences.txt      one sentence per line, in document order
    sentences.jsonl    per-sentence metadata
    formulas.json      the formula table
    unresolved.log     items that need review before feeding the parsers

Only the Python standard library is used.
"""

from __future__ import annotations

import preserved
import argparse
import bisect
import issues as _issues
import json
import os
import re
import sys
import tempfile
import hashlib
import rule_reviews
import manual_review
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

#: environments that hold display mathematics
MATH_ENVS = (
    "equation", "equation*", "align", "align*", "gather", "gather*",
    "multline", "multline*", "displaymath", "eqnarray", "eqnarray*",
    "flalign", "flalign*",
)

#: environments whose content is one proposition-level unit
UNIT_ENVS = (
    "theorem", "proposition", "lemma", "claim", "corollary", "observation",
    "question", "remark", "conjecture", "definition", "problem", "proof",
    "abstract",
)

#: environments that belong to a formula; they must never be unwrapped
MATH_INNER_ENVS = (
    "cases", "dcases", "rcases", "array", "matrix", "pmatrix", "bmatrix",
    "vmatrix", "Vmatrix", "smallmatrix", "split", "aligned", "alignedat",
    "gathered", "alignat", "alignat*", "subequations", "multlined",
    # drawing environments: a formula may hold a diagram
    "tikzcd", "tikzpicture", "xy",
)

#: environments whose items are enumerated separately
LIST_ENVS = ("enumerate", "itemize", "description")

#: environments dropped wholesale
DROP_ENVS = (
    "thebibliography", "picture", "tikzpicture", "comment", "verbatim",
    "filecontents", "filecontents*", "theindex",
    # floats: captions and table cells are not the argument of the paper
    "table", "table*", "tabular", "tabular*", "figure", "figure*",
)

#: sectioning commands, outermost first
HEADING_CMDS = ("part", "chapter", "section", "subsection", "subsubsection", "paragraph",
                "subparagraph")

#: accent commands -> combining character
ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308",
    "~": "\u0303", "=": "\u0304", ".": "\u0307", "u": "\u0306",
    "v": "\u030c", "H": "\u030b", "c": "\u0327", "k": "\u0328",
    "b": "\u0331", "r": "\u030a",
}

#: abbreviations that must not end a sentence.  Latin and reference forms are a
#: closed set in practice; add one here and both the sentence splitter and the
#: tokeniser pick it up, so the two can no longer drift apart.
#: the built-in list, used when data/abbreviations.json is absent
ABBREVIATIONS = (
    "e.g.", "i.e.", "cf.", "c.f.", "et al.", "resp.", "vs.", "w.r.t.", "s.t.",
    "no.",
    "Thm.", "Lem.", "Prop.", "Cor.", "Def.", "Fig.", "Eq.", "Sec.", "Ex.",
    "viz.", "a.k.a.", "N.B.", "esp.", "incl.", "approx.", "ca.", "pp.",
    "vol.", "ed.", "trans.", "w.l.o.g.", "i.i.d.", "a.s.", "a.e.",
)

#: the table: "always" protects the dot anywhere, "before_digit" only when a
#: number follows ("No. 5" is an abbreviation, "No. We disagree." is a sentence)
ABBREVIATIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "data",
    "abbreviations.json")


def load_abbreviations(path: str = ABBREVIATIONS_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return tuple(ABBREVIATIONS), ()
    always = tuple(doc.get("always") or ()) or tuple(ABBREVIATIONS)
    return always, tuple(doc.get("before_digit") or ())


ABBREVIATIONS_ALWAYS, ABBREVIATIONS_BEFORE_DIGIT = load_abbreviations()

#: the forms that can appear as one token: keep every single word entry, plus
#: the tail of each multi-word one ("et al." -> "al.")
_ALL_ABBREVIATIONS = tuple(dict.fromkeys(
    ABBREVIATIONS_ALWAYS + ABBREVIATIONS_BEFORE_DIGIT))
TOKEN_ABBREVIATIONS = sorted(
    {a for a in _ALL_ABBREVIATIONS if " " not in a}
    | {a.split()[-1] for a in _ALL_ABBREVIATIONS if " " in a},
    key=len, reverse=True)

#: relation control sequences, mapped to the symbol stored in the table
REL_COMMANDS = {
    "le": "\u2264", "leq": "\u2264", "leqslant": "\u2264", "lt": "<",
    "ge": "\u2265", "geq": "\u2265", "geqslant": "\u2265", "gt": ">",
    "ne": "\u2260", "neq": "\u2260", "equiv": "\u2261",
    "in": "\u2208", "notin": "\u2209", "ni": "\u220b",
    "subseteq": "\u2286", "subset": "\u2282", "subsetneq": "\u228a",
    "supseteq": "\u2287", "supset": "\u2283", "supsetneq": "\u228b",
    "nsubseteq": "\u2288", "nsupseteq": "\u2289",
    "approx": "\u2248", "sim": "\u223c", "simeq": "\u2243",
    # \lesssim is "less than or similar": a relation, and the only one of
    # the three candidates whose scale is small (21 formulas, 14 of them
    # terms); \rightarrow is a function arrow in most of its 1,179 uses
    "lesssim": "\u2272", "gtrsim": "\u2273",
    "cong": "\u2245", "asymp": "\u224d",
    "mid": "\u2223", "nmid": "\u2224",
    "ll": "\u226a", "gg": "\u226b",
    "preceq": "\u2aaf", "succeq": "\u2ab0",
    "models": "\u22a8", "vdash": "\u22a2",
}
REL_LITERALS = {"=": "=", "<": "<", ">": ">"}

#: words that introduce a binding/constraint use of a formula
CONSTRAINT_TRIGGERS = {
    "for", "let", "suppose", "assume", "with", "where", "any", "every",
    "each", "choose", "given", "integer", "real", "number", "positive",
    "negative", "nonnegative", "finite", "subspace", "map", "function",
}

#: verbs that make the sentence a definition rather than an assertion
DEFINITION_VERBS = {"let", "put", "define", "write", "set", "denote",
                    "choose", "call"}
ASSUMPTION_VERBS = {"suppose", "assume"}

#: sentinel for a hard sentence break inside a block
HARD_BREAK = "\x00"
#: sentinel protecting a period that does not end a sentence
PROTECTED_DOT = "\x01"

#: delimiter commands whose direction the *name* states
OPEN_DELIMS = {"left", "bigl", "Bigl", "biggl", "Biggl"}
CLOSE_DELIMS = {"right", "bigr", "Bigr", "biggr", "Biggr"}

#: \big, \Big, \bigg, \Bigg are one name for both directions: only the
#: delimiter that follows says whether it opens or closes.  Keeping them in
#: OPEN_DELIMS made \big(x\big) add two levels of depth and never come back.
SIZE_DELIMS = {"big", "Big", "bigg", "Bigg"}

_OPEN_AFTER = {"(", "["}
_CLOSE_AFTER = {")", "]"}
_OPEN_NAMES = {"{", "langle", "lfloor", "lceil", "lbrack"}
_CLOSE_NAMES = {"}", "rangle", "rfloor", "rceil", "rbrack"}


def delim_direction(body: str, after: int) -> int:
    """+1 opens, -1 closes, 0 neither: what follows a \big-style command."""
    j = after
    while j < len(body) and body[j] in " \t":
        j += 1
    if j >= len(body):
        return 0
    ch = body[j]
    if ch in _OPEN_AFTER:
        return 1
    if ch in _CLOSE_AFTER:
        return -1
    if ch in "|/":                       # |, \| and / look the same both ways
        return 0
    if ch == "\\" and j + 1 < len(body):
        nxt = body[j + 1]
        if nxt == "{":
            return 1
        if nxt == "}":
            return -1
        if nxt == "|":
            return 0
        m = re.match(r"\\([A-Za-z]+)", body[j:])
        if m:
            if m.group(1) in _OPEN_NAMES:
                return 1
            if m.group(1) in _CLOSE_NAMES:
                return -1
    return 0

# a placeholder may carry an affix ("$n$th" -> MATH971th) or sit right after a
# letter ("CY" + MATH1729): only a digit would make it a different id
PLACEHOLDER_RE = re.compile(r"(?<![0-9])(MATH\d+|REF\d+)(?!\d)")

#: control words whose arguments are pure document machinery
META_CMDS = (
    "newcommand", "renewcommand", "providecommand", "DeclareMathOperator",
    "newtheorem", "usepackage", "documentclass", "setlength", "addtolength",
    "newcommand*", "renewcommand*", "pagestyle", "thispagestyle",
    "bibliographystyle", "bibliography", "include", "input", "includeonly",
    "setcounter", "addtocounter", "stepcounter", "refstepcounter",
    "addcontentsline", "addtocontents",
)

#: \usepackage[a,b]{x,y}
USE_PACKAGE_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*"
                            r"(?:\[[^\]]*\])?\s*\{([^}]*)\}")

#: TeX conditionals.  ``\ifdefined\SHOWPROOFS`` is how a paper switches whole
#: blocks on and off; left in place, each one becomes a "sentence".
#: any ``\if...`` word is an opener: a paper declares its own with ``\newif``,
#: and the fixed list used to leave ``\iffoo`` in the text as raw markup
IF_TOKEN_RE = re.compile(r"\\(if[A-Za-z@]*|else|or|fi)(?![A-Za-z])")

#: ``\newif\ifX`` followed by ``\Xtrue`` or ``\Xfalse`` is a switch the
#: pipeline can read, instead of taking the first branch (audit J1, OPEN.md)
NEWIF_RE = re.compile(r"\\newif\s*\\if([A-Za-z@]+)")
NEWIF_SET_RE = re.compile(r"\\([A-Za-z@]+)(true|false)(?![A-Za-z])")
IF_OPENERS = {"ifdefined", "ifx", "ifnum", "ifdim", "ifcase", "iftrue",
              "iffalse", "ifodd", "ifvmode", "ifhmode", "ifmmode"}
IF_NAME_RE = re.compile(r"\s*\\([A-Za-z@]+)")


NUM_TOKEN_RE = re.compile(r"[+-]?\s*(?:\d+(?:\.\d+)?\s*(?:pt|em|ex|bp|cm|mm|in|sp)?|\\[A-Za-z@]+(?:\d+)?(?:\s*\{[^{}]*\})?)")


def skip_ifnum_test(text: str, i: int) -> int:
    r"""Where the test of ``\ifnum`` ends and its branch begins.

    ``\ifnum\mathgroup=\symbold A\else B\fi`` -- the branch is `` A``; the test
    is ``\mathgroup=\symbold``.  Leaving the test in place put its operands into
    the text (``\mathgroup``, ``\symbold``), which is how 3,726 font numbers
    reached the vocabulary.  Only the shape ``operand relation operand`` is
    consumed; anything else is left where it was.
    """
    def operand(j: int) -> int:
        m = NUM_TOKEN_RE.match(text, j)
        return m.end() if m else j

    j = operand(i)
    while j < len(text) and text[j] in " \t\n":
        j += 1
    if j >= len(text) or text[j] not in "=<>":
        return i
    j += 1
    while j < len(text) and text[j] in " \t\n":
        j += 1
    k = operand(j)
    return k if k != j else i


# Keep these public names for callers; conditional content travels in the
# normalized source, not mutable process-global branch fragments.
from conditionals import resolve as resolve_conditionals, read_switches, switch_option
COND_TODOS: List[dict] = []  # compatibility only; no longer used for transport


# --------------------------------------------------------------------------
# datatypes
# --------------------------------------------------------------------------


@dataclass
class Unit:
    """A proposition-level chunk of the document."""

    uid: str
    env: str
    section: str
    title: Optional[str]
    body: str
    section_scope: tuple = ()
    enclosing_units: tuple = ()


@dataclass
class FormulaRecord:
    """One entry of the formula table."""

    id: str
    raw: str
    expanded: str
    kind: str                      # inline | display | ref
    role: str                      # term | proposition | constraint |
                                   # sequence | modifier | piecewise
    env: Optional[str] = None
    label: Optional[str] = None
    affix: Optional[str] = None
    trailing_punct: str = ""
    side_conditions: List[str] = field(default_factory=list)
    text_slots: List[dict] = field(default_factory=list)
    top_relation: Optional[dict] = None
    flags: List[str] = field(default_factory=list)
    unit: Optional[str] = None
    context_before: str = ""
    context_after: str = ""
    ref_target: Optional[str] = None
    ref_kind: Optional[str] = None
    opaque: bool = False
    unrecognized_commands: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    rows: List[dict] = field(default_factory=list)
    display_tags: List[dict] = field(default_factory=list)
    #: the sentence this formula came from was dropped, so nothing points at
    #: the record; the anchors say where it stood
    orphan: bool = False
    prev_sentence: Optional[str] = None

    def to_dict(self) -> dict:
        out = dict(self.__dict__)
        for key in ('labels', 'rows', 'display_tags'):
            if not out[key]:
                del out[key]
        if not self.opaque:
            out.pop('opaque', None)
        if not self.unrecognized_commands:
            out.pop('unrecognized_commands', None)
        if not self.orphan:
            # a normal record does not carry the two keys at all
            out.pop("orphan", None)
            out.pop("prev_sentence", None)
        return out


@dataclass
class Span:
    """A stretch of math found in a text block."""

    start: int
    end: int
    kind: str                      # inline | display
    body: str
    env: Optional[str] = None
    label: Optional[str] = None

    labels: List[str] = field(default_factory=list)
    row_labels: List[List[str]] = field(default_factory=list)
    display_tags: List[dict] = field(default_factory=list)


#: ``\partmarker{step}`` comes from normalize when a paper writes a bare
#: command TeX defined through ``\newtheorem`` (RULES.md).  It becomes a
#: sentinel here so no other pass sees it, and the sentence it starts carries the
#: part number.
#: a brace group whose content is plain text (no command, no nesting).  Such a
#: group is left over from a format command or a macro expansion -- \z@'s
#: arguments, {{\ensuremath{..}}}, \noindent{\emph{..}} -- and in the text it
#: is only noise; math is already a placeholder by the time this is applied
#: (RULES.md).
TEXT_GROUP_RE = re.compile(r"(?<!\\)\{([^{}\\]*)\}")

PART_MARKER_RE = re.compile(r"\\partmarker\{([^{}]*)\}")
PART_SENTINEL_RE = re.compile("(\ue000[^\ue001]*\ue001)")
PART_PIECE_RE = re.compile("\ue000([^\ue001]*)\ue001")


@dataclass
class Sentence:
    sid: str
    text: str
    unit: str
    env: str
    section: str
    title: Optional[str]
    speech_act: str
    formulas: List[str] = field(default_factory=list)
    tokens: List[str] = field(default_factory=list)
    words: int = 0
    flags: List[str] = field(default_factory=list)
    #: {"kind": "step", "index": 3} when the sentence starts a declared part
    part: Optional[dict] = None
    content_kind: str = "prose"
    section_scope: tuple = ()
    enclosing_units: tuple = ()

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        if d.get("part") is None:
            d.pop("part", None)      # absent, not null, when there is no part
        return d


# --------------------------------------------------------------------------
# low level TeX helpers
# --------------------------------------------------------------------------


def strip_comments(tex: str, line_origins=None) -> str:
    """Drop ``%`` comments, keeping escaped ``\\%``."""
    out = []
    for line_number, line in enumerate(tex.splitlines(), 1):
        i = 0
        cut = len(line)
        while True:
            j = line.find("%", i)
            if j == -1:
                break
            # count the backslashes: \\% is a line break then a comment,
            # \\% inside a line is an escaped percent (audit M12, OPEN.md)
            k, slashes = j - 1, 0
            while k >= 0 and line[k] == "\\":
                slashes += 1
                k -= 1
            if slashes % 2 == 1:
                i = j + 1
                continue
            cut = j
            break
        kept = line[:cut]
        if cut < len(line) and not kept.strip():
            # the line holds nothing but a comment.  In TeX it produces no output
            # at all -- not even a newline, that is what % is for -- so dropping
            # the text but keeping the line would turn it into a blank line, and a
            # blank line is a paragraph break downstream (RULES.md).
            continue
        out.append(kept)
        if line_origins is not None:
            line_origins.append(line_number)
    return "\n".join(out)


_LITERALS = re.compile(
    r'\\begin\{(verbatim\*?|Verbatim|lstlisting|minted|comment)\}.*?\\end\{\1\}'
    r'|\\verb\*?(?![A-Za-z@])([^\s]).*?\2|%[^\n]*|\\.', re.S)
_DOCUMENT_ENV = re.compile(r'\\(begin|end)\s*\{document\}|\\.')


def lexical_view(raw: str) -> str:
    """Mask comments and verbatim material without changing source offsets."""
    def mask(m):
        if len(m[0]) == 2 and m[0][0] == '\\':
            return m[0]  # Escaped character, including percent.
        return re.sub(r'[^\n]', ' ', m[0])
    return _LITERALS.sub(mask, raw)


def document_markers(tex):
    view = lexical_view(tex)
    definitions = definition_spans(view)
    for m in _DOCUMENT_ENV.finditer(view):
        if m[1] and not inside_definition(definitions, m.start()):
            yield m


def split_document(tex: str) -> Tuple[str, str]:
    """Keep earlier candidate closers for the source-order conditional/macro pass."""
    if r'\begin' not in tex:
        return "", tex
    markers = document_markers(tex)
    begin = next((m for m in markers if m[1] == 'begin'), None)
    if not begin:
        return "", tex
    stop = len(tex)
    for m in markers:
        if m[1] == 'end':
            stop = m.start()
    return tex[:begin.start()], tex[begin.end():stop]


def use_canonical_names() -> Tuple[str, ...]:
    """Register the canonical container names for this process.

    ``display``/``grid``/``list`` are read as module-level tuples by helpers
    that have no ``self`` (the math-span finder, the list splitter), so the
    canonical names have to be added there rather than on an instance.  That
    makes this process-global by design: one paper per process is the unit this
    pipeline works in, and the CLI is the only caller.  Named and documented so
    it is one place to change if that ever stops being true.

    ``statement`` is different -- it is a unit, and units live on the instance.

    ``diagram`` is here for the same reason ``grid`` is: normalize.py puts
    every drawing into one opaque ``\\begin{diagram}{kind}`` block, and without
    this name the block was read as an unknown environment.  Its markers went,
    its body stayed, and the drawing's own commands became words of the prose
    (``\\ar`` 44 uses in one paper, ``\\save``/``\\restore`` 20 each).
    """
    global MATH_ENVS, MATH_INNER_ENVS, LIST_ENVS
    if "display" not in MATH_ENVS:
        MATH_ENVS = tuple(MATH_ENVS) + ("display",)
    if "grid" not in MATH_INNER_ENVS:
        MATH_INNER_ENVS = tuple(MATH_INNER_ENVS) + ("grid",)
    if "diagram" not in MATH_INNER_ENVS:
        MATH_INNER_ENVS = tuple(MATH_INNER_ENVS) + ("diagram",)
    if "list" not in LIST_ENVS:
        LIST_ENVS = tuple(LIST_ENVS) + ("list",)
    return MATH_ENVS


def find_group_end(text: str, open_idx: int) -> Optional[int]:
    """Index just past the group opened at ``open_idx`` (``{`` or ``[``)."""
    if open_idx >= len(text) or text[open_idx] not in "{[(":
        return None
    opener = text[open_idx]
    closer = {"{": "}", "[": "]", "(": ")"}[opener]
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def find_env_end(text: str, after_begin: int, env: str) -> Optional["re.Match[str]"]:
    """Find the ``\\end{env}`` matching a begin that ended at ``after_begin``."""
    # a space before the brace is legal TeX and common in old papers:
    # "\\end {equation}" was not matched, so the display swallowed the prose after
    begin_re = re.compile(r"\\begin\s*\{" + re.escape(env) + r"\}")
    end_re = re.compile(r"\\end\s*\{" + re.escape(env) + r"\}")
    depth = 0
    i = after_begin
    while True:
        mb = begin_re.search(text, i)
        me = end_re.search(text, i)
        if me is None:
            return None
        if mb is not None and mb.start() < me.start():
            depth += 1
            i = mb.end()
        else:
            if depth == 0:
                return me
            depth -= 1
            i = me.end()


#: a control sequence that is structure, not an argument: ``\B\end{x}`` must not
#: swallow ``\end`` as the argument of ``\B``.
NOT_AN_ARGUMENT = frozenset("""
begin end item label tag ref cite section subsection subsubsection paragraph
par cr crcr relax hfil vfil hfill vfill noindent indent
""".split())


def _single_token_end(text: str, i: int) -> Optional[int]:
    """End of the single token TeX would take as an argument at ``i``.

    TeX does not need braces around a one-token argument: ``\\B C`` passes ``C``
    to ``\\B``, and so does ``\\B\\Gamma``.  Returns ``None`` when the token at
    ``i`` is structure (a closing brace, a math shift, ``\\end``) -- taking that
    as an argument would eat the document instead of a character.
    """
    n = len(text)
    if i >= n or text[i] in "}$&]":
        return None
    if text[i] != "\\":
        return i + 1                     # one character
    j = i + 1
    if j >= n:
        return None
    if text[j].isalpha() or text[j] == "@":
        while j < n and (text[j].isalpha() or text[j] == "@"):
            j += 1
        if text[i + 1:j] in NOT_AN_ARGUMENT:
            return None
        return j
    if text[j] in "{[":                  # \\{ or \\[ : one character token
        return j + 1
    return j + 1                         # \%, \,, \\ and friends


def skip_control_sequence(text: str, i: int) -> int:
    """Advance past ``\\cmd`` plus one directly attached optional/mandatory group."""
    j = i + 1
    n = len(text)
    if j < n and text[j].isalpha():
        while j < n and text[j].isalpha():
            j += 1
    else:
        return min(j + 1, n)          # \\, \{, \%, \  ...
    while j < n and text[j] == "*":
        j += 1
    if j < n and text[j] in "[{":
        k = find_group_end(text, j)
        if k:
            return k
    return j


# --------------------------------------------------------------------------
# macro expansion
# --------------------------------------------------------------------------


def xparse_nargs(spec: str) -> int:
    """How many arguments an xparse argument spec asks for.

    ``m`` is mandatory, ``o``/``O{..}`` optional, ``s``/``t`` are stars; only
    mandatory and optional ones consume an argument we can substitute.
    """
    return len(re.findall(r"[moOv]", spec))


#: declaration forms besides ``\newcommand``: ``(regex, kind)``.  ``kind`` says
#: how to read the arguments: ``"plain"`` = one group, ``"op"`` = a group used
#: as operator name, ``"xparse"`` = an xparse argument spec instead of a count.
#: mathtools: ``\\DeclarePairedDelimiter\\norm{\\lVert}{\\rVert}``
PAIRED_DELIM_RE = re.compile(
    r"\\DeclarePairedDelimiter\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*"
    r"(?:\[(\d)\])?\s*\{")
#: ``\\DeclarePairedDelimiterX\\norm[2]{\\lVert}{\\rVert}{#1\\mid#2}``
PAIRED_DELIMX_RE = re.compile(
    r"\\DeclarePairedDelimiterX\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*"
    r"\[(\d+)\]\s*\{")
#: amssymb: ``\\DeclareMathSymbol{\\R}{\\mathbin}{AMSb}{"52}``
DECLARE_MATH_SYMBOL_RE = re.compile(
    r"\\DeclareMathSymbol\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*"
    r"\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
#: ``\\def\\pair(#1,#2){...}`` -- parameters delimited, not braced
DELIMITED_DEF_RE = re.compile(
    r"\\(?:g|e|x)?def\s*\\([A-Za-z@]+)\s*\(([^()]*#[1-9][^()]*)\)\s*\{")

#: symbol fonts whose declaration states the glyph as a character code
MATH_SYMBOL_FONTS = {"AMSb": "mathbb", "operators": "operatorname",
                     "letters": "mathrm", "symbols": "mathrm"}

DECL_FORMS = (
    ("newcommand", "plain", re.compile(
        r"\\(?:re)?newcommand\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*"
        r"(?:\[(\d)\])?\s*(?:\[([^\]]*)\])?\s*\{")),
    ("DeclareRobustCommand", "plain", re.compile(
        r"\\DeclareRobustCommand\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*"
        r"(?:\[(\d)\])?\s*(?:\[([^\]]*)\])?\s*\{")),
    ("DeclareMathOperator", "op", re.compile(
        r"\\DeclareMathOperator\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*\{")),
)

#: xparse's shorthand: ``\NewDocumentCommand{\x}{mm}{body}``
XPARS_NEW_RE = re.compile(
    r"\\(?:New|Renew|Declare|Provide)DocumentCommand\s*\{?\s*"
    r"\\([A-Za-z@]+)\s*\}?\s*\{([^{}]*)\}")

#: alphabet declarations: ``\DeclareMathAlphabet{\mathbbe}{U}{bbold}{m}{n}``
#: makes ``\mathbbe`` a one-argument name that switches to the named font
#: family.  It is a definition like any other, but its arguments are a font
#: specification rather than a replacement body: ``bbold`` is a blackboard
#: font, so ``\mathbbe{x}`` means what ``\mathbb{x}`` means.  Without this the
#: name was never in the table and every use was reported as a command the
#: pipeline does not understand (84 uses in one paper).
ALPHABET_DECL_RE = re.compile(
    r"\\DeclareMathAlphabet\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*"
    r"\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
#: ``\DeclareSymbolFont{name}{encoding}{family}{weight}{shape}``: the family
#: behind a symbol font, needed to read ``\DeclareSymbolFontAlphabet``
SYMBOL_FONT_RE = re.compile(
    r"\\DeclareSymbolFont\s*\{?\s*([A-Za-z@]+)\s*\}?\s*"
    r"\{([^{}]*)\}\s*\{([^{}]*)\}")
#: ``\DeclareSymbolFontAlphabet{\Z}{msb}``: the second argument names the
#: symbol font, so it has to be resolved through the line above
SYMBOL_ALPHABET_RE = re.compile(
    r"\\DeclareSymbolFontAlphabet\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*\{([^{}]*)\}")

#: A font family, read from its name, and the font command it stands for.  The
#: name is split on everything that is not a letter, and a *word* of it has to
#: start with the key: ``BOONDOX-calo`` and ``rsfs`` match, but ``zeus`` does
#: not match ``eus`` (Euler script) just because the letters are there.  Only
#: families whose name states their shape are mapped; anything else (``zeus``,
#: ``mathc``, ``zplm``) falls back to upright, and the family is counted so the
#: fallback can be reviewed.
ALPHABET_FAMILIES = (
    (("bbold", "bbm", "msb", "msbm", "bb", "blackboard"), "mathbb"),
    (("calligra", "dutchcal", "stixscr", "rsfs", "cmsy", "script", "cal",
      "pzc", "eus", "rsf", "scr"), "mathcal"),
    (("eufrak", "eufm", "frak", "goth", "euf", "eur"), "mathfrak"),
    (("sans", "cmss", "sf"), "mathsf"),
    (("bold", "bf"), "mathbf"),
    (("typewriter", "cmtt", "tt"), "mathtt"),
)
#: what an unreadable family becomes: upright, the neutral choice
ALPHABET_FALLBACK = "mathrm"


def alphabet_font_command(family: str) -> str:
    """The font command an ``\\DeclareMathAlphabet`` family stands for."""
    words = re.split(r"[^a-z]+", (family or "").strip().strip("\\").lower())
    for keys, command in ALPHABET_FAMILIES:
        for word in words:
            if any(word.startswith(key) for key in keys):
                return command
    return ALPHABET_FALLBACK

#: the paper may name a declarer and then use that name: ``\nc{\Hom}{...}``
DECLARERS = ("newcommand", "renewcommand", "providecommand", "def", "gdef",
             "edef", "xdef", "DeclareMathOperator", "DeclareRobustCommand",
             "NewDocumentCommand", "RenewDocumentCommand",
             "DeclareDocumentCommand", "ProvideDocumentCommand")

#: every way a definition can start; used only to find where its text ends
DEF_HEADS = (r"(?:re)?newcommand|providecommand|DeclareRobustCommand"
             r"|DeclareMathOperator|DeclareMathAlphabet|DeclareSymbolFont"
             r"|DeclareSymbolFontAlphabet"
             r"|(?:New|Renew|Declare|Provide)Document(?:Command|Environment)"
             r"|(?:re)?newenvironment|[gex]?def")


def definition_spans(text: str, extra_heads: Sequence[str] = ()
                     ) -> List[Tuple[int, int]]:
    """``(start, end)`` of every definition that is not inside another one.

    A definition written inside the replacement text of another macro takes
    effect only while that macro runs, usually inside a group.  We never run
    macros, so such a definition must not be read as if it held for the whole
    paper: a ``biblatex.sty`` shipped with three papers contains

        \\protected\\def\\blx@bbl@warn#1{\\begingroup \\def\\item{\\MessageBreak-\\space}...

    and reading the inner ``\\def\\item`` rewrote every ``\\item`` of the paper.
    """
    heads = DEF_HEADS
    if extra_heads:
        heads += "|" + "|".join(re.escape(h) for h in extra_heads)
    head_re = re.compile(r"\\(" + heads + r")\*?(?![A-Za-z@])")
    n = len(text)
    spans: List[Tuple[int, int]] = []
    outer_end = -1
    for m in head_re.finditer(text):
        if m.start() < outer_end:
            continue                    # inside the previous top-level one
        i = m.end()
        end: Optional[int] = None
        if m.group(1).endswith("def"):
            # \def\x<parameter text>{body}: the first brace opens the body
            j = text.find("{", i)
            if j < 0 or j - i > 200 or "\n\n" in text[i:j]:
                continue
            end = find_group_end(text, j)
        else:
            named = False
            while True:
                newlines = 0
                while i < n and text[i] in " \t\r\n*":
                    newlines += text[i] == "\n"
                    i += 1
                if newlines > 1:
                    break               # a blank line ends the definition
                if i < n and text[i] in "{[":
                    e = find_group_end(text, i)
                    if e is None:
                        break
                    end = i = e
                    continue
                if i < n and text[i] == "\\" and end is None and not named:
                    j = i + 1
                    while j < n and (text[j].isalpha() or text[j] == "@"):
                        j += 1
                    if j == i + 1:
                        break
                    named = True        # \newcommand\x{..}: a bare name
                    i = j
                    continue
                break
        if end is None:
            continue
        spans.append((m.start(), end))
        outer_end = end
    return spans


def inside_definition(spans: List[Tuple[int, int]], pos: int) -> bool:
    """Is ``pos`` strictly inside one of the top-level definitions?"""
    k = bisect.bisect_right(spans, (pos, float("inf"))) - 1
    return k >= 0 and spans[k][0] < pos < spans[k][1]


#: control words that move or rule a box.  A macro made only of these draws a
#: symbol; expanding it hands the picture to the maths reader, which then reads
#: the drawing's arguments as factors (``\stackengine``'s ``{O}{r}{F}{F}{S}``
#: became five letter names, and ``\htext (48 15)`` five formulas in one paper).
DRAWING_MARKERS = ("\\setbox", "\\rule", "\\stackengine", "\\htext",
                   "\\bsegment", "\\esegment", "\\lvec", "\\clvec",
                   "\\rlvec", "\\ravec", "\\avec", "\\move", "\\kern",
                   "\\rlap", "\\llap", "\\clap", "\\smash", "\\hbox",
                   "\\vbox", "\\raisebox")


def only_draws(body: str) -> bool:
    """True when a macro body is a picture and nothing else.

    A drawing shows letters inside ``$..$`` labels and positions them with
    dimensions; what is left once labels, commands and dimensions go is
    punctuation.  A body with any letter left over is maths or text and must be
    expanded as before.
    """
    if not any(marker in body for marker in DRAWING_MARKERS):
        return False
    stripped = re.sub(r"\$[^$]*\$", " ", body)
    stripped = re.sub(r"\\[A-Za-z@]+\*?", " ", stripped)
    stripped = re.sub(r"-?\d*\.?\d+\s*(?:pt|em|ex|cm|mm|in|bp|pc|sp)\b",
                      " ", stripped)
    # the arguments of a drawing command are positions, rules and alignment
    # letters (\stackengine{..}{..}{..}{O}{r}{F}{F}{S}), never content
    for _ in range(4):
        stripped = re.sub(r"\{[^{}]*\}", " ", stripped)
    return not re.search(r"[A-Za-z]", stripped)


#: A paper that redefines one of these with \@startsection replaces the command
#: with a pile of layout arguments, and the title ends up in the prose instead
#: of the section field (RULES.md).  The definition is not read, so the
#: standard meaning survives.
STANDARD_SECTION_NAMES = {
"part", "chapter", "section", "subsection", "subsubsection",
"paragraph", "subparagraph",
}


def macro_arguments(text, i, nargs, default):
    """Read one invocation without consuming a following structural token."""
    args: List[str] = []
    ok = True
    if default is not None:
        j = i
        while j < len(text) and text[j] in " \t\n":
            j += 1
        if j < len(text) and text[j] == "[":
            end = find_group_end(text, j)
            if end is None:
                ok = False
            else:
                args.append(text[j + 1: end - 1])
                i = end
        if ok and not args:
            args.append(default)      # the optional argument is absent
    need = nargs - len(args) if default is not None else nargs
    for _ in range(max(0, need)):
        while i < len(text) and text[i] in " \t\n":
            i += 1
        if i < len(text) and text[i] == "{":
            end = find_group_end(text, i)
            if end is None:
                ok = False
                break
            args.append(text[i + 1: end - 1])
            i = end
        else:
            # TeX takes one token when the braces are missing:
            # ``\def\B#1{\mathbf{#1}}`` is called as ``\B C``,
            # ``\mathbbe n``, ``\Set{x \mid ...}``.  Only accepting
            # ``{..}`` left those calls unexpanded, and the name then
            # looked like an unknown command of the paper (\B 125 uses,
            # \mathbbe 91, \Set 42 in the 20-paper sample).
            end = _single_token_end(text, i)
            if end is None:
                ok = False
                break
            args.append(text[i:end])
            i = end
    return (args, i) if ok else None


class MacroTable:
    """Collects ``\\newcommand`` and the other ways a paper defines a macro."""

    NEWCMD_RE = DECL_FORMS[0][2]
    DECLOP_RE = DECL_FORMS[2][2]
    THEOREM_RE = re.compile(r"\\newtheorem\*?\s*\{\s*([A-Za-z*]+)\s*\}")
    #: ``\def\x#1#2{..}`` -- the parameter text sits between name and body
    #: a name is a control word, or one character for a control symbol
    #: (``\def\7#1{...}``) -- the character form was not read at all
    DEF_RE = re.compile(r"\\(?:g|e|x)?def\s*"
                        r"(?:\\([A-Za-z@]+)|\\([^A-Za-z@\s]))"
                        r"((?:[ \t]*#[1-9])*)\s*\{")
    #: ``\let\x\y`` / ``\let\x=\y``
    LET_RE = re.compile(r"\\let\s*\\([A-Za-z@]+)\s*=?\s*\\([A-Za-z@]+)")
    #: ``\\let\\x=a``: gives a control sequence a character meaning
    LET_CHAR_RE = re.compile(r"\\let\s*\\([A-Za-z@]+)\s*=?\s*([^\\\s{}])")

    def __init__(self) -> None:
        #: name -> (nargs, replacement body, default for the optional first arg)
        self.macros: Dict[str, Tuple[int, str, Optional[str]]] = {}
        #: environments declared by \newtheorem, i.e. theorem-like blocks
        self.envs: set = set()
        #: paper-defined aliases of a declarer, e.g. ``nc`` for ``newcommand``
        self.decl_aliases: Dict[str, str] = {}
        self._lets: List[Tuple[str, str]] = []
        #: symbol font name -> font family, from ``\DeclareSymbolFont``
        self._symbol_families: Dict[str, str] = {}
        #: font family -> how many alphabet names were declared with it.  A
        #: family the table does not recognise is not an error: it becomes the
        #: neutral upright font, and this count says how often that happened.
        self.alphabet_families: Dict[str, int] = {}
        #: definitions not read because they sit inside another definition
        self.nested_skipped = 0
        #: macros kept as they are because their body only draws a symbol
        self.drawing_kept: set = set()
        #: macros kept because their expansion introduces an unknown name
        self.kept_unknown: set = set()
        #: name -> (nargs, body, separator) for ``\def\x(#1,#2)``
        self.delimited: Dict[str, Tuple[int, str, str]] = {}
        #: (name, character) pairs from ``\\let\\x=a``
        self._lets_char: List[Tuple[str, str]] = []
        #: standard section commands whose paper definition was ignored
        self.skipped_standard: set = set()

    # -- parsing --------------------------------------------------------
    def _store(self, name: str, value) -> None:
        """Record a definition unless it redefines a standard section command."""
        if name in STANDARD_SECTION_NAMES:
            self.skipped_standard.add(name)
            return
        self.macros[name] = value

    def parse(self, text: str) -> None:
        self._parse_once(text)
        for _ in range(3):
            found = self._find_decl_aliases()
            if not found:
                break
            self.decl_aliases.update(found)
            self._parse_once(text)
        self._resolve_lets()

    def _parse_once(self, text: str) -> None:
        spans = definition_spans(text, tuple(self.decl_aliases))

        def nested(m) -> bool:
            if inside_definition(spans, m.start()):
                self.nested_skipped += 1
                return True
            return False

        # the symbol fonts first: an alphabet may name one of them, and the
        # declaration may sit anywhere in the file
        for m in SYMBOL_FONT_RE.finditer(text):
            self._symbol_families[m.group(1)] = m.group(3).strip()
        for m in ALPHABET_DECL_RE.finditer(text):
            if nested(m):
                continue
            self._add_alphabet(m.group(1), m.group(3))
        for m in SYMBOL_ALPHABET_RE.finditer(text):
            if nested(m):
                continue
            font = m.group(2).strip()
            self._add_alphabet(m.group(1), self._symbol_families.get(font, font))

        for _name, kind, pat in DECL_FORMS:
            for m in pat.finditer(text):
                if nested(m):
                    continue
                end = find_group_end(text, m.end() - 1)
                if end is None:
                    continue
                body = text[m.end(): end - 1]
                if kind == "op":
                    self._store(m.group(1),
                                (0, "\\operatorname{" + body + "}", None))
                    continue
                nargs = int(m.group(2) or 0)
                default = m.group(3)
                self._store(m.group(1), (nargs, body, default))
        for m in XPARS_NEW_RE.finditer(text):
            if nested(m):
                continue
            end = find_group_end(text, m.end() - 1)
            if end is None:
                continue
            self._store(m.group(1),
                        (xparse_nargs(m.group(2)), text[m.end(): end - 1], None))
        for m in self.DEF_RE.finditer(text):
            if nested(m):
                continue
            end = find_group_end(text, m.end() - 1)
            if end is None:
                continue
            self._store(m.group(1) or m.group(2),
                        (len(re.findall(r"#[1-9]", m.group(3))),
                         text[m.end(): end - 1], None))
        for alias, declarer in self.decl_aliases.items():
            self._parse_alias(text, alias, declarer, nested)
        for m in self.LET_RE.finditer(text):
            if nested(m):
                continue
            self._lets.append((m.group(1), m.group(2)))
        for m in self.LET_CHAR_RE.finditer(text):
            if nested(m):
                continue
            self._lets_char.append((m.group(1), m.group(2)))
        for m in self.THEOREM_RE.finditer(text):
            self.envs.add(m.group(1))
        self._parse_more_declarations(text, nested)

    def _parse_more_declarations(self, text: str, nested) -> None:
        """Read additional macro and symbol declarations."""
        for m in PAIRED_DELIMX_RE.finditer(text):
            if nested(m):
                continue
            pos = find_group_end(text, m.end() - 1)
            if pos is None:
                continue
            right = text[m.end(): pos - 1]
            pos2 = find_group_end(text, pos - 1)
            if pos2 is None:
                continue
            body = text[pos: pos2 - 1]
            self._store(m.group(1), (int(m.group(2)), body, None))
        for m in PAIRED_DELIM_RE.finditer(text):
            if nested(m) or m.group(0).startswith("\\DeclarePairedDelimiterX"):
                continue
            end = find_group_end(text, m.end() - 1)
            if end is None:
                continue
            right = text[m.end(): end - 1]
            end2 = find_group_end(text, end - 1)
            if end2 is None:
                continue
            # ``\lVert`` + arg + ``\rVert``
            self._store(m.group(1), (1, right + "#1" + text[end: end2 - 1], None))
        for m in DECLARE_MATH_SYMBOL_RE.finditer(text):
            if nested(m):
                continue
            code = m.group(4).strip()
            hexcode = code[1:] if code.startswith('"') else code
            try:
                glyph = chr(int(hexcode, 16))
            except ValueError:
                continue
            font = MATH_SYMBOL_FONTS.get(m.group(3).strip())
            if font == "operatorname":
                body = "\\operatorname{%s}" % glyph
            elif font:
                body = "\\%s{%s}" % (font, glyph)
            else:
                body = glyph
            self._store(m.group(1), (0, body, None))
        for m in DELIMITED_DEF_RE.finditer(text):
            if nested(m):
                continue
            end = find_group_end(text, m.end() - 1)
            if end is None:
                continue
            parts = [p.strip() for p in m.group(2).split(",")]
            self.delimited[m.group(1)] = (len(parts), text[m.end(): end - 1], ",")

    def _add_alphabet(self, name: str, family: str) -> None:
        """One declared alphabet: ``\\name{arg}`` is the font applied to ``arg``.

        A paper is free to declare its own alphabet names -- ``\\mathbbe`` for
        blackboard bold, ``\\Cat`` for calligraphic -- and then use them as if
        they were ordinary commands.  The declaration states the font family,
        so the name can be read without asking anything.
        """
        command = alphabet_font_command(family)
        self.alphabet_families[(family or "").strip() or "?"] = (
            self.alphabet_families.get((family or "").strip() or "?", 0) + 1)
        self.macros[name] = (1, "\\%s{#1}" % command, None)

    def _parse_alias(self, text: str, alias: str, declarer: str,
                     nested=lambda m: False) -> None:
        # ``(?![A-Za-z])``: an alias ``\nc`` must not match inside ``\ncong``
        a = (r"\\" + re.escape(alias) + r"(?![A-Za-z@])\*?\s*\{?\s*"
             r"\\([A-Za-z@]+)\s*\}?\s*")
        if declarer in ("DeclareMathOperator",):
            for m in re.finditer(a + r"\{", text):
                if nested(m):
                    continue
                end = find_group_end(text, m.end() - 1)
                if end is not None:
                    self.macros[m.group(1)] = (
                        0, "\\operatorname{" + text[m.end(): end - 1] + "}", None)
            return
        pat = re.compile(a + r"(?:\[(\d)\])?\s*(?:\[([^\]]*)\])?\s*\{")
        for m in pat.finditer(text):
            if nested(m):
                continue
            end = find_group_end(text, m.end() - 1)
            if end is None:
                continue
            nargs = int(m.group(2) or 0)
            default = m.group(3)
            if default is not None and not nargs:
                nargs = 1
            self._store(m.group(1), (nargs, text[m.end(): end - 1], default))
    def _find_decl_aliases(self) -> Dict[str, str]:
        found = {}
        for name, spec in list(self.macros.items()):
            nargs, body, default = spec
            b = body.strip()
            if (nargs == 0 and default is None and b.startswith("\\")
                    and "#" not in b and b[1:] in DECLARERS
                    and name not in self.decl_aliases):
                found[name] = b[1:]
        for alias in found:
            self.macros.pop(alias, None)
        return found

    def _resolve_lets(self) -> None:
        """Turn ``\\let\\a\\b`` into a substitution, when that is faithful.

        Two situations defeat a textual substitution and must be left alone:

        * the paper defines the target itself, so ``\\let`` captured whatever
          meaning was in force at that line, not the final one;
        * the ``\\let`` lines form a cycle (``\\let\\temp\\phi \\let\\phi\\varphi
          \\let\\varphi\\temp`` swaps two symbols).  Substituting one name by
          the other would merge the two -- exactly what must not happen to
          ``\\phi`` and ``\\varphi`` -- so a cycle is dropped whole.
        """
        for name, char in getattr(self, "_lets_char", ()):
            self.macros.setdefault(name, (0, char, None))
        aliases = {a for a, _t in self._lets}
        targets = {t for _a, t in self._lets}
        tangled = aliases & targets
        for alias, target in self._lets:
            if alias in tangled or target in tangled:
                continue
            if alias not in self.macros and target not in self.macros:
                self.macros[alias] = (0, "\\" + target, None)

    # -- expansion ------------------------------------------------------
    def _cyclic_names(self) -> set:
        """Macros that (directly or through others) call themselves.

        ``lacromay.sty`` writes ``\\newcommand{\\mathbb}[1]{\\protect\\text{$\\protect\\Bbb{#1}$}}``
        and ``\\Bbb`` points back at ``\\mathbb``; substituting either one wraps
        the argument once more on every pass, so the text grows without limit.
        Neither macro refers to *itself*, so the cycle has to be found in the
        graph, not by looking at one body.
        """
        refs = {name: set(re.findall(r"\\([A-Za-z@]+)", spec[1]))
                for name, spec in self.macros.items()}
        index: Dict[str, int] = {}
        low: Dict[str, int] = {}
        on: set = set()
        stack: List[str] = []
        out: set = set()
        counter = [0]

        def strong(root: str) -> None:
            index[root] = low[root] = counter[0]
            counter[0] += 1
            stack.append(root)
            on.add(root)
            work = [(root, iter(sorted(refs[root])))]
            while work:
                node, it = work[-1]
                pushed = False
                for w in it:
                    if w not in refs:
                        continue
                    if w not in index:
                        index[w] = low[w] = counter[0]
                        counter[0] += 1
                        stack.append(w)
                        on.add(w)
                        work.append((w, iter(sorted(refs[w]))))
                        pushed = True
                        break
                    if w in on:
                        low[node] = min(low[node], index[w])
                if pushed:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    if len(comp) > 1 or node in refs[node]:
                        out.update(comp)

        for name in refs:
            if name not in index:
                strong(name)
        return out

    def expand(self, text: str, rounds: int = 8) -> str:
        cyclic = self._cyclic_names()
        for _ in range(rounds):
            before = text
            for name, (nargs, body, sep) in self.delimited.items():
                text = self._expand_delimited(text, name, nargs, body, sep)
            for name, spec in self.macros.items():
                if "@" in name or name in cyclic:
                    # ``@``: LaTeX internals (\@ifstar, \tagform@) -- inlining
                    # them hands private tokens to the cleanup passes.
                    # cyclic: substituting would grow the text on every pass.
                    continue
                nargs, body, default = spec
                if name in self.kept_unknown:
                    # its expansion reaches a name nothing knows: see
                    # normalize._macros_with_unknown_names
                    continue
                if only_draws(body):
                    self.drawing_kept.add(name)
                    continue
                text = self._expand_one(text, name, nargs, body, default)
            if text == before:
                break
        return text

    @staticmethod
    def _expand_delimited(text: str, name: str, nargs: int, body: str,
                          sep: str) -> str:
        pat = re.compile(r"\\" + re.escape(name) + r"\s*\(([^()]*)\)")

        def repl(m) -> str:
            args = [a.strip() for a in m.group(1).split(sep)]
            if len(args) != nargs:
                return m.group(0)
            out = body
            for i, a in enumerate(args, 1):
                out = out.replace("#%d" % i, a)
            return out

        return pat.sub(repl, text)

    @staticmethod
    def _expand_one(text: str, name: str, nargs: int, body: str,
                    default: Optional[str] = None) -> str:
        pat = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")

        def guard(repl: str, start: int, end: int) -> str:
            """Keep a substitution from fusing with its neighbours.

            ``\\in\\Hal`` is legal TeX: two control words need no separator.
            Substituting ``\\Hal`` by ``H^*_{\\alg}`` produced
            ``\\inH^*_{\\alg}`` -- a command that does not exist, which then
            showed up as an open name of its own (28 times in 1901.04848).
            A space goes in whenever the join would change the token.
            """
            if not repl:
                return repl
            if repl[0].isalpha() and re.search(r"\\[A-Za-z@]+$", text[:start]):
                repl = " " + repl
            if text[end:end + 1].isalpha() and \
                    re.search(r"\\[A-Za-z@]+$", repl):
                repl = repl + " "
            return repl

        if nargs == 0 and default is None:
            return pat.sub(lambda m: guard(body, m.start(), m.end()), text)
        out: List[str] = []
        pos = 0
        for m in pat.finditer(text):
            if m.start() < pos:
                # this invocation sits inside an argument list the previous
                # substitution already consumed: expanding it again duplicated
                # the text and left a stray brace (C7, RULES.md)
                continue
            out.append(text[pos: m.start()])
            parsed = macro_arguments(text, m.end(), nargs, default)
            if parsed is None:
                out.append(m.group(0))
                pos = m.end()
                continue
            args, i = parsed
            repl = re.sub(r"#([1-9])", lambda h: args[int(h[1])-1] if int(h[1]) <= len(args) else h[0], body)
            out.append(guard(repl, m.start(), i))
            pos = i
        out.append(text[pos:])
        return "".join(out)


# --------------------------------------------------------------------------
# text cleanup
# --------------------------------------------------------------------------


#: ``\begin{x}`` / ``\end{x}`` as a token, for deciding what a piece of code
#: leaves open
ENV_TOKEN_RE = re.compile(r"\\(begin|end)\s*\{([A-Za-z@*]+)\}")


def env_stack(text: str) -> Tuple[List[str], List[str]]:
    """Return ``(still open, closed without an opener)`` for a fragment."""
    stack: List[str] = []
    stray: List[str] = []
    for m in ENV_TOKEN_RE.finditer(text):
        if m.group(1) == "begin":
            stack.append(m.group(2))
        elif stack and stack[-1] == m.group(2):
            stack.pop()
        else:
            stray.append(m.group(2))
    return stack, stray


#: control symbols such as \'e.  A non-letter after the backslash cannot start
#: a control word, so these are unambiguous
ACCENT_SYMBOL_RE = re.compile(r"\\(['`\"^~=.])\s*\{?([A-Za-z])\}?")
#: letter accents.  A bare \u, \b, \c ... is a control word far more often
#: than an accent (\begin!), so braces or whitespace are required here
ACCENT_LETTER_RE = re.compile(
    r"\\([uvHckbr])\s*(?:\{([A-Za-z])\}|\s([A-Za-z]))")


#: ``\i`` and ``\j`` are dotless letters, not accents: they carry a base
#: character that the accent pass needs.  ``\v{S}\v{t}ov\'\i\v{c}ek`` kept
#: both command names because neither regex could see a letter after the
#: accent, and ``Be\u{\i}linson`` lost its breve.  They are replaced first,
#: and only when no argument follows -- a paper that defines ``\i`` as a macro
#: of its own keeps it.
DOTLESS_RE = re.compile(r"\\([ij])(?![A-Za-z{])")

#: The same lost argument, in the shape the brace guard leaves behind:
#: ``Be\u{\i}linson`` loses its braces first and reaches this pass as
#: ``Be\u\ilinson``.  TeX would read that as the undefined ``\ilinson``, so
#: only the accent-plus-dotless shape is repaired here, never a name the paper
#: might define.
MANGLED_ACCENT_RE = re.compile(r"\\([uvHckbr])\\([ij])(?=[A-Za-z])")


def apply_accents(text: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        acc = ACCENTS.get(m.group(1))
        if acc is None:
            return m.group(0)
        base = m.group(2) or m.group(3)
        return unicodedata.normalize("NFC", base + acc)

    text = MANGLED_ACCENT_RE.sub(
        lambda m: unicodedata.normalize("NFC", m.group(2) + ACCENTS[m.group(1)]),
        text)
    text = DOTLESS_RE.sub(lambda m: m.group(1), text)
    text = ACCENT_SYMBOL_RE.sub(repl, text)
    text = ACCENT_LETTER_RE.sub(repl, text)
    return text


#: purely typographic commands: their argument is prose, so a formula inside
#: them must still be found and lifted
FORMAT_CMDS = ("emph", "textit", "textbf", "textsc", "textsl", "textup",
               "texttt", "textnormal", "underline", "mbox", "hbox")

#: the six maths fonts.  ``normalize`` puts them back where a switch used to say
#: which glyphs a run is set in (``{\rm WEH}`` -> ``\mathrm{WEH}``), and prose
#: treats them as transparent.  Deliberately *not* in FORMAT_CMDS:
#: verify_formulas uses that tuple as the whitelist of commands whose
#: disappearance is not content loss, and a vanished \mathbf is a loss.
MATH_FONT_CMDS = ("mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathcal")


def unwrap_formatting(text: str) -> str:
    """``\\emph{x}`` -> ``x`` for purely typographic commands."""
    cmds = FORMAT_CMDS + MATH_FONT_CMDS
    changed = True
    while changed:
        changed = False
        for cmd in cmds:
            m = re.search(r"\\" + cmd + r"\s*\{", text)
            if not m:
                continue
            end = find_group_end(text, m.end() - 1)
            if end is None:
                continue
            text = text[: m.start()] + text[m.end(): end - 1] + text[end:]
            changed = True
    return text


#: commands that only say how a display may break across pages.  Written as a
#: declaration or wrapped around the display: \allowdisplaybreaks{ \begin{align}
LAYOUT_WRAPPERS = ("allowdisplaybreaks",)


def unwrap_layout(text: str) -> str:
    """Remove a layout wrapper and keep what its braces hold.

    Left in place, the math scanner skips the group as the command's argument
    and the display inside reaches the sentence stream as raw LaTeX.
    """
    for cmd in LAYOUT_WRAPPERS:
        pat = re.compile(r"\\" + cmd + r"(?![A-Za-z])\s*(?:\[[^\]]*\])?")
        pos = 0
        while True:
            m = pat.search(text, pos)
            if not m:
                break
            pos = m.start()
            if text.startswith("{", m.end()):
                end = find_group_end(text, m.end())
                if end is not None:
                    # strip: the newline after "{" must not join the one
                    # before the command into a paragraph break
                    text = (text[: m.start()]
                            + text[m.end() + 1: end - 1].strip() + text[end:])
                    continue
            text = text[: m.start()] + " " + text[m.end():]
    return text


def drop_command_with_arg(text: str, cmds: Sequence[str]) -> str:
    for cmd in cmds:
        while True:
            m = re.search(r"\\" + re.escape(cmd) + r"(?![A-Za-z])\s*(\[[^\]]*\])?\s*\{",
                          text)
            if not m:
                break
            end = find_group_end(text, m.end() - 1)
            if end is None:
                break
            text = text[: m.start()] + " " + text[end:]
    return text


def drop_command_all_args(text: str, cmds: Sequence[str]) -> str:
    """Remove ``\\cmd`` together with every brace/bracket group attached to it."""
    for cmd in cmds:
        pat = re.compile(r"\\" + re.escape(cmd) + r"(?![A-Za-z])")
        while True:
            m = pat.search(text)
            if not m:
                break
            i = m.end()
            while i < len(text) and text[i] in " \t\n":
                i += 1
            while i < len(text) and text[i] in "[{":
                end = find_group_end(text, i)
                if end is None:
                    break
                i = end
                while i < len(text) and text[i] in " \t\n":
                    i += 1
            text = text[: m.start()] + " " + text[i:]
    return text


def drop_metadata(text: str) -> str:
    """A group after the last metadata argument belongs to the paper."""
    arities = {'setcounter': 2, 'addtocounter': 2, 'stepcounter': 1, 'refstepcounter': 1,
               'setlength': 2, 'addtolength': 2, 'pagestyle': 1, 'thispagestyle': 1,
               'bibliographystyle': 1, 'bibliography': 1, 'includeonly': 1,
               'addcontentsline': 3, 'addtocontents': 2}
    pattern = re.compile(r'\\(' + '|'.join(arities) + r')(?![A-Za-z@])')
    out, pos = [], 0
    for m in pattern.finditer(text):
        if m.start() < pos:
            continue
        args = macro_arguments(text, m.end(), arities[m[1]], None)
        if args is not None:
            out.extend((text[pos:m.start()], ' '))
            pos = args[1]
    out.append(text[pos:])
    return drop_command_all_args(''.join(out), [cmd for cmd in META_CMDS if cmd not in arities])


def normalize_dashes_quotes(text: str) -> str:
    text = text.replace("---", "-").replace("--", "-")
    text = text.replace("``", '"').replace("''", '"')
    text = text.replace("~", " ")
    text = text.replace("\\%", "%").replace("\\&", "&")
    text = text.replace("\\_", "_").replace("\\#", "#").replace("\\$", "$")
    text = text.replace("\\\\", " ").replace("\\ ", " ")
    text = text.replace("{}", "")
    return text


def collapse_ws(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def split_sentences(text: str) -> List[str]:
    """Split prose into sentences, protecting abbreviations and initials."""
    t = re.sub(r"([。！？][”’）]*)", lambda m: m.group(1) + '\ue202', text)
    for ab in ABBREVIATIONS_ALWAYS:
        t = re.sub(r"(?<![A-Za-z])" + re.escape(ab),
                   ab[:-1] + PROTECTED_DOT, t)
    for ab in ABBREVIATIONS_BEFORE_DIGIT:
        # "Vol. 3" and "No. 12" are labels; "No. We disagree." is a sentence
        t = re.sub(r"(?<![A-Za-z])" + re.escape(ab) + r"(?=\s*[\d(\[])",
                   ab[:-1] + PROTECTED_DOT, t)
    # a run of initials ("S.N.", "U.S.") is protected; a single "A." before a
    # capitalised word is a sentence end (AUDIT.md 26)
    t = re.sub(r"(?<=\b[A-Z]\.)([A-Z])\.", r"\1" + PROTECTED_DOT, t)
    t = re.sub(r"\b([A-Z])\.(?=\s+[A-Z]\.)", r"\1" + PROTECTED_DOT, t)
    t = re.sub(r"(?<=\d)\.(?=\d)", PROTECTED_DOT, t)
    parts = re.split(r"(?<=[.!?。！？])\s+|\ue202", t)
    # "(In particular ... MATH529.) Then ..." -- the full stop sits before the
    # closing parenthesis, so the split has to look one character back
    split_more: List[str] = []
    for p in parts:
        split_more.extend(re.split(r"(?<=[.!?]\))\s+", p))
    return [p.replace(PROTECTED_DOT, ".").strip() for p in split_more if p.strip()]


#: separator inside a boundary signature; the two halves are the text on either
#: side of the full stop, so the signature survives renumbering
BOUNDARY_SEP = " ‖ "

#: answers for "is this full stop the end of a sentence?"
BOUNDARY_ANSWERS = ("split", "join")

BOUNDARY_INSTRUCTIONS = (
    "Each item is one full stop that the splitter treated as the end of a "
    "sentence, although the next word does not start with a capital letter. "
    "Answer 'split' when it really is the end of a sentence (a short last "
    "sentence, an abbreviation, a symbol), 'join' when the two halves are one "
    "sentence (the stop belongs to an abbreviation the list does not know, or "
    "the author broke the sentence). 'before' and 'after' are the two halves; "
    "'sig' is how the pipeline recognises the same stop next time."
)


def boundary_sig(left: str, right: str, width: int = 32) -> str:
    return (collapse_ws(left)[-width:] + BOUNDARY_SEP
            + collapse_ws(right)[:width])


#: a full stop that belongs to the token in front of it: a run of initials
#: ("S.N.", "U.S.") or an abbreviation the splitter protects.  A single "A." is a
#: sentence end -- keeping it turned "by A. This set is open." into one sentence
#: (AUDIT.md 26).
_KEEP_DOT_RE = re.compile(r"(?:[A-Za-z]\.){2,}$")


_ABBREVIATION_KEYS = {a.lower() for a in
                      ABBREVIATIONS_ALWAYS + ABBREVIATIONS_BEFORE_DIGIT}


def _keeps_its_dot(left: str) -> bool:
    tail = left.rstrip().rsplit(" ", 1)[-1]
    if _KEEP_DOT_RE.fullmatch(tail):
        return True
    # case-insensitive: "No." is in the before_digit list, "no." in always
    return tail.lower() in _ABBREVIATION_KEYS


def join_boundary(left: str, right: str) -> str:
    """Glue two halves back together.

    A stop that was not a sentence end is dropped, unless it belongs to the
    token before it: "No." and "S.N." keep their dot, "the map." does not.
    """
    left = left.rstrip()
    if left.endswith(".") and not _keeps_its_dot(left):
        left = left[:-1].rstrip()
    return left + " " + right.lstrip()


def _starts_sentence(text: str) -> bool:
    """A capital letter, or a bracket in front of one.

    "(See Figure 3.)" and "(Note that ...)" open sentences just as "See does";
    only a lower-case first word is evidence that the previous full stop was
    not the end of a sentence.
    """
    t = text.lstrip()
    while t[:1] in ("(", "[", "«", "“", '"'):
        t = t[1:].lstrip()
    return bool(t[:1].isupper() or (t[:1].isalpha() and not t[:1].islower()))


# --------------------------------------------------------------------------
# tokenisation
# --------------------------------------------------------------------------

#: one pass over a sentence; every non-space character is matched exactly once,
#: so ``"".join(tokens)`` reproduces the sentence with whitespace removed.
#: Order matters: placeholders and hyphenated compounds must win over plain
#: words, otherwise ``MATH95-face`` would be split into three tokens.
TOKEN_RE = re.compile(r"""
      MATH\d+(?:-[^\W\d_]+)*          # placeholder, optionally with its affix
    | REF\d+
    | (?:%(abbr)s)(?![^\W\d_])        # abbreviation as one token
    | [^\W\d_]+(?:-[^\W\d_]+)+        # red-blue
    | [^\W\d_]+['\u2019]s(?![^\W\d_])  # Norine's -> Norine | 's handled below
    | [^\W\d_]+
    | \d+(?:[.,]\d+)*
    | [^\s]
""" % {"abbr": "|".join(re.escape(a) for a in TOKEN_ABBREVIATIONS)}, re.X)

POSSESSIVE_RE = re.compile(r"^([^\W\d_]+)(['\u2019]s)$")


def tokenize(sentence: str) -> List[str]:
    """PTB-like tokenisation, tuned for this pipeline.

    * ``MATH7`` / ``MATH7-face`` / ``REF3`` stay single tokens, so the formula
      table can be spliced back position by position;
    * hyphenated compounds stay single tokens (CCGbank treats them so);
    * possessives are split into ``Norine`` + ``'s``;
    * every other punctuation mark becomes its own token.

    The last alternative used to be ``[^\\w\\s]``, which leaves out everything
    ``\\w`` matches -- and ``\\w`` includes the underscore.  So ``_`` matched no
    alternative at all and was silently deleted: ``x_1`` came out as ``x 1`` and
    the promised round trip failed on 197 sentences of the 20-paper sample.  Any
    leftover non-space character now becomes a token of its own.
    """
    out: List[str] = []
    for tok in TOKEN_RE.findall(sentence):
        m = POSSESSIVE_RE.match(tok)
        if m:
            out.append(m.group(1))
            out.append(m.group(2))          # keep the apostrophe the paper uses
        else:
            out.append(tok)
    # Fallback for abbreviations that are not in ABBREVIATIONS: sentence
    # splitting already happened, so only the final period of a sentence can be
    # the sentence period.  Any other period belongs to the token before it
    # ("J. Smith", "al.").  This is what keeps an unknown abbreviation from
    # turning into a spurious full stop.
    for i in range(len(out) - 1):
        if out[i] == "." and i and out[i - 1][-1:] not in ",;:.!?":
            out[i - 1] += "."
            out[i] = ""
    return [t for t in out if t]


# --------------------------------------------------------------------------
# math scanning
# --------------------------------------------------------------------------


ALIGN_TOKEN = re.compile(r"\\(?:begin|end)\s*\{[^{}]+\}|\\\\|\\[a-zA-Z@]+|\\.|[{}&]")
ROW_NUMBERED_ENVS = {"align", "alignat", "flalign", "xalignat", "xxalignat", "gather", "eqnarray", "IEEEeqnarray"}
DISPLAY_LABEL = re.compile(r"\\(rowlabel|label|taglabel|tag)\*?\s*\{")


def alignment_cells(body):
    """Split only alignment tabs and row breaks belonging to the outer display."""
    rows, cells = [], []
    start = depth = 0
    for token in ALIGN_TOKEN.finditer(body):
        if token.start() < start:
            continue
        value = token[0]
        if value == "{" or value.startswith(r"\begin") and value.endswith("}"):
            depth += 1
        elif value == "}" or value.startswith(r"\end") and value.endswith("}"):
            depth = max(0, depth - 1)
        elif not depth and value in ("&", r"\\"):
            cells.append(body[start:token.start()])
            start = token.end()
            if value == r"\\":
                rows.append(cells)
                cells = []
                option = re.match(r"\*?\s*\[", body[start:])
                if option:
                    start = find_group_end(body, start + option.end() - 1) or start
                elif body.startswith("*", start):
                    start += 1
    cells.append(body[start:])
    if not rows or any(cell.strip() for cell in cells):
        rows.append(cells)
    return rows


def display_marks(body):
    """Read balanced label/tag arguments, preserving offsets for source cleanup."""
    pos = 0
    for m in DISPLAY_LABEL.finditer(body):
        if m.start() < pos or (m.start() - len(body[:m.start()].rstrip("\\"))) % 2:
            continue
        pos = find_group_end(body, m.end()-1)
        if pos is None:
            return
        yield m.start(), pos, m[1], body[m.end():pos-1]


def formula_destinations(formulas):
    targets = {}
    for f in formulas:
        if f.get("kind") not in ("display", "inline"):
            continue
        for label in f.get("labels", []):
            targets[label] = {"formula": f["id"]}
        for n, row in enumerate(f.get("rows", []), 1):
            for label in row.get("labels", []):
                targets[label] = {"formula": f["id"], "row": n}
    return targets


def reference_keys(target):
    # A comma may also occur inside a single label; callers prefer an exact match.
    return [k.strip() for k in (target or "").split(",") if k.strip()]


def find_math_spans(text: str, clean: bool = True, inspect_arguments=False,
                    report_unclosed=True) -> List[Span]:
    """Locate ``$..$``, ``$$..$$``, ``\\(..\\)``, ``\\[..\\]`` and display envs."""
    spans: List[Span] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]

        if c == "\\":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "[":
                end = text.find("\\]", i + 2)
                if end == -1:
                    # unclosed: the rest is still a display, not prose
                    if report_unclosed:
                        _issues.record("math:unclosed", "latex2nl.find_math_spans",
                                   where="$$", size=1)
                    spans.append(Span(i, n, "display", text[i + 2:]))
                    i = n
                    continue
                spans.append(Span(i, end + 2, "display", text[i + 2: end]))
                i = end + 2
                continue
            if nxt == "(":
                end = text.find("\\)", i + 2)
                if end == -1:
                    if report_unclosed:
                        _issues.record('math:unclosed', 'latex2nl.find_math_spans', where=r'\(', size=1)
                    spans.append(Span(i, n, "inline", text[i + 2:]))
                    i = n
                    continue
                spans.append(Span(i, end + 2, "inline", text[i + 2: end]))
                i = end + 2
                continue
            if nxt == "$":                       # escaped dollar: literal text
                i += 2
                continue
            em = re.match(r"\\ensuremath\s*\{", text[i:])
            if em:
                open_at = i + em.end() - 1
                end = find_group_end(text, open_at)
                if end is not None:
                    spans.append(Span(i, end, "inline", text[open_at + 1: end - 1]))
                    i = end
                    continue
                i += em.end()
                continue
            m = re.match(r"\\begin\s*\{([A-Za-z]+\*?)\}", text[i:])
            if m and m.group(1) == "diagram":
                # a drawing that is not inside a display is still an opaque
                # object, not prose: left in the text, a texdraw body became 108
                # "sentences" of \move/\bsegment/\special.  It becomes one
                # formula record marked as a diagram, so the reader still sees
                # that something was drawn here.
                after = i + m.end()
                em = find_env_end(text, after, "diagram")
                if em is None:
                    i = after
                    continue
                spans.append(Span(i, em.end(), "inline",
                                  text[i: em.end()], env="diagram"))
                i = em.end()
                continue
            if m and (m.group(1) in MATH_ENVS or m.group(1) in ('display', 'math')):
                env = m.group(1)
                kind = 'inline' if env == 'math' else 'display'
                after = i + m.end()
                em = find_env_end(text, after, env)
                if em is None:
                    if report_unclosed:
                        _issues.record("math:unclosed", "latex2nl.find_math_spans",
                                   where="display-env", size=1)
                    # an unclosed display env is still mathematics; treating it
                    # as prose turned \mathfrak{F}=1 into a sentence (AUDIT.md 22)
                    spans.append(Span(i, n, kind, text[after:], env=env))
                    i = n
                    continue
                spans.append(Span(i, em.end(), kind,
                                  text[after: em.start()], env=env))
                i = em.end()
                continue
            name_m = re.match(r"\\([A-Za-z]+)", text[i:])
            if name_m and (name_m.group(1) in FORMAT_CMDS
                           or name_m.group(1) in MATH_FONT_CMDS):
                # skip the name only: \textbf{Property $(i)$} holds a formula
                i += name_m.end()
                continue
            if inspect_arguments and name_m:
                i += name_m.end()
                continue
            i = skip_control_sequence(text, i)
            continue

        if c == "$":
            if text.startswith("$$", i):
                end = text.find("$$", i + 2)
                if end == -1:
                    if report_unclosed:
                        _issues.record('math:unclosed', 'latex2nl.find_math_spans', where='$$', size=1)
                    # unclosed: the rest is still a display (AUDIT.md 21)
                    spans.append(Span(i, n, "display", text[i + 2:]))
                    i = n
                    continue
                spans.append(Span(i, end + 2, "display", text[i + 2: end]))
                i = end + 2
                continue
            j = i + 1
            depth = 0
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}" and depth:
                    depth -= 1
                elif text[j] == "$" and depth == 0:
                    # a $ inside a group (\mbox{$x$}) is nested maths, not the
                    # end of this formula: closing there cut the span in half
                    # (C7, RULES.md)
                    break
                j += 1
            if j >= n:
                if report_unclosed:
                    _issues.record("math:unclosed", "latex2nl.find_math_spans",
                               where="$", size=1)
                # an unclosed $: the rest of the block is still mathematics, and
                # leaving it as prose put \mathfrak, \mathcal and \mathbb into
                # 8,000 sentences.  A span to the end keeps the content in the
                # formula table and out of the sentences.
                spans.append(Span(i, n, "inline", text[i + 1:]))
                i = n
                continue
            spans.append(Span(i, j + 1, "inline", text[i + 1: j]))
            i = j + 1
            continue

        i += 1

    for s in spans:
        # Opaque formula interiors are data, not a place for heuristic cleanup.
        # In particular, unknown argument groups may hold text or nested math.
        if not clean:
            continue
        marks = list(display_marks(s.body))
        if marks:
            s.labels = [name for _, _, kind, name in marks if kind in ("label", "rowlabel")]
            s.label = s.labels[0] if s.labels else marks[0][3]
            cells_by_row = alignment_cells(s.body)
            for n, cells in enumerate(cells_by_row, 1):
                for _, _, kind, tag in display_marks("&".join(cells)):
                    if kind in ("tag", "taglabel"):
                        s.display_tags.append({"value":tag, "line":n})
            if len(cells_by_row) > 1:
                kinds = {"rowlabel"}
                if (s.env or "").rstrip("*") in ROW_NUMBERED_ENVS:
                    kinds.add("label")
                s.row_labels = [[name for _, _, kind, name in display_marks("&".join(cells)) if kind in kinds]
                                for cells in cells_by_row]
        if unknown_commands(s.body):
            continue
        for start, end, _, _ in reversed(marks):
            s.body = s.body[:start] + " " + s.body[end:]
        if marks:
            s.body = s.body.strip()
        if "\\ensuremath" in s.body:
            # the wrapper means "this is maths", already true inside a formula
            s.body = re.sub(r"\\ensuremath\s*\{([^{}]*)\}", r"\1", s.body)
        if "$" in s.body:
            # maths nested inside maths ("$\mathcal W$\subset ..." inside a
            # display): the content belongs to the formula, the delimiters do not,
            # and a leftover "$" is what C6 counts
            s.body = re.sub(r"\$", " ", s.body)
            s.body = collapse_ws(s.body)
        # tag placement, no content: \raisetag{1.2\baselineskip}
        if "\\raisetag" in s.body:
            s.body = re.sub(r"\\raisetag\s*\{[^{}]*\}", " ", s.body).strip()
    return spans


def strip_math(text: str) -> str:
    """Return the text with every math span deleted (used for label lookup)."""
    out: List[str] = []
    cursor = 0
    for span in find_math_spans(text):
        out.append(text[cursor: span.start])
        cursor = span.end
    out.append(text[cursor:])
    return "".join(out)


#: things that may follow the last punctuation mark of a display without being
#: part of the mathematics: the end of an inner block, a tag switch
TRAILER = r"(?:\\end\{[A-Za-z]+\*?\}|\\notag|\\nonumber)"
# Inside the math delimiters "!" may be a postfix operator (n!, n!!).
TRAILING_PUNCT_RE = re.compile(r"([.,;:?])((?:\s*" + TRAILER + r")*)\s*$")

# These substitutions depend on the active math style or a document counter.
# Keeping their source protects operands even when the name table knows them.
COUNTER_TOKEN = r'\\the(?:equation|eqnarray|section|subsection|subsubsection|figure|table|chapter|part|page|appendix|footnote|enumi|enumii|enumiii|enumiv|theorem|lemma|definition|proposition|corollary|remark|example)(?![A-Za-z@])'
DRAWING = re.compile(r"\\(?:xymatrix\b|begin\{(?:diagram|tikzcd|tikzpicture|xy)\})")
CONTEXTUAL_MATH = re.compile(r'\\mathchoice(?![A-Za-z@])|' + COUNTER_TOKEN)


def strip_trailing_punct(body: str) -> Tuple[str, str]:
    """Take the sentence punctuation off the end of a formula.

    ``... x. \\end{split}`` ends in a full stop just as ``... x.`` does; the
    closing ``\\end`` is put back.
    """
    body = body.rstrip()
    m = TRAILING_PUNCT_RE.search(body)
    if m:
        prefix = body[:m.start()]
        # The punctuation token in \, or \right. belongs to the formula.
        if (len(prefix) - len(prefix.rstrip('\\'))) % 2:
            return body, ""
        if m[1] == '.' and re.search(r'\\(?:left|right|middle|[bB]igg?[lrm]?)\s*$', prefix):
            return body, ""
        return prefix.rstrip() + m.group(2), m.group(1)
    return body, ""


SIDE_RE = re.compile(r"^(.*?)\\(?:quad|qquad)\s*(\([^()]*\))$", re.S)
SIDE_MARK_RE = re.compile(r"[<>=]|\\le(?![A-Za-z])|\\ge(?![A-Za-z])"
                          r"|\\ne(?![A-Za-z])|\\in(?![A-Za-z])")


def split_side_condition(core: str) -> Tuple[str, Optional[str]]:
    """Split a trailing ``\\quad(1\\le i\\le d)`` range condition off a formula."""
    m = SIDE_RE.match(core.strip())
    if not m:
        return core, None
    tail = m.group(2)
    if not SIDE_MARK_RE.search(tail) or TEXT_CMD_RE.search(tail):
        return core, None
    return m.group(1).strip(), tail


#: \\text{...} inside a formula: natural language used as a relation, a guard or
#: a branch label.  It gets its own placeholder, same idea as MATH7 itself.
#: The whole \\text<variant> family belongs here: papers use \\textup{Ric} as an
#: upright operator name, which is text, not mathematics.
TEXT_CMD_RE = re.compile(
    r"\\(?:text|textrm|textup|textsl|textsf|texttt|textnormal|textbf|textit"
    r"|textsc|mbox)\s*\{")

#: what a phrase inside \\text{...} does in its formula.  The choice needs only
#: the formula and its sentence. Paper-local answers refer to a single slot.
SLOT_ANSWERS = (
    "condition",        # the condition in {x | condition}
    "proposition",      # a whole statement, e.g. between two \iff
    "predicate",        # a verb or relation: "has rank", "is mapped to"
    "predicate_part",   # the preposition of a predicate: "on"
    "guard",            # the "if" of an implication
    "consequent",       # the "then" of an implication
    "scope",            # a quantifier: "for", "for all"
    "connective",       # "and", "or"
    "restriction",      # "where"
    "default",          # "otherwise"
    "label",            # a name inside a subscript, not a statement
    "text",             # running words that carry no formula structure
    "operator",         # an upright name that is a function: pr, gcd, Ric
)

SLOT_TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "data", "slot_roles.json")

SLOT_INSTRUCTIONS = (
    "Each item is one phrase that a paper wrote inside \\text{...} in a "
    "formula. Decide what it does in that formula: 'condition' when it is the "
    "condition of {x | ...}, 'proposition' when it is a whole statement such "
    "as one side of an \\iff, 'predicate' for a verb or relation such as 'has "
    "rank' or 'is mapped to', 'guard'/'consequent' for the two halves of an "
    "if-then, 'scope' for a quantifier, 'connective' for and/or, 'restriction' "
    "for 'where', 'default' for 'otherwise', 'label' for a name used inside a "
    "subscript, 'text' for running words that carry no structure. 'sig' is the "
    "normalised phrase. Answer each occurrence by its own id and anchor."
)


def slot_key(raw: str) -> str:
    """The identity of a phrase: spacing and ``~`` do not count.

    A phrase that holds mathematics keeps its case: ``$E$`` and ``$e$`` are
    different things, while ``For all`` and ``for all`` are not.
    """
    key = re.sub(r"\s+", " ", raw.replace("~", " ")).strip()
    return key if "$" in key else key.lower()


def classify_text_slot(raw: str, roles: Optional[Dict[str, str]] = None) -> str:
    return (roles or {}).get(slot_key(raw), "unclassified")


#: a bare word of two or more characters, no space anywhere inside the braces
OPERATOR_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+\Z")


#: function names the LaTeX layer already knows (\log, \sin, ...).  A paper
#: that writes \text{log} means the same function.
_LEXICON_FUNCTIONS: Optional[set] = None


def lexicon_functions() -> set:
    global _LEXICON_FUNCTIONS
    if _LEXICON_FUNCTIONS is None:
        try:
            with open(LEXICON_PATH, encoding="utf-8") as stream:
                data = json.load(stream)
            _LEXICON_FUNCTIONS = {
                n.lower() for n in
                ((data.get("commands") or {}).get("function") or [])}
        except (OSError, ValueError):
            _LEXICON_FUNCTIONS = set()
    return _LEXICON_FUNCTIONS


def is_operator_name(raw: str, roles: Optional[Dict[str, str]] = None,
                     declared: Optional[set] = None) -> bool:
    """``\\textup{Ric}`` is a name set upright, not a phrase.

    A word inside \\text{} is promoted only when something says it is a name:

    - its slot answer is ``operator`` (someone judged it),
    - the same paper writes ``\\operatorname{Ric}`` somewhere -- a
      ``\\DeclareMathOperator`` expands to exactly that form,
    - or it is a function the LaTeX layer already knows (log, sin, ...).

    Anything else stays a text slot: two letters used to be enough, which turned
    ``$x \\text{so} y$`` into ``x \\operatorname{so} y`` and kept "so" out of
    the queue where someone could have judged it.
    """
    role = classify_text_slot(raw, roles)
    if role == "operator":
        return True
    if role != "unclassified":
        return False                    # already judged to be a phrase
    if not OPERATOR_NAME_RE.match(raw):
        return False                    # a single letter, or a phrase
    if declared and (raw in declared or slot_key(raw) in declared):
        return True
    return slot_key(raw) in lexicon_functions()


def text_groups(body):
    """Yield complete outer text commands with their argument and source bounds."""
    cursor = 0
    while match := TEXT_CMD_RE.search(body, cursor):
        end = find_group_end(body, match.end() - 1)
        if end is None:
            break
        yield match.start(), end, body[match.end():end - 1]
        cursor = end


def extract_text_slots(body: str, promoted: Optional[List[str]] = None,
                       roles: Optional[Dict[str, str]] = None,
                       declared: Optional[set] = None
                       ) -> Tuple[str, List[dict]]:
    """Replace every ``\\text{...}`` by ``<TXT n>`` and record it.

    The point is the same as the outer MATH placeholders: keep the position in
    the formula, keep the raw phrasing in the table, and never let an English
    phrase be silently treated as a mathematical symbol.

    The converse case is handled here too: an upright *name* becomes
    ``\\operatorname{name}`` and stays in the formula, reported in ``promoted``.
    """
    slots: List[dict] = []
    out: List[str] = []
    cursor = 0
    for start, end, raw in text_groups(body):
        out.append(body[cursor:start])
        # A standalone word labelling a symbol's subscript has its own role,
        # including when that word also names an operator elsewhere.
        prefix, suffix = body[:start], body[end:]
        named = is_operator_name(raw, roles, declared)
        script = re.search(r"([_^])\{\s*$", prefix)
        simple_script = script and re.match(r"\s*\}", suffix) and raw.strip().isalpha()
        label = simple_script and (named or (
            re.search(r"[A-Za-z0-9]_\{\s*$", prefix)
            and not re.search(r"\\[A-Za-z]+_\{\s*$", prefix)))
        applied = named and re.match(r"\s*(?:\\left\s*)?[(\[]", suffix)
        if applied and not re.match(r"\\text(?![A-Za-z@])", body[start:]):
            out.append("\\operatorname{%s}" % raw)
            if promoted is not None:
                promoted.append(raw)
            cursor = end
            continue
        role = classify_text_slot(raw, roles)
        rule = "slot_table"
        if label:
            role, rule = "label", "symbol_script_label"
            if script[1] == "_":
                rule = "symbol_subscript_label"
        elif applied:
            role, rule = "operator", "applied_operator_name"
        elif role == "operator":
            role = "unclassified"
        slots.append({
            "index": len(slots) + 1,
            "raw": raw,
            "key": slot_key(raw),
            "role": role,
            "rule": rule,
            "offset": start,
            "before": "".join(out)[-30:],
            "after": body[end: end + 30],
        })
        out.append("<TXT%d>" % len(slots))
        cursor = end
    out.append(body[cursor:])
    return "".join(out), slots


#: the placeholder extract_text_slots leaves behind
SLOT_RE = re.compile(r"<TXT(\d+)>")


#: Brackets are depth.  ``\left( .. \right)`` always was; plain ``( .. )`` and
#: ``[ .. ]`` were not, so ``f(x,y,z)`` was a list of three and the ``=`` of
#: ``P(X=0)`` was the relation of the whole formula -- while the same two
#: formulas written with ``\left(`` were a term.  One switch, so that a run with
#: it off is the behaviour before.
PAREN_DEPTH = True


def _skip_sized(body: str, i: int) -> int:
    """Step over the bracket a ``\\left`` or ``\\big`` command applies to.

    The command has already changed the depth: counting its bracket as well
    would count the pair twice, and ``\\left( .. \\right.`` would never close.
    ``\\{``/``\\}`` count the same way, so they are stepped over too.
    """
    j = i
    while j < len(body) and body[j] in " \t":
        j += 1
    if j < len(body) and body[j] in "()[]":
        return j + 1
    if j + 1 < len(body) and body[j] == "\\" and body[j + 1] in "{}|":
        return j + 2
    return i


def _scan_tokens(body: str, parens: bool
                 ) -> Tuple[List[Tuple[int, int, str]], bool, bool]:
    """Tokens at depth 0, whether the brackets pair up, whether everything does.

    ``parens`` makes ``( )`` and ``[ ]`` depth.  They share one counter, apart
    from the one for braces and sized delimiters, so the half-open ``(a,b]``
    pairs up.  The second value is False when a bracket closes that was never
    opened or one is left open; the third is False as well when the same
    happens to a brace or a sized delimiter.
    """
    out: List[Tuple[int, int, str]] = []
    depth = pdepth = 0
    paired = strict = True
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            # \{ .. \} names a set: what is inside is not the formula's
            # statement, so it must be depth like a brace pair
            if body.startswith("\\{", i):
                depth += 1
                i += 2
                continue
            if body.startswith("\\}", i):
                if depth == 0:
                    strict = False
                depth = max(0, depth - 1)
                i += 2
                continue
            m = re.match(r"\\([A-Za-z]+)", body[i:])
            if not m:
                i += 2
                continue
            name = m.group(1)
            step = 0
            if name in ("begin", "end"):
                # a grid/cases block is one piece: its & and , are internal,
                # and treating them as top level made a piecewise definition
                # look like a sequence
                step = 1 if name == "begin" else -1
                if step < 0 and depth == 0:
                    strict = False
            elif name in SIZE_DELIMS:
                step = delim_direction(body, i + m.end())
                if step == 0 and depth == 0 and pdepth == 0:
                    out.append((i, i + m.end(), name))
            elif name in OPEN_DELIMS:
                step = 1
            elif name in CLOSE_DELIMS:
                step = -1
            elif depth == 0 and pdepth == 0:
                out.append((i, i + m.end(), name))
            if step > 0:
                out.append((i, i + m.end(), "left"))
                depth += 1
            elif step < 0:
                if depth == 0:
                    strict = False
                depth = max(0, depth - 1)
                out.append((i, i + m.end(), "right"))
            i += m.end()
            if step:
                i = _skip_sized(body, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                strict = False
            depth = max(0, depth - 1)
        elif parens and c in "([":
            pdepth += 1
        elif parens and c in ")]":
            if pdepth == 0:
                paired = False
            else:
                pdepth -= 1
        elif c == "<" and SLOT_RE.match(body, i):
            # a text slot, not the relations "<" and ">"
            m = SLOT_RE.match(body, i)
            if depth == 0 and pdepth == 0:
                out.append((i, m.end(), "TXT" + m.group(1)))
            i = m.end()
            continue
        elif depth == 0 and pdepth == 0 and c in "=<>,;:":
            out.append((i, i + 1, c))
        i += 1
    if pdepth:
        paired = False
    return out, paired, strict and paired and depth == 0


def _scan_depth(body: str) -> List[Tuple[int, int, str]]:
    """Tokens that sit at depth 0: (pos, end, kind).

    Depth is braces, sized delimiters and brackets.  When the brackets of a
    formula do not pair up -- ``]a,b[``, or one the author left open -- there is
    no telling which part is inside, and the formula is read as it was before
    brackets counted: braces and sized delimiters only.
    """
    if PAREN_DEPTH:
        out, paired, _strict = _scan_tokens(body, True)
        if paired:
            return out
    return _scan_tokens(body, False)[0]


def brackets_pair_up(body: str) -> bool:
    return _scan_tokens(body, True)[1]


_OUTER_OPEN_RE = re.compile(r"\s*(?:\\(?:left|[bB]igg?l?)\s*)?\(")
_OUTER_CLOSE_RE = re.compile(r"(?:\\(?:right|[bB]igg?r?)\s*)?\)\s*$")


def inside_outer_brackets(body: str) -> Optional[str]:
    """What one pair of round brackets holds, when it holds the whole formula.

    ``(a)(b)`` starts and ends with a bracket too, but they are not one pair:
    what is between them has to balance on its own.
    """
    mo = _OUTER_OPEN_RE.match(body)
    mc = _OUTER_CLOSE_RE.search(body)
    if not mo or not mc or mc.start() < mo.end():
        return None
    if ("\\" in mo.group(0)) != ("\\" in mc.group(0)):
        return None
    inner = body[mo.end(): mc.start()]
    return inner if _scan_tokens(inner, True)[2] else None


def statement_in_brackets(body: str,
                          slots: Optional[Sequence[dict]] = None
                          ) -> Optional[str]:
    """``(n \\geq 2)`` states ``n \\geq 2``; return that, or None.

    With brackets as depth a formula written inside one pair has nothing at the
    top level, and the parenthetical condition would become a term.  So the
    pair is taken off -- but only to find the relation, and only when what is
    inside is a statement:

    - ``(a_n : n \\in N)`` is a family, the round form of a set-builder.  The
      colon makes ``n \\in N`` the range of the index, not what the formula
      says;
    - ``(N, \\mid)`` names a relation without using it: nothing stands on one
      side of it.

    Square brackets are left alone: ``[x=y]`` is a value, not a statement.
    """
    inner = body
    while True:
        deeper = inside_outer_brackets(inner)
        if deeper is None:
            break
        inner = deeper
    if inner is body:
        return None
    for _pos, end, kind in _scan_depth(inner):
        if kind == "colon" or (kind == ":" and inner[end: end + 1] != "="):
            return None
    rels = top_level_relations(inner, slots)
    if not rels:
        return None
    last = rels[-1]
    if not inner[: last["pos"]].strip() or not inner[last["end"]:].strip():
        return None
    return inner


def top_level_relations(body: str,
                        slots: Optional[Sequence[dict]] = None) -> List[dict]:
    """Relations at depth 0, in order of appearance.

    A text slot whose role is ``predicate`` ("has rank", "facet of") is the
    relation of its formula and is returned as one; every other slot is skipped.
    """
    predicates = {str(s["index"]): s["raw"].strip() for s in slots or ()
                  if s.get("role") == "predicate"}
    found: List[dict] = []
    for pos, end, kind in _scan_depth(body):
        if kind in REL_COMMANDS:
            found.append({"pos": pos, "end": end, "op": "\\" + kind,
                          "symbol": REL_COMMANDS[kind]})
        elif kind in REL_LITERALS:
            if pos > 0 and body[pos - 1] == ":":      # ":=" is a binder
                continue
            found.append({"pos": pos, "end": end, "op": kind,
                          "symbol": REL_LITERALS[kind]})
        elif kind[:3] == "TXT" and kind[3:] in predicates:
            found.append({"pos": pos, "end": end, "op": body[pos:end],
                          "symbol": predicates[kind[3:]]})
    negated = {"=":"≠", "∈":"∉", "∋":"∌", "<":"≮", ">":"≯", "≤":"≰", "≥":"≱",
               "⊂":"⊄", "⊆":"⊈", "⊃":"⊅", "⊇":"⊉", "≡":"≢", "≈":"≉", "∼":"≁"}
    for relation in found:
        modifier = re.search(r"(?<!\\)\\not\s*$", body[:relation["pos"]])
        if modifier:
            relation.update(pos=modifier.start(), op=body[modifier.start():relation["end"]],
                            symbol=negated.get(relation["symbol"], r"\not " + relation["op"]))
    return found


def count_top_level(body: str, token: str) -> int:
    return sum(1 for _p, _e, k in _scan_depth(body) if k == token)


def _starts_row(text: str, i: int) -> bool:
    """True when position ``i`` is the first thing on an alignment row.

    ``\\\\ & \\qquad + ...`` uses the space as indentation for a continued line,
    not as a gap between two statements.
    """
    j = i
    while j > 0 and text[j - 1] in " \t\n&":
        j -= 1
    return text.endswith("\\\\", 0, j)


def _split_top_level(text: str, sep: str, parens: bool
                     ) -> Tuple[List[str], bool]:
    """The pieces, and whether the brackets paired up (see _scan_tokens)."""
    depth = pdepth = 0
    paired = True
    parts: List[str] = []
    start = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)", text[i:])
            name = m.group(1) if m else ""
            step = 0
            sized = True
            if text.startswith("\\{", i):
                depth += 1
                i += 2
                continue
            if text.startswith("\\}", i):
                depth = max(0, depth - 1)
                i += 2
                continue
            if name in SIZE_DELIMS:
                step = delim_direction(text, i + m.end())
            elif name in OPEN_DELIMS:
                step = 1
            elif name in CLOSE_DELIMS:
                step = -1
            elif name == "begin":
                step, sized = 1, False
            elif name == "end":
                step, sized = -1, False
            elif depth == 0 and pdepth == 0 and text.startswith(sep, i) \
                    and not _starts_row(text, i):
                parts.append(text[start:i])
                i += len(sep)
                start = i
                continue
            if step > 0:
                depth += 1
            elif step < 0:
                depth = max(0, depth - 1)
            i += m.end() if m else 2
            if step and sized:
                i = _skip_sized(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif parens and c in "([":
            pdepth += 1
        elif parens and c in ")]":
            if pdepth == 0:
                paired = False
            else:
                pdepth -= 1
        i += 1
    parts.append(text[start:])
    return parts, paired and pdepth == 0


def split_top_level(text: str, sep: str) -> List[str]:
    """Split on ``sep`` occurrences that sit at depth 0.

    Depth counts braces, brackets, ``\\left``/``\\right`` and
    ``\\begin``/``\\end``: a ``split``, ``aligned`` or ``cases`` block is one
    piece, whatever spacing it uses inside, and so is ``f(a \\qquad b)``.  A
    text whose brackets do not pair up is split as if they were not depth, the
    same fallback as in _scan_depth.
    """
    if PAREN_DEPTH:
        parts, paired = _split_top_level(text, sep, True)
        if paired:
            return parts
    return _split_top_level(text, sep, False)[0]


# --------------------------------------------------------------------------
# formula classification
# --------------------------------------------------------------------------

IDENT_RE = re.compile(r"[A-Za-z](?:_(?:\{[^{}]*\}|[A-Za-z0-9]))*")


def is_simple_identifier(s: str) -> bool:
    return bool(IDENT_RE.fullmatch(s.strip()))


def last_word_prefix(before: str) -> str:
    m = re.search(r"([A-Za-z][A-Za-z\-']*)[^A-Za-z]*$", before)
    return m.group(1).lower() if m else ""


def aligned_relations(body, slots=None):
    if "&" not in body and r"\\" not in body:
        return []
    lines = alignment_cells(body)
    if len(lines) == 1 and len(lines[0]) == 1:
        return []
    rows = []
    for cells in lines:
        row = {"expanded": "&".join(cells).strip(), "relations": []}
        # eqnarray puts the relation alone in the middle of three cells.
        groups = [cells[i:i+2] for i in range(0, len(cells), 2)]
        if len(cells) == 3:
            middle = cells[1].strip()
            rels = top_level_relations(middle, slots)
            if len(rels) == 1 and rels[0]["pos"] == 0 and rels[0]["end"] == len(middle):
                groups = [cells]
        for group in groups:
            expression = "".join(group).strip().rstrip(",;").rstrip()
            relations = top_level_relations(expression, slots)
            if relations:
                rel = relations[-1]
                row["relations"].append({"symbol": rel["symbol"],
                                         "lhs": expression[:rel["pos"]].strip(),
                                         "rhs": expression[rel["end"]:].strip()})
        rows.append(row)
    return rows


def classify_role(body: str, kind: str, before: str, after: str,
                  slots: Optional[Sequence[dict]] = None
                  ) -> Tuple[str, List[str], Optional[dict]]:
    """Decide what the formula is doing in its sentence."""
    flags: List[str] = []
    # ``scope`` is where the relation is looked for: the formula itself, or
    # what "(n \\geq 2)" has inside its brackets.  Everything else below is
    # asked of the formula as written -- "(X,m,e)" is one tuple, not a list.
    scope = body
    if PAREN_DEPTH:
        if not brackets_pair_up(body):
            flags.append("unbalanced")
        scope = statement_in_brackets(body, slots) or body
    rels = top_level_relations(scope, slots)

    top = None
    if rels:
        last = rels[-1]
        top = {
            "symbol": last["symbol"],
            "lhs": scope[: last["pos"]].strip(),
            "rhs": scope[last["end"]:].strip(),
        }
        if len(rels) > 1:
            flags.append("relation_chain")

    if "\\begin{cases}" in body or "\\begin{grid}{cases}" in body \
            or "\\begin{grid}{dcases}" in body:
        return "piecewise", flags + ["piecewise"], top
    # the slots have been lifted out by now; what is left is a \text{ whose
    # braces do not balance.  \textstyle is a size switch, not text.
    if TEXT_CMD_RE.search(body):
        flags.append("has_text")
    if re.search(r"\\(?:left|right)(?![A-Za-z])", body):
        # \rightarrow and \rightsquigarrow are not sized delimiters
        flags.append("big_delimiter")
    if re.search(r"\\(sum|prod|int|oint|bigcup|bigcap)(?![A-Za-z])", body):
        flags.append("binder_operator")

    # hyphenated modifier: "$i$-face", "$r$-dimensional"
    if after.startswith("-") and len(after) > 1 and after[1].isalpha():
        return "modifier", flags + ["hyphen_affix"], top

    if rels:
        last = rels[-1]
        lhs = scope[: last["pos"]]
        rhs = scope[last["end"]:]
        neighbour = last_word_prefix(before)
        if neighbour in CONSTRAINT_TRIGGERS and (is_simple_identifier(lhs)
                                                 or is_simple_identifier(rhs)):
            return "constraint", flags + ["constraint_from_trigger"], top
        return "proposition", flags, top

    if (("\u2026" in body) or count_top_level(body, "\\ldots")
            or count_top_level(body, "\\cdots") or count_top_level(body, ",") >= 2):
        return "sequence", flags + ["list_syntax"], top

    if re.fullmatch(r"[A-Za-z]{2,}", body.strip()):
        flags.append("implicit_product_or_application")

    return "term", flags, top


LEXICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            os.pardir, "data", "latex_lexicon.json")


def lexicon_commands() -> set:
    """Every command the LaTeX layer knows, read from its one table.

    The two files used to keep separate lists and drifted: ``\\wedge`` and
    ``\\simeq`` had a category in the lexicon but were reported as unknown
    here.  Reading the table keeps a single source of truth.
    """
    try:
        with open(LEXICON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    names = set()
    for entries in (data.get("commands") or {}).values():
        names.update(entries)
    return names


LEXICON_COMMANDS = lexicon_commands()

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "data")
#: the two files where an authority other than the word table records what a
#: command is: TeX's own answer (``tex_oracle.py``) and a person's or a model's
#: answer for the names TeX does not know
ANSWER_PATHS = (os.path.join(DATA_DIR, "tex_names.json"),
                os.path.join(DATA_DIR, "name_answers.json"))
#: the actions that mean "this name is understood": it stays as a symbol, its
#: argument is the content, or it carries nothing
ANSWERED_ACTIONS = ("allow", "font", "expand", "unwrap", "ignore", "drop")


#: names answered for one paper only (``papers`` in the answer file).  Set by
#: ``use_paper_id``; without it a per-paper answer reached normalize but the
#: lexicon still called the name unknown.
_PAPER_ANSWERED: set = set()


def use_paper_id(paper: str, path: str = "") -> None:
    """Load the per-paper answers so the unknown-command check honours them."""
    global _PAPER_ANSWERED
    _PAPER_ANSWERED = set()
    if not paper:
        return
    target = path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "name_answers.json")
    try:
        with open(target, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return
    for name, rec in ((doc.get("papers") or {}).get(paper) or {}).items():
        if (rec or {}).get("action") in ANSWERED_ACTIONS:
            _PAPER_ANSWERED.add(name)


def answered_commands(paths: Sequence[str] = ANSWER_PATHS) -> set:
    """Names that TeX or an answer has already decided about.

    The word table says what a *standard* command is.  A command TeX itself
    classified -- ``\\ncong`` is a relation, ``\\bigm`` is a text command --
    is understood just as well, and so is a name a person answered for.  They
    used to be reported as unknown anyway: 208 of the 580 remaining uses in the
    20-paper sample came from this file, and every one of them had an answer.
    """
    names = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        for name, rec in (doc.get("names") or {}).items():
            if ((rec or {}).get("action") in ANSWERED_ACTIONS):
                names.add(name)
    return names


ANSWERED_COMMANDS = answered_commands()


def control_words(text: str) -> List[str]:
    """Every control *word* in ``text``, in order, without repeats removed.

    A backslash followed by a backslash is a row break, and a backslash followed
    by one non-letter is a control symbol (``\\,`` ``\\%``).  Neither is a
    command name, and reading them as one invented names out of nothing:
    ``\\\\s_0`` in a display was reported as an unknown ``\\s``, and
    ``\\\\alpha`` as ``\\alpha``.  One walker, used by every check that counts
    names, so the checks cannot disagree about what a name is.
    """
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "\\":
            i += 1
            continue
        if i + 1 < n and text[i + 1] == "\\":
            i += 2                       # row break
            continue
        j = i + 1
        while j < n and (text[j].isalpha() or text[j] == "@"):
            j += 1
        if j == i + 1:                   # a control symbol
            i += 2
            continue
        out.append(text[i + 1: j])
        i = j
    return out


def unknown_commands(body: str) -> List[str]:
    """Commands in a formula that no table, TeX, or an answer explains.

    Two sources of truth sit behind this: ``data/latex_lexicon.json``, which is
    the word table, and the answer files, which hold what TeX said about a name
    it knows and what a person or a model said about the rest.  There used to be
    a second, hardcoded list here as well; it drifted from the table
    (``\\wedge`` and ``\\simeq`` had a category but were still reported) and,
    once the table was read, held nothing the table did not.
    """
    out = []
    for name in control_words(body):
        if name in ('taglabel', 'rowlabel'):
            continue  # Internal label transport produced by normalize, not math vocabulary.
        if (name not in LEXICON_COMMANDS and name not in ANSWERED_COMMANDS
                and name not in _PAPER_ANSWERED):
            out.append("\\" + name)
    return sorted(set(out))


def classify_speech_act(sentence: str) -> str:
    words = re.findall(r"[A-Za-z]+", sentence)
    if not words:
        return "assertion"
    head = words[0].lower()
    second = words[1].lower() if len(words) > 1 else ""
    if head in DEFINITION_VERBS:
        return "definition"
    if head in ASSUMPTION_VERBS:
        return "assumption"
    if head == "we" and second in DEFINITION_VERBS | ASSUMPTION_VERBS:
        return "definition" if second in DEFINITION_VERBS else "assumption"
    return "assertion"


# --------------------------------------------------------------------------
# main converter
# --------------------------------------------------------------------------


class LatexToNL:
    def __init__(self,
                 proposition_form: str = "bare",
                 max_words: int = 90,
                 merge_display: bool = True,
                 split_qquad: bool = True,
                 keep_standalone: bool = True,
                 tok_mode: str = "ptb",
                 sentence_answers: Optional[Dict[str, str]] = None,
                 style_paths: Optional[Sequence[str]] = None,
                 base_dir: str = ".",
                 slot_roles: Optional[Dict[str, str]] = None, switches=None,
                 slot_answers=None, paper="", reviewed=None) -> None:
        self.proposition_form = proposition_form
        self.max_words = max_words
        self.merge_display = merge_display
        self.split_qquad = split_qquad
        self.keep_standalone = keep_standalone
        self.tok_mode = tok_mode

        self.macros = MacroTable()
        self.style_paths = list(style_paths or ())
        self.sources = {}
        self.switch_defaults = switches or {}
        self.base_dir = base_dir
        #: style files that were actually read
        self.styles: List[str] = []
        #: names the paper defines, for \ifdefined
        self.defined_names: set = set()
        #: phrase -> role for phrases inside \text{...}
        self.slot_roles: Dict[str, str] = dict(
            load_slot_roles() if slot_roles is None else slot_roles)
        #: phrases still without a role; for the answer file
        self.slots_pending: List[dict] = []
        self.slot_questions: List[dict] = []
        self.reviewed = reviewed or {}
        self.applied_boundaries = []
        self.slot_answers = slot_answers or {}
        self.paper = paper
        #: environments unwrapped because they were not known
        self.unwrapped_envs: Dict[str, int] = {}
        #: theorem-like environments: the built-in ones plus \newtheorem
        self.unit_envs: Tuple[str, ...] = tuple(UNIT_ENVS)
        #: set by ``--canonical``: the input went through normalize.py, so a
        #: statement block is ``\begin{statement}{kind}`` instead of
        #: ``\begin{kind}``, and the same holds for display/grid/list
        self.canonical: bool = False
        self.content_blocks: List[dict] = []
        self.formulas: List[FormulaRecord] = []
        self.sentences: List[Sentence] = []
        self.unresolved: List[str] = []
        self.formula_diagnostics: List[dict] = []
        self._opaque_math: List[str] = []
        #: upright names read as operators, e.g. \textup{Ric}; one per use
        self.operator_names: List[str] = []
        #: names this paper writes as \operatorname{...} (or declares with
        #: \DeclareMathOperator, which expands to that form)
        self.declared_operators: set = set()
        #: boundary signature -> "split" | "join"
        self.sentence_answers: Dict[str, str] = dict(sentence_answers or {})
        #: full stops whose sentence role is not obvious; for the answer file
        self.boundaries: List[dict] = []
        self._boundary_seen: Dict[str, dict] = {}
        self._boundaries_joined = 0
        self._answers_used: set = set()
        self._math_count = 0
        self._ref_count = 0
        self._unit_count = 0
        self._sentence_count = 0
        #: brace groups dropped because they held only text
        self.brace_unwrapped = 0

    # -- ids ------------------------------------------------------------
    def _next_math_id(self) -> str:
        self._math_count += 1
        return "MATH%d" % self._math_count

    def _next_ref_id(self) -> str:
        self._ref_count += 1
        return "REF%d" % self._ref_count

    def _note(self, where: str, what: str) -> None:
        self.unresolved.append("%s: %s" % (where, what))
        _issues.record("unresolved:line", "latex2nl._note", where=what[:120],
                       size=1, cls=what.split(":")[0][:40])

    def _formula_diagnostic(self, fid: str, kind: str, detail: str, **fields) -> None:
        """Optional downstream interpretation; never a splitting failure."""
        self.formula_diagnostics.append(dict(formula=fid, kind=kind, detail=detail, **fields))
        _issues.record('formula:diagnostic', 'latex2nl._formula_diagnostic',
                       where=fid + ': ' + detail, size=1, diagnostic_kind=kind)

    # -- entry point ----------------------------------------------------
    def convert(self, tex: str) -> Dict[str, object]:
        if not self.paper:
            self.paper = hashlib.sha256(tex.encode("utf-8")).hexdigest()
        tex = strip_comments(tex)
        self.macros.parse(tex)
        # the switches a paper declares live in its preamble, which is stripped
        # before the body is read (audit J1, OPEN.md)
        preamble, body = split_document(tex)
        import normalize
        keeper = normalize.Normalizer(switches=self.switch_defaults)
        keeper.sources = self.sources
        self.styles = list(dict.fromkeys([*self.style_paths, *normalize.local_styles(
            os.path.join(self.base_dir, 'main.tex'), preamble)]))
        styles = keeper.read_styles(self.styles)
        self.styles = [p for p in self.styles if os.path.isfile(p)]
        for sty in styles.values():
            self.macros.parse(sty)
            normalize.augment_macros(self.macros, sty)
        normalize.augment_macros(self.macros, tex)
        extra = [e for e in sorted(self.macros.envs) if e not in UNIT_ENVS]
        if extra:
            self.unit_envs = tuple(UNIT_ENVS) + tuple(extra)
        if self.canonical:
            # Only ``statement`` is a proposition unit: a 127-word
            # ``\begin{statement}{lemma}`` read as prose is cut into 14-word
            # pieces (1140 sentences instead of 839 on 0708.2512).
            # ``display``/``grid``/``list`` are containers, not units; the CLI
            # adds them to MATH_ENVS / MATH_INNER_ENVS / LIST_ENVS instead.
            if "statement" not in self.unit_envs:
                self.unit_envs = tuple(self.unit_envs) + ("statement",)
        body = keeper.execute_document(preamble, body, self.macros, styles, tex)
        body = self.macros.expand(body)
        body = keeper._ifthenelse(body)
        body = normalize.conditionals.preserve_late_conditions(body)
        body = keeper._preserve_floats(body)
        body = keeper._preserve_notes(body)
        body = keeper._preserve_graphics(body)
        body = preserved.restore_calls(body, keeper.held_calls)
        body = keeper.preserve_prose_calls(body, self.macros)
        body = preserved.locate(body, self.sources)
        self._by_id = {}
        body = self._extract_preserved(body)
        body = self._shield_opaque_math(body)
        body = self._drop_meta(body)
        body = self._unwrap_unknown_envs(body)

        # \DeclareMathOperator lives in the preamble, which _drop_meta has
        # already removed from body: the declaration has to be read from the
        # source, or it is never seen at all
        self.declared_operators = {
            m.group(1) for m in re.finditer(
                r"\\operatorname\*?\s*\{\s*([A-Za-z][A-Za-z0-9]*)\s*\}",
                body)} | {m.group(1) for m in re.finditer(
                r"\\DeclareMathOperator\*?\s*\{\s*\\([A-Za-z]+)\s*\}",
                tex)}
        self._by_id = {r.id: r for r in self.formulas}
        for unit in self._segment(body):
            self._process_unit(unit)
        # Notes are separate units, never spliced into the middle of a claim.
        # The REF anchor maintains the parent relationship; opaque blocks stay
        # in content_blocks.json and prevent a clean verdict.
        self._process_preserved()
        # every record should be reachable from a sentence, or say why not
        # sent.formulas is filled with a \b-anchored pattern, so "MATH931th"
        # never lands in it while the text does use the record; the text is
        # what decides here, as in verify_formulas
        used = set()
        for sent in self.sentences:
            used.update(re.findall(r"(MATH\d+|REF\d+)", sent.text))
        unmarked = [r.id for r in self.formulas
                    if r.id not in used and not r.orphan]
        if unmarked:
            self._note("formulas", "%d records are referenced by no sentence "
                       "and carry no orphan anchor: %s"
                       % (len(unmarked), ", ".join(unmarked[:5])))
        self._resolve_boundary_sids()
        by_fid = {fid: sent for sent in self.sentences for fid in sent.formulas}
        raw_by_id = {rec.id: rec.raw for rec in self.formulas if rec.kind != "ref"}
        for item in self.slot_questions:
            sent = by_fid.get(item["formula"])
            if sent:
                item["sid"] = sent.sid
                item["sentence"] = re.sub(r"MATH\d+", lambda m: "$" + raw_by_id[m[0]] + "$"
                                          if m[0] in raw_by_id else m[0], sent.text)

        names: Dict[str, int] = {}
        for name in self.operator_names:
            names[name] = names.get(name, 0) + 1
        formulas = [f.to_dict() for f in self.formulas]
        targets = formula_destinations(formulas)
        for f in formulas:
            if f.get("ref_kind") == "ref":
                key = f["ref_target"]
                keys = [key] if key in targets else reference_keys(key)
                matches = [dict(label=k, **targets[k]) for k in keys if k in targets]
                if matches:
                    f["ref_destinations"] = matches
        return {
            "sentences": [s.to_dict() for s in self.sentences],
            "formulas": formulas,
            "content_blocks": self.content_blocks,
            "unresolved": list(self.unresolved),
            "formula_diagnostics": list(self.formula_diagnostics),
            "operator_names": names,
            "sentence_boundaries": list(self.boundaries),
            "boundaries_joined": self._boundaries_joined,
            "manual_boundaries": self.applied_boundaries,
            "answers_unused": sorted(set(self.sentence_answers)
                                     - self._answers_used),
            "styles": [os.path.basename(p) for p in self.styles],
            "unwrapped_envs": dict(self.unwrapped_envs),
            "slots_pending": list(self.slots_pending),
            "slot_questions": self.slot_questions,
        }

    def _extract_preserved(self, body: str) -> str:
        # A capsule inside math cannot become an embedded REF inside another
        # formula: that REF would be unreachable from any sentence. Preserve
        # the containing formula as pending, with all alternatives in its raw.
        for span in reversed(find_math_spans(body)):
            fragment = body[span.start:span.end]
            if preserved.MARKER.search(fragment):
                raw = preserved.MARKER.sub(lambda m: preserved.unpack(m)['raw'], fragment)
                replacement = preserved.pack('conditional_formula', raw,
                    status='pending', reason='preserved_content_inside_formula',
                    components=[preserved.unpack(m) for m in preserved.MARKER.finditer(fragment)])
                body = body[:span.start] + replacement + body[span.end:]
        def sub(m):
            block = preserved.unpack(m)
            rid = self._next_ref_id()
            bid = "content%d" % (len(self.content_blocks) + 1)
            block.update(id=bid, anchor_ref=rid, anchor_sids=[], sentence_ids=[])
            self.content_blocks.append(block)
            rec = FormulaRecord(id=rid, raw=block["raw"], expanded="",
                                kind="ref", role="term", ref_kind="content",
                                ref_target=bid)
            self.formulas.append(rec)
            self._by_id[rid] = rec
            if block['kind'] in ('float', 'conditional'):
                return "\n\n" + rid + "\n\n"
            return " " + rid + " "
        return preserved.MARKER.sub(sub, body)

    def _process_preserved(self) -> None:
        index = 0
        while index < len(self.content_blocks):
            block = self.content_blocks[index]
            index += 1
            anchors = [s for s in self.sentences if block["anchor_ref"] in s.formulas]
            block["anchor_sids"] = [s.sid for s in anchors]
            block["parent_units"] = list(dict.fromkeys(s.unit for s in anchors))
            pending = block.get("status") == "pending"
            if pending and not block.get("previews"):
                continue
            # Macros in notes have already expanded before normalization stores
            # the block. Delayed note normalization protects its content from
            # destructive passes over the surrounding prose.
            import normalize
            note = normalize.Normalizer().normalize(
                '\n\n'.join(block['previews']) if pending else block.get("body", block["raw"]))
            note = self._extract_preserved(note)
            note = self._shield_opaque_math(note)
            before = len(self.sentences)
            unit = Unit(uid=block["id"], env="caption" if pending else block["kind"],
                        section=anchors[0].section if anchors else "", section_scope=anchors[0].section_scope if anchors else (),
                        title=None, body=note)
            self._process_unit(unit)
            block["preview_sentence_ids" if pending else "sentence_ids"] = [s.sid for s in self.sentences[before:]]
            if not pending:
                block["status"] = "parsed" if block["sentence_ids"] else "pending"

    def _shield_opaque_math(self, body: str) -> str:
        # Protect formula interiors from prose metadata and environment passes.
        import normalize
        masked, records = normalize.Normalizer._protect_opaque_math(body, len(self._opaque_math))
        self._opaque_math.extend(records)
        return masked

    # -- stage: let the paper define its own vocabulary -----------------
    def _unwrap_unknown_envs(self, body: str) -> str:
        """Keep the content of an environment this script does not know.

        A paper may load its theorem environments from a package file that is
        not available; leaving ``\\begin{notation}`` in the text turns each one
        into a "sentence".  The default here is the safe one: an unknown
        environment is a container, so its markers go and its content stays as
        its own paragraph.
        """
        known = ({'math'} | set(MATH_ENVS) | set(MATH_INNER_ENVS) | set(self.unit_envs)
                 | set(LIST_ENVS) | set(DROP_ENVS))
        begin_re = re.compile(r"\\begin\{([A-Za-z]+\*?)\}")
        while True:
            for m in begin_re.finditer(body):
                env = m.group(1)
                if env in known:
                    continue
                # rule 5: remove the markers, add no separator -- an inline
                # environment (\begin{math}..\end{math}) must not cut its
                # sentence in three -- and a brace argument keeps its words,
                # losing only the braces (a title is text, not a deletion).
                after = m.end()
                j = after
                while j < len(body) and body[j] in " \t\n":
                    j += 1
                if j < len(body) and body[j] == "{":
                    close = find_group_end(body, j)
                    if close:
                        body = body[:j] + body[j + 1:close - 1] + body[close:]
                em = find_env_end(body, after, env)
                if em is None:
                    body = body[: m.start()] + body[after:]
                else:
                    body = body[: m.start()] + body[after: em.start()] \
                        + body[em.end():]
                self.unwrapped_envs[env] = self.unwrapped_envs.get(env, 0) + 1
                break
            else:
                return body

    # -- stage: drop document-level material ----------------------------
    def _drop_meta(self, body: str) -> str:
        body = drop_command_with_arg(body, ("thanks", "footnote"))
        body = drop_metadata(body)
        body = unwrap_layout(body)
        for cmd in ("maketitle", "tableofcontents", "newpage", "clearpage",
                    "noindent", "centering", "small", "large", "normalsize",
                    "bfseries", "itshape", "par", "hfill", "vfill",
                    "bigskip", "medskip", "smallskip", "leavevmode",
                    "allowbreak", "appendix", "frontmatter", "mainmatter"):
            body = re.sub(r"\\" + cmd + r"(?![A-Za-z@])", " ", body)
        for env in DROP_ENVS:
            while True:
                m = re.search(r"\\begin\{" + re.escape(env) + r"\}", body)
                if not m:
                    break
                em = find_env_end(body, m.end(), env)
                if em is None:
                    # unclosed: drop the opening marker only.  Cutting the body
                    # here deleted every sentence after an unclosed \begin{figure}.
                    body = body[: m.start()] + " " + body[m.end():]
                    self._note("unclosed", env)
                    break
                body = body[: m.start()] + " " + body[em.end():]
        return body

    # -- stage: segmentation -------------------------------------------
    def _segment(self, body: str) -> List[Unit]:
        chunks = []
        heading_re = re.compile(r"\\(" + "|".join(HEADING_CMDS) + r")(?![A-Za-z@])\*?\s*")
        pos = 0
        stack: Dict[str, str] = {}
        current = ""
        scope = {}
        for number, m in enumerate(heading_re.finditer(body), 1):
            start = m.end()
            if m.start() < pos:
                continue
            if body.startswith("[", start):
                after_short = find_group_end(body, start)
                if after_short is None:
                    continue
                start = after_short
                while start < len(body) and body[start].isspace():
                    start += 1
            if not body.startswith("{", start):
                continue
            end = find_group_end(body, start)
            if end is None:
                continue
            chunks.append((current, tuple(scope.values()), body[pos: m.start()]))
            title = self._clean_heading(body[start + 1:end - 1])
            if m.group(1) in ("paragraph", "subparagraph") and title.endswith("."):
                # a run-in heading ("Run-in. Text follows.") is the start of the
                # sentence, not a section of its own (AUDIT.md small items)
                chunks.append((current, tuple(scope.values()), " " + title))
                pos = end
                continue
            level = HEADING_CMDS.index(m.group(1))
            for k in list(stack):
                if HEADING_CMDS.index(k) >= level:
                    del stack[k]
                    del scope[k]
            scope[m.group(1)] = number
            stack[m.group(1)] = title
            ordered = sorted(stack.items(),
                             key=lambda kv: HEADING_CMDS.index(kv[0]))
            current = " > ".join(t for _k, t in ordered)
            pos = end
        chunks.append((current, tuple(scope.values()), body[pos:]))

        units: List[Unit] = []
        for section, section_scope, chunk in chunks:
            for unit in self._units_in_chunk(chunk, section):
                unit.section_scope = section_scope
                units.append(unit)
        return units

    def _clean_heading(self, title: str) -> str:
        title = drop_command_with_arg(title, ("cite", "label"))
        # a heading may contain maths ("... of $X$"): the words stay, the delimiters
        # do not, or the section field trips C8 on every sentence under it
        title = re.sub(r"\$([^$]*)\$", r" \1 ", title)
        title = re.sub(r"\\(?:eq|auto|page)?ref\{([^}]*)\}", r"\1", title)
        title = unwrap_formatting(title)
        title = apply_accents(title)
        title = normalize_dashes_quotes(title)
        return collapse_ws(title)

    def _units_in_chunk(self, chunk: str, section: str) -> List[Unit]:
        units: List[Unit] = []
        begin_re = re.compile(r"\\begin\{(" + "|".join(self.unit_envs) + r")\}")
        cursor = 0
        pos = 0
        last_claim = ""
        while True:
            m = begin_re.search(chunk, pos)
            if not m:
                break
            units.extend(self._prose_units(chunk[cursor: m.start()], section))
            env = m.group(1)
            wrapper = env                  # what closes the environment
            after = m.end()
            if env == "statement" and chunk[after: after + 1] == "{":
                # the canonical form keeps the kind in a brace:
                # ``\begin{statement}{lemma}`` means what ``\begin{lemma}`` meant,
                # so the unit takes that name -- titles, ``proof of ...`` and the
                # role rules all key off it.  The wrapper is still ``statement``.
                end = find_group_end(chunk, after)
                if end:
                    kind = chunk[after + 1: end - 1].strip()
                    if re.fullmatch(r"[a-z][a-z*]*", kind):
                        env = kind
                    after = end
            title = None
            if chunk[after: after + 1] == "[":
                end = find_group_end(chunk, after)
                if end:
                    title = self._clean_heading(chunk[after + 1: end - 1])
                    after = end
            em = find_env_end(chunk, after, wrapper)
            if em is None:
                # the env never closes: what follows the marker is still prose,
                # and the text before it has already been emitted -- advancing
                # only pos left cursor behind, so the lead came out twice
                # (AUDIT.md 20)
                units.extend(self._prose_units(chunk[after:], section))
                cursor = len(chunk)
                pos = len(chunk)
                break
            content = chunk[after: em.start()]
            self._unit_count += 1
            # a \label inside display maths names the equation, not the unit,
            # so only labels in the surrounding prose can name a unit
            plain = strip_math(content)
            lm = re.search(r"\\label\{([^}]*)\}", plain)
            label = lm.group(1) if lm else None
            uid = label or "%s#%d" % (env, self._unit_count)
            if env == "proof" and title is None and last_claim:
                title = "proof of " + last_claim
            pieces = self._split_items(content)
            if begin_re.search(content):
                for child in self._units_in_chunk(content, section):
                    if child.env == "prose":
                        units.append(Unit(uid, env, section, title, child.body))
                    else:
                        child.enclosing_units = (uid,) + child.enclosing_units
                        units.append(child)
            elif len(pieces) > 1:
                for k, piece in enumerate(pieces, start=1):
                    units.append(Unit("%s/item%d" % (uid, k), env, section,
                                      title, piece))
            else:
                units.append(Unit(uid, env, section, title, content))
            if env != "proof":
                last_claim = uid
            cursor = em.end()
            pos = em.end()
        units.extend(self._prose_units(chunk[cursor:], section))
        return [u for u in units if collapse_ws(u.body)]

    #: a blank line before a display; if the text before it does not end a
    #: sentence, the display belongs to that sentence (audit M1, OPEN.md)
    BEFORE_DISPLAY = re.compile(r"(?s)([^\n]*?)\n\s*\n(?=\s*\\begin\{display\})")
    AFTER_DISPLAY = re.compile(r"(?s)(\\end\{display\})\s*\n\s*\n(?=\s*[a-z(])")

    @classmethod
    def _join_display_breaks(cls, text: str) -> str:
        def before(m):
            head = m.group(1).rstrip()
            if not head or head[-1] in ".!?":
                return m.group(0)          # a real paragraph end
            # a colon introduces what follows: the display belongs to it
            return head + " "

        def after(m):
            return m.group(1) + " "

        text = cls.BEFORE_DISPLAY.sub(before, text)
        return cls.AFTER_DISPLAY.sub(after, text)

    def _prose_units(self, text: str, section: str) -> List[Unit]:
        units: List[Unit] = []
        text = self._tighten_math_blocks(text)
        text = self._join_display_breaks(text)
        for kind, content in self._blocks(text):
            if kind == "item":
                self._unit_count += 1
                units.append(Unit("prose#%d/item" % self._unit_count, "item",
                                  section, None, content))
                continue
            for para in re.split(r"\n\s*\n+", content):
                if not collapse_ws(para):
                    continue
                self._unit_count += 1
                units.append(Unit("prose#%d" % self._unit_count, "prose",
                                  section, None, para))
        return units

    @staticmethod
    def _tighten_math_blocks(text: str) -> str:
        """Drop blank lines inside a display or a diagram.

        Paragraphs are split on a blank line, and a drawing is full of them
        (texdraw emits a blank line between segments).  Splitting there cut the
        ``\\begin{display}`` from its ``\\end``, so no span could be found and
        the drawing's body came out as 89 sentences of ``\\move``/``\\bsegment``
        in one paper.  The blank lines inside these blocks are layout, not
        paragraph breaks.
        """
        for env in ("display", "diagram"):
            pat = re.compile(r"\\begin\{" + env + r"\}")
            pos = 0
            while True:
                m = pat.search(text, pos)
                if not m:
                    break
                end = find_env_end(text, m.end(), env)
                if end is None:
                    pos = m.end()
                    continue
                block = text[m.start(): end.end()]
                tight = re.sub(r"\n\s*\n+", "\n", block)
                if tight != block:
                    text = text[:m.start()] + tight + text[end.end():]
                    end = find_env_end(text, m.start() + m.end() - m.start(),
                                       env)
                    pos = end.end() if end else m.end()
                else:
                    pos = end.end()
        return text

    def _blocks(self, text: str) -> List[Tuple[str, str]]:
        """Split prose into ``("prose"|"item", text)``, consuming list envs.

        A list usually sits in the middle of a paragraph, so the text before
        and after it stays prose while each ``\\item`` becomes its own block.
        """
        blocks: List[Tuple[str, str]] = []
        # the canonical container carries its kind as an argument -- the
        # ``{enumerate}`` in ``\begin{list}{enumerate}`` -- and not consuming it
        # made that brace the first item, so every list contributed a sentence
        # consisting of nothing but ``{enumerate}``
        pat = re.compile(r"\\begin\{(" + "|".join(LIST_ENVS)
                         + r")\}\s*(?:\{[^{}]*\})?\s*(?:\[[^\]]*\])?")
        pos = 0
        while True:
            m = pat.search(text, pos)
            if not m:
                break
            if collapse_ws(text[pos: m.start()]):
                blocks.append(("prose", text[pos: m.start()]))
            env = m.group(1)
            em = find_env_end(text, m.end(), env)
            if em is None:
                pos = m.end()
                continue
            inner = text[m.end(): em.start()]
            for piece in re.split(r"\\item(?![A-Za-z])", inner):
                # ``\item[(i)] x`` renders as "[(i)] x" if the bracket stays;
                # the label is text, the brackets are its syntax
                piece = re.sub(r"^\s*\[([^\]]*)\]\s*", r"\1 ", piece)
                if collapse_ws(piece):
                    blocks.append(("item", piece))
            pos = em.end()
        if collapse_ws(text[pos:]):
            blocks.append(("prose", text[pos:]))
        return blocks

    def _split_items(self, content: str) -> List[str]:
        """Split an ``enumerate``/``itemize`` body into item texts."""
        env_m = re.search(r"\\begin\{(" + "|".join(LIST_ENVS)
                          + r")\}\s*(?:\{[^{}]*\})?\s*(?:\[[^\]]*\])?", content)
        if not env_m:
            return [content]
        env = env_m.group(1)
        em = find_env_end(content, env_m.end(), env)
        if em is None:
            return [content]
        inner = content[env_m.end(): em.start()]
        def _strip_label(part: str) -> str:
            return re.sub(r"^\s*\[([^\]]*)\]\s*", r"\1 ", part)

        parts = [_strip_label(p) for p in re.split(r"\\item(?![A-Za-z])", inner)
                 if collapse_ws(p)]
        # whatever came before the first \item is prose, not an item: it used
        # to be numbered as item 1 and shift every label.  The text sits both
        # before \begin{...} and between the marker and the first \item.
        lead_parts = []
        if collapse_ws(content[:env_m.start()]):
            lead_parts.append(collapse_ws(content[:env_m.start()]))
        first = re.search(r"\\item(?![A-Za-z])", inner)
        if first is not None:
            pre = collapse_ws(inner[:first.start()])
            if pre:
                lead_parts.append(pre)
            inner = inner[first.start():]
            parts = [_strip_label(p)
                     for p in re.split(r"\\item(?![A-Za-z])", inner)
                     if collapse_ws(p)]
        lead = "\n".join(lead_parts)
        tail = content[em.end():]
        if collapse_ws(re.sub(r"\\end\{" + re.escape(env) + r"\}", "", tail)):
            parts.append(tail)
        if lead:
            parts.insert(0, lead)
        return parts if parts else [content]

    # -- stage: one unit -> sentences -----------------------------------
    def _strip_text_groups(self, text: str) -> str:
        """Drop brace groups that hold only text; count how many went.

        When the group belongs to a command -- ``\\tsadi{x}`` -- the braces go but
        a space stays, or the name and the content fuse into ``\\tsadix``
        (RULES.md).
        """
        def repl(m) -> str:
            if re.search(r"\\[A-Za-z@]+\s*$", text[:m.start()]):
                return " " + m.group(1)
            return m.group(1)

        while True:
            new = TEXT_GROUP_RE.sub(repl, text)
            if new == text:
                return text
            self.brace_unwrapped += 1
            text = new

    def _process_unit(self, unit: Unit) -> None:
        out, records = self._rewrite(unit.body, unit)
        # \ref{a}\ref{b} produced REF1REF2: two placeholders in one word, and both
        # records lose their anchor (AUDIT.md 24)
        out = re.sub(r"((?:MATH|REF)\d+)(?=(?:MATH|REF)\d+)", r"\1 ", out)
        for rec in records:
            rec.unit = unit.uid
            self._by_id[rec.id] = rec
        self.formulas.extend(records)
        out = re.sub(r"[ \t]*\n[ \t]*", " ", out)
        out = self._strip_text_groups(out)
        part = None
        counts: dict = {}
        for piece in PART_SENTINEL_RE.split(out):
            m = PART_PIECE_RE.fullmatch(piece)
            if m:
                kind = m.group(1)
                counts[kind] = counts.get(kind, 0) + 1
                part = {"kind": kind, "index": counts[kind]}
                continue
            for chunk in piece.split(HARD_BREAK):
                if "/item" in (unit.uid or ""):
                    # "1. text" / "a. text": the label's full stop is syntax, not
                    # a sentence end -- otherwise "1." becomes its own sentence
                    # and is dropped as a fragment
                    chunk = re.sub(r"^(\s*)(\d+|[A-Za-z]|[ivxIVX]+)\.(?=\s)",
                                   lambda m: m.group(1) + m.group(2) + PROTECTED_DOT,
                                   chunk, count=1)
                for sent in self._join_sentences(split_sentences(chunk), unit):
                    if not sent:
                        continue
                    # A group that spanned two units can leave one half of a
                    # brace pair in a sentence.  Strip it (and count it) before
                    # the payload test: this guard was lost when the sentence
                    # filter was rewritten (MERGE-PLAN F2).
                    if "{" in sent or "}" in sent:
                        cleaned = re.sub(r"(?<!\\)[{}]", "", sent)
                        if cleaned != sent:
                            self.brace_unwrapped += 1
                            _issues.record("sentence:brace_unwrapped",
                                           "latex2nl._process_unit",
                                           where=sent[:80], size=len(sent) - len(cleaned))
                            sent = cleaned
                    # Formula-only and non-Latin content are valid units.  Only
                    # punctuation/spacing without a payload may be discarded.
                    residue = PLACEHOLDER_RE.sub(" ", sent)
                    has_payload = bool(PLACEHOLDER_RE.search(sent))
                    has_text = any(ch.isalnum() for ch in residue)
                    if not has_payload and not has_text:
                        _issues.record("drop:empty_sentence", "latex2nl._process_unit",
                                       where=sent[:80], size=len(sent))
                        continue
                    content_kind = "formula_only" if has_payload and not has_text else "prose"
                    if has_payload and not has_text and not re.search(r'MATH\d+', sent):
                        content_kind = "reference_only"
                    self._sentence_count += 1
                    ids = [m.group(1) for m in PLACEHOLDER_RE.finditer(sent)]
                    toks = sent.split() if self.tok_mode == "space" else tokenize(sent)
                    if "".join(toks) != re.sub(r"\s+", "", sent):
                        self._note("s%d" % self._sentence_count,
                                   "tokenisation is not lossless; check this line "
                                   "before feeding the parsers")
                    words = len(toks)
                    # punctuation is not a word: 98 words plus ", . ! ?" must
                    # not trip the Spring length flag (AUDIT.md small items)
                    real_words = sum(1 for t in toks if re.search(r"[A-Za-z0-9]", t))
                    flags = (["too_long_for_spring"]
                             if real_words > self.max_words else [])
                    self.sentences.append(Sentence(
                        sid="s%d" % self._sentence_count, text=sent, unit=unit.uid,
                        env=unit.env, section=unit.section, title=unit.title, section_scope=unit.section_scope, enclosing_units=unit.enclosing_units,
                        speech_act=classify_speech_act(sent),
                        formulas=ids, tokens=toks, words=words, flags=flags,
                        part=part, content_kind=content_kind,
                    ))

    def _join_sentences(self, parts: Sequence[str], unit: Unit) -> List[str]:
        """Undo wrong sentence breaks, and list the ones that need a person.

        A break whose next word starts with a capital is taken as given: in
        these papers that is a real sentence boundary, whether or not the
        author wrote a full stop.  A break before a lower-case word is the
        doubtful kind: the stop may belong to an abbreviation the list does not
        know, or the author may have split one sentence in two.  Those go to
        ``sentence_todo.json``; an answered ``join`` merges them here.
        """
        out: List[str] = []
        for raw in parts:
            sent = collapse_ws(raw)
            if not sent:
                continue
            if out:
                left = out[-1]
                sig = boundary_sig(left, sent)
                answer = self.sentence_answers.get(sig)
                manual = self.reviewed.get(("sentence", sig))
                if manual:
                    answer = manual["answer"]
                    self.applied_boundaries.append({"sig": sig, "answer": answer, **manual_review.provenance(manual)})
                if answer is not None:
                    self._answers_used.add(sig)
                # A single initial before a capitalized word is ambiguous:
                # "J. Smith" versus "by A. This set...". Keep the default
                # split, but require a boundary decision instead of claiming it.
                initial = bool(re.search(r'\b[A-Z]\.$', left))
                if answer is None and (not _starts_sentence(sent) or initial):
                    self._record_boundary(sig, left, sent, unit)
                if answer == "join":
                    out[-1] = join_boundary(left, sent)
                    self._boundaries_joined += 1
                    continue
            out.append(sent)
        return out

    def _record_boundary(self, sig: str, left: str, right: str,
                         unit: Unit) -> None:
        if sig in self._boundary_seen:
            self._boundary_seen[sig]["count"] += 1
            return
        item = {
            "sig": sig,
            "unit": unit.uid,
            "before": collapse_ws(left)[-80:],
            "after": collapse_ws(right)[:80],
            "count": 1,
            "default": "split",
            "answer": None,
        }
        self._boundary_seen[sig] = item
        self.boundaries.append(item)

    def _resolve_boundary_sids(self) -> None:
        """Attach sentence ids once every sentence has its number."""
        for item in self.boundaries:
            tail, _sep, head = item["sig"].partition(BOUNDARY_SEP)
            item["sid_before"] = item["sid_after"] = None
            for i in range(len(self.sentences) - 1):
                if (self.sentences[i].text.endswith(tail)
                        and self.sentences[i + 1].text.startswith(head)):
                    item["sid_before"] = self.sentences[i].sid
                    item["sid_after"] = self.sentences[i + 1].sid
                    break

    def _clean_segment(self, text: str) -> str:
        """Clean prose that sits outside math spans."""
        text = re.sub(r"(?<!\\)\\(th|TH)(?![A-Za-z@])(?:[ \t]+|\n[ \t]*)?",
                      lambda m: "þ" if m[1] == "th" else "Þ", text)
        # spacing and page machinery, starred or not: \vspace*{0.2in} was
        # reaching the sentence stream as a "sentence" of its own
        text = re.sub(r"\\(?:vspace|hspace|vskip|hskip|kern|rule)\*?"
                      r"\s*(?:\{[^{}]*\}|\d+\s*[A-Za-z]{2})", " ", text)
        # a container marker left in prose is a nesting leftover: the content
        # is already here, only the \begin/\end pair is noise.  The canonical
        # containers carry their kind as an argument -- ``\begin{statement}{proof}``,
        # ``\begin{list}{enumerate}``, ``\begin{grid}{matrix}``,
        # ``\begin{diagram}{xymatrix}`` -- and a unit may follow it with a title,
        # ``\begin{statement}{proof}[Proof of Proposition 2.8]``.  Stripping only
        # the name left ``{proof}[Proof of ...]`` at the head of the sentence, on
        # 55 sentences of the 20-paper sample.
        text = re.sub(
            r"\\(?:begin|end)\s*\{(?:statement|list|grid|diagram)\}"
            r"\s*\{[^{}]*\}\s*(?:\[[^\]]*\])?", " ", text)
        text = re.sub(r"\\(?:begin|end)\s*\{[^{}]*\}\s*(?:\[[^\]]*\])?", " ",
                      text)
        text = re.sub(r"\\item(?![A-Za-z])\s*(?:\[[^\]]*\])?", " ", text)
        # "\ " is a plain space, and so is a backslash that ends a line; both
        # survive as a stray backslash otherwise
        text = re.sub(r"\\\s", " ", text)
        text = drop_command_with_arg(text, ("index", "footnote", "label"))

        def ref_repl(m: "re.Match[str]") -> str:
            rid = self._next_ref_id()
            target = m.group(2)
            if m.lastindex and m.lastindex >= 3 and m.group(3):
                target = "%s,%s" % (target, m.group(3))   # a range names two
            rec = FormulaRecord(
                id=rid, raw=m.group(0), expanded=m.group(0), kind="ref",
                role="term", ref_target=target, ref_kind=m.group(1),
            )
            self.formulas.append(rec)
            # registered like the others, so a dropped sentence can still mark
            # it orphan instead of leaving it unanchored
            self._by_id[rid] = rec
            # "flat\cite{..}" has no space in the source; without one the
            # placeholder fuses with the word ("flatREF58")
            if m.start() and text[m.start() - 1].isalnum():
                return " " + rid
            return rid

        # ``\crefrange{a}{b}`` is one reference to a range: both labels stay in
        # ref_target and the sentence keeps one placeholder.  Left alone it
        # reached the prose as raw LaTeX.  It is matched first and separately:
        # ``\ref{thm:main}{\bf (i)}`` is a reference followed by a sub-item
        # marker, and reading that second group as a range label deleted the
        # marker from the sentence.
        text = re.sub(r"\\([Cc]refrange)\s*\{([^}]*)\}\s*\{([^}]*)\}",
                      ref_repl, text)
        # a label may hold braces: \ref{a{b}} used to match up to the first
        # "}" and leave the rest in the sentence (audit M5, OPEN.md)
        def scan_refs(text: str) -> str:
            pat = re.compile(r"\\(eqref|ref|autoref|[Cc]ref)(?![A-Za-z])")
            out, pos = [], 0
            for m in pat.finditer(text):
                j = m.end()
                while j < len(text) and text[j] in " \t\n":
                    j += 1
                if j >= len(text) or text[j] != "{":
                    continue
                close = find_group_end(text, j)
                if not close:
                    continue
                rid = self._next_ref_id()
                raw_ref = text[m.start():close]
                rec = FormulaRecord(
                    id=rid, raw=raw_ref, expanded=raw_ref,
                    kind="ref", role="term",
                    ref_target=text[j + 1:close - 1], ref_kind=m.group(1),
                )
                self.formulas.append(rec)
                self._by_id[rid] = rec
                before = text[pos:m.start()]
                out.append(before)
                if before and before[-1].isalnum():
                    # "flat\ref{lem}" has no space in the source
                    out.append(" ")
                out.append(rid)
                pos = close
            out.append(text[pos:])
            return "".join(out)

        text = scan_refs(text)

        def cite_repl(m: "re.Match[str]") -> str:
            rid = self._next_ref_id()
            rec = FormulaRecord(
                id=rid, raw=m.group(0), expanded=m.group(0), kind="ref",
                role="term", ref_target=m.group(2), ref_kind="cite",
            )
            self.formulas.append(rec)
            self._by_id[rid] = rec
            if m.start() and text[m.start() - 1].isalnum():
                return " " + rid
            return rid

        # a citation is a cross-reference: dropping it leaves a hole in the
        # sentence ("we refer the reader to  for details")
        text = re.sub(r"\\(cite[pt]?|nocite|parencite|textcite|autocite"
                      r"|citeauthor|citeyear|citep|citet)\*?\s*"
                      r"(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}", cite_repl, text)
        # \href{url}{text}: the URL is the link target, the text is the content.
        text = re.sub(r"\\href\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
                      lambda m: m.group(2), text)
        text = re.sub(r"\\url\s*\{([^{}]*)\}", lambda m: m.group(1), text)
        return text

    # -- stage: formula -> placeholder ----------------------------------
    def _rewrite(self, text: str, unit: Unit) -> Tuple[str, List[FormulaRecord]]:
        text = preserved.restore(text, self._opaque_math, 'OPAQUEMATH')
        text = PART_MARKER_RE.sub(
            lambda m: "\ue000" + m.group(1) + "\ue001", text)
        spans = find_math_spans(text)
        records: List[FormulaRecord] = []
        out: List[str] = []
        cursor = 0

        for span in spans:
            out.append(self._clean_segment(text[cursor: span.start]))
            before = "".join(out)[-120:]
            after = text[span.end: span.end + 40]
            body = span.body.strip()
            if not body:
                cursor = span.end
                continue

            recs = self._make_records(body, span, before, after)
            records.extend(recs)
            if not recs:
                cursor = span.end
                continue

            prefix = ""
            tail = "".join(out).rstrip()
            standalone = (not self.merge_display) or (not tail) or tail[-1] in ".!?"
            if span.kind == "display" and standalone and self.keep_standalone:
                prefix = HARD_BREAK

            placeholder = " and ".join(r.id for r in recs)
            if len(recs) == 1 and recs[0].role == "proposition" \
                    and self.proposition_form == "holds":
                placeholder += " holds"

            affix = recs[0].affix if len(recs) == 1 else None
            if affix:
                placeholder += affix
                cursor = span.end + len(affix)
            else:
                cursor = span.end

            trailing = recs[-1].trailing_punct or ""
            break_after = False
            if span.kind == "display":
                break_after, drop = self._display_end(text[span.end:], trailing,
                                                      recs[-1])
                if drop:
                    trailing = ""
            if prefix:
                lead = "".join(out).rstrip()
                if lead[-1:].isalnum():
                    prefix = " " + prefix
            else:
                lead = "".join(out).rstrip()
                if lead[-1:].isalnum():
                    placeholder = " " + placeholder
            out.append(prefix + placeholder + trailing)
            if break_after:
                out.append(HARD_BREAK)

        out.append(self._clean_segment(text[cursor:]))
        joined = "".join(out)
        joined = drop_command_with_arg(joined, ("label",))
        joined = unwrap_formatting(joined)
        joined = apply_accents(joined)
        joined = normalize_dashes_quotes(joined)
        joined = re.sub(r" +([,;.!?:])", r"\1", joined)
        return joined, records

    def _slot_record(self, fid, slot, formula, source_id, before, after):
        ident = "%s.t%d" % (fid, slot["index"])
        fields = [self.paper, ident, formula, slot["offset"], before, after]
        anchor = hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()
        item = {"id": ident, "anchor": anchor, "paper": self.paper, "sig": slot["key"],
                "formula": fid, "source_formula": source_id, "latex": formula, "offset": slot["offset"], "raw": slot["raw"],
                "count": 1, "answer": None}
        row = self.slot_answers.get(anchor, {})
        answer = row.get("answer")
        if row.get("id") != ident or answer not in SLOT_ANSWERS:
            answer = None
        role = slot["role"]
        record = {"id": ident, "anchor": anchor, "raw": slot["raw"],
                  "before": slot["before"].strip(), "after": slot["after"].strip()}
        manual = self.reviewed.get(("slot", anchor))
        if manual:
            answer = role = manual["answer"]
            record.update(manual_review.provenance(manual))
        elif role != "unclassified":
            record["rule"] = slot["rule"]
            if answer and rule_reviews.from_model(row.get("source")):
                record["model_review"] = rule_reviews.compare(slot["rule"], role, answer,
                                                              row["source"], row.get("model"))
        elif answer:
            role = answer
            record["source"] = row.get("source") or "manual"
            if row.get("model"):
                record["model"] = row["model"]
        slot["role"] = record["role"] = role
        if not answer:
            self.slot_questions.append(item)
            if role == "unclassified":
                self.slots_pending.append(item)
        return record

    #: commands that print nothing, so the character after them is the one the
    #: reader sees.  Without this the lookahead stopped at the backslash of a
    #: ``\label`` and called the boundary unclear (43 times over 22 papers).
    INVISIBLE_AFTER_DISPLAY = re.compile(
        r"\s*(?:%(?:[^\n]*\n\s*)|(?:\\(?:label|index|protect|nolinebreak"
        r"|allowbreak|ignorespaces|relax|noindent)\b\s*(?:\{[^{}]*\}|\[[^\]]*\])?"
        r"\s*))+")
    #: a block that starts after a display is a new unit, never a continuation
    BLOCK_AFTER_DISPLAY = re.compile(
        r"\s*(?:\\begin\{(?:display|list|statement|grid|diagram)\}|"
        r"\\begin\{(?:equation|align|gather|multline|eqnarray)\*?\})")
    #: an enumerated item: "(b) Let ...", "(iv) The ...", "1. Let ..."
    ITEM_LABEL_AFTER_DISPLAY = re.compile(
        r"\s*[\[(]?\s*(?:[0-9]+|[a-zA-Z]|[ivxIVX]+)\s*[\]).:]\s")

    def _display_decision(self, rec, message, split, drop):
        key = manual_review.digest([rec.id, rec.raw, message])
        decision = self.reviewed.get(("display", key))
        if decision:
            split = decision["answer"] == "split"
            mark = rec.trailing_punct
            drop = (mark in (",", ";") if split else mark in (".", "!", "?"))
            self.applied_boundaries.append({"id": rec.id, "answer": decision["answer"], **manual_review.provenance(decision)})
        else:
            self._note(rec.id, message)
        if drop:
            rec.flags.append("mark_dropped")
        return split, drop

    def _display_end(self, rest: str, trailing: str,
                     rec: FormulaRecord) -> Tuple[bool, bool]:
        r"""Does a display formula end its sentence?  (break, drop the mark)

        The character after the formula answers the question; the punctuation
        inside the formula is what the author wrote and is the fallback.  They
        disagree about three times in two thousand displays, and both signals
        are kept so the disagreement can be reviewed.

        Only what the reader sees counts: a ``\label`` between the display and
        the next word prints nothing, and a block that follows the display
        (another display, a list, a theorem) always starts a new unit.
        """
        while True:
            trimmed = self.INVISIBLE_AFTER_DISPLAY.sub("", rest, count=1)
            if trimmed == rest:
                break
            rest = trimmed
        if not re.search(r"[^\s~]", rest):
            return False, False          # end of the paragraph: nothing to cut
        if self.BLOCK_AFTER_DISPLAY.match(rest):
            return True, False
        if self.ITEM_LABEL_AFTER_DISPLAY.match(rest):
            return True, False
        # a bracketed aside or heading: judge by the first letter inside it
        opening = re.match(r"\s*[\[(]\s*", rest)
        if opening:
            inner = rest[opening.end():]
            first = re.search(r"[^\s~{]", inner)
            if first and inner[first.start()].isupper():
                return True, False
            if first and inner[first.start()].islower():
                return False, False
        m = re.search(r"[^\s~]", rest)
        nxt = rest[m.start()] if m else ""
        if not nxt:
            return False, False          # end of the paragraph: nothing to cut
        if nxt.isupper():
            # a new sentence.  A comma here means the author joined two
            # sentences with the wrong mark.
            if trailing and trailing in ",;":
                return self._display_decision(rec, "a new sentence follows but the display ends "
                                              "with %r; the mark is dropped" % trailing, True, True)
            return True, False
        if nxt.islower():
            # the sentence goes on.  A full stop here was not the end of one.
            if trailing == ".":
                return self._display_decision(rec, "the sentence continues in lower case but the "
                                              "display ends with '.'; the mark is dropped", False, True)
            return False, False
        # an inline formula, a number, a bracket, a reference: the character
        # says nothing, so the author's punctuation decides
        if trailing and trailing in ".!?":
            return True, False
        if trailing and trailing in ",;":
            return False, False
        rec.flags.append("sentence_end_unclear")
        return self._display_decision(rec, "display ends without punctuation and an inline "
                                      "formula follows; sentence boundary not decided", False, False)

    def _make_records(self, body: str, span: Span, before: str,
                      after: str) -> List[FormulaRecord]:
        """Turn one math span into one or several table records."""
        unknown = unknown_commands(body)
        drawing = bool(DRAWING.search(body))
        if (unknown or CONTEXTUAL_MATH.search(body)) and not drawing:
            # No whitelist completion required. One formula, intact arguments,
            # no qquad/side-condition/slot rewriting or inferred mathematical role.
            core, punct = strip_trailing_punct(body)
            pid = self._next_math_id()
            rec = FormulaRecord(id=pid, raw=body, expanded=core, kind=span.kind,
                                role='opaque', opaque=True, unrecognized_commands=unknown,
                                env=span.env, label=span.label, trailing_punct=punct,
                                labels=list(span.labels), display_tags=list(span.display_tags),
                                rows=[{"labels": names} if names else {} for names in span.row_labels] if any(span.row_labels) else [],
                                flags=['opaque_formula'], context_before=before[-120:],
                                context_after=after[:80])
            if unknown:
                self._formula_diagnostic(pid, 'unrecognized_commands',
                    'Formula preserved without interpreting internal commands', commands=unknown)
            return [rec]
        parts: List[str] = [body]
        original_parts = parts
        side: List[str] = []
        terminal = ""

        if span.kind == "display" and self.split_qquad and not span.label and r"\\" not in body:
            raw_parts = [p.strip() for p in split_top_level(body, "\\qquad")]
            raw_parts = [p for p in raw_parts if p]
            if len(raw_parts) > 1:
                last = raw_parts[-1]
                core_last, punct_last = strip_trailing_punct(last)
                if core_last.startswith("(") and core_last.endswith(")") \
                        and len(core_last) > 2 and not TEXT_CMD_RE.search(core_last):
                    side.append(core_last)
                    terminal = punct_last
                    raw_parts = raw_parts[:-1]
                cleaned: List[str] = []
                origins = []
                for k, piece in enumerate(raw_parts):
                    core, punct = strip_trailing_punct(piece)
                    if k == len(raw_parts) - 1 and not terminal:
                        terminal = punct
                    if core:
                        cleaned.append(core)
                        origins.append(piece)
                if cleaned:
                    parts, original_parts = cleaned, origins

        records: List[FormulaRecord] = []
        source_id = "MATH%d" % (self._math_count + 1)
        next_offset = 0
        for idx, part in enumerate(parts):
            original_part = original_parts[idx]
            part_offset = body.index(original_part, next_offset)
            next_offset = part_offset + len(original_part)
            # ``\(..\)`` inside a formula is nested math, which TeX does not
            # allow: the author meant the content, and the delimiters arrive
            # from a macro that wrapped its argument in inline math (a
            # ``\raisebox{..}{\(x\)}`` whose box was dropped).  They are not
            # tokens of the formula.
            part = re.sub(r"\\[()]", " ", part)
            core, punct = strip_trailing_punct(part)
            if not core:
                continue
            # a commutative diagram is one opaque object: keep the record so the
            # sentence still has a placeholder, but do not parse the drawing.
            # ``diagram`` is the canonical form normalize.py puts every drawing
            # into, so it has to be recognised here as well: the raw names below
            # never appear in canonical text, and the drawing's body was being
            # read as a formula (its ``$..$`` inside a ``\makebox`` even became a
            # separate record), with ``\ar`` and friends counted as words.
            diagram = bool(DRAWING.search(core))
            if diagram:
                core = "\\operatorname{diagram}"
            core, extra_side = split_side_condition(core)
            if extra_side:
                side.append(extra_side)
            pid = self._next_math_id()
            original_core = core
            core, raw_slots = extract_text_slots(
                core, self.operator_names, self.slot_roles,
                self.declared_operators)
            # Mark the complete math span even for split displays and cleaned delimiters.
            if raw_slots:
                offsets = {clean[0]: raw[0] for clean, raw in
                           zip(text_groups(original_core), text_groups(original_part))}
                for slot in raw_slots:
                    slot["offset"] = part_offset + offsets[slot["offset"]]
            text_slots = [self._slot_record(pid, slot, body, source_id, before, after)
                          for slot in raw_slots]
            role, flags, top = classify_role(core, span.kind, before, after, raw_slots)
            rows = aligned_relations(core, raw_slots) if span.kind == "display" and not diagram else []
            if rows:
                flags = [flag for flag in flags if flag != "relation_chain"] + ["alignment"]
                top = rows[0]["relations"][0] if len(rows) == 1 and len(rows[0]["relations"]) == 1 else None
                for row, labels in zip(rows, span.row_labels):
                    if labels:
                        row["labels"] = labels
            if diagram:
                role, flags = "term", ["diagram"]
            affix = None
            if role == "modifier":
                m = re.match(r"-([A-Za-z]+)", after)
                if m:
                    affix = "-" + m.group(1)
            if idx == len(parts) - 1:
                punct = terminal or punct
            rec = FormulaRecord(
                id=pid, raw=part, expanded=core, kind=span.kind, role=role,
                env=span.env, label=span.label, affix=affix,
                trailing_punct=punct if idx == len(parts) - 1 else "",
                side_conditions=list(side) if idx == len(parts) - 1 else [],
                text_slots=text_slots,
                top_relation=top, flags=list(flags), rows=rows,
                labels=list(span.labels), display_tags=list(span.display_tags),
                context_before=re.sub(r"\s+", " ", normalize_dashes_quotes(
                    before.replace(HARD_BREAK, " "))).strip()[-80:],
                context_after=re.sub(r"\s+", " ", normalize_dashes_quotes(
                    after.replace(HARD_BREAK, " "))).strip()[:80],
            )
            unknown = unknown_commands(core)
            if unknown:
                rec.flags.append("unknown_cmd:" + ",".join(unknown))
                rec.unrecognized_commands = unknown
                self._formula_diagnostic(pid, 'unrecognized_commands',
                                         ', '.join(unknown), commands=unknown)
            vague = [s for s in text_slots if s["role"] == "unclassified"]
            if vague:
                rec.flags.append("text_slot_unclassified")
                self._formula_diagnostic(pid, 'unclassified_text_slot', "text slot(s) with no role hint: " + ", ".join(
                    "%s=%r" % (s["id"], s["raw"].strip()) for s in vague))
            if "has_text" in flags:
                self._formula_diagnostic(pid, 'unparsed_formula_text', "leftover \\text{...} that could not be lifted "
                                "into a text slot")
            if "piecewise" in flags:
                self._formula_diagnostic(pid, 'formula_structure', "piecewise definition; needs a cases node")
            # a bare run of letters ("xy", "Cf") is a question for the word
            # table, not a defect: lexicon.py exports it like any other
            # juxtaposition, so it must not also be reported here
            if "big_delimiter" in flags and top and top["symbol"] == "=":
                self._formula_diagnostic(pid, 'formula_structure', "set-builder or large bracket: relation inside "
                                "the brackets was treated as opaque")
            records.append(rec)
        return records


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def write_boundary_todo(items: List[dict], path: str) -> None:
    payload = {
        "instructions": BOUNDARY_INSTRUCTIONS,
        "allowed_answers": list(BOUNDARY_ANSWERS),
        "answer_meaning": {
            "split": "the full stop ends a sentence",
            "join": "it does not; the two halves are one sentence",
        },
        "meta": {"items": len(items),
                 "sentences": len({i.get("sid_before") for i in items
                                   if i.get("sid_before")})},
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_boundary_answers(path: str) -> Dict[str, str]:
    """Read a boundary answer file; refuse anything outside the answer set."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("%s: expected a list under 'items'" % path)
    answers: Dict[str, str] = {}
    for item in items:
        sig, answer = item.get("sig"), item.get("answer")
        if not sig or not answer:
            continue
        if answer not in BOUNDARY_ANSWERS:
            raise ValueError("%s: %r is not one of %s (sig %r)"
                             % (path, answer, list(BOUNDARY_ANSWERS), sig))
        answers[sig] = answer
    return answers


def load_slot_roles(path: str = SLOT_TABLE_PATH) -> Dict[str, str]:
    """The committed phrase -> role table, shared by every paper."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    roles = data.get("roles") if isinstance(data, dict) else data
    return {k: v for k, v in (roles or {}).items() if v in SLOT_ANSWERS}


def write_slot_todo(items: List[dict], path: str) -> None:
    payload = {
        "instructions": SLOT_INSTRUCTIONS,
        "allowed_answers": list(SLOT_ANSWERS),
        "answer_meaning": {k: v for k, v in zip(
            SLOT_ANSWERS, ("the condition in {x | ...}", "a whole statement",
                           "a verb or relation", "the preposition of one",
                           "the 'if' of an implication",
                           "the 'then' of an implication",
                           "a quantifier", "and / or", "'where'", "'otherwise'",
                           "a name inside a subscript",
                           "running words, no structure",
                           "an upright name that is a function"))},
        "meta": {"items": len(items),
                 "occurrences": sum(i.get("count", 1) for i in items)},
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def merge_slot_answers(path: str, table_path: str = SLOT_TABLE_PATH
                       ) -> Tuple[int, List[Tuple[str, str]]]:
    """Fold an answered slot list into the shared table; (applied, rejected)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("%s: expected a list under 'items'" % path)
    try:
        with open(table_path, encoding="utf-8") as fh:
            table = json.load(fh)
    except (OSError, ValueError):
        table = {}
    if not isinstance(table, dict) or "roles" not in table:
        table = {"_about": "Phrase -> role for text inside formulas. Each "
                           "answer needs only the formula around the phrase.",
                 "allowed_answers": list(SLOT_ANSWERS),
                 "roles": table if isinstance(table, dict) else {}}
    applied, rejected = 0, []
    for item in items:
        sig, answer = item.get("sig"), item.get("answer")
        if not sig or not answer:
            continue
        if answer not in SLOT_ANSWERS:
            rejected.append((sig, answer))
            continue
        table["roles"][sig] = answer
        applied += 1
    table["allowed_answers"] = list(SLOT_ANSWERS)
    with open(table_path, "w", encoding="utf-8") as fh:
        json.dump(table, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    return applied, rejected


def write_outputs(result: Dict[str, object], out_dir: str,
                  write_tokens: bool = True) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "sentences": os.path.join(out_dir, "sentences.txt"),
        "jsonl": os.path.join(out_dir, "sentences.jsonl"),
        "formulas": os.path.join(out_dir, "formulas.json"),
        "unresolved": os.path.join(out_dir, "unresolved.log"),
    }
    if write_tokens:
        paths["tokens"] = os.path.join(out_dir, "sentences.tok.txt")
    sentences = result["sentences"]
    with open(paths["sentences"], "w", encoding="utf-8") as f:
        for s in sentences:
            f.write(s["text"] + "\n")
    if write_tokens:
        with open(paths["tokens"], "w", encoding="utf-8") as f:
            for s in sentences:
                f.write(" ".join(s["tokens"]) + "\n")
    with open(paths["jsonl"], "w", encoding="utf-8") as f:
        for s in sentences:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(paths["formulas"], "w", encoding="utf-8") as f:
        json.dump(result["formulas"], f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "content_blocks.json"), "w", encoding="utf-8") as f:
        json.dump(result.get("content_blocks", []), f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "split_status.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 2, "paper_id": result.get("paper_id"),
                   "manual_boundaries": result.get("manual_boundaries", []),
                   "structure_schema": result.get("structure_schema", 0),
                   "counts": {key: len(result.get(key, [])) for key in ('sentences','formulas','content_blocks')},
                   "pending": {"content": sum(b.get('status') == 'pending' for b in result.get('content_blocks', [])),
                               "boundaries": sum(not b.get('answer') for b in result.get('sentence_boundaries', [])),
                               "unresolved": len(result.get('unresolved', []))},
                   "downstream": {"slots": len(result.get('slots_pending', [])),
                                  "formula_diagnostics": len(result.get('formula_diagnostics', []))}}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, 'formula_diagnostics.json'), 'w', encoding='utf-8') as f:
        json.dump({'scope': 'downstream', 'blocks_splitting': False,
                   'items': result.get('formula_diagnostics', [])}, f, ensure_ascii=False, indent=2)
    pending_blocks = [b for b in result.get("content_blocks", []) if b.get("status") == "pending"]
    todo = os.path.join(out_dir, "content_todo.json")
    if pending_blocks:
        with open(todo, "w", encoding="utf-8") as f:
            json.dump({"items": pending_blocks}, f, ensure_ascii=False, indent=2)
    elif os.path.exists(todo):
        os.remove(todo)
    conditionals = [b for b in pending_blocks if b.get("kind") == "conditional"]
    conditional_path = os.path.join(out_dir, "conditional_todo.json")
    if conditionals:
        with open(conditional_path, "w", encoding="utf-8") as f:
            json.dump({"items": conditionals}, f, ensure_ascii=False, indent=2)
    elif os.path.exists(conditional_path):
        os.remove(conditional_path)
    with open(paths["unresolved"], "w", encoding="utf-8") as f:
        for line in result["unresolved"]:
            f.write(line + "\n")
    return paths


# --------------------------------------------------------------------------
# self test (synthetic snippets only; never reads a .tex file)
# --------------------------------------------------------------------------


def _self_test() -> int:
    failures = 0

    # -- a one-token argument, as TeX reads it --------------------------
    _m = MacroTable()
    _m.parse(r"\def\B#1{\mathbf{#1}}\def\G{\Gamma}"
             r"\def\bind#1#2{#1|_{#2}}\def\two#1#2{(#1,#2)}")
    _cases = [
        (r"\B C", r"\mathbf{C}", "a letter without braces"),
        (r"\B\Gamma", r"\mathbf{\Gamma}", "a control sequence"),
        (r"\B \Gamma", r"\mathbf{\Gamma}", "a space before it"),
        (r"\bind X 0", r"X|_{0}", "two one-token arguments"),
        (r"\two a b", r"(a,b)", "two letters"),
        (r"\B}", r"\B}", "a closing brace is not an argument"),
        (r"\B\end{x}", r"\B\end{x}",
         r"\end is structure, not an argument"),
    ]
    for src, want, why in _cases:
        got = _m.expand(src)
        if got != want:
            failures += 1
            print("  FAIL one-token argument (%s): %r -> %r, wanted %r"
                  % (why, src, got, want))

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        if cond:
            print("  ok   %s" % name)
        else:
            failures += 1
            print("  FAIL %s   %s" % (name, detail))

    def run(tex: str, **kw: object) -> Dict[str, object]:
        return LatexToNL(**kw).convert(tex)  # type: ignore[arg-type]

    def table(res: Dict[str, object]) -> Dict[str, dict]:
        return {f["id"]: f for f in res["formulas"]}  # type: ignore[index]

    m_boundary_sig = boundary_sig

    print("[1] constraint vs term")
    r = run(r"For an integer $d\ge1$, the cube graph $Q_d$ has vertex set "
            r"$\{0,1\}^d$.")
    forms = table(r)
    check("one sentence", len(r["sentences"]) == 1, str(r["sentences"]))
    check("$d\\ge1$ is a constraint",
          forms["MATH1"]["role"] == "constraint", forms["MATH1"]["role"])
    check("constraint split",
          forms["MATH1"]["top_relation"]["lhs"] == "d"
          and forms["MATH1"]["top_relation"]["symbol"] == "\u2265",
          str(forms["MATH1"]["top_relation"]))
    check("$Q_d$ is a term", forms["MATH2"]["role"] == "term",
          forms["MATH2"]["role"])
    check("no dollar left", "$" not in r["sentences"][0]["text"],
          r["sentences"][0]["text"])

    print("[2] hyphenated modifier")
    r = run(r"An $i$-face of $Q_K$ is obtained by fixing coordinates.")
    forms = table(r)
    check("modifier role", forms["MATH1"]["role"] == "modifier",
          forms["MATH1"]["role"])
    check("affix kept", forms["MATH1"]["affix"] == "-face",
          str(forms["MATH1"]["affix"]))
    check("token in text", "MATH1-face" in r["sentences"][0]["text"],
          r["sentences"][0]["text"])

    print("[3] display merges into the sentence")
    r = run("For $x\\in\\{0,1\\}^d$, write\n\\[\n \\bar x=(1-x_1,\\ldots,"
            "1-x_d).\n\\]\nThe vertices $x$ and $\\bar x$ are called "
            "antipodal.")
    texts = [s["text"] for s in r["sentences"]]
    check("two sentences", len(texts) == 2, str(texts))
    check("display merged", "write MATH2." in texts[0], texts[0])

    print("[4] \\qquad splits a multi-formula display")
    r = run("Let $e_1,\\ldots,e_K$ be the standard basis, and set\n\\[\n"
            " W=\\left\\{z:\\sum_{a=1}^K z_a=0\\right\\}, \\qquad d=K-1.\n\\]\n")
    check("three formulas", len(r["formulas"]) == 3, str(len(r["formulas"])))
    check("joined with and", " and " in r["sentences"][-1]["text"],
          r["sentences"][-1]["text"])
    check("set-builder not split",
          "\\qquad" not in str(table(r)["MATH2"]["expanded"]),
          str(table(r)["MATH2"]["expanded"]))

    print("[5] trailing parenthetical becomes a side condition")
    r = run("We claim that\n\\begin{equation}\\label{eq:goal}\n"
            " c_{i-1}\\equiv c_i\\pmod2\\qquad(1\\le i\\le d).\n\\end{equation}\n")
    eqs = [f for f in r["formulas"] if f["kind"] == "display"]
    check("one display record", len(eqs) == 1, str(len(eqs)))
    check("label recorded", eqs[0]["label"] == "eq:goal", str(eqs[0]["label"]))
    check("side condition captured",
          bool(eqs[0]["side_conditions"])
          and "1\\le i" in eqs[0]["side_conditions"][0],
          str(eqs[0]["side_conditions"]))
    check("terminal punctuation kept",
          r["sentences"][-1]["text"].endswith("."),
          r["sentences"][-1]["text"])

    print("[6] macro expansion")
    r = run("\\newcommand{\\R}{\\mathbb R}\nThe maps $\\ell:W\\to\\R$ exist.")
    check("macro expanded", "\\mathbb R" in r["formulas"][0]["expanded"],
          r["formulas"][0]["expanded"])
    check("definition line dropped",
          "newcommand" not in str(r["sentences"]),
          str(r["sentences"]))

    print("[7] citations, refs, accents")
    r = run("Solt\\'esz~\\cite{Soltesz} proved this in Theorem~\\ref{thm:main}.")
    txt = r["sentences"][0]["text"]
    check("citation gone", "Soltesz" not in txt, txt)
    check("accent applied", "Solt\u00e9sz" in txt, txt)
    check("ref placeholder", "REF1" in txt, txt)
    refs = [f for f in r["formulas"] if f["kind"] == "ref"]
    check("ref target", bool(refs) and refs[0]["ref_target"] == "thm:main",
          str(refs))
    r = run("See \\crefrange{sec:one}{sec:two} and \\Cref{sec:three}.")
    txt = r["sentences"][0]["text"]
    refs = [f for f in r["formulas"] if f["kind"] == "ref"]
    check("a range reference keeps both labels",
          len(refs) == 2
          and refs[0]["ref_target"] == "sec:one,sec:two"
          and refs[1]["ref_target"] == "sec:three", str(refs))
    check("no raw LaTeX left in the sentence", "crefrange" not in txt, txt)
    # an unclosed $ used to abandon the rest of the block as prose, which is how
    # \mathfrak and \mathcal reached thousands of sentences
    r = run("We have $\\mathfrak{F}_i and that is all.")
    text = r["sentences"][0]["text"]
    check("an unclosed dollar keeps the maths out of the sentence",
          "\\mathfrak" not in text
          and any("\\mathfrak" in f["expanded"] for f in r["formulas"]),
          "%r / %r" % (text, [f["expanded"] for f in r["formulas"]]))
    # nested inline math survives a dropped box: ``\raisebox{..}{\(x\)}``
    r = run("We get $a+\\(y\\)$ here.")
    got = [" ".join(f["expanded"].split()) for f in r["formulas"]]
    check("nested \\(..\\) inside a formula is not a token",
          [g.replace(" ", "") for g in got] == ["a+y"], str(got))
    # a reference followed by a sub-item marker is not a range
    r = run("We apply Theorem~\\ref{thm:main}{\\bf (i)} now.")
    txt = r["sentences"][0]["text"]
    check("the sub-item marker after a reference survives",
          "(i)" in txt, txt)

    print("[8] abbreviations do not split")
    r = run("Let $x$ be a vertex, e.g. $x=(0,1)$. Then $x$ is fine.")
    check("two sentences", len(r["sentences"]) == 2,
          str([s["text"] for s in r["sentences"]]))

    print("[9] theorem environment labelling")
    r = run("\\section{Main}\n\\begin{theorem}[Norine]\n\\label{thm:main}\n"
            "Let $n\\ge2$. Then some vertex $y$ is joined to $\\bar y$.\n"
            "\\end{theorem}\n")
    s0 = r["sentences"][0]
    check("unit id from label", s0["unit"] == "thm:main", s0["unit"])
    check("env recorded", s0["env"] == "theorem", s0["env"])
    check("section recorded", s0["section"] == "Main", s0["section"])
    check("speech act definition", s0["speech_act"] == "definition",
          s0["speech_act"])

    print("[10] enumerate items are separate units")
    r = run("\\begin{lemma}\n\\begin{enumerate}\n\\item The entries are "
            "distinct.\n\\item If $q(x)=(a,b)$, then $q(\\bar x)=(b,a)$.\n"
            "\\end{enumerate}\n\\end{lemma}\n")
    check("one sentence per item", len(r["sentences"]) == 2,
          str([s["text"] for s in r["sentences"]]))
    check("item units differ",
          r["sentences"][0]["unit"] != r["sentences"][1]["unit"])

    print("[11] bibliography dropped")
    r = run("Text $x$.\n\\begin{thebibliography}{10}\n\\bibitem{a} A. Author, "
            "Title, 2020.\n\\end{thebibliography}\n")
    check("no bib sentence",
          all("Author" not in s["text"] for s in r["sentences"]),
          str([s["text"] for s in r["sentences"]]))

    print("[12] unknown command reported")
    r = run("We have $\\weirdop(x)=0$.")
    check("unknown formula is preserved with a nonblocking diagnostic",
          not r['unresolved'] and r['formulas'][0]['raw'] == r'\weirdop(x)=0'
          and any(r'\weirdop' in d.get('commands', []) for d in r['formula_diagnostics']),
          str(r['formula_diagnostics']))

    print("[12b] \\text{} inside a formula becomes a text slot")
    r = run("If $q(x)=(a,b)$, then $u\\mapsto\\ell(u)\\ \\text{has rank }h"
            "\\text{ on }U$.")
    f = [x for x in r["formulas"] if x["text_slots"]][0]
    check("two slots", len(f["text_slots"]) == 2, str(f["text_slots"]))
    check("placeholder in formula", "<TXT1>" in f["expanded"], f["expanded"])
    check("role of 'has rank'", f["text_slots"][0]["role"] == "predicate",
          f["text_slots"][0]["role"])
    check("raw kept", f["text_slots"][0]["raw"].strip() == "has rank",
          f["text_slots"][0]["raw"])
    check("no manual-review note",
          not any("embeds natural language" in u for u in r["unresolved"]),
          str(r["unresolved"]))

    print("[12c] an upright name is an operator, not a text slot")
    # An applied upright name and an isolated script label have distinct roles.
    r = run("We have $\\textup{Ric}(S)\\ge 0\\quad\\text{for every } S"
            "\\in C_{\\textup{PIC}}$ and $\\textbf{v}=0$ and $n\\text{ even}$,"
            " where $\\operatorname{Ric}(T)$ and $\\operatorname{PIC}$ are "
            "the same objects.")
    f = r["formulas"][0]
    check("name kept in formula", "\\operatorname{Ric}(S)" in f["expanded"],
          f["expanded"])
    check("label kept in subscript", "C_{<TXT2>}" in f["expanded"] and f["text_slots"][1]["role"] == "label",
          f["expanded"])
    check("phrase is still a slot",
          [s["raw"] for s in f["text_slots"]] == ["for every ", "PIC"],
          str(f["text_slots"]))
    check("raw untouched", "\\textup{Ric}" in f["raw"], f["raw"])
    check("names reported", r["operator_names"] == {"Ric": 1},
          str(r["operator_names"]))
    check("single letter left alone",
          len(r["formulas"][1]["text_slots"]) == 1, str(r["formulas"][1]))
    check("word with a space inside the braces left alone",
          [s["raw"] for s in r["formulas"][2]["text_slots"]] == [" even"],
          str(r["formulas"][2]))

    print("[12d] a word with no evidence stays a text slot")
    r = run("We have $x \\text{so} y$ here.")
    f = [x for x in r["formulas"] if x["text_slots"]][0]
    check("no \\operatorname for a plain word",
          "\\operatorname{so}" not in f["expanded"], f["expanded"])
    check("it is a slot, and reported",
          [s["raw"] for s in f["text_slots"]] == ["so"]
          and any(i.get("sig") == "so" for i in r.get("slots_pending") or []),
          "%s / %s" % (f["text_slots"], r.get("slots_pending")))

    print("[12c2] text slots are judged per phrase, from a data file")
    r = run("We have $\\text{for all } i$ and $\\text{~or~}$ and "
            "$\\text{whatever it takes}$.")
    f = r["formulas"]
    check("a judged phrase gets its role",
          [s["role"] for s in f[0]["text_slots"]] == ["scope"],
          str(f[0]["text_slots"]))
    check("~ is the same as a space",
          [s["role"] for s in f[1]["text_slots"]] == ["connective"],
          str(f[1]["text_slots"]))
    pend = r["slots_pending"]
    check("an unjudged phrase is listed once",
          len(pend) == 1 and pend[0]["sig"] == "whatever it takes"
          and pend[0]["count"] == 1, str(pend))
    r = run("A $\\text{odd phrase}$ then $\\text{odd phrase}$.")
    check("each occurrence of the same phrase has its own question",
          [i["id"] for i in r["slots_pending"]] == ["MATH1.t1", "MATH2.t1"], str(r["slots_pending"]))
    r = run("A $\\text{odd phrase}$.", slot_roles={"odd phrase": "predicate"})
    check("a supplied answer is used",
          r["formulas"][0]["text_slots"][0]["role"] == "predicate"
          and not r["slots_pending"], str(r["formulas"][0]["text_slots"]))
    with tempfile.TemporaryDirectory() as d:
        p, table = os.path.join(d, "todo.json"), os.path.join(d, "t.json")
        write_slot_todo(r["slots_pending"] or [{
            "sig": "odd phrase", "raw": "odd phrase", "answer": "predicate"}], p)
        data = json.load(open(p, encoding="utf-8"))
        for item in data["items"]:
            item["answer"] = "condition"
        json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        applied, rejected = merge_slot_answers(p, table)
        check("answers fold into the table",
              applied == 1 and not rejected
              and load_slot_roles(table).get("odd phrase") == "condition",
              str((applied, rejected, load_slot_roles(table))))

    print("[12d] a text slot is not the relations < and >")
    r = run("We get \\[ f(x)\\quad\\text{for every } x. \\] Also "
            "$u\\mapsto\\ell(u)\\ \\text{has rank }h\\text{ on }U$ and "
            "$\\textstyle{\\sum_i a_i}=1$.")
    f = r["formulas"]
    check("slot alone gives no relation", f[0]["role"] == "term"
          and "relation_chain" not in f[0]["flags"], str(f[0]))
    check("predicate slot is the relation",
          f[1]["role"] == "proposition"
          and f[1]["top_relation"]["symbol"] == "has rank"
          and f[1]["top_relation"]["rhs"].startswith("h"), str(f[1]))
    check("\\textstyle is not leftover text",
          "has_text" not in f[2]["flags"] and not r["unresolved"],
          str(f[2]["flags"]) + str(r["unresolved"]))

    print("[12e] multi-line displays stay whole")
    r = run("We get\n\\allowdisplaybreaks{\n\\begin{align}\n a &= b, \\\\\n"
            " c &= d.\n\\end{align}}\nNext we stop.")
    texts = [s["text"] for s in r["sentences"]]
    check("wrapped display is lifted", texts == ["We get MATH1.",
                                                 "Next we stop."], str(texts))
    r = run("We get\n\\begin{equation}\\begin{split} a &= b + c \\\\ "
            "& \\qquad + d. \\end{split}\\end{equation}\nNext we stop.")
    f = r["formulas"]
    check("split block is one record", len(f) == 1
          and "\\begin{split}" in f[0]["expanded"]
          and f[0]["expanded"].rstrip().endswith("\\end{split}"), str(f))
    check("full stop found before \\end", f[0]["trailing_punct"] == "."
          and "period_added" not in f[0]["flags"]
          and "d." not in f[0]["expanded"], str(f[0]))
    r = run("We get\n\\begin{align} a &= b \\\\ &\\qquad + c, \\notag "
            "\\end{align}\nwhere $c$ is big.")
    texts = [s["text"] for s in r["sentences"]]
    check("row-leading \\qquad is indentation",
          len([x for x in r["formulas"] if x["kind"] == "display"]) == 1,
          str(r["formulas"]))
    check("comma before \\notag keeps the sentence going",
          texts == ["We get MATH1, where MATH2 is big."], str(texts))
    r = run("We get \\[ a=b, \\qquad c=d. \\]")
    check("a real \\qquad still separates two statements",
          len(r["formulas"]) == 2, str(r["formulas"]))

    print("[12e2] \\ifdefined blocks, and diagrams")
    r = run("\\documentclass{article}\n\\newcommand*{\\SHOWPROOFS}{}\n"
            "\\begin{document}\n"
            "A first claim.\n\\ifdefined\\SHOWPROOFS\nProof of it.\n\\fi\n"
            "A second claim.\n\\ifdefined\\SHOWDETAIL\nHidden detail.\n\\fi\n"
            "\\end{document}\n")
    texts = [s["text"] for s in r["sentences"]]
    import format_names
    hidden_defined = format_names.lookup('article', ('SHOWDETAIL', 'SHOWPROOFS'), 'SHOWDETAIL')
    expected = ['A first claim.', 'Proof of it.', 'A second claim.']
    if hidden_defined is None:
        expected.append('REF1')
    elif hidden_defined:
        expected.append('Hidden detail.')
    check("defined block kept, undefined dropped",
          texts == expected,
          str(texts))
    r = run("\\ifdefined\\A\none\n\\else\ntwo\n\\fi\n")
    bound = format_names.lookup('kernel', ('A',), 'A')
    check("the else branch wins when the name is undefined",
          [s["text"] for s in r["sentences"]] == [
              'REF1' if bound is None else 'one' if bound else 'two'],
          str([s["text"] for s in r["sentences"]]))
    r = run("We have\n\\[\\begin{tikzcd} A \\arrow[r] & B \\end{tikzcd}\\]\n"
            "and that is all.")
    f = r["formulas"]
    check("tikzcd becomes one diagram record",
          len(f) == 1 and "diagram" in f[0]["flags"]
          and f[0]["expanded"] == "\\operatorname{diagram}",
          str(f))
    check("no unknown command from the drawing",
          not any("arrows" in u for u in r["unresolved"]),
          str(r["unresolved"]))

    print("[12e4] an alphabet declaration is a definition")
    r = run("\\DeclareMathAlphabet{\\bb}{U}{bbold}{m}{n}\n"
            "\\DeclareMathAlphabet{\\Cat}{U}{rsfs}{m}{n}\n"
            "\\DeclareSymbolFont{mysym}{U}{msb}{m}{n}\n"
            "\\DeclareSymbolFontAlphabet{\\Zs}{mysym}\n"
            "We have $\\bb{\\Delta}$, $\\Cat{A}$ and $\\Zs{Q}$.")
    done = [f["expanded"] for f in r["formulas"]]
    check("bbold reads as blackboard, rsfs as calligraphic, msb as blackboard",
          done == ["\\mathbb{\\Delta}", "\\mathcal{A}", "\\mathbb{Q}"], str(done))
    check("no unknown command from the declared names",
          not r["unresolved"], str(r["unresolved"]))
    r = run("\\DeclareMathAlphabet{\\odd}{U}{zeus}{m}{n}\nUse $\\odd{x}$.")
    check("an unreadable family falls back to upright",
          [f["expanded"] for f in r["formulas"]] == ["\\mathrm{x}"],
          str([f["expanded"] for f in r["formulas"]]))

    print("[12e5] a name an authority decided is understood")
    # 208 of the 580 remaining uses in the 20-paper sample were names TeX had
    # already classified; they were reported as unknown all the same.
    answered = [n for n in ("ncong", "heartsuit", "Bbbk")
                if n in ANSWERED_COMMANDS]
    check("the answer files are read", bool(answered), str(answered))
    known = unknown_commands("a \\ncong b \\heartsuit c")
    check("an answered name is not reported",
          "\\ncong" not in known and "\\heartsuit" not in known, str(known))
    check("a name nobody answered still is",
          unknown_commands("\\definitelynotacommand x")
          == ["\\definitelynotacommand"],
          str(unknown_commands("\\definitelynotacommand x")))
    # a row break followed by a letter is not a command: ``\\s_0`` in an
    # ``aligned`` block was read as ``\s`` and reported as an unknown name
    check("a row break is not a command",
          unknown_commands("a &= b \\\\s_0(c)") == [],
          str(unknown_commands("a &= b \\\\s_0(c)")))
    check("a control symbol is not a name",
          unknown_commands("x\\,y \\\\ \\alpha") == [],
          str(unknown_commands("x\\,y \\\\ \\alpha")))

    print("[12f] what ends the sentence after a display")
    r = run("We say that $T$ is PIC if\n\\begin{equation} Z(T)\\ge 0 \\end{equation}\n"
            "for every frame $F$.")
    check("no punctuation, lower case: one sentence",
          [s["text"] for s in r["sentences"]]
          == ["We say that MATH1 is PIC if MATH2 for every frame MATH3."],
          str([s["text"] for s in r["sentences"]]))
    check("nothing added, nothing to review",
          "period_added" not in r["formulas"][1]["flags"] and not r["unresolved"],
          str(r["formulas"][1]["flags"]) + str(r["unresolved"]))

    r = run("We compute\n\\begin{equation} x = 1 \\end{equation}\n"
            "Taking traces we get $y$.")
    check("no punctuation, capital: split without adding a period",
          [s["text"] for s in r["sentences"]]
          == ["We compute MATH1", "Taking traces we get MATH2."],
          str([s["text"] for s in r["sentences"]]))

    r = run("We prove that\n\\begin{equation} x = 1. \\end{equation}\n"
            "for every frame.")
    check("full stop but lower case: kept in one sentence",
          [s["text"] for s in r["sentences"]]
          == ["We prove that MATH1 for every frame."],
          str([s["text"] for s in r["sentences"]]))
    check("dropped mark flagged and logged",
          "mark_dropped" in r["formulas"][0]["flags"]
          and any("lower case" in u for u in r["unresolved"]),
          str(r["formulas"][0]["flags"]) + str(r["unresolved"]))
    check("the mark stays in the record",
          r["formulas"][0]["trailing_punct"] == ".", str(r["formulas"][0]))

    r = run("We bound $a$ and $b$\n\\begin{align} a &\\le b, \\end{align}\n"
            "Summing over $i$ we get $c$.")
    check("comma but capital: split",
          [s["text"] for s in r["sentences"]]
          == ["We bound MATH1 and MATH2 MATH3",
              "Summing over MATH4 we get MATH5."],
          str([s["text"] for s in r["sentences"]]))
    check("the comma does not reach the text",
          "," not in r["sentences"][0]["text"],
          r["sentences"][0]["text"])

    r = run("We have\n\\[ x = 1 \\]\n$y$ is positive.")
    check("inline formula next: undecided, logged",
          "sentence_end_unclear" in r["formulas"][0]["flags"]
          and any("not decided" in u for u in r["unresolved"]),
          str(r["formulas"][0]["flags"]) + str(r["unresolved"]))

    r = run("We compute\n\\[ x = 1 \\]")
    check("nothing follows: no mark invented",
          [s["text"] for s in r["sentences"]] == ["We compute MATH1"]
          and "period_added" not in r["formulas"][0]["flags"],
          str([s["text"] for s in r["sentences"]]))

    print("[12g] a full stop before a lower-case word is sent to review")
    tex = ("We use the notation $x$ and so on. this half starts lower case "
           "and continues.")
    r = run(tex)
    b = r["sentence_boundaries"]
    check("one doubtful break recorded", len(b) == 1, str(b))
    check("the stopping point is kept", len(r["sentences"]) == 2,
          str([s["text"] for s in r["sentences"]]))
    check("default answer is split", b[0]["default"] == "split"
          and b[0]["answer"] is None, str(b[0]))

    sig = m_boundary_sig("We use the notation MATH1 and so on.",
                         "this half starts lower case and continues.")
    check("signature is built from the two halves", b[0]["sig"] == sig,
          "%r != %r" % (b[0]["sig"], sig))

    r = run(tex, sentence_answers={sig: "join"})
    check("answer join merges the two halves",
          [s["text"] for s in r["sentences"]]
          == ["We use the notation MATH1 and so on this half starts lower case "
              "and continues."],
          str([s["text"] for s in r["sentences"]]))
    check("nothing left to review", not r["sentence_boundaries"],
          str(r["sentence_boundaries"]))

    r = run("We use $x$. This half starts upper case.")
    check("a capital after the stop is not reviewed",
          not r["sentence_boundaries"] and len(r["sentences"]) == 2,
          str(r["sentence_boundaries"]))

    r = run("We stop here. (See Figure 3 for the rest.) Then we go on.")
    check("a bracket then a capital is a sentence start too",
          not r["sentence_boundaries"], str(r["sentence_boundaries"]))

    r = run("This is one sentence. and again. This is one sentence. and again.")
    check("repeated signature counted once",
          len(r["sentence_boundaries"]) == 1
          and r["sentence_boundaries"][0]["count"] == 2,
          str(r["sentence_boundaries"]))

    print("[12h] the boundary answer file")
    r = run("We use the notation $x$ and so on. this half continues.")
    check("one item to answer", len(r["sentence_boundaries"]) == 1,
          str(r["sentence_boundaries"]))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sentence_todo.json")
        write_boundary_todo(r["sentence_boundaries"], p)
        got = load_boundary_answers(p)
        check("round trip without answers is empty", got == {}, str(got))
        data = json.load(open(p, encoding="utf-8"))
        check("allowed answers listed", data["allowed_answers"] == ["split",
                                                                   "join"],
              str(data.get("allowed_answers")))
        for item in data["items"]:
            item["answer"] = "join"
        json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        check("answers load by signature",
              list(load_boundary_answers(p).values()) == ["join"],
              str(load_boundary_answers(p)))
        for item in data["items"]:
            item["answer"] = "unsure"
        json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        try:
            load_boundary_answers(p)
            ok = False
        except ValueError:
            ok = True
        check("an answer outside the set is refused", ok, "no error raised")

    print("[13] long sentence flagged")
    r = run("Let $x$ be " + " ".join(["very"] * 100) + " long.")
    check("spring flag set",
          any("too_long_for_spring" in s["flags"] for s in r["sentences"]),
          str([s["flags"] for s in r["sentences"]]))

    print("[14] proposition form option")
    r = run("We have $p(y)\\ne p(\\bar y)$.", proposition_form="holds")
    check("holds appended", "MATH1 holds" in r["sentences"][0]["text"],
          r["sentences"][0]["text"])

    print("[15] tokenisation")
    r = run(r"An $i$-face of Norine's cube, e.g. $Q_K$, is red-blue.")
    toks = r["sentences"][0]["tokens"]
    check("placeholder with affix kept whole", "MATH1-face" in toks, str(toks))
    check("hyphenated compound kept whole", "red-blue" in toks, str(toks))
    check("possessive split", "Norine" in toks and "'s" in toks, str(toks))
    check("abbreviation one token", "e.g." in toks, str(toks))
    check("punctuation separate", "," in toks and "." in toks, str(toks))
    check("round trip",
          "".join(toks) == re.sub(r"\s+", "", r["sentences"][0]["text"]),
          str(toks))
    check("tokens on every sentence",
          all(s["tokens"] for s in r["sentences"]))
    # the round trip must hold for every character, not just the ones that
    # happened to appear in that sentence: ``_`` used to vanish silently
    for probe in (r"W_{\mathrm{af}}", "x_1 and y_2", "MATH12_3", "a^2_b",
                  "caf\u00e9 na\u00efve", "a\u00a0b"):
        got = tokenize(probe)
        check("round trip on %r" % probe,
              "".join(got) == re.sub(r"\s+", "", probe), str(got))
    r = run("Kirchweger et al. improved this to $O(n)$, and Hollom did too.")
    toks = r["sentences"][0]["tokens"]
    check("et al. keeps its dot", "al." in toks and "." not in toks[:-1],
          str(toks))
    check("et al. does not split the sentence", len(r["sentences"]) == 1,
          str([s["text"] for s in r["sentences"]]))
    r = run("We need $x$ such that $f(x)=0$, and $y$ s.t. $g(y)=1$.")
    toks = r["sentences"][0]["tokens"]
    check("s.t. is one token", "s.t." in toks, str(toks))
    r = run("Cited after J. Smith and co., see w.l.o.g. the case $n=1$.")
    toks = r["sentences"][0]["tokens"]
    check("single initial before a name is an explicit boundary question",
          any(b['before'].endswith('J.') and b['after'].startswith('Smith')
              for b in r['sentence_boundaries']), str(r['sentence_boundaries']))

    print("[16] the canonical pipeline, end to end")
    # Everything above runs on raw LaTeX.  The pipeline does not: it feeds
    # latex2nl the normalised text and passes ``--canonical``, so the canonical
    # container names have to be exercised here too or the two can drift.
    global MATH_ENVS, MATH_INNER_ENVS, LIST_ENVS
    saved = (MATH_ENVS, MATH_INNER_ENVS, LIST_ENVS)

    def run_canonical(tex: str) -> Dict[str, object]:
        use_canonical_names()
        conv = LatexToNL()
        conv.canonical = True
        return conv.convert(tex)

    try:
        r = run_canonical(
            "\\DeclareMathAlphabet{\\mathbbe}{U}{bbold}{m}{n}\n"
            "\\begin{statement}{lemma}\nThe subcategory $\\mathbbe{\\Delta}$ "
            "is not functorial.\n"
            "\\begin{display}\\begin{diagram}{xymatrix}\n"
            " [0] \\ar@{ >->}[r] & [n] \\ar[d]\\\\\n"
            " [m] \\ar[r] & [m]\\vee[n] \\makebox[0em][l]{${}=[m+n]$}"
            "\\save@^{|-}\\restore\n"
            " \\end{diagram}\\end{display}\n"
            "\\end{statement}\n")
        texts = [s["text"] for s in r["sentences"]]
        f = r["formulas"]
        check("statement is one unit, display one formula",
              len(f) == 2 and f[0]["expanded"] == "\\mathbb{\\Delta}"
              and "diagram" in f[1]["flags"], str(f))
        check("no drawing command in the prose",
              all("xymatrix" not in t and "\\ar" not in t for t in texts),
              str(texts))
        check("nothing left to review in canonical form",
              not r["unresolved"], str(r["unresolved"]))
        # the kind brace and the title of a canonical container belong to the
        # marker, not to the prose: ``\begin{statement}{proof}[Proof of 2.8]``
        # used to leave ``{proof}[Proof of 2.8]`` at the head of the sentence
        r = run_canonical(
            "We have\n\\begin{statement}{proof}[Proof of Proposition 2.8]\n"
            "The map is injective.\\end{statement}\nand that is all.\n")
        texts = [s["text"] for s in r["sentences"]]
        check("a container keeps neither its kind nor its title in the prose",
              all("{proof}" not in t and "Proof of Proposition" not in t
                  for t in texts), str(texts))
        check("the title reaches the unit instead",
              any(s.get("title") == "Proof of Proposition 2.8"
                  for s in r["sentences"]), str([s.get("title") for s in r["sentences"]]))
    finally:
        MATH_ENVS, MATH_INNER_ENVS, LIST_ENVS = saved

    print("[17] brackets are depth")
    global PAREN_DEPTH

    def judged(body: str, before: str = "") -> Tuple[str, object, List[str]]:
        role, flags, top = classify_role(body, "inline", before, "")
        return role, (top or {}).get("symbol"), flags

    for body, role, symbol in (
            (r"f(x,y,z)", "term", None),
            (r"(X,m,e)", "term", None),
            (r"\mathbb{P}(X=0)", "term", None),
            (r"[x=y]", "term", None),
            (r"a_1,\ldots,a_n", "sequence", None),
            (r"g(a,b)=h(c,d)", "proposition", "="),
            (r"x\in(a,b],\ y=1", "proposition", "="),
            (r"F=\left(a,b\right.", "proposition", "="),
            (r"(n \geq 2)", "proposition", "\u2265"),
            (r"\bigl(n \geq 2\bigr)", "proposition", "\u2265"),
            (r"(a)(b=c)", "term", None),
            (r"(a_n: n\in\mathbb{N}_0)", "term", None),
            (r"(\mathbb{N}^\times,\mid)", "term", None)):
        got = judged(body)
        check("%s is a %s" % (body, role),
              got[:2] == (role, symbol) and "unbalanced" not in got[2],
              str(got))
    top = classify_role(r"(n \geq 2)", "inline", "", "")[2]
    check("the outer brackets are on neither side of the relation",
          bool(top) and (top["lhs"], top["rhs"]) == ("n", "2"), str(top))
    check("a trigger word reaches the formula inside the brackets",
          judged(r"(n \geq 2)", "for ")[0] == "constraint",
          str(judged(r"(n \geq 2)", "for ")))
    for body, role in ((r"]a,b[", "term"), (r"f(x=1", "proposition"),
                       (r"x\in[0,1[,\ y=1", "proposition")):
        got = judged(body)
        check("%s does not pair up and is read as before" % body,
              got[0] == role and "unbalanced" in got[2], str(got))
    for body in (r"f(x,y,z)", r"\mathbb{P}(X=0)", r"(n \geq 2)",
                 r"g(a,b)=h(c,d)", r"(a)(b=c)", r"(a_n: n\in\mathbb{N}_0)"):
        sized = body.replace("(", "\\left(").replace(")", "\\right)")
        a, b = judged(body), judged(sized)
        check("%s is judged like its \\left( form" % body, a[:2] == b[:2],
              "%s | %s" % (a, b))
    r = run("We get\n\\[ f(a \\qquad b) = c \\]\nand stop.")
    check("\\qquad inside brackets does not split the display",
          len(r["formulas"]) == 1, str(r["formulas"]))
    r = run("We get\n\\[ x\\in[0,1[ \\qquad y=1 \\]\nand stop.")
    check("a display whose brackets do not pair up still splits",
          len(r["formulas"]) == 2, str(r["formulas"]))
    PAREN_DEPTH = False
    try:
        check("with the switch off it is as it was",
              judged(r"f(x,y,z)")[0] == "sequence"
              and judged(r"\mathbb{P}(X=0)")[:2] == ("proposition", "=")
              and "unbalanced" not in judged(r"]a,b[")[2],
              "%s %s" % (judged(r"f(x,y,z)"), judged(r"\mathbb{P}(X=0)")))
    finally:
        PAREN_DEPTH = True

    print()
    if failures:
        print("%d check(s) failed" % failures)
        return 1
    print("all checks passed")
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Turn a LaTeX maths paper into plain sentences plus a "
                    "formula table for DepCCG / SPRING.")
    parser.add_argument("tex", nargs="?", help="input .tex file")
    parser.add_argument("-o", "--out", default="nlprep_out",
                        help="output directory (default: nlprep_out)")
    parser.add_argument("--proposition-form", choices=("bare", "holds"),
                        default="bare",
                        help="leave a propositional placeholder bare (MATH7) "
                             "or add a verb (MATH7 holds)")
    parser.add_argument("--max-words", type=int, default=90,
                        help="flag sentences longer than this; SPRING drops "
                             "input above 100 words")
    parser.add_argument("--no-merge-display", action="store_true",
                        help="never merge display maths into the sentence")
    parser.add_argument("--no-split-qquad", action="store_true",
                        help="keep a multi-formula display as one record")
    parser.add_argument("--no-tok", action="store_true",
                        help="do not write sentences.tok.txt (the "
                             "space-separated token stream)")
    parser.add_argument("--tok-mode", choices=("ptb", "space"), default="ptb",
                        help="'ptb' splits punctuation off (matches CCGbank); "
                             "'space' splits on whitespace only")
    parser.add_argument("--limit", type=int, default=0,
                        help="print the first N sentences on stdout")
    parser.add_argument("--sentence-todo", metavar="PATH", default=None,
                        help="where to write the doubtful sentence breaks "
                             "(default: <out>/sentence_todo.json)")
    parser.add_argument("--merge-sentence-answers", metavar="PATH",
                        help="answer file for the doubtful sentence breaks")
    parser.add_argument("--slot-todo", metavar="PATH", default=None,
                        help="phrases inside \\text{...} that still need a "
                             "role (default: <out>/slot_todo.json)")
    parser.add_argument("--merge-slot-answers", metavar="PATH",
                        help="apply answers by paper, slot ID and occurrence anchor")
    parser.add_argument("--slot-table", metavar="PATH", default=SLOT_TABLE_PATH,
                        help="phrase -> role table (default: data/slot_roles.json)")
    parser.add_argument("--style", action="append", metavar="PATH",
                        default=[],
                        help="extra .sty file; the ones loaded by the paper "
                             "from its own directory are read automatically")
    parser.add_argument("--issues", metavar="PATH", default="",
                        help="write the ledger of let-throughs here")
    parser.add_argument("--paper-id", metavar="ID", default="",
                        help="load this paper's name answers from data/name_answers.json")
    parser.add_argument("--canonical", action="store_true",
                        help="the input is normalize.py output: a statement is "
                             "\\begin{statement}{kind}, and display/grid/list "
                             "are canonical too")
    parser.add_argument("--switch", type=switch_option, action="append", default=[],
                        metavar="ifNAME=true|false", help="initial value of a document switch")
    import tex_execution
    parser.add_argument("--tex-engine", choices=tex_execution.ENGINES,
                        help="observe a clean build of the original TeX project before splitting")
    parser.add_argument("--self-test", action="store_true",
                        help="run built-in checks on synthetic snippets")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    slot_answers = {}
    if args.merge_slot_answers:
        with open(args.merge_slot_answers, encoding="utf-8") as fh:
            doc = json.load(fh)
        rows = doc.get("items", []) if isinstance(doc, dict) else doc
        slot_answers = {row["anchor"]: row for row in rows if row.get("id") and row.get("anchor")}
        if not args.tex:
            if slot_answers or os.path.abspath(args.slot_table) == os.path.abspath(SLOT_TABLE_PATH):
                parser.error("use an input paper to apply occurrence answers; legacy import requires an explicit private --slot-table")
            used, rejected = merge_slot_answers(args.merge_slot_answers, args.slot_table)
            return 1 if rejected else 0
        if rows and not slot_answers:
            print("Legacy phrase-only answers lack locations; regenerating slot questions.")
    if not args.tex:
        parser.error("no input file given (or use --self-test)")

    if args.canonical:
        use_canonical_names()

    import normalize
    sources = {}
    if args.tex_engine:
        if args.canonical or args.style or args.switch:
            parser.error("--tex-engine requires original source and the project's own styles and switches")
        tex = tex_execution.observe(args.tex, args.tex_engine,
                                    os.path.join(args.out, 'execution.json'), sources)
    else:
        if not args.canonical or os.path.dirname(os.path.abspath(args.tex)) != os.path.abspath(args.out):
            tex_execution.clear_report(args.out)
        tex = normalize.read_source(args.tex, sources=sources)

    answers = {}
    if args.merge_sentence_answers:
        answers = load_boundary_answers(args.merge_sentence_answers)
    conv = LatexToNL(
        proposition_form=args.proposition_form,
        max_words=args.max_words,
        merge_display=not args.no_merge_display,
        split_qquad=not args.no_split_qquad,
        tok_mode=args.tok_mode,
        sentence_answers=answers,
        style_paths=args.style,
        switches=dict(args.switch),
        base_dir=os.path.dirname(os.path.abspath(args.tex)),
        slot_roles=load_slot_roles(args.slot_table), slot_answers=slot_answers,
        paper=args.paper_id or "",
        reviewed=manual_review.decisions(args.out, args.paper_id or hashlib.sha256(tex.encode()).hexdigest()),
    )
    conv.sources = sources
    conv.canonical = bool(args.canonical)
    use_paper_id(args.paper_id)
    _PAPER_ANSWERED.update(key for kind, key in conv.reviewed if kind == "names")
    result = conv.convert(tex)
    result["paper_id"] = conv.paper
    import formula_review
    evidence, numbers = formula_review.evidence(args.out, conv.paper) if args.canonical else ({}, {})
    formula_review.attach(result["formulas"], evidence, numbers, conv.reviewed)
    result["structure_schema"] = 1
    for block in result["content_blocks"]:
        if (block.get("kind") == "float" and block.get("status") == "pending"
                and ("content", manual_review.block_key(block)) in conv.reviewed):
            block.update(status="retained", **manual_review.provenance(conv.reviewed[("content", manual_review.block_key(block))]))
    paths = write_outputs(result, args.out, write_tokens=not args.no_tok)
    # after the conversion: writing here used to produce an empty ledger,
    # because nothing had been recorded yet
    if getattr(args, "issues", ""):
        _issues.write(args.issues)
        print("issues written: %s" % args.issues)

    print("sentences : %d" % len(result["sentences"]))
    print("formulas  : %d" % len(result["formulas"]))
    print("unresolved: %d" % len(result["unresolved"]))
    if answers:
        print("boundaries joined from answers: %d" % result["boundaries_joined"])
        for sig in result["answers_unused"]:
            print("  unused answer: %r" % sig)
    if result.get("styles"):
        print("style files read: " + ", ".join(result["styles"]))
    if result.get("unwrapped_envs"):
        print("environments unwrapped: " + ", ".join(
            "%s x%d" % kv for kv in sorted(result["unwrapped_envs"].items(),
                                           key=lambda kv: -kv[1])))
    todo_path = args.sentence_todo or os.path.join(args.out,
                                                   "sentence_todo.json")
    pending = [b for b in result["sentence_boundaries"] if not b.get("answer")]
    if pending:
        write_boundary_todo(result["sentence_boundaries"], todo_path)
        # I2, against what is on disk: the file must hold every break in memory,
        # or "no questions" would be indistinguishable from a lost write
        with open(todo_path, encoding="utf-8") as fh:
            disk = {i["sig"] for i in json.load(fh)["items"]}
        assert disk == {b["sig"] for b in result["sentence_boundaries"]}, (
            len(disk), len(result["sentence_boundaries"]))
        assert {b["sig"] for b in pending} <= disk
        print("sentence breaks to judge: %d -> %s" % (len(pending), todo_path))
    elif os.path.exists(todo_path):
        os.remove(todo_path)
    slot_path = args.slot_todo or os.path.join(args.out, "slot_todo.json")
    review_path = os.path.join(args.out, "slot_questions.json")
    if result["slot_questions"]:
        write_slot_todo(result["slot_questions"], review_path)
    elif os.path.exists(review_path):
        os.remove(review_path)
    if result["slots_pending"]:
        write_slot_todo(result["slots_pending"], slot_path)
        with open(slot_path, encoding="utf-8") as fh:
            disk = {i["id"] for i in json.load(fh)["items"]}
        assert disk == {i["id"] for i in result["slots_pending"]}, (
            len(disk), len(result["slots_pending"]))
        print("text slots to judge: %d phrases (%d uses) -> %s"
              % (len(result["slots_pending"]),
                 sum(i["count"] for i in result["slots_pending"]), slot_path))
    elif os.path.exists(slot_path):
        os.remove(slot_path)
    names = result.get("operator_names") or {}
    if names:
        # a judgement the script made on its own: show it, so a wrong one
        # (an English word taken for a name) is seen at a glance
        print("upright names read as operators: " + ", ".join(
            "%s x%d" % kv for kv in sorted(names.items(),
                                           key=lambda kv: (-kv[1], kv[0]))))
    for label, path in paths.items():
        print("  %-10s %s" % (label, path))

    if args.limit:
        print()
        for s in result["sentences"][: args.limit]:
            print("%-6s %s" % (s["sid"], s["text"]))
            print("       %s" % " ".join(s["tokens"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
