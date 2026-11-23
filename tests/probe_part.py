#!/usr/bin/env python3
"""Probes for SPLITTING.md plan A: a declared class used bare is a part."""
import json
import subprocess
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
    norm = N.Normalizer(paper="p").normalize(tex)
    conv = L.LatexToNL()
    conv.canonical = True
    return norm, conv.convert(norm)


DOC = "\\documentclass{article}\n\\newtheorem{step}{Step}\n\\begin{document}\n"
tex = (DOC + "\\begin{proof}\n\\step First, we show this. Then more.\n"
       "\\step Next, the other case.\n\\end{proof}\n\\end{document}\n")
norm, doc = run(tex)
sents = doc["sentences"]
parts = [(s["part"]["index"], s["text"][:20]) for s in sents if s.get("part")]
check("1. two steps give parts 1 and 2", [p[0] for p in parts][:1] == [1]
      and max(p[0] for p in parts) == 2, str(parts))

check("2. env stays proof and the unit is the proof",
      {s["env"] for s in sents if s.get("part")} == {"proof"}
      and {s["unit"] for s in sents if s.get("part")} == {"proof#1"},
      str([(s["env"], s["unit"]) for s in sents]))

check("3. the text of the step is untouched",
      any(s["text"].startswith("First, we show this") for s in sents),
      str([s["text"][:30] for s in sents]))

check("4. no marker and no heading word in the sentences",
      not any("partmarker" in s["text"] or s["text"].startswith("Step")
              for s in sents), str([s["text"][:20] for s in sents]))

stats = N.Normalizer(paper="p")
stats.normalize(tex)
check("5. the run counts the markers",
      stats.stats.get("part_marker:step") == 2,
      {k: v for k, v in stats.stats.items() if "part_marker" in k})

# ---- a step outside a proof still gets a part ---------------------------
tex2 = DOC + "\\step Alone in the document. Done.\n\\end{document}\n"
norm2, doc2 = run(tex2)
part2 = [s.get("part") for s in doc2["sentences"] if s.get("part")]
check("6. a step outside a proof is still a part",
      bool(part2) and part2[0]["kind"] == "step", str(part2))

# ---- the paper that motivated it ---------------------------------------
run_dir = os.path.abspath(os.path.join(HERE, "..", "..", "test-danmark", "danmark"))
try:
    rows = [json.loads(l) for l in open(run_dir + "/sentences.jsonl",
                                        encoding="utf-8")]
    with_part = [r for r in rows if r.get("part")]
    check("7. danmark: 16 steps, the sentences of each carry a part",
          100 <= len(with_part) <= 150
          and max(r["part"]["index"] for r in with_part) == 3,
          "%d sentences" % len(with_part))
    check("8. danmark: no sentence mentions the marker",
          not any("partmarker" in r["text"] for r in rows))
except OSError:
    print("  SKIP danmark corpus fixture is not shipped (2 checks)")

bad = [x for x in RESULTS if not x[1]]
print("\n%d green, %d red" % (len(RESULTS) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
