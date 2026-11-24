"""Exercise the review store through the same runners used by the browser."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline/scripts"))
from review_catalog import catalog, change, read
from manual_review import Changed
from review_labels import queue_counts


class ReviewRoundTrip(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.out = self.base / "out"
        self.src = self.base / "paper.tex"
        self.shell = shutil.which("sh")
        if os.name == "nt":
            self.shell = str(Path(os.environ["ProgramFiles"]) / "Git/bin/bash.exe")

    def paper(self, body):
        self.src.write_text("\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\n"
                            + body + "\n\\end{document}", encoding="utf8")

    def pipeline(self, runner="run_pipeline", expected=None):
        p = subprocess.run([self.shell, "pipeline/scripts/"+runner+".sh", self.src.as_posix(),
                            self.out.as_posix(), "--no-ask"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf8", errors="replace",
                           env=dict(os.environ, PYTHON=sys.executable, PYTHONUTF8="1"), timeout=100)
        self.assertIn(p.returncode, (0,3) if expected is None else (expected,), p.stdout+p.stderr)
        return p.returncode

    def edit(self, row, answer=None, **extra):
        change(self.out, dict(paper=catalog(self.out)["paper"], kind=row["kind"], target=row["target"],
                              version=row["version"], op="save" if answer else "undo",
                              answer=answer, note="checked", **extra))

    def get(self, kind):
        return [i for i in catalog(self.out)["items"] if i["kind"] == kind]

    def test_decide_change_conflict_revoke_and_new_paper(self):
        self.paper(r"Let $a=b$. Compare $u\text{ zorp }v$ and $f(x)$.")
        self.pipeline(expected=3)
        choices={"role":"constraint", "slot":"predicate", "construction":"apply"}
        original = {k:self.get(k)[0] for k in choices}
        self.assertTrue(original["slot"]["required"])
        for kind in ("role", "construction"):
            self.assertFalse(original[kind]["required"])
            self.assertTrue(original[kind]["suggestion"])
        for kind, answer in choices.items(): self.edit(original[kind], answer)
        self.assertFalse(self.get("slot")[0]["required"])
        with self.assertRaises(Changed): self.edit(original["role"], "proposition")
        self.pipeline("run_pipeline_llm", 0)
        self.assertEqual(read(self.out,"roles.json")["roles"]["MATH1"]["source"], "manual")
        self.assertEqual(read(self.out,"formulas.json")[1]["text_slots"][0]["source"], "manual")
        lex = read(self.out,"paper_lexicon.json")["constructions"]
        self.assertEqual(next(iter(lex.values()))["source"], "manual")
        self.edit(self.get("role")[0], "proposition")
        self.pipeline(expected=0)
        self.assertEqual(read(self.out,"roles.json")["roles"]["MATH1"]["final"],"proposition")
        for kind in choices: self.edit(self.get(kind)[0])
        self.pipeline(expected=3)
        self.assertTrue(self.get("slot")[0]["required"])
        for kind in choices: self.assertNotIn("decision",self.get(kind)[0])
        self.edit(self.get("role")[0], "constraint")
        self.paper(r"Let $a=b$. A different paper.")
        self.pipeline(expected=0)
        self.assertNotIn("decision", self.get("role")[0])

    def test_placeholders_and_conflicts_do_not_count_as_suggestions(self):
        self.out.mkdir()
        queues = {
            "role": [{"fid": "MATH1", "role_now": "unsure"},
                     {"fid": "MATH2", "role_now": "proposition", "answer": "unsure"}],
            "construction": [{"key": "f", "default_if_unanswered": "apply", "conflict": {"pair": "label"}},
                             {"key": "g", "default_if_unanswered": "apply"},
                             {"key": "h", "answer": "apply"}],
            "slot": [{"anchor": "unknown", "default_if_unanswered": "unclassified"}],
            "human": [{"kind": "role", "source": "role_todo", "detail": fid}
                      for fid in ("MATH1", "MATH2", "MATH3")],
            "sentence": [{"default": "split"}],
        }
        for kind, rows in queues.items():
            (self.out/(kind + "_todo.json")).write_text(json.dumps({"items": rows}), encoding="utf8")
        counts = queue_counts(self.out)
        self.assertEqual(counts, {"role_todo.json": (1, 1), "construction_todo.json": (2, 1),
                                  "slot_todo.json": (1, 0), "human_todo.json": (2, 1),
                                  "sentence_todo.json": (1, 0)})

    def test_sentence_display_and_retained_block(self):
        self.paper(r"""We use A. another convention follows.

We find \[x=y,\] Composing gives a map.

\begin{figure}A picture.\caption{An example.}\end{figure}""")
        self.pipeline(expected=3)
        for kind, answer in [("display","join"),("sentence","join"),("content","retain")]:
            self.edit(self.get(kind)[0],answer)
        self.pipeline(expected=0)
        states = read(self.out,"split_status.json")["manual_boundaries"]
        self.assertEqual(len(states),2)
        self.assertIn("MATH1, Composing", (self.out/"sentences.txt").read_text(encoding="utf8"))
        self.assertEqual(read(self.out,"content_blocks.json")[0]["status"],"retained")
        self.edit(self.get("content")[0])
        self.pipeline("run_pipeline_llm",3)
        self.assertTrue((self.out/"content_todo.json").exists())

    def test_rule_disagreement_and_prior_model_survive_undo(self):
        self.paper(r"Let us choose vector spaces $V_i$ for each $i\in I$.")
        self.pipeline()
        q = read(self.out,"role_questions.json")
        for it in q["items"]: it.update(answer="proposition",source="model:test")
        (self.out/"role_questions.json").write_text(json.dumps(q),encoding="utf8")
        self.pipeline(expected=0)
        warning=self.get("role")[0]
        self.assertEqual(warning["comparison"]["rule_answer"],"constraint")
        self.edit(warning,"proposition")
        self.pipeline("run_pipeline_llm",0)
        rec=read(self.out,"roles.json")["roles"]["MATH2"]
        self.assertEqual((rec["source"],rec["final"]),("manual","proposition"))
        self.assertFalse((self.out/"role_questions.json").exists())
        self.edit(self.get("role")[0])
        self.pipeline(expected=0)
        self.assertEqual(self.get("role")[0]["comparison"]["model_answer"],"proposition")

    def test_name_is_scoped_and_reversible(self):
        self.paper(r"We use $\zorp x$.")
        def static_run():
            self.out.mkdir(exist_ok=True)
            norm = self.out / "normalized.tex"
            stats = self.out / "normalize-stats.json"
            def run(*args):
                subprocess.run([sys.executable, *args], cwd=ROOT, check=True, capture_output=True)
            run("scripts/normalize.py", str(self.src), "-o", str(norm), "--stats", str(stats), "--hash-paper")
            paper = read(self.out, stats.name)["paper_id"]
            run("scripts/latex2nl.py", str(norm), "-o", str(self.out), "--canonical", "--paper-id", paper)
            run("pipeline/scripts/lexicon.py", str(self.out/"formulas.json"), "--paper-id", paper)
        static_run()
        self.edit(next(i for i in self.get("names") if i["target"]=="zorp"), "allow", **{"class":"named_operator"})
        static_run()
        self.assertEqual(read(self.out,"normalize-stats.json")["manual_names"]["zorp"]["source"],"manual")
        self.assertNotIn(r"\zorp",read(self.out,"paper_lexicon.json")["unknown_commands"])
        self.edit(self.get("names")[0])
        static_run()
        self.assertFalse(read(self.out,"normalize-stats.json")["manual_names"])
        self.assertNotIn("decision",next(i for i in self.get("names") if i["target"]=="zorp"))


if __name__ == "__main__":
    unittest.main()
