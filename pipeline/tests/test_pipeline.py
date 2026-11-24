"""End-to-end check for the pipeline layer: nothing but Python and the package.

It runs the offline chain on the fixture paper and asserts what a reader would
notice if the layer broke: the four stages finish, the strict gate is clean, and
the answers already on disk are actually applied.
"""
import json
import subprocess
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


if __name__ == '__main__':
    unittest.main()
