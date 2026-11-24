"""Exercise the reviewer HTTP API, offline runners, and their shared decision store."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"pipeline"), str(ROOT/"pipeline/tests")]
import review
import test_manual_review as existing


class ModelReview(unittest.TestCase):
    paper = existing.ReviewRoundTrip.paper
    pipeline = existing.ReviewRoundTrip.pipeline
    get = existing.ReviewRoundTrip.get

    def setUp(self):
        existing.ReviewRoundTrip.setUp(self)
        self.server = review.Reviewer(self.out, 0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        cfg = patch.object(review.pending_names, "backend_config", return_value={"model":"test-local","backend":"local-openai"})
        cfg.start()
        self.addCleanup(cfg.stop)

    def api(self, path, body=None):
        headers = {"X-Review-Token":self.server.token, "Content-Type":"application/json"}
        req = urllib.request.Request(self.server.url+"/api/"+path, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    def start(self, item=None, scope="all"):
        key = {k:item[k] for k in ("kind","target","version")} if item else {}
        self.api("model-review", dict(paper=self.api("items")["paper"], scope="item" if item else scope, **key))

    def finish(self):
        end = time.monotonic()+20
        while time.monotonic() < end:
            job = self.api("status")["model_job"]
            if not job["running"]:
                return job
            time.sleep(.05)
        self.fail("model job did not finish")

    def edit(self, row, op, **fields):
        key = {k:row[k] for k in ("kind","target","version")}
        self.api("decision",dict(paper=self.api("items")["paper"], op=op, **key, **fields))

    def test_apply_conflict_undo_and_manual_override(self):
        self.paper(r"""Let us choose vector spaces $V_i$ for each $i\in I$.
Use $u\text{ outranks }v$ and $R_{\text{and}}$.
Then $f(x)=2$.""")
        self.pipeline()
        def answer(cfg, user, **kwargs):
            question = json.loads(user)
            kind = question["category"]
            if kind == "slot":
                self.assertTrue(question["evidence"]["latex"])
                return '{"answer":"predicate","note":"test alternative"}'
            if kind == "role":
                return '{"answer":"proposition"}'
            if kind == "construction":
                return '{"answer":"multiply"}'
            if kind == "relation":
                return json.dumps(dict(answer="review",value=question["evidence"]["proposal"]))
            return '{"answer":"abstain"}'
        with patch.object(review.pending_names,"call_model",side_effect=answer) as client:
            self.start()
            job=self.finish()
            self.assertGreater(job["saved"],3)
            self.assertGreater(job["disagreements"],0)
            self.assertEqual(job["failed"],0,job)
            calls=client.call_count
            for runner in ("run_pipeline_llm","run_pipeline"):
                self.pipeline(runner)
                role=review.read(self.out,"roles.json")["roles"]["MATH2"]
                self.assertEqual((role["final"],role["source"]),("proposition","model"))
                self.assertEqual(role["model_review"]["rule_answer"],"constraint")
                self.assertEqual(role["model_review"]["applied_source"],"model")
                forms=review.read(self.out,"formulas.json")
                slots=[s for f in forms for s in f.get("text_slots",[])]
                self.assertEqual([(s["role"],s["source"]) for s in slots],[("predicate","model")]*2)
                self.assertTrue(any(f.get("structure_reviews",{}).get("relation",{}).get("source")=="model" for f in forms))
                self.assertTrue(any(e.get("source")=="model" for e in review.read(self.out,"paper_lexicon.json")["constructions"].values()))
            self.assertEqual(client.call_count,calls)
        slot=self.get("slot")[0]
        self.edit(slot,"save",answer="text",source="model")
        rule_role=next(r for r in self.get("role") if r["location"]=="MATH2")
        self.edit(rule_role,"undo")
        self.pipeline()
        self.assertEqual(self.get("slot")[0]["decision"]["source"],"manual")
        role=review.read(self.out,"roles.json")["roles"]["MATH2"]
        self.assertEqual((role["final"],role["source"]),("constraint","rule"))
        self.assertNotIn("decision",next(r for r in self.get("role") if r["location"]=="MATH2"))

    def test_model_boundaries_and_blocks(self):
        self.paper(r"""We use A. another convention follows.

We find \[x=y,\] Composing gives a map.

\begin{figure}A picture.\caption{An example.}\end{figure}""")
        self.pipeline()
        replies = {"sentence":{"answer":"join"}, "display":{"answer":"join"}, "content":{"answer":"retain"}}
        originals = {}
        for kind, result in replies.items():
            item = next(i for i in self.get(kind) if i["model_eligible"])
            with patch.object(review.pending_names,"call_model",return_value=json.dumps(result)):
                self.start(item)
                self.assertEqual(self.finish()["saved"],1)
            originals[kind]=item["target"]
        self.pipeline()
        decisions = review.read(self.out,"split_status.json")["manual_boundaries"]
        self.assertEqual([(i["answer"],i["source"]) for i in decisions],[("join","model")]*2)
        self.assertEqual(review.read(self.out,"content_blocks.json")[0]["source"],"model")
        for kind, target in originals.items():
            self.edit(next(i for i in self.get(kind) if i["target"]==target),"undo")
        self.pipeline()
        self.assertEqual(review.read(self.out,"content_blocks.json")[0]["status"],"pending")

    def test_paper_local_name(self):
        self.paper(r"We use $\zorp x$.")
        self.out.mkdir()
        def run():
            norm=self.out/"normalized.tex"
            subprocess.run([sys.executable, "scripts/normalize.py", self.src, "-o", norm, "--stats",
                            self.out/"normalize-stats.json", "--hash-paper"], cwd=ROOT, check=True, capture_output=True)
            paper=review.read(self.out,"normalize-stats.json")["paper_id"]
            subprocess.run([sys.executable, "scripts/latex2nl.py", norm, "-o", self.out, "--canonical",
                            "--paper-id", paper], cwd=ROOT, check=True, capture_output=True)
        run()
        with patch.object(review.pending_names,"call_model",return_value='{"answer":"allow","class":"const"}'):
            self.start(next(i for i in self.get("names") if i["target"]=="zorp"))
            self.assertEqual(self.finish()["saved"],1)
        run()
        self.assertEqual(review.read(self.out,"normalize-stats.json")["manual_names"]["zorp"]["source"],"model")
        self.edit(next(i for i in self.get("names") if i["target"]=="zorp"),"undo")
        run()
        self.assertFalse(review.read(self.out,"normalize-stats.json")["manual_names"])

    def test_stale_reply_abstention_invalid_and_stop(self):
        self.paper(r"Write $u\text{ zorp }v$.")
        self.pipeline()
        target=self.get("slot")[0]
        for response, outcome in [('{"answer":"abstain"}',"abstained"),('{"answer":"unrecognized"}',"failed"),
                                  ('{"answer":"predicate","target":"other"}',"failed")]:
            with patch.object(review.pending_names,"call_model",return_value=response):
                self.start(target)
                self.assertEqual(self.finish()[outcome],1)
            self.assertNotIn("decision",self.get("slot")[0])
        for stop in (True, False):
            waiting,release=threading.Event(),threading.Event()
            def slow(*args,**kw):
                waiting.set()
                release.wait(10)
                return '{"answer":"predicate"}'
            with patch.object(review.pending_names,"call_model",side_effect=slow):
                self.start(target)
                self.assertTrue(waiting.wait(5))
                if stop:
                    self.api("model-stop",{})
                else:
                    self.edit(target,"save",answer="label")
                    self.edit(self.get("slot")[0],"undo")
                release.set()
                job=self.finish()
                self.assertEqual(job["saved"],0)
                self.assertTrue(job.get("stopped") if stop else job["skipped"]==1)
                self.assertNotIn("decision",self.get("slot")[0])


if __name__ == "__main__":
    unittest.main()
