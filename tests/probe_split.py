#!/usr/bin/env python3
"""Probes for the font-group rule and the abbreviation table.

The two defects goldbach.tex exposed: `{\\bf Proposition ...}` kept its braces in
the sentence text (408 of 12,160 sentences in the 20-paper sample), and a
capitalised abbreviation ("No. 5") was not in the list, so it split a sentence.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# the same file runs inside the repository (nlprep/tests) and inside the
# standalone splitting package (tests/ + scripts/).  The package comes first:
# a checkout of the package that happens to sit next to the repository must
# test its own scripts, or a probe reports on code it does not ship.
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


def sentences(tex):
    doc = L.LatexToNL().convert(N.Normalizer().normalize(tex))
    return [s["text"] for s in doc["sentences"]]


# ---- the font group loses its braces, keeps its words --------------------
texts = sentences(r"{\bf Proposition $(1+a)$.} Fix $n$ first.")
check("1. {\\bf ...} keeps the words, drops the braces",
      texts and "{" not in texts[0] and "}" not in texts[0]
      and "Proposition" in texts[0], str(texts[:2]))

texts = sentences(r"We have {\it{stationary maps}} here.")
check("2. {\\it{...}} nested group is unwrapped",
      texts and "{" not in texts[0] and "stationary maps" in texts[0],
      str(texts[:1]))

texts = sentences(r"{\em A heading.} Body follows.")
check("3. a group at the start of a sentence is unwrapped",
      texts and not any("{" in t for t in texts), str(texts[:2]))

texts = sentences(r"Noindent. \noindent{\bf Step 1.} We show it.")
check("4. the heading after \\noindent survives without braces",
      any("Step 1" in t for t in texts)
      and not any("{" in t for t in texts), str(texts[:3]))

# ---- abbreviations ------------------------------------------------------
cases = [
    ("See No. 5 for the proof. Then it ends.", 2),
    ("No. We disagree. That is all.", 3),
    ("Vol. 3 of the series. Next.", 2),
    ("Ref. [3] gives it. Next.", 2),
    ("The map is onto, e.g. the case n=1. Next.", 2),
]
for text, want in cases:
    got = len(L.split_sentences(text))
    check("5. %-44s -> %d" % (text[:44], want), got == want, "got %d" % got)

# ---- a footnote body is not a sentence ----------------------------------
texts = sentences(r"Text. \footnotetext{Date: \today} More.")
check("6. \\footnotetext is not a sentence",
      not any("footnotetext" in t for t in texts), str(texts[:3]))

# ---- joining keeps a dot that belongs to the token ----------------------
pairs = [("S.N.", "was supported", "S.N. was supported"),
         ("No.", "5 is odd", "No. 5 is odd"),
         ("e.g.", "the map", "e.g. the map"),
         ("the map.", "we then", "the map we then")]
for left, right, want in pairs:
    got = L.join_boundary(left, right)
    check("7. join %-10s -> %r" % (left, want), got == want, repr(got))

# ---- the maths fonts: R1 (normalize) + R2 (latex2nl) ---------------------
def expanded(tex):
    doc = L.LatexToNL().convert(N.Normalizer().normalize(tex))
    return [f["expanded"] for f in doc["formulas"]]


checks = [
    (r"${\rm WEH}(\theta)$ holds.", "\\mathrm{WEH}", "multi-letter name"),
    (r"${\rm d}x$ holds.", "\\mathrm{d}", "single letter keeps its font"),
    (r"${\bf R^2}$ holds.", "\\mathbf{R}^2", "structure is kept"),
    (r"$\rm f(x)=0$ holds.", "\\mathrm{f}", "unbraced switch"),
    (r"$\mathop{\rm deg}\nolimits f$ holds.", "\\mathop{deg}",
     "operator argument: switch deleted"),
    (r"$v\in{\rm Lip}(\Omega)$ holds.", "\\mathrm{Lip}",
     "a group after a relation is content"),
]
for tex, want, label in checks:
    got = expanded(tex)
    check("8. %-38s -> %s" % (label, want),
          bool(got) and want in got[0], str(got))

texts = sentences(r"\noindent{\bf Step 1.} We show it.")
check("9. a heading after \\noindent stays two sentences",
      len(texts) == 2 and not any("{" in t for t in texts), str(texts))

texts = sentences(r"We call them {\it{stationary maps}} and more.")
check("9b. a nested prose group leaves no code letter",
      texts and not any(ch.isdigit() for ch in texts[0])
      and "c{" not in texts[0], str(texts[:1]))

# ---- a comment-only line is not a paragraph break -----------------------
COMMENT_CASES = [
    ("a comment line joins", "A sentence\n%comment\ncontinues here.", 1),
    ("an inline comment joins", "A sentence % inline comment\ncontinues here.", 1),
    ("a blank line still splits", "A sentence\n\nAnother.", 2),
    ("a comment plus a blank line splits", "A sentence\n%c\n\nAnother.", 2),
]
for label, tex, want in COMMENT_CASES:
    norm = N.Normalizer().normalize("\\begin{document}\n" + tex + "\n\\end{document}\n")
    conv = L.LatexToNL()
    conv.canonical = True
    got = len(conv.convert(norm)["sentences"])
    check(label, got == want, "%d sentences, want %d" % (got, want))

# ---- what follows a display decides the boundary ------------------------
# The lookahead used to stop at the backslash of a \\label or at the opening
# bracket of an aside, and called the boundary unclear: 43 displays over the 21
# papers plus danmark were left undecided, and an undecided boundary keeps a
# paper at exit code 3.
DISPLAY_CASES = [
    ("a block after a display starts a new unit",
     "Text before.\n\\[ a=b \\]\n\\[ c=d \\]\nAfter.",
     ["Text before.", "MATH1", "MATH2", "After."]),
    ("a list after a display starts a new unit",
     "Text before.\n\\[ a=b \\]\n\\begin{enumerate}\\item one\\item two\\end{enumerate}",
     ["Text before.", "MATH1", "1. one", "2. two"]),
    ("a label prints nothing, so the next word decides",
     "Text before.\n\\[ a=b \\]\n\\label{eq:x} holds for every $x$.",
     ["Text before.", "MATH1", "holds for every MATH2."]),
    ("a lower-case aside continues the sentence",
     "Text before.\n\\[ a=b \\]\n(obtained by composing) and more.",
     ["Text before.", "MATH1 (obtained by composing) and more."]),
    ("an enumerated item after a display is new",
     "Text before.\n\\[ a=b \\]\n(b) Let $x$ be a set.",
     ["Text before.", "MATH1", "(b) Let MATH2 be a set."]),
]
L.use_canonical_names()          # the package's own environment names
for label, tex, want in DISPLAY_CASES:
    conv = L.LatexToNL()
    conv.canonical = True
    doc = conv.convert(N.Normalizer().normalize(
        "\\documentclass{article}\n\\begin{document}\n" + tex
        + "\n\\end{document}\n"))
    got = [x["text"] for x in doc["sentences"]]
    unclear = [f["id"] for f in doc["formulas"]
               if "sentence_end_unclear" in (f.get("flags") or [])]
    check(label, got == want and not unclear, "%r unclear=%s" % (got, unclear))

bad = [x for x in RESULTS if not x[1]]
print("\n%d green, %d red" % (len(RESULTS) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
