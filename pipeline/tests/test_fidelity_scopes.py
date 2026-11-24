"""Source-to-record regressions for displays, explicit text and notation rebinding."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "pipeline/scripts")]
import latex2nl
import lexicon
import normalize
import verify_output


class FidelityTests(unittest.TestCase):
    def convert(self, source):
        for attr in ("MATH_ENVS", "MATH_INNER_ENVS", "LIST_ENVS"):
            self.addCleanup(setattr, latex2nl, attr, getattr(latex2nl, attr))
        latex2nl.use_canonical_names()
        normalized = normalize.Normalizer().normalize(r"\begin{document}" + source + r"\end{document}")
        parser = latex2nl.LatexToNL(slot_roles=latex2nl.load_slot_roles())
        parser.canonical = True
        doc = parser.convert(normalized)
        formulas = doc["formulas"]
        self.assertEqual(verify_output.formula_anchor_errors(formulas, normalized), [])
        for record in formulas:
            self.assertIsNone(verify_output.formula_error(record))
        return normalized, doc

    def test_brackets_and_command_signatures(self):
        _, doc = self.convert(r"\begin{quote}[Assume $p=7$.] End.\end{quote}"
                              r"\begin{equation}\begin{split}[A,B]&=AB-BA.\end{split}\end{equation}"
                              r"Use \raisebox{0pt}[1pt][2pt]{$a>0$}{ and $b>0$}. "
                              r"See \hyperref[sec:s]{$u=1$}{ and $v=2$}.")
        self.assertEqual([f["expanded"] for f in doc["formulas"]],
                         ["p=7", "[A,B]&=AB-BA", "a>0", "b>0", "u=1", "v=2"])

    def test_rows_columns_and_anchor_validation(self):
        norm, doc = self.convert(r"\begin{alignat}{2}a&=1,& b&=9\label{first}\\"
                                 r"c&=2,& d&=3.\label{second}\end{alignat}"
                                 r"See \eqref{second} and \eqref{first}.")
        forms = doc["formulas"]
        display = next(f for f in forms if f["kind"] == "display")
        self.assertEqual([(r["lhs"], r["rhs"]) for row in display["rows"] for r in row["relations"]],
                         [("a","1"), ("b","9"), ("c","2"), ("d","3")])
        self.assertIsNone(display["top_relation"])
        self.assertEqual([f["ref_destinations"][0]["row"] for f in forms if f["kind"] == "ref"], [2,1])
        for mutate in ("row", "ref", "remove"):
            records = copy.deepcopy(forms)
            d = next(f for f in records if f["kind"] == "display")
            if mutate == "row":
                d["rows"].reverse()
                self.assertIsNotNone(verify_output.formula_error(d))
            elif mutate == "ref":
                next(f for f in records if f["kind"] == "ref")["ref_destinations"][0]["row"] = 1
            else:
                d["labels"].clear()
            self.assertTrue(verify_output.formula_anchor_errors(records, norm))

    def test_whole_equation_and_nested_alignment(self):
        _, doc = self.convert(r"\begin{equation}\label{full}\begin{alignedat}{2}"
                              r"a&=\begin{matrix}1&2\\3&4\end{matrix}\\ &=b.\end{alignedat}\end{equation}"
                              r"Use \eqref{full}.")
        f = next(f for f in doc["formulas"] if f["kind"] == "display")
        self.assertEqual(len(f["rows"]), 2)
        self.assertTrue(f["expanded"].startswith("a&="))
        self.assertEqual(next(f for f in doc["formulas"] if f["kind"] == "ref")["ref_destinations"],
                         [{"label":"full", "formula":f["id"]}])
        rows = latex2nl.aligned_relations(r"a&=&b\\ c&=&d")
        self.assertEqual([r["relations"][0]["rhs"] for r in rows], ["b","d"])

    def test_text_and_operator_stay_distinct(self):
        _, doc = self.convert(r"Use $\operatorname{mix}(x)$ and call $R^{\text{mix}}$ a state. "
                              r"Also use $\text {mix}(x)$.")
        operator, label, applied = doc["formulas"]
        self.assertEqual(operator["expanded"], r"\operatorname{mix}(x)")
        self.assertEqual(label["text_slots"][0]["role"], "label")
        self.assertEqual(applied["text_slots"][0]["role"], "operator")
        for f in (label, applied):
            tampered = dict(f, text_slots=[], expanded=f["expanded"].replace("<TXT1>",r"\operatorname{mix}"))
            self.assertIsNotNone(verify_output.formula_error(tampered))

    def test_chapter_and_example_scope_restoration(self):
        _, doc = self.convert(r"Throughout, let $f\colon X\to Y$ be a function. "
                              r"\chapter[Short]{Same} Use $f(x)$. \section{Local} Let $f=2,x=3$. Use $f(x)$. "
                              r"\begin{example}Let $f$ be a function. Use $f(x)$.\end{example}"
                              r"Use $f(x)$. \chapter{Same} Use $f(x)$.")
        self.assertEqual(normalize.Normalizer().vocabulary(r"\chapter{Same}\part{Start}"), {})
        self.assertEqual(doc["sentences"][1]["section"], "Same")
        result = lexicon.build_paper_lexicon(doc["formulas"], lexicon.Lexicon.load(), sentences=doc["sentences"])
        uses = [r for r in result["constructions"].values() if r["left"] == "f" and r["right"] == "("]
        self.assertEqual([r["reading"] for r in uses], ["apply","multiply","apply","multiply","apply"])
        self.assertEqual([r["scope_definition"]["formula"] for r in uses], ["MATH1","MATH3","MATH5","MATH3","MATH1"])
        defs = [d for d in result["notation_definitions"] if d["symbol"] == "f"]
        self.assertEqual([[(r["start"], r["end"]) for r in d["ranges"]] for d in defs],
                         [[("MATH1","MATH2"),("MATH8","MATH8")],
                          [("MATH3","MATH4"),("MATH7","MATH7")], [("MATH5","MATH6")]])

    def test_indexed_symbol_is_distinct_from_other_family_members(self):
        _, doc = self.convert(r"Let $f_1\colon A\to B$ be a function. Use $f_1(x)$ and $f_2(x)$. "
                              r"\begin{example}Let $f_{1}=2$. Use $f_1(x)$.\end{example} Use $f_1(x)$.")
        result = lexicon.build_paper_lexicon(doc["formulas"], lexicon.Lexicon.load(), sentences=doc["sentences"])
        uses = {r["location"]: r["reading"] for r in result["constructions"].values() if r["right"] == "("}
        self.assertEqual(uses, {"MATH2@0":"apply", "MATH3@0":None, "MATH5@0":"multiply", "MATH6@0":"apply"})

    def test_local_notation_and_variable_shadow_old_meaning(self):
        _, doc = self.convert(r"\section{A} $xy$ denotes the product of two numbers. Let $f$ be a function. "
                              r"\subsection{Local} Now $xy$ denotes an edge. Let $f$ be a variable. Use $xy$ and $f(x)$. "
                              r"\subsection{Local} Use $xy$ and $f(x)$. \section{A} Use $xy$ and $f(x)$.")
        result = lexicon.build_paper_lexicon(doc["formulas"], lexicon.Lexicon.load(), sentences=doc["sentences"])
        uses = {r["location"]:r for r in result["constructions"].values()}
        for key, expected in [("MATH5@0",None),("MATH6@0",None),("MATH7@0","multiply"),
                              ("MATH8@0","apply"),("MATH9@0",None),("MATH10@0",None)]:
            self.assertEqual(uses[key]["reading"], expected)
        self.assertEqual(uses["MATH6@0"]["scope_definition"]["kind"],"variable")


if __name__ == "__main__":
    unittest.main()
