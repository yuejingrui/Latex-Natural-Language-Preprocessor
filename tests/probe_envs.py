#!/usr/bin/env python3
"""Acceptance probes for the environment rules in RULES.md."""
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
    L.use_canonical_names()
    norm = N.Normalizer().normalize(tex)
    conv = L.LatexToNL()
    conv.canonical = True
    return norm, conv.convert(norm)


# ---- 1. an inline math environment is a formula, not three sentences ----
norm, doc = run(r"Let \begin{math}{\mathcal M}\end{math} be a moduli space.")
texts = [s["text"] for s in doc["sentences"]]
forms = [f["expanded"] for f in doc["formulas"]]
check("1. inline math: one sentence", len(texts) == 1, str(texts)[:140])
check("2. inline math: one formula", forms == [r"\mathcal M"], str(forms))

# ---- 2. a display environment with a column spec -----------------------
norm, doc = run(r"We have \begin{IEEEeqnarray*}{rCl} a &=& b+c \end{IEEEeqnarray*} for all $x$.")
forms = [f["expanded"] for f in doc["formulas"]]
shown = " ".join(s["text"] for s in doc["sentences"])
check("3. IEEEeqnarray* is a display", len(forms) == 2, str(forms)[:140])
check("4. the column spec is not prose", "{rCl}" not in shown, shown[:120])

# ---- 3. \begin{section}{T} becomes a heading ---------------------------
norm, doc = run(r"\begin{section}{Stability of the Kostant section} Body text here."
                r"\end{section}")
texts = [s["text"] for s in doc["sentences"]]
check("5. the title is not in the sentence", "Stability" not in " ".join(texts),
      str(texts)[:140])
check("6. the section field carries it",
      any(s.get("section") == "Stability of the Kostant section" for s in doc["sentences"]),
      str([s.get("section") for s in doc["sentences"]]))

# ---- 4. a paper-declared proof wrapper --------------------------------
PROOF = r"""\newenvironment{pfof}[1]{\par\noindent\textbf{Proof of #1.}}{\qed}
\begin{pfof}{Theorem 3} The proof is short. \end{pfof}
Afterwards."""
norm, doc = run(PROOF)
texts = [s["text"] for s in doc["sentences"]]
kinds = [s.get("env") for s in doc["sentences"]]
check("7. it becomes a proof statement", "proof" in kinds, str(kinds))
check("8. the title keeps its words without braces",
      any("Theorem 3" in t for t in texts) and not any("{" in t for t in texts),
      str(texts)[:150])

# ---- 5. a declaration keeps no text behind ----------------------------
norm, doc = run(r"""\newtheorem{Def}[theorem]{Definition}
\begin{Def} A definition. \end{Def}""")
texts = [s["text"] for s in doc["sentences"]]
check("9. the theorem declaration leaves no title in the text",
      not any("Definition}" in t for t in texts), str(texts)[:120])
check("10. the block is a definition unit",
      any(s.get("env") == "definition" for s in doc["sentences"]),
      str([s.get("env") for s in doc["sentences"]]))

# ---- 6. a paralist environment is a list ------------------------------
norm, doc = run(r"""\begin{compactitem}
\item one
\end{compactitem}""")
check("11. compactitem becomes a list", "begin{list}" in norm, repr(norm)[:90])
check("12. its item is an item, not a sentence",
      any(s.get("env") == "item" for s in doc["sentences"]),
      str([s.get("env") for s in doc["sentences"]]))

# ---- 7. a proof wrapper without \qed in the end code ------------------
norm, doc = run(r"""\newenvironment{prooff}{\par\noindent{\bf Proof} }{\hfill$\square$}
\begin{prooff} It follows. \end{prooff}""")
check("13. the proof wrapper is recognised without \\qed",
      any(s.get("env") == "proof" for s in doc["sentences"]),
      str([s.get("env") for s in doc["sentences"]]))

# F6 (MERGE-PLAN 3): a declaration that shares a counter inherits its kind
norm, doc = run("\\documentclass{article}\n"
                "\\newtheorem{lemma}{Lemma}\n"
                "\\newtheorem{taller}[lemma]{$\\!\\!$}\n"
                "\\begin{document}\n"
                "\\begin{taller}Let MATH1 be a set.\\end{taller}\n"
                "\\end{document}\n")
envs = [s.get("env") for s in doc["sentences"]]
check("an empty display name inherits its counter's kind",
      envs and all(e == "lemma" for e in envs), str(envs))
named = N.read_theorem_decls(r"\newtheorem{lem}{Lemma}\newtheorem{ax}[lem]{Axiom}")
check("a shared counter preserves the explicit statement type",
      named == {"lem": "lemma", "ax": "axiom"}, str(named))

bad = [x for x in RESULTS if not x[1]]
print("\n%d green, %d red" % (len(RESULTS) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
