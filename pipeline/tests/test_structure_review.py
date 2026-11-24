"""End-to-end sources, manual values and the independent fidelity gate."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"scripts"), str(ROOT/"pipeline/scripts")]
import formula_review as review
import latex2nl
import normalize
import review_catalog
import tex_execution
import verify_output


class StructureReviews(unittest.TestCase):
    def test_manual_round_trip_and_source_gate(self):
        src = (r"\begin{document}Use \begin{align}x&=\begin{aligned}a&+b\\&+c\end{aligned}\label{x}\\"
               r"y&=9.\label{y}\end{align} Compare \eqref{x} and \eqref{y}. "
               r"Also $a\not=b$.\end{document}")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            source = out/"paper.tex"
            source.write_text(src,encoding="utf8")
            norm = out/"normalized.tex"
            normalize.main([str(source),"-o",str(norm),"--stats",str(out/"normalize-stats.json"),"--paper-id","book"])
            def run():
                latex2nl.main([str(norm),"-o",tmp,"--canonical","--paper-id","book"])
                return json.loads((out/"formulas.json").read_text(encoding="utf8"))
            def item(kind, fid):
                return next(i for i in review_catalog.catalog(out)["items"] if i["location"]==fid and i["kind"]==kind)
            def change(kind, fid, op, value=None):
                e = item(kind,fid)
                review_catalog.change(out, dict(paper="book",kind=kind,target=e["target"],version=e["version"],
                                                op=op,answer="review",value=value))
            forms=run()
            ref=next(f for f in forms if f.get("ref_target")=="y")
            target=ref["resolved_references"][0]
            self.assertEqual(target["row"],2)
            f=next(f for f in forms if r"\not" in f["raw"])
            self.assertEqual(f["logical_relations"][0]["symbol"],"≠")
            self.assertFalse(item("relation",f["id"])["required"])
            change("relation", f["id"], "save", [{"lhs":"a","symbol":"!=","rhs":"b"}])
            change("destination",ref["id"],"save",[target])
            forms=run()
            f=next(row for row in forms if row["id"]==f["id"])
            self.assertEqual(f["structure_reviews"]["relation"]["source"],"manual")
            self.assertEqual(f["logical_relations"][0]["symbol"],"!=")
            self.assertEqual(f["top_relation"]["symbol"],"!=")
            self.assertEqual(verify_output.run(tmp,True),0)
            with self.assertRaises(ValueError):
                change("destination",ref["id"],"save",[dict(target,row=99)])
            change("relation", f["id"], "undo")
            change("destination",ref["id"],"undo")
            forms=run()
            f=next(row for row in forms if row["id"]==f["id"])
            self.assertEqual(f["logical_relations"][0]["symbol"],"≠")
            f.pop("structure_reviews")
            (out/"formulas.json").write_text(json.dumps(forms),encoding="utf8")
            self.assertEqual(verify_output.run(tmp,True),1)

    def test_aux_and_counter_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"main.aux").write_text(r"\@input{chapter.aux}"+"\n",encoding="utf8")
            (root/"chapter.aux").write_text(r"\newlabel{key}{{2a}{1}{}{equation.7}{}}"+"\n",encoding="utf8")
            self.assertEqual(tex_execution.aux_labels(root,"main.aux"),{"key":"2a"})
        text=normalize.Normalizer().normalize(r"\begin{document}Equation \theequation.\end{document}")
        block=latex2nl.LatexToNL().convert(text)["content_blocks"][0]
        self.assertEqual((block["raw"],block["status"]),(r"\theequation","pending"))

    def test_continuation_keeps_source_lines(self):
        rows=latex2nl.aligned_relations(r"s&=a+b\\&+c+d")
        values=review.logical_relations({"rows":rows})
        self.assertEqual(values,[dict(lhs="s",symbol="=",rhs="a+b +c+d",rows=[1,2])])
        self.assertEqual(len(rows),2)

    def test_compiled_inline_order_independent_of_generated_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            data=dict(status="observed",visible_inline=[dict(latex=x,file="p.tex",offset=i)
                                                       for i,x in enumerate(["a=1","b=2"])])
            (root/"execution.json").write_text(json.dumps(data),encoding="utf8")
            formulas=[dict(kind="inline",raw=x,id=f"MATH{i}") for i,x in enumerate(["b=2","a=1"])]
            errors,_=review.verify(root,formulas,"","p",{},[])
            self.assertTrue(any("out of order" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
