#!/usr/bin/env python3
"""Splitter regressions for malformed input, retained content and notation."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import latex2nl as L
import normalize as N

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, ok))
    print("  %-58s %s%s" % (label, "green" if ok else "RED",
                            ("  " + detail) if not ok else ""))


def run(tex, paper="p"):
    norm = N.Normalizer(paper=paper).normalize(tex)
    L.use_canonical_names()
    conv = L.LatexToNL()
    conv.canonical = True
    return norm, conv.convert(norm)


# 20: an unclosed unit env must not duplicate the text before it
norm, doc = run("\\begin{document}\nFirst. Second.\n\\begin{lemma}\nUnclosed\n\\end{document}\n")
texts = [s["text"] for s in doc["sentences"]]
check("20 an unclosed env does not duplicate the lead",
      len(texts) == len(set(texts)), str(texts))

# 21: an unclosed $$ must not abandon the rest of the scan
norm, doc = run("\\begin{document}\nWe have $$x=1 and $y$ here.\n\\end{document}\n")
check("21 an unclosed $$ still yields a formula", len(doc["formulas"]) >= 1,
      str(len(doc["formulas"])))

# 22: an unclosed display env must not become prose
norm, doc = run("\\begin{document}\n\\begin{equation}\\mathfrak{F}=1\n\\end{document}\n")
check("22 an unclosed display is not prose",
      not any("\\mathfrak" in s["text"] for s in doc["sentences"]),
      str([s["text"] for s in doc["sentences"]]))

# 23: definitions are meta, not text
norm, doc = run("\\begin{document}\n\\def\\foo{bar} Text.\n\\end{document}\n")
check("23 a \\def does not leak into the text", "\\def" not in norm, repr(norm))

# 24: adjacent placeholders must not merge
norm, doc = run("\\begin{document}\nSee \\ref{a}\\ref{b} now.\n\\end{document}\n")
text = doc["sentences"][0]["text"] if doc["sentences"] else ""
check("24 two refs side by side stay separate", "REF1REF2" not in text, text)

# 26: initials must not swallow a sentence boundary
norm, doc = run("\\begin{document}\nWe denote the set by A. This set is open.\n\\end{document}\n")
check("26 a single initial keeps the sentence boundary",
      len(doc["sentences"]) == 2, str([s["text"] for s in doc["sentences"]]))

# 28: a nested diagram leaves no control characters
norm, doc = run("\\begin{document}\n\\begin{tikzpicture}\\xymatrix{A \\ar[r] & B}"
                "\\end{tikzpicture}\nAfter.\n\\end{document}\n")
check("28 no control characters reach a sentence",
      not any("\x02" in s["text"] for s in doc["sentences"]),
      str([s["text"] for s in doc["sentences"]]))

# 33: an ellipsis makes a sequence
norm, doc = run("\\begin{document}\nWe have $a_1 \\ldots a_n$ here.\n\\end{document}\n")
check("33 an ellipsis makes a sequence",
      "sequence" in [f["role"] for f in doc["formulas"]],
      str([f["role"] for f in doc["formulas"]]))

# 36: a tag is kept as the formula's label
norm, doc = run("\\begin{document}\n\\begin{equation}\\tag{3}x=1\\end{equation}\n\\end{document}\n")
check("36 a \\tag becomes the formula label",
      any((f.get("label") or "") == "3" for f in doc["formulas"]),
      str([(f.get("label"), f["expanded"]) for f in doc["formulas"]]))

# 27: an unclosed drawing does not leak its commands, and the text after the
# paragraph survives
norm, doc = run("\\begin{document}\nBefore.\\begin{tikzpicture}[x=1]\n"
                "\\draw (0,0) -- (1,1);\n\nAfter.\n\\end{document}\n")
texts = [s["text"] for s in doc["sentences"]]
check("27 an unclosed drawing leaves no commands", "Before." in texts and "After." in texts
      and not any("\\draw" in t or "x=1" in t for t in texts), str(texts))

# 34: \ifcase keeps the branch its number selects
for n, want in ((0, "Alpha"), (2, "Gamma")):
    norm, doc = run("\\begin{document}\n\\ifcase" + str(n)
                    + " Alpha\\or Beta\\or Gamma\\fi\n\\end{document}\n")
    texts = [s["text"] for s in doc["sentences"]]
    check("34 \\ifcase%d keeps %s" % (n, want), texts == [want], str(texts))

# 35: \ifodd skips its operand
norm, doc = run("\\begin{document}\n\\ifodd\\count0 A\\else B\\fi\n\\end{document}\n")
check("35 \\ifodd leaves no operand", "\\count" not in norm, repr(norm))

# a \let with a character target leaves nothing
norm, doc = run("\\begin{document}\n\\let\\a=a The text.\n\\end{document}\n")
check("a \\let\\a=a leaves no stray character", norm.strip() == "The text.", repr(norm))

# a display ending in ":" keeps the mark out of the body
norm, doc = run("\\begin{document}\nWe have\n\\[ x=1: \]\nafter.\n\\end{document}\n")
check("a display ending in ':' keeps the mark",
      any((f.get("trailing_punct") or "") == ":" for f in doc["formulas"]),
      str([(f.get("trailing_punct"), f["expanded"]) for f in doc["formulas"]]))

# a run-in \paragraph is text, not a section
norm, doc = run("\\begin{document}\n\\paragraph{Run-in.} Text follows.\n\\end{document}\n")
check("a run-in \\paragraph is not a section",
      not any((s.get("section") or "").find("Run-in") >= 0 for s in doc["sentences"]),
      str([(s.get("section"), s["text"]) for s in doc["sentences"]]))

# punctuation is not a word for the Spring length flag
long_tex = " ".join(["word"] * 85) + " , . ! ?"
norm, doc = run("\\begin{document}\n" + long_tex + "\n\\end{document}\n")
flags = doc["sentences"][0]["flags"] if doc["sentences"] else []
check("punctuation does not trip the length flag", "too_long_for_spring" not in flags,
      str(flags))

# a space before the closing brace is legal TeX ("\\end {equation}")
norm, doc = run("\\begin{document}\n\\begin {equation}x=1\\end {equation} After text."
                "\n\\end{document}\n")
check("a spaced \\end {equation} closes the display",
      norm.count("\\begin{display}") == 1
      and any("After text" in s["text"] for s in doc["sentences"]),
      repr(norm))

# a lone backslash at the end of the text used to crash the font pass
norm, doc = run("\\begin{document}\nText with a trailing backslash \\\n\\end{document}\n")
check("a lone backslash does not crash", "Text with a trailing" in norm, repr(norm[-40:]))

# OPEN-PROBLEMS 7.1: a macro whose expansion reaches an unknown name is kept
pre = ("\\documentclass{article}\n\\def\\tsadi#1{\\pretsadi{#1}}\n"
       "\\newcommand{\\R}{\\mathbb R}\n"
       "\\renewcommand{\\lesssim}{\\mbox{\\mathfonta\\&}}\n"
       "\\begin{document}\n")
norm, doc = run(pre + "We have \\tsadi{x} and \\R and $a\\lesssim b$ here."
                "\n\\end{document}\n")
check("a macro with an unknown expansion is kept",
      any(b["raw"] == r"\tsadi{x}" for b in doc["content_blocks"]) and "\\mathbb R" in norm and "$a\\lesssim b$" in norm,
      repr(norm))

_n = N.Normalizer(paper="p")
_n.normalize(pre + "We have \\tsadi{x} here.\n\\end{document}\n")
check("the kept macro is recorded with its body",
      "tsadi" in _n.kept_macros and "pretsadi" in _n.kept_macros["tsadi"],
      repr(_n.kept_macros))

_n2 = N.Normalizer(paper="p")
_n2.normalize("\\documentclass{article}\n\\newcommand{\\R}{\\mathbb R}\n"
              "\\begin{document}\nWe have \\R here.\n\\end{document}\n")
check("a known macro is still expanded", _n2.kept_macros == {}, repr(_n2.kept_macros))

# Declarations: the declaration forms the macro table could not read
for label, pre, body, want in (
        ("paired delimiter", "\\DeclarePairedDelimiter\\norm{\\lVert}{\\rVert}\n",
         "We have $\\norm{x}$ here.", "x"),
        ("math symbol", '\\DeclareMathSymbol{\\R}{\\mathbin}{AMSb}{"52}\n',
         "We have $\\R^2$ here.", "\\mathbb{R}"),
        ("control symbol", "\\def\\7#1{\\mathbf{#1}}\n",
         "We have $\\7 x$ here.", "\\mathbf{x}"),
        ("delimited params", "\\def\\pair(#1,#2){\\langle #1,#2\\rangle}\n",
         "We have $\\pair(a,b)$ here.", "\\langle a,b\\rangle")):
    norm, doc = run("\\documentclass{article}\n" + pre + "\\begin{document}\n"
                    + body + "\n\\end{document}\n")
    exps = [f.get("expanded") or "" for f in doc["formulas"]]
    check("the macro table reads a %s" % label,
          any(want in e for e in exps), str(exps))

# Text boundaries: colour and kept macros must not glue words into the sentence
norm, doc = run("\\begin{document}\n\\textcolor{blue}{Let it be.}\n\\end{document}\n")
check("textcolor keeps only its content",
      [x["text"] for x in doc["sentences"]] == ["Let it be."],
      str([x["text"] for x in doc["sentences"]]))
norm, doc = run("\\documentclass{article}\n\\newcommand{\\rev}[1]{{\\color{blue}#1}}\n"
                "\\begin{document}\n\\rev{Let it be.}\n\\end{document}\n")
check("a macro that only colours expands",
      [x["text"] for x in doc["sentences"]] == ["Let it be."],
      str([x["text"] for x in doc["sentences"]]))
norm, doc = run("\\documentclass{article}\n\\def\\tsadi#1{\\pretsadi{#1}}\n"
                "\\begin{document}\nWe have \\tsadi{x} here.\\end{document}\n")
check("a kept macro keeps a space before its argument",
      "\\tsadix" not in " ".join(x["text"] for x in doc["sentences"]),
      str([x["text"] for x in doc["sentences"]]))

# GAPS M2: a manual override can correct what TeX reported
with tempfile.TemporaryDirectory() as tmp:
    overrides = Path(tmp) / "name_overrides.json"
    overrides.write_text(json.dumps({"names": {"frenchspacing": {"action": "ignore"}}}), encoding="utf8")
    with patch.object(N, "OVERRIDES_PATH", str(overrides)):
        table = N.NameTable()
        check("the override table beats a TeX decision",
              "frenchspacing" in table.ignore and table.origin.get("frenchspacing") == "override",
              table.origin.get("frenchspacing"))

# Expansion: a nested macro call must expand once, not twice
_m = L.MacroTable()
_m.parse("\\newcommand{\\io}[2]{\\iota(#1,#2)}")
_exp = _m.expand("$\\io{\\pi}{\\io{\\eta}{y}}$")
check("a nested macro invocation expands once",
      _exp == "$\\iota(\\pi,\\iota(\\eta,y))$", repr(_exp))

bad = [x for x in RESULTS if not x[1]]
print("\n%d green, %d red" % (len(RESULTS) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
