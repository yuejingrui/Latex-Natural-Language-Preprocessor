"""Occurrence-local model decisions and independent checks of decisive rules."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "pipeline/scripts")]
import ask_slots
import latex2nl
import lexicon
import roles
import rule_reviews


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class OccurrenceDecisions(unittest.TestCase):
    def split(self, source, rows=(), paper="sample", **kwargs):
        return latex2nl.LatexToNL(slot_roles=latex2nl.load_slot_roles(), paper=paper,
            slot_answers={r["anchor"]: r for r in rows}, **kwargs).convert(source)

    def test_slots_partial_cache_and_precedence(self):
        source = r"Compare $u\text{ outranks }v + R_{\text{outranks}}$."
        first = self.split(source)
        a, b = first["slot_questions"]
        self.assertEqual([i["id"] for i in first["slots_pending"]], [a["id"]])
        prompt = ask_slots.occurrence_prompt([a,b], ["predicate","label"], {})
        self.assertIn(r"[[MATH1.t1]]\text{ outranks }v + R_{[[MATH1.t2]]\text{outranks}}", prompt)
        self.assertIn("Source sentence: Compare $u", prompt)
        invalid = {"answers":[{"id":a["id"],"answer":"predicate"},
                              {"id":a["id"],"answer":"label"},{"id":"elsewhere","answer":"label"}]}
        self.assertEqual(ask_slots.occurrence_answers(json.dumps(invalid), [a,b], ["predicate","label"]), {})
        rows = [dict(it, answer="predicate", source="model", model="test") for it in (a,b)]
        for expected, answer in (("confirmed","label"),("disagreement","predicate")):
            rows[1]["answer"] = answer
            result = self.split(source, rows)
            slots = result["formulas"][0]["text_slots"]
            self.assertEqual([s["role"] for s in slots], ["predicate","label"])
            self.assertEqual(slots[1]["model_review"]["status"],expected)
            self.assertFalse(result["slot_questions"])
        self.assertTrue(self.split(source, rows[1:])["slots_pending"])
        self.assertTrue(self.split(source, rows, paper="another")["slots_pending"])

    def test_full_display_single_request_and_partial_reply(self):
        source = r"We have \[a\text{ zorp }b\qquad c\text{ zorp }d.\]"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"slot_questions.json"
            result = self.split(source, split_qquad=True)
            a,b = result["slot_questions"]
            self.assertNotEqual(a["formula"],b["formula"])
            latex2nl.write_slot_todo([a,b],str(path))
            cfg = {"backend":"local","model":"test","protocol":"openai"}
            def reply(_, prompt, **kw):
                for row in (a,b):
                    self.assertIn("[["+row["id"]+r"]]\text{ zorp }",prompt)
                return json.dumps({"answers":[{"id":b["id"],"answer":"predicate"}]})
            with patch.object(ask_slots.P,"backend_config",return_value=cfg), \
                 patch.object(ask_slots.P,"call_model",side_effect=reply) as mock:
                ask_slots.main([str(path),"--limit","1"])
                mock.assert_called_once()
            answers = read(path.with_name("slot_answers.json"))["items"]
            self.assertEqual([i["id"] for i in self.split(source,answers,split_qquad=True)["slots_pending"]],[a["id"]])

    def test_same_shape_can_have_two_model_answers_or_two_rule_answers(self):
        formulas = [dict(id=f"MATH{i}", expanded="f(x)") for i in (1,2)]
        def build(texts,previous=None):
            sentences = [dict(text=t, formulas=[f["id"]]) for t,f in zip(texts,formulas)]
            return lexicon.build_paper_lexicon(formulas,lexicon.Lexicon.load(),previous,
                                                paper="A",sentences=sentences)
        plain = ("Look at MATH1.", "Also MATH2.")
        direct = ("We evaluate the function at a point, obtaining MATH1.",
                  "Here MATH2 means their ordinary product.")
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"answers.json"
            for texts in (plain,direct):
                lex = build(texts)
                entries = lex["constructions"]
                keys = sorted(entries,key=lambda k: entries[k]["location"])
                def submit(answers):
                    rows = [dict(key=k,anchor=entries[k]["anchor"],answer=a,source="model")
                            for k,a in zip(keys,answers)]
                    p.write_text(json.dumps({"meta":{"paper":"A"},"items":rows}))
                    lexicon.merge_answers(lex,p)
                if texts==plain:
                    submit(["apply"])
                    self.assertIsNone(entries[keys[1]]["reading"])
                    submit(["apply","multiply"])
                    elsewhere = build(("A different MATH1.",plain[1]),lex)
                    self.assertIsNone(elsewhere["constructions"][keys[0]]["reading"])
                else:
                    submit(["apply","apply"])
                    self.assertEqual([entries[k]["model_review"]["status"] for k in keys],
                                     ["confirmed","disagreement"])
                self.assertEqual([entries[k]["reading"] for k in keys],["apply","multiply"])

    def test_negated_or_conditional_product_is_not_a_binding_rule(self):
        for text in ("We do not write MATH1 for their product.", "We ask whether MATH1 is their product.",
                     "MATH1 denotes the product of two elements only if they commute."):
            formulas = [dict(id="MATH1", expanded="f(x)")]
            lex = lexicon.build_paper_lexicon(formulas, lexicon.Lexicon.load(),
                        sentences=[dict(text=text, formulas=["MATH1"])])
            self.assertIsNone(next(iter(lex["constructions"].values()))["reading"])

    def test_slot_operator_feeds_lexicon_at_its_own_position(self):
        source = r"Use $\text{zorp}(x)$ and $R_{\text{zorp}}$."
        a,b = self.split(source)["slot_questions"]
        result = self.split(source, [dict(a,answer="operator",source="model"),
                                     dict(b,answer="label",source="model")])
        lex = lexicon.build_paper_lexicon(result["formulas"],lexicon.Lexicon.load())
        self.assertEqual(lex["operators"].get("zorp"),1)
        self.assertEqual(lex["summary"]["decided_by_latex_layer"]["prefix application"],1)

    def test_role_rule_wins_and_weak_equality_stays_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            f,s,q = (out/n for n in ("formulas.json","sentences.jsonl","role_questions.json"))
            formulas = [dict(id="MATH1",role="constraint",expanded=r"i\in I",top_relation={"symbol":"∈"}),
                        dict(id="MATH2",role="proposition",expanded="a=b",top_relation={"symbol":"="})]
            f.write_text(json.dumps(formulas),encoding="utf-8")
            sentences = [dict(text=t,sid=str(i),unit="u",speech_act="assumption",formulas=[r["id"]])
                         for i,(t,r) in enumerate(zip(("Let us choose spaces for each MATH1.","Let MATH2."),formulas))]
            s.write_text("\n".join(map(json.dumps,sentences)),encoding="utf-8")
            args=[str(f),str(s),"--paper-id","A","--no-judgements"]
            roles.main(args)
            self.assertEqual([i["fid"] for i in read(out/"role_todo.json")["items"]],["MATH2"])
            doc=read(q)
            for item in doc["items"]:
                item.update(answer="proposition" if item["fid"]=="MATH1" else "constraint",by="model:test")
            q.write_text(json.dumps(doc),encoding="utf-8")
            roles.main(args+["--merge-answers",str(q)])
            roles.main(args)
            final=read(out/"roles.json")["roles"]
            self.assertEqual((final["MATH1"]["final"],final["MATH1"]["model_review"]["status"]),
                             ("constraint","disagreement"))
            self.assertEqual(final["MATH2"]["source"],"model:test")
            self.assertFalse(q.exists())
            self.assertFalse((out/"role_todo.json").exists())
            (out/"paper_lexicon.json").write_text('{"constructions":{}}')
            rule_reviews.report(out)
            self.assertEqual(read(out/"rule_reviews.json")["items"][0]["location"],"MATH1")


if __name__ == "__main__":
    unittest.main()
