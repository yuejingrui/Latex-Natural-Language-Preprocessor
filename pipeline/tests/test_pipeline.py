"""End-to-end check for the pipeline layer: nothing but Python and the package.

It runs the offline chain on the fixture paper and asserts what a reader would
notice if the layer broke: the four stages finish, the strict gate is clean, and
the answers already on disk are actually applied.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the package root
FIXTURE = ROOT / 'pipeline' / 'tests' / 'fixtures' / 'paper.tex'


class Pipeline(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name) / 'run'

    def test_offline_chain_is_clean(self):
        proc = subprocess.run(['sh', str(ROOT / 'pipeline' / 'scripts' / 'run_pipeline.sh'),
                               str(FIXTURE), str(self.out), '--no-ask'],
                              capture_output=True, encoding='utf-8', cwd=ROOT, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        status = json.loads((self.out / 'split_status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['counts']['sentences'], 5)
        self.assertEqual(status['counts']['formulas'], 9)
        self.assertEqual(status['pending']['content'], 0)

        sentences = (self.out / 'sentences.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(sentences[0], 'We consider a functor MATH1 and note that MATH2 grows.')

        formulas = json.loads((self.out / 'formulas.json').read_text(encoding='utf-8'))
        formulas = formulas['formulas'] if isinstance(formulas, dict) else formulas
        by_id = {f['id']: f for f in formulas}
        # $n!$ keeps its exclamation: it is part of the formula, not sentence punctuation
        self.assertEqual(by_id['MATH2']['expanded'], 'n!')
        self.assertEqual(by_id['MATH3']['role'], 'constraint')
        # a footnote becomes a content block and leaves an anchor behind
        blocks = json.loads((self.out / 'content_blocks.json').read_text(encoding='utf-8'))
        blocks = blocks['content_blocks'] if isinstance(blocks, dict) else blocks
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['kind'], 'footnote')

        # the word tables ran too
        self.assertTrue((self.out / 'paper_lexicon.json').exists())
        self.assertTrue((self.out / 'issues.json').exists())

    def test_the_staged_runner_gives_the_same_result(self):
        proc = subprocess.run(['sh', str(ROOT / 'pipeline' / 'scripts' / 'run_pipeline_llm.sh'),
                               str(FIXTURE), str(self.out), '--no-ask'],
                              capture_output=True, encoding='utf-8', cwd=ROOT, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        status = json.loads((self.out / 'split_status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['counts'], {'sentences': 5, 'formulas': 9, 'content_blocks': 1})
        self.assertIn('status         : clean', proc.stdout)

    def run_stage(self, *args):
        proc = subprocess.run(args, cwd=ROOT, capture_output=True, encoding="utf-8", timeout=120)
        return proc.returncode, proc.stdout + proc.stderr

    def test_roles_with_priors_are_optional_with_either_offline_runner(self):
        source = self.out.parent / "role.tex"
        source.write_text(r"\documentclass{article}\begin{document}Let $a=b$.\end{document}", encoding="utf-8")
        for entry in ("run_pipeline.sh", "run_pipeline_llm.sh"):
            with self.subTest(entry=entry):
                out = self.out / entry / "paper"
                code, log = self.run_stage("sh", ROOT/"pipeline/scripts"/entry, source, out, "--no-ask")
                self.assertEqual(code, 0, log)
                self.assertTrue((out/"roles.json").is_file())
                items = json.loads((out/"role_todo.json").read_text(encoding="utf-8"))["items"]
                self.assertEqual([i["fid"] for i in items], ["MATH1"])

    def test_construction_answers_stay_with_their_paper(self):
        self.out.mkdir()
        formulas = self.out / "formulas.json"
        formulas.write_text(json.dumps([{"id": "MATH1", "kind": "inline", "expanded": "xy"}]), encoding="utf-8")
        key = "x · y"
        lib = self.out / "judgements.json"
        lib.write_text(json.dumps({"items": [{"key": key, "answer": "label", "paper": "graph"}]}), encoding="utf-8")
        lexicon = self.out / "paper_lexicon.json"
        def extract(paper, *opts):
            code, log = self.run_stage(sys.executable, ROOT/"pipeline/scripts/lexicon.py",
                                       formulas, "--paper-id", paper, *opts)
            self.assertEqual(code, 0, log)
            return json.loads(lexicon.read_text(encoding="utf-8"))["constructions"]

        # Simulate an old cache, after a rerun that has cleared answers_applied.
        lexicon.write_text(json.dumps({"constructions": {key: {"reading": "label",
                          "source": "manual", "note": "an edge is named by its two endpoints, l.64"}}}), encoding="utf-8")
        self.assertIsNone(extract("algebra")[key]["reading"])
        self.assertEqual(extract("graph", "--judgements", lib)[key]["reading"], "label")
        self.assertIsNone(extract("graph", "--no-judgements")[key]["reading"])
        self.assertIsNone(extract("algebra", "--judgements", lib)[key]["reading"])
        sent = {"text": "Throughout, MATH1 denotes the ordinary product of two real numbers.",
                "formulas": ["MATH1"]}
        (self.out/"sentences.jsonl").write_text(json.dumps(sent) + "\n", encoding="utf-8")
        current = extract("graph", "--judgements", lib)
        for k in (key, "xy"):
            self.assertEqual((current[k]["reading"], current[k]["status"]), ("multiply", "settled_by_paper"))

    def test_reused_output_for_two_main_files(self):
        self.out.mkdir()
        forms = self.out / "formulas.json"
        forms.write_text(json.dumps([{"id": "MATH1", "kind": "inline", "expanded": "xy"}]), encoding="utf-8")
        answers = self.out / "construction_answers.json"
        answers.write_text(json.dumps({"items": [{"key": "xy", "answer": "label"}]}), encoding="utf-8")
        for path, expected in (("graph/main.tex", "label"), ("graph/main.tex", "label"),
                               ("algebra/main.tex", None), ("algebra/main.tex", None)):
            with self.subTest(source=path):
                (self.out / "normalize-stats.json").write_text(json.dumps({"input": path}), encoding="utf-8")
                code, log = self.run_stage(sys.executable, ROOT/"pipeline/scripts/lexicon.py", forms,
                                           "--paper-id", "main", "--merge-answers", answers)
                self.assertEqual(code, 0, log)
                current = json.loads((self.out / "paper_lexicon.json").read_text(encoding="utf-8"))
                self.assertEqual(current["constructions"]["xy"]["reading"], expected)

    def test_relative_paths_and_automatic_tex_selection(self):
        # The caller's relative path is identical to a file in the package.
        folder = self.out.parent
        source = folder/"tests/fixtures/tex_execution/main.tex"
        source.parent.mkdir(parents=True)
        source.write_text(r"\documentclass{article}\includeonly{first}\begin{document}"
                          r"\include{first}\include{second}\end{document}", encoding="utf-8")
        (source.parent/"first.tex").write_text("First $z$.", encoding="utf-8")
        (source.parent/"second.tex").write_text("Excluded $b$.", encoding="utf-8")
        for entry in ("run_pipeline.sh", "run_pipeline_llm.sh"):
            out = folder/entry
            proc = subprocess.run(["sh", ROOT/"pipeline/scripts"/entry, source.relative_to(folder),
                                   out.name, "--no-ask"], cwd=folder, capture_output=True, text=True)
            sentences = (out/"sentences.txt").read_text(encoding="utf-8")
            self.assertNotIn("Excluded", sentences)
            if any(shutil.which(e) for e in ("pdflatex", "xelatex", "lualatex")):
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(sentences.strip(), "First MATH1.")
                self.assertEqual(json.loads((out/"execution.json").read_text())["status"], "observed")
            else:
                self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
                self.assertIn("file selection requires", (out/"content_todo.json").read_text())

    def test_source_hash_scopes_names_and_roles(self):
        a = self.out.parent/"a"
        b = self.out.parent/"b"
        for folder in (a, b):
            folder.mkdir()
            (folder/"main.tex").write_text(r"\documentclass{article}\begin{document}"
                                           r"\input{part}\end{document}", encoding="utf-8")
            (folder/"part.tex").write_text("Let $a=b$.", encoding="utf-8")

        def run(folder, expected):
            code, log = self.run_stage("sh", ROOT/"pipeline/scripts/run_pipeline.sh",
                                       folder/"main.tex", self.out, "--no-ask")
            self.assertEqual(code, expected, log)
            stats = json.loads((self.out/"normalize-stats.json").read_text(encoding="utf-8"))
            self.assertIn("part.tex", stats["source_sha256"])
            return stats["paper_id"]

        first = run(a, 0)
        queue = self.out/"role_todo.json"
        data = json.loads(queue.read_text(encoding="utf-8"))
        data["items"][0]["answer"] = "unsure"
        queue.write_text(json.dumps(data))
        self.assertEqual(run(a, 0), first)
        self.assertEqual(json.loads((self.out/"human_todo.json").read_text())["counts"], {"role_unsure": 1})
        data["items"][0]["answer"] = "constraint"
        queue.write_text(json.dumps(data))
        self.assertEqual(run(a, 0), first)
        self.assertFalse((self.out/"human_todo.json").exists())
        second = run(b, 0)
        self.assertNotEqual(first, second)
        self.assertFalse(json.loads((self.out/"roles.json").read_text())["roles"])
        (b/"part.tex").write_text("Assume $a=b$.", encoding="utf-8")
        self.assertNotEqual(run(b, 0), second)

    def test_conflicting_answers_are_reopened(self):
        source = self.out.parent/"letters.tex"
        source.write_text(r"\documentclass{article}\begin{document}We study $xy$.\end{document}")
        command = ["sh", ROOT/"pipeline/scripts/run_pipeline_llm.sh", source, self.out, "--no-ask"]
        self.assertEqual(self.run_stage(*command)[0], 0)
        queue = self.out/"construction_todo.json"
        answers = json.loads(queue.read_text(encoding="utf-8"))
        for item in answers["items"]:
            item["answer"] = "multiply" if item["key"] == "xy" else "label"
        cache = self.out/"construction_answers.json"
        cache.write_text(json.dumps(answers))
        code, log = self.run_stage(*command)
        self.assertEqual(code, 3, log)
        self.assertTrue(all(i.get("conflict") for i in json.loads(queue.read_text())["items"]))
        for item in answers["items"]:
            item["answer"] = "multiply"
        cache.write_text(json.dumps(answers))
        self.assertEqual(self.run_stage(*command)[0], 0)

    def test_slot_only_and_explicit_multiplication(self):
        folder = self.out.parent
        for body, expected in [
                (r"We have $x\ \text{florping}\ y$.", 3),
                (r"Let $c$ be a scalar. Then $c(x,y)$ denotes scalar multiplication of the vector.", 0),
                (r"Let $f\colon X\to Y$ be a function. We write $f\,g$ for its pointwise product.", 0)]:
            source = folder/"main.tex"
            source.write_text(r"\documentclass{article}\usepackage{amsmath}\begin{document}" +
                              body + r"\end{document}")
            for entry in ("run_pipeline.sh", "run_pipeline_llm.sh"):
                code, log = self.run_stage("sh", ROOT/"pipeline/scripts"/entry, source, self.out, "--no-ask")
                self.assertEqual(code, expected, log)
                if expected:
                    self.assertTrue((self.out/"slot_todo.json").is_file())
                else:
                    cons = json.loads((self.out/"paper_lexicon.json").read_text(encoding="utf-8"))["constructions"]
                    self.assertEqual([v["reading"] for v in cons.values()], ["multiply"])

    def test_both_gates_reject_reversed_relation(self):
        source = self.out.parent / "relation.tex"
        source.write_text(r"\documentclass{article}\begin{document}We have $x<y$.\end{document}", encoding="utf-8")
        code, log = self.run_stage("sh", ROOT/"run.sh", source, self.out)
        self.assertEqual(code, 0, log)
        path = self.out/"formulas.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["raw"], "x<y")
        rows[0]["expanded"] = "x>y"
        path.write_text(json.dumps(rows), encoding="utf-8")
        for script, opts in (("scripts/verify_output.py", ["--strict"]),
                             ("pipeline/scripts/verify_formulas.py", [])):
            with self.subTest(script=script):
                code, log = self.run_stage(sys.executable, ROOT/script, self.out, *opts)
                self.assertEqual(code, 1, log)


if __name__ == '__main__':
    unittest.main()
