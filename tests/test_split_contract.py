"""Splitting accepts opaque formulas without requiring an exhaustive lexicon."""
from pathlib import Path
import contextlib
import io
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import issues
import latex2nl as L
import normalize
import verify_output as V


class SplitContractTests(unittest.TestCase):
    def convert(self, body, canonical=True):
        issues.reset()
        L.use_canonical_names()
        normalized = normalize.Normalizer().normalize(body) if canonical else body
        cv = L.LatexToNL(); cv.canonical = canonical
        result = cv.convert(normalized)
        return normalized, result

    def verify(self, normalized, result, mutate=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out/'normalized.tex').write_text(normalized, encoding='utf-8')
            L.write_outputs(result, tmp)
            issues.write(str(out/'issues_latex2nl.json'))
            # write_outputs is also a public API; CLI additionally writes this.
            if result['slots_pending']:
                L.write_slot_todo(result['slots_pending'], str(out/'slot_todo.json'))
            if mutate: mutate(out)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = V.run(tmp, True)
            report = json.loads((out/'issues.json').read_text(encoding='utf-8'))
        return code, report

    def test_accent_preserved_and_nonblocking(self):
        norm, result = self.convert(r'The value $\accentset{\ast}{W}$ differs from $W$.')
        self.assertEqual([s['text'] for s in result['sentences']], ['The value MATH1 differs from MATH2.'])
        self.assertEqual(result['formulas'][0]['raw'], r'\accentset{\ast}{W}')
        self.assertEqual(result['formulas'][0]['expanded'], r'\accentset{\ast}{W}')
        self.assertEqual(result['formulas'][0]['role'], 'opaque')
        self.assertEqual(result['unresolved'], [])
        code, report = self.verify(norm, result)
        self.assertEqual(code, 0)
        self.assertEqual(report['pending'], [])
        self.assertTrue(report['downstream_diagnostics'])

    def test_arbitrary_names_need_no_registration(self):
        for name in ('paperSpecificAlpha', 'entirelyNewBeta', 'notInAnyCommandTable'):
            with self.subTest(name=name):
                raw = '\\'+name+r'[mode]{x}{y_{i}}'
                norm, result = self.convert('We study $'+raw+'$.')
                self.assertEqual(result['formulas'][0]['raw'], raw)
                self.assertEqual(result['formulas'][0]['unrecognized_commands'], ['\\'+name])
                self.assertEqual(self.verify(norm, result)[0], 0)

    def test_math_style_counter_and_terminal_tokens(self):
        formulas = [r'\mathchoice{a}{b}{c}{d}', r'x^{\thesection}', 'n!!', r'\left(x\right.', r'x\,']
        for canonical in (False, True):
            body = ' '.join('Value $' + raw + '$.' for raw in formulas)
            norm, result = self.convert(body, canonical)
            expected = formulas[:3] + ['(x', 'x'] if canonical else formulas
            self.assertEqual([f['expanded'] for f in result['formulas']], expected)
            self.assertTrue(all(not f['trailing_punct'] for f in result['formulas']))
            self.assertEqual(self.verify(norm, result)[0], 0)
            result['formulas'][2]['trailing_punct'] = '!'
            self.assertEqual(self.verify(norm, result)[0], 1)

    def test_metadata_ends_before_grouped_formula(self):
        body = r'First. \setcounter{section}{2}{Keep $x$.} \noindent{Keep $y$.}'
        for canonical in (False, True):
            _, result = self.convert(body, canonical)
            self.assertEqual([s['text'] for s in result['sentences']], ['First.', 'Keep MATH1.', 'Keep MATH2.'])

    def test_parameters_and_internal_text_not_rewritten(self):
        raw = r'\novelTransform[option]{\textbf{x}}{\text{a long phrase with several words}}'
        norm, result = self.convert('We study $'+raw+'$.')
        f = result['formulas'][0]
        self.assertIn(raw, norm)
        self.assertEqual(f['raw'], raw)
        self.assertEqual(f['expanded'], raw)
        self.assertEqual(f['text_slots'], [])
        self.assertEqual(self.verify(norm, result)[0], 0)
        self.assertEqual(normalize.Normalizer().normalize(norm), norm)

    def test_opaque_display_not_split_on_qquad(self):
        raw = r'\novelTransform{x}\qquad z=1.'
        norm, result = self.convert('We have \\['+raw+'\\]')
        self.assertEqual(len(result['formulas']), 1)
        self.assertEqual(result['formulas'][0]['raw'], raw)
        self.assertEqual(result['sentences'][0]['text'], 'We have MATH1.')
        self.assertEqual(self.verify(norm, result)[0], 0)

    def test_inner_environment_and_metadata_survive_raw_route(self):
        raw = r'\novelTransform{\begin{myarray}a & b\end{myarray}}{\small x}'
        norm, result = self.convert('We study $'+raw+'$.', canonical=False)
        self.assertEqual(result['formulas'][0]['raw'], raw)
        self.assertEqual(self.verify(norm, result)[0], 0)

    def test_slot_role_is_downstream(self):
        norm, result = self.convert(r'We have $x=0\text{if some}y=1$.')
        self.assertTrue(result['slots_pending'])
        self.assertEqual(result['unresolved'], [])
        code, report = self.verify(norm, result)
        self.assertEqual(code, 0)
        self.assertTrue(report['downstream_diagnostics'])

    def test_unknown_prose_is_preserved_and_pending(self):
        norm, result = self.convert(r'We use \novelTransform{x} here.')
        self.assertEqual(result['content_blocks'][0]['raw'], r'\novelTransform{x}')
        self.assertEqual(self.verify(norm, result)[0], 3)

    def test_unclosed_delimiters_still_pending(self):
        for opening in ('$', '$$', r'\(', r'\['):
            with self.subTest(opening=opening):
                norm, result = self.convert('We study '+opening+r'\novelTransform{x}.')
                self.assertNotEqual(self.verify(norm, result)[0], 0)

    def test_bad_braces_still_fail(self):
        norm, result = self.convert(r'We have \[\novelTransform}{x.\]')
        code, report = self.verify(norm, result)
        self.assertEqual(code, 1)
        self.assertIn('check:C7', report['counts'])

    def test_missing_formula_still_fails(self):
        norm, result = self.convert(r'We study $\novelTransform{x}$.')
        code, _ = self.verify(norm, result, lambda out: (out/'formulas.json').write_text('[]',encoding='utf-8'))
        self.assertEqual(code, 1)

    def test_conditional_is_not_just_an_unknown_command(self):
        norm, result = self.convert(r'We have $x=\ifx\a\b 1\else 2\fi$.')
        self.assertTrue(result['content_blocks'])
        self.assertEqual(self.verify(norm, result)[0], 3)

    def test_diagnostics_are_still_validated(self):
        norm, result = self.convert(r'We study $\novelTransform{x}$.')
        code, _ = self.verify(norm, result, lambda out: (out/'formula_diagnostics.json').write_text('{bad',encoding='utf-8'))
        self.assertEqual(code, 2)

    def test_opaque_internal_math_preserved(self):
        raw = r'\novelTransform{\text{for $x>0$}}'
        norm, result = self.convert('We study $'+raw+'$.')
        self.assertEqual(result['formulas'][0]['raw'], raw)
        self.assertEqual(result['formulas'][0]['expanded'], raw)
        self.assertEqual(self.verify(norm, result)[0], 0)

    def test_opaque_rewrite_is_rejected(self):
        norm, result = self.convert(r'We study $\novelTransform{x}$.')
        result['formulas'][0]['expanded'] = 'x'
        code, report = self.verify(norm, result)
        self.assertEqual(code, 1)
        self.assertIn('check:linkage', report['counts'])

    def test_legacy_vocabulary_is_scoped_from_source(self):
        norm, result = self.convert(r'We study $\novelTransform{x}$.')
        def old_stats(out):
            (out/'normalize-stats.json').write_text(
                json.dumps({'outside_vocabulary': {'novelTransform': 1}}),encoding='utf-8')
        self.assertEqual(self.verify(norm, result, old_stats)[0], 0)


if __name__ == '__main__':
    unittest.main()
