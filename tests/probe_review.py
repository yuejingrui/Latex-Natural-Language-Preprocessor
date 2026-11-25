#!/usr/bin/env python3
"""The five boundary cases the 2005.01702 review found (RULES.md)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
if not os.path.isdir(SCRIPTS):
    SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "nlprep", "scripts")
sys.path.insert(0, SCRIPTS)
import latex2nl as L  # noqa: E402
import normalize as N  # noqa: E402

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, ok))
    print("%-58s %s%s" % (label, "green" if ok else "RED",
                          "" if ok else "  " + str(detail)))


def run(body, pre=""):
    tex = "\\documentclass{article}\n" + pre + "\\begin{document}\n" + body + "\n\\end{document}\n"
    n = N.Normalizer(paper="p")
    norm = n.normalize(tex)
    L.use_canonical_names()
    c = L.LatexToNL()
    c.canonical = True
    return norm, c.convert(norm)


def texts(doc):
    return [s["text"] for s in doc["sentences"]]


# 1-2: a full stop inside the parentheses ends the sentence
norm, doc = run("(In particular, only MATH1.) Then MATH2 and MATH3.")
check("a full stop before ) ends the sentence", len(texts(doc)) == 2,
      texts(doc))
norm, doc = run("(One can deduce it.) This completes the proof.")
check(".) before This completes splits", len(texts(doc)) == 2, texts(doc))

# 3-5: list labels
norm, doc = run("\\begin{enumerate}[(i)]\n\\item $\\mathbf v=\\sum_\\ell \\mathbf v^{(\\ell)}$,\n"
                "\\item for each MATH1 there is at most one.\n\\end{enumerate}")
sent = texts(doc)
check("a formula-only item is kept", any("(i)" in s for s in sent), sent)
check("the label keeps no brackets", not any("[" in s for s in sent), sent)
norm, doc = run("\\begin{enumerate}\n\\item First point.\n\\item Second point.\n\\end{enumerate}")
check("a numbered label does not split off",
      not any(s.strip() in ("1.", "2.") for s in texts(doc)), texts(doc))

# 6: a colon before a display joins it
norm, doc = run("There is a grading on the ring:\n\\[ H=\\bigoplus H_\\varkappa \\]\n"
                "and we use it.")
check("a display after a colon joins its lead-in",
      any(s.endswith("MATH1") or "ring: MATH" in s for s in texts(doc)), texts(doc))

# 7-8: a placeholder next to a word gets a space
norm, doc = run("The map is flat\\cite{stacks} and flat\\ref{lem}.")
sent = " ".join(texts(doc))
check("a cite keeps a space before REF", "flat REF" in sent, sent)
check("a ref keeps a space before REF", sent.count("flat REF") >= 2, sent)

# 9-10: a float exposes its caption and preserves its full body as pending
norm, doc = run("\\begin{figure}\n\\includegraphics{x.png}\n"
                "\\caption{The quiver $Q$ is connected.}\n\\end{figure}\n"
                "Text follows.")
check("a figure caption is kept", any("quiver" in s for s in texts(doc)), texts(doc))
check("the figure body is preserved as pending",
      any(b['status'] == 'pending' and 'includegraphics' in b['raw']
          for b in doc['content_blocks']), doc['content_blocks'])

# F2 (MERGE-PLAN 3): a group that spanned two units leaves one brace half
norm, doc = run("\\begin{definition}\\rm{Let $x$ be a set.\n"
                "\\begin{itemize}\\item a\\end{itemize} Then $x$ is fine.}"
                "\\end{definition}\n")
check("no sentence keeps a stray brace",
      not any(("{" in s["text"] or "}" in s["text"]) for s in doc["sentences"]),
      texts(doc))

# F2 root cause: the group after a font switch belongs to the switch
for body, want in (
        ("\\begin{definition} \\rm{Let $x$ be a set.\\begin{itemize}\\item a\\end{itemize}"
         " Then $x$ is fine.}\\end{definition}\n", "Let $x$ be a set."),
        ("Text \\bf{bold words} more.\n", "Text bold words more."),
        ("Text \\bf {two words} more.\n", "Text two words more.")):
    norm, doc = run(body)
    body_norm = norm.split("\\begin{document}")[-1]
    check("a font switch takes the group it is applied to (%r)" % want,
          want in body_norm and not any(
              "{" in s["text"] or "}" in s["text"] for s in doc["sentences"]),
          body_norm.strip()[:120])

# F3 (MERGE-PLAN 3): a note body goes through the same name pass
norm, doc = run("Text.\\footnotetext{\\emph{Date:} \\today} More text.\n")
sent = " ".join(texts(doc))
date = next((b for b in doc["content_blocks"] if b["raw"] == r"\today"), {})
check("a note keeps the build-dependent date anchored",
      date.get("status") == "pending" and date.get("anchor_ref") in sent
      and "Date:" in sent and r"\emph" not in sent, sent)
norm, doc = run(r"Body.\footnote{Valid until \today.}", r"\renewcommand{\today}{Monday}")
check("a redefined date uses the author's text", "Valid until Monday." in texts(doc), texts(doc))

# F1 (MERGE-PLAN 3): a placeholder with an affix still belongs to the sentence
for body, want in (("We write $n$th.\n", "MATH1th"),
                   ("These are $X$-Segal.\n", "MATH1-Segal"),
                   ("The map $f$push exists.\n", "MATH1push")):
    norm, doc = run(body)
    sent = doc["sentences"]
    ok = bool(sent) and sent[0]["formulas"] == ["MATH1"] and want in sent[0]["text"]
    check("affix %r is registered" % want, ok,
          str([(x["text"], x["formulas"]) for x in sent]))

# a placeholder right after a letter is still one id (not a longer number)
norm, doc = run("We write CYMATH1729 here.\n")
check("a placeholder after a letter is still found",
      any(s["formulas"] for s in doc["sentences"]),
      str([(s["text"], s["formulas"]) for s in doc["sentences"]]))

# A standard thorn prints even before a label; a paper may redefine it as empty.
norm, doc = run("\\begin{proposition}\\th\\label{a} Let MATH1 be a set."
                "\\end{proposition}\nAs in \\th\\ref{a}, MATH2 holds.\n")
sent = " ".join(texts(doc))
check("thorn before a reference or label remains visible", sent.count("þ") == 2, sent)
_, doc = run(r"A\th\label{x} B.", r"\renewcommand{\th}{}")
check("an explicitly empty authoring marker stays empty", "þ" not in " ".join(texts(doc)), texts(doc))
_, doc = run(r"Body $q$.\footnote{Compare $\vb{q}$ and $q$.}")
note = [f for f in doc["formulas"] if f["kind"] == "inline"]
check("a note keeps the vector notation distinct from the scalar",
      [f["raw"] for f in note] == ["q", r"\vb{q}", "q"]
      and [f["expanded"] for f in note] == ["q", r"\vb{q}", "q"], note)

# C2: a bare \includegraphics outside a float is a picture, not prose
norm, doc = run("The quiver is given by\n\\begin{center}\n"
                "  \\includegraphics[width=0.33\\textwidth]{figures/Delta13}\n"
                "\\end{center}\nThe elements are highlighted.\n")
sent = " ".join(texts(doc))
check("a bare graphic never reaches a sentence",
      "includegraphics" not in sent and "figures/" not in sent, sent)
check("the bare graphic is preserved as pending",
      any(b["kind"] == "graphic" and "figures/Delta13" in b["raw"]
          and b["status"] == "pending" for b in doc["content_blocks"]),
      doc["content_blocks"])

# C2: an accent on a dotless letter keeps its base
for body, want in ((r"Be\u{\i}linson proved it.\n", "Be\u012dlinson"),
                   (r"Groth and \v{S}\v{t}ov\'\i\v{c}ek showed it.\n", "\u0160\u0165ov\u00ed\u010dek")):
    norm, doc = run(body)
    sent = " ".join(texts(doc))
    check("an accent keeps its base %r" % want, want in sent and "\\i" not in sent, sent)

bad = [x for x in RESULTS if not x[1]]
print("\n%d green, %d red" % (len(RESULTS) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
