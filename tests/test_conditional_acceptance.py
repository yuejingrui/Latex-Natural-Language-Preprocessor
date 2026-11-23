"""Check which branch was emitted and whether the strict gate accepted it."""
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import conditionals
import issues
import latex2nl as L
import normalize
import verify_output as verify


CASES = {
    'integer_suffix': (r'\def\digit{0}', r'\ifnum 10=1\digit Correct.\else Wrong.\fi'),
    'primitive': ('', r'\ifdefined\ifodd Correct.\else Wrong.\fi'),
    'register': (r'\newcount\branchcounter', r'\ifdefined\branchcounter Correct.\else Wrong.\fi'),
    'theorem': (r'\newtheorem{paperlemma}{Lemma}', r'\ifdefined\paperlemma Correct.\else Wrong.\fi'),
    'space_token': (r'\usepackage{ifthen}', r'\ifthenelse{\equal{x }{x}}{Wrong.}{Correct.}'),
    'math_scope': (r'\newif\ifdraft', r'$x\drafttrue$. \ifdraft Wrong.\else Correct.\fi'),
    'math_global': (r'\newif\ifdraft', r'$x\global\drafttrue$. \ifdraft Correct.\else Wrong.\fi'),
    'unless': ('', r'\unless\iftrue Wrong.\else Correct.\fi'),
    'hook_order': (r'\newif\ifdraft\AtBeginDocument{\drafttrue}\draftfalse',
                   r'\ifdraft Correct.\else Wrong.\fi'),
    'environment': (r'\newif\ifdraft\newenvironment{toggle}{\drafttrue}{}',
                    r'\begin{toggle}\ifdraft Correct.\else Wrong.\fi\end{toggle}'),
    'scaled_points': ('', r'\ifdim 1.5sp=1sp Correct.\else Wrong.\fi'),
}


def document(pre, body):
    return '\\documentclass{article}\n'+pre+'\n\\begin{document}\n'+body+'\n\\end{document}\n'


def split(source, canonical):
    issues.reset()
    L.use_canonical_names()
    text = normalize.Normalizer().normalize(source) if canonical else source
    converter = L.LatexToNL()
    converter.canonical = canonical
    return text, converter.convert(text)


def strict(text, result):
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, 'normalized.tex').write_text(text, encoding='utf-8')
        L.write_outputs(result, tmp)
        issues.write(str(Path(tmp, 'issues_latex2nl.json')))
        with contextlib.redirect_stdout(io.StringIO()):
            return verify.run(tmp, True)


class ConditionalAcceptance(unittest.TestCase):
    def test_decided_text_and_gate(self):
        for name, (pre, body) in CASES.items():
            for canonical in (False, True):
                with self.subTest(case=name, canonical=canonical):
                    text, result = split(document(pre, body), canonical)
                    sentences = [s['text'] for s in result['sentences']]
                    self.assertIn('Correct.', sentences)
                    self.assertFalse(any('Wrong.' in s for s in sentences))
                    self.assertEqual(strict(text, result), 0)

    @unittest.skipUnless(shutil.which('pdflatex') and shutil.which('pdftotext'), 'TeX/PDF tools unavailable')
    def test_reference_pdf(self):
        # Typeset the exact same inputs. The expected word is in the PDF,
        # independent of either parser and without modifying the condition.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, 'case.tex')
            for name, spec in CASES.items():
                with self.subTest(case=name):
                    source.write_text(document(*spec), encoding='ascii')
                    subprocess.run(['pdflatex', '-no-shell-escape', '-interaction=batchmode',
                                    '-halt-on-error', source.name], cwd=tmp, check=True,
                                   capture_output=True, timeout=20)
                    pdf = subprocess.check_output(['pdftotext', 'case.pdf', '-'], cwd=tmp)
                    self.assertIn(b'Correct.', pdf)
                    self.assertNotIn(b'Wrong.', pdf)

    def test_unknown_numeric_suffix_stays_pending(self):
        pre = r'\def\digit{\number0}'
        for raw in (r'\ifnum 10=1\digit Correct.\else Wrong.\fi',
                    r'\unless\ifodd1\digit Correct.\else Wrong.\fi',
                    r'\ifcase1\digit Zero.\or One.\else Many.\fi'):
            for canonical in (False, True):
                with self.subTest(raw=raw, canonical=canonical):
                    text, result = split(document(pre, raw), canonical)
                    self.assertEqual([b['raw'] for b in result['content_blocks']], [raw])
                    self.assertEqual(strict(text, result), 3)

    def test_unverified_format_cannot_select_else(self):
        raw = r'\ifdefined\formatFeature First.\else Second.\fi'
        with patch('format_names._probe', return_value={}):
            for canonical in (False, True):
                text, result = split(document('', raw), canonical)
                self.assertEqual(result['content_blocks'][0]['raw'], raw)
                self.assertEqual(strict(text, result), 3)

    def test_engine_disagreement(self):
        import format_names
        with patch('format_names.shutil.which', side_effect=lambda engine: engine), \
             patch('format_names._probe', side_effect=lambda engine, *_: {'directlua': engine == 'lualatex'}):
            self.assertIsNone(format_names.lookup('article', ('directlua',), 'directlua'))

    def test_pending_condition_cannot_be_relabelled(self):
        raw = r'\ifx\a\b Left.\else Right.\fi'
        text, result = split(document('', raw), True)
        # Simulate synchronously regenerated manifests/todos with the wrong
        # classification; the normalized source still carries the question.
        block = result['content_blocks'][0]
        block['kind'] = 'footnote'
        self.assertEqual(strict(text, result), 1)

    def test_late_expansion_keeps_mathgroup_alternatives(self):
        import preserved
        raw = r'\ifnum\mathgroup=\symbold First.\else Second.\fi'
        record = conditionals.preserve_late_conditions(raw)
        self.assertEqual(preserved.unpack(preserved.MARKER.fullmatch(record))['raw'], raw)


if __name__ == '__main__':
    unittest.main()
