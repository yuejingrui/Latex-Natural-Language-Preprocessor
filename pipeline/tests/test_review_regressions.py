"""Regressions for formula-free papers, layout arguments, ownership and the model client."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline/scripts"))
import lexicon
import pending_names
import roles
import verify_formulas
from paper_metadata import run_paper_id
from verify_output import check_c13


class ReviewRegressions(unittest.TestCase):
    def test_formula_id_adjacent_to_letters(self):
        self.assertIsNotNone(roles._find("AFMATH207AFMATH208", "MATH207"))
        self.assertIsNone(roles._find("MATH2071", "MATH207"))
        self.assertEqual(roles.preceding_words("in a CYMATH1733 triangulated", "MATH1733")[-3:],
                         ["in", "a", "cy"])

    def test_empty_table_still_checks_anchors_and_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(verify_formulas.main([tmp]), 2)
            (root / "formulas.json").write_text("[]")
            sentences = root / "sentences.jsonl"
            sentences.write_text('{"text":"Plain prose."}\n')
            self.assertEqual(verify_formulas.main([tmp]), 0)
            sentences.write_text('{"text":"Missing MATH1."}\n')
            self.assertEqual(verify_formulas.main([tmp]), 1)

    def test_layout_arguments_and_real_cell_math(self):
        lex = lexicon.Lexicon.load()
        for body in [r"x\includegraphics*[height=1em]{an-image}y",
                     r"x\includesvg{abc}y", r"x\includegraphics[alt={one]two}]{pic}y", r"x\includestandalone[width={2cm}]{paper}y"]:
            self.assertFalse(lexicon.FormulaScan(lex, body).sites)
        for body in [r"\begin{grid}{subarray}{c}ab\\c\end{grid}",
                     r"\begin{matrix}{ab}&\begin{array}{c}c\end{array}\end{matrix}"]:
            sites = lexicon.FormulaScan(lex, body).sites
            self.assertEqual([(i["L"]["sig"], i["R"]["sig"]) for i in sites], [("a","b")])

    def test_product_scope(self):
        lex = lexicon.Lexicon.load()
        formulas = [dict(id=f"MATH{i}", expanded="xy") for i in (1,2)]
        def readings(a, b):
            sentences = [dict(text=t, formulas=[f["id"]]) for t, f in zip((a,b), formulas)]
            result = lexicon.build_paper_lexicon(formulas, lex, sentences=sentences)
            return sorted([(r["location"], r["reading"]) for r in result["constructions"].values()])
        for prefix in ("Throughout, ", ""):
            self.assertEqual(readings(prefix+"MATH1 denotes the ordinary product of two numbers.",
                                      "Then MATH2 is positive."), [("MATH1@0","multiply")]*2 + [("MATH2@0","multiply")]*2)
        formulas[1]["expanded"] = "xy>0"
        self.assertTrue(all(r == "multiply" for _, r in readings("MATH1 denotes the product of two numbers.",
                                                               "Then MATH2.")))
        formulas[1]["expanded"] = "xy"
        self.assertEqual(readings("The product MATH1 is positive.", "Consider MATH2."), [("MATH1@0","multiply")]*2 + [("MATH2@0",None)]*2)
        self.assertEqual(readings("MATH1 denotes the product of two numbers.",
                                  "Now MATH2 denotes an edge."), [("MATH1@0","multiply")]*2 + [("MATH2@0",None)]*2)

    def test_float_label_and_literal_lookalikes(self):
        names = ["figure", "absent", "comment", "verb", "inactive", "definition"]
        doc = {"formulas": [dict(id=n, ref_kind="ref", kind="ref", ref_target=n) for n in names],
               "content_blocks": [dict(kind="float", source_stage="expanded_body",
                    raw=r"\label{figure} % \label{comment}"+"\n"+
                        r"\verb|\label{verb}|\iffalse\label{inactive}\fi\def\x{\label{definition}}")]}
        self.assertEqual([r["where"].split(" -> ")[0] for r in check_c13(doc, "")], names[1:])

    def test_recorded_identity_survives_renaming(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(run_paper_id(root), "")
            path = root / "normalize-stats.json"
            path.write_text(json.dumps({"input": r"D:\old\paper.tex"}))
            self.assertEqual(run_paper_id(root), "paper")
            path.write_text(json.dumps({"paper_id":"sha256-example","input":"source.tex"}))
            dest = root / "another-output"
            dest.mkdir()
            path.rename(dest / path.name)
            self.assertEqual(run_paper_id(dest), "sha256-example")

    def test_private_model_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "model.json"
            with patch.dict(os.environ, {"NL_PREP_MODEL_CONFIG":str(config), "MODEL_TEST_KEY":"nonstandard"}):
                fields = dict(backend="local-openai",base_url="http://localhost:8080/v1",model="test")
                config.write_text(json.dumps(fields))
                self.assertEqual(pending_names.backend_config()["key"], "")
                fields.update(backend="api-openai", api_key_env="MODEL_TEST_KEY")
                config.write_text(json.dumps(fields))
                self.assertEqual(pending_names.backend_config()["key"], "nonstandard")
                fields["api_key_env"] = ""
                config.write_text(json.dumps(fields))
                with self.assertRaises(ValueError):
                    pending_names.backend_config()

if __name__ == "__main__":
    unittest.main()
