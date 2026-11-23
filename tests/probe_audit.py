#!/usr/bin/env python3
"""Probes for the core defects that AUDIT.md section 2 used to list.

Each one is the reproduction from that table; the ones still open (27, 34) are not
asserted here, they stay listed in AUDIT.md.
"""
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

import importlib.util

SKIPPED = []

def skip(reason):
    SKIPPED.append(reason)
    print("  SKIP " + reason)

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

import normalize as _N
_n = _N.Normalizer(paper="p")
_n.normalize(pre + "We have \\tsadi{x} here.\n\\end{document}\n")
check("the kept macro is recorded with its body",
      "tsadi" in _n.kept_macros and "pretsadi" in _n.kept_macros["tsadi"],
      repr(_n.kept_macros))

_n2 = _N.Normalizer(paper="p")
_n2.normalize("\\documentclass{article}\n\\newcommand{\\R}{\\mathbb R}\n"
              "\\begin{document}\nWe have \\R here.\n\\end{document}\n")
check("a known macro is still expanded", _n2.kept_macros == {}, repr(_n2.kept_macros))

# REVIEW.md 二.6: _find only guards against a preceding digit
if importlib.util.find_spec("roles") is not None:
    import roles as _R
    check("_find finds a placeholder glued to letters",
          _R._find("AFMATH207AFMATH208", "MATH207") is not None)
    check("_find rejects a longer id", _R._find("MATH2071", "MATH207") is None)
    _words = _R.preceding_words("in a CYMATH1733 triangulated", "MATH1733")
    check("preceding_words splits a glued placeholder",
          _words[-3:] == ["in", "a", "cy"], repr(_words))

else:
    skip('roles module is outside the splitting package (3 checks)')

# REVIEW.md 三.1: fill_failures must not rewrite a paper that has its sources
import os
import json as _json
import shutil as _shutil
import subprocess as _sp
import tempfile as _tf

if os.path.isfile("/home/roger/gted/scripts/fill_failures.py"):
    _base = _tf.mkdtemp(prefix="fillfail.")
    _work = os.path.join(_base, "work")
    _out = os.path.join(_base, "newarticle")
    os.makedirs(_work)
    os.makedirs(os.path.join(_out, "good"))
    open(os.path.join(_out, "good", "paper.tex"), "w").write("x\n")
    open(os.path.join(_out, "good", "meta.json"), "w").write('{"has_tex": true}\n')
    _shutil.copy("/home/roger/gted/scripts/fill_failures.py",
                 os.path.join(_work, "fill_failures.py"))
    _rows = [{"arxiv_id": a, "doi": "", "title": a, "authors": "", "date": "",
              "journal": "", "volume": "", "issue": "", "first_page": "", "last_page": ""}
             for a in ("good", "gone404", "pipe")]
    open(os.path.join(_work, "works.jsonl"), "w").write(
        "".join(_json.dumps(r) + "\n" for r in _rows))
    open(os.path.join(_work, "failures.txt"), "w").write(
        "good\t[Errno 32] Broken pipe\ngone404\thttp 404\npipe\t[Errno 32] Broken pipe\n")
    _env = dict(os.environ, GTED_NEWARTICLE=_out,
                PYTHONPATH="/home/roger/gted/scripts")
    _r = _sp.run([sys.executable, os.path.join(_work, "fill_failures.py")],
                 capture_output=True, text=True, env=_env)
    _meta_good = open(os.path.join(_out, "good", "meta.json")).read().strip()
    _meta_404 = _json.load(open(os.path.join(_out, "gone404", "meta.json")))
    _meta_pipe = _json.load(open(os.path.join(_out, "pipe", "meta.json")))
    check("fill_failures leaves a downloaded paper alone",
          _meta_good == '{"has_tex": true}' and "skipped 1" in _r.stdout, _r.stdout.strip()[:80])
    check("fill_failures writes a real 404 as 404",
          "404" in _meta_404.get("status", "") and not _meta_404.get("retry"), str(_meta_404.get("status")))
    check("fill_failures marks a network failure retryable",
          _meta_pipe.get("retry") is True and _meta_pipe.get("status", "").startswith("download failed"),
          str(_meta_pipe.get("status")))

else:
    skip('fill_failures upstream integration is unavailable (3 checks)')

# OPEN.md 1.1: a name the paper defines is asked, not dropped
import tempfile as _tfa

if importlib.util.find_spec("pending_names") is not None:
    _d = _tfa.mkdtemp(prefix="names.")
    os.makedirs(os.path.join(_d, "p"))
    open(os.path.join(_d, "p", "main.tex"), "w").write(
        "\\documentclass{article}\n\\def\\R#1.#2{a#1b#2}\n"
        "\\begin{document}\nWe have $\\R x.y$ and text.\n\\end{document}\n")
    import pending_names as _PN
    _PN.CORPUS, _PN.STEP = _d, 1
    _pid, _rec = _PN.one(("p", "main.tex"))
    check("a defined-but-unparsed name is a question",
          "R" in _rec and _rec["R"].get("reason") == "definition_not_parsed",
          str({k: v.get("reason") for k, v in _rec.items() if isinstance(v, dict)}))
    check("the question carries its definition",
          "def" in (_rec.get("R", {}).get("definition") or ""),
          (_rec.get("R", {}).get("definition") or "")[:60])

else:
    skip('pending_names module is outside the splitting package (2 checks)')

# OPEN.md 1.2: the declaration forms the macro table could not read
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

# OPEN.md 1.0: colour and kept macros must not glue words into the sentence
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
import json as _j2
_ov = os.path.join(os.path.dirname(SCRIPTS), "data", "name_overrides.json")
with open(_ov, 'rb') as _fh:
    _before_bytes = _fh.read()
_before = _before_bytes.decode('utf-8')
try:
    _d = _j2.loads(_before)
    _d["names"]["frenchspacing"] = {"action": "ignore"}
    open(_ov, "w", encoding="utf-8").write(_j2.dumps(_d, indent=2))
    _nt = _N.NameTable(paper="")
    check("the override table beats a TeX decision",
          "frenchspacing" in _nt.ignore and _nt.origin.get("frenchspacing") == "override",
          _nt.origin.get("frenchspacing"))
finally:
    with open(_ov, 'wb') as _fh:
        _fh.write(_before_bytes)

# OPEN.md 1.8: a nested macro call must expand once, not twice
_m = L.MacroTable()
_m.parse("\\newcommand{\\io}[2]{\\iota(#1,#2)}")
_exp = _m.expand("$\\io{\\pi}{\\io{\\eta}{y}}$")
check("a nested macro invocation expands once",
      _exp == "$\\iota(\\pi,\\iota(\\eta,y))$", repr(_exp))

bad = [x for x in RESULTS if not x[1]]
print("\n%d green, %d red, %d unavailable groups" % (len(RESULTS) - len(bad), len(bad), len(SKIPPED)))
sys.exit(1 if bad else 0)
