#!/usr/bin/env python3
"""Probes for SPLITTING.md: no bare brace groups reach a sentence."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# the same file runs inside the repository (nlprep/tests) and inside the
# standalone splitting package (tests/ + scripts/)
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
if not os.path.isdir(SCRIPTS):
    SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                           "nlprep", "scripts")
sys.path.insert(0, SCRIPTS)
import latex2nl as L  # noqa: E402
import normalize as N  # noqa: E402

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, ok))
    print("  %-58s %s%s" % (label, "green" if ok else "RED",
                            ("  " + detail) if not ok else ""))


def run(tex):
    norm = N.Normalizer(paper="p").normalize(tex)
    L.use_canonical_names()
    conv = L.LatexToNL()
    conv.canonical = True
    return norm, conv.convert(norm)


def sentences(tex):
    return [s["text"] for s in run(tex)[1]["sentences"]]


PRE = "\\documentclass{article}\n\\begin{document}\n"
POST = "\n\\end{document}\n"

# 1. selectlanguage takes its argument
print("BRA" + "CE") if False else None
texts = sentences(PRE + "\\selectlanguage{english} The text follows." + POST)
check("1. selectlanguage leaves nothing behind",
      not any("{english}" in t for t in texts), str(texts))
check("2. and the sentence survives", any("The text follows" in t for t in texts),
      str(texts))

# 2. a redefined section command keeps its title in the section field
pre = ("\\documentclass{article}\n\\makeatletter\n"
       "\\def\\subsection{\\@startsection{subsection}{2}%\n"
       "  \\z@{.5\\linespacing\\@plus.7\\linespacing}{.5\\linespacing}%\n"
       "  {\\normalfont\\bfseries}}\n\\makeatother\n\\begin{document}\n")
norm, doc = run(pre + "\\subsection*{Background and motivation}\nThe body text.\n\\end{document}\n")
secs = [s.get("section") for s in doc["sentences"]]
check("3. a redefined \\subsection puts the title in section",
      secs == ["Background and motivation"], str(secs))
check("4. and the body has no layout soup",
      [s["text"] for s in doc["sentences"]] == ["The body text."],
      str([s["text"] for s in doc["sentences"]]))

# 3. unitlength's dimension is eaten
texts = sentences(PRE + "{\\unitlength 0.1in $x$} after." + POST)
check("5. \\unitlength takes its dimension", not any("0.1in" in t for t in texts),
      str(texts))

# 4. a text-only group is stripped
texts = sentences(PRE + "Text {is uniformly bounded.} more." + POST)
check("6. a text-only group loses its braces",
      not any("{" in t or "}" in t for t in texts), str(texts))

# 5. ensuremath is math, not text
norm, doc = run(PRE + "the Waldhausen {{\\ensuremath{S_\\bullet}}}-construction." + POST)
texts = [s["text"] for s in doc["sentences"]]
check("7. \\ensuremath becomes a formula, not stray braces",
      not any("{" in t or "}" in t for t in texts)
      and any("MATH" in t for t in texts), str(texts))

# 6. \ensuremath already inside math must not open a second $
norm, doc = run(PRE + "We have $\\ensuremath{\\mathcal W}$ here." + POST)
kinds = [(f["id"], f["kind"]) for f in doc["formulas"]]
check("8. \\ensuremath inside math stays one inline", kinds == [("MATH1", "inline")],
      str(kinds))

# 7. a macro that holds \ensuremath, used inside math
pre2 = ("\\documentclass{article}\n\\def\\WW{\\ensuremath{\\mathcal W}}\n"
        "\\begin{document}\n")
norm, doc = run(pre2 + "We have $\\WW\\subset S$ here.\n\\end{document}\n")
kinds = [(f["id"], f["kind"]) for f in doc["formulas"]]
check("9. a \\ensuremath macro used inside math stays inline",
      kinds == [("MATH1", "inline")], str(kinds))

bad = [x for x in RESULTS if not x[1]]
print("\n%d green, %d red" % (len(RESULTS) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
