"""Check source replay, the actual CLI handoff, and the strict acceptance gate."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import tex_execution as trace

FIXTURE = ROOT / 'tests' / 'fixtures' / 'tex_execution'


def article(body, pre=''):
    return '\\documentclass{article}\n' + pre + '\n\\begin{document}\n' + body + '\n\\end{document}\n'


class ProjectCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name) / 'paper'
        self.project.mkdir()
        self.main = self.project / 'main.tex'
        self.out = Path(temp.name) / 'result'

    def script(self, name, *args, code=0):
        cmd = [sys.executable, '-X', 'utf8', str(ROOT / 'scripts' / (name + '.py')), *map(str, args)]
        proc = subprocess.run(cmd, capture_output=True, encoding='utf-8', timeout=150)
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)

    def read(self, name):
        return json.loads((self.out / name).read_text(encoding='utf-8'))

    def observe(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return trace.observe(self.main, 'pdflatex', self.out / 'execution.json', {})


class TeXExecution(ProjectCase):
    def test_missing_engine_preserves_sources_and_blocks_acceptance(self):
        self.main.write_text(article(r'\input{one}'), encoding='utf-8')
        (self.project / 'one.tex').write_text('Source line.', encoding='utf-8')
        with patch('tex_execution.compile_project', side_effect=FileNotFoundError('missing engine')):
            tex = self.observe()
        self.assertEqual(self.read('execution.json')['status'], 'pending')
        (self.out / 'normalized.tex').write_text(tex, encoding='utf-8')
        self.script('latex2nl', self.out / 'normalized.tex', '--canonical', '-o', self.out)
        self.script('verify_output', self.out, '--strict', code=3)
        item = self.read('content_todo.json')['items'][0]
        self.assertEqual(item['raw'], self.main.read_text(encoding='utf-8'))
        self.assertEqual(item['input_sources'], {'one.tex': 'Source line.'})

    def test_missing_or_extra_event_is_rejected(self):
        self.main.write_text(article(r'\iftrue Yes.\else No.\fi'), encoding='utf-8')
        plan = trace.Plan(self.project, 'TEST')
        plan.load('main.tex')
        events = [(i, {'file':'enter', 'begin':'begin', 'condition':'then', 'end':'end'}[site.kind])
                  for i, (_, site) in plan.sites.items()]
        self.assertIn('Yes.', trace.Replay(plan, events).document('main.tex'))
        for broken in (events[:-1], events[:2] + events[3:], events + events[:1]):
            with self.subTest(events=broken), self.assertRaises(trace.Unobserved):
                trace.Replay(plan, broken).document('main.tex')

    def test_cli_rejects_unshared_build_configuration(self):
        for script, extra in [('normalize', ['--no-inputs']), ('latex2nl', ['--canonical']),
                              ('latex2nl', ['--switch', 'ifdraft=true'])]:
            with self.subTest(extra=extra):
                self.script(script, self.main, '--tex-engine', 'pdflatex', '-o', self.out, *extra, code=2)


@unittest.skipUnless(shutil.which('pdflatex') and shutil.which('pdftotext'), 'TeX/PDF tools unavailable')
class CompiledExecution(ProjectCase):

    def test_document_closer_and_math_switch_match_pdf(self):
        pre = r'\pagestyle{empty}\newif\ifdraft\edef\enable{\noexpand\drafttrue}\enable'
        body = (r'First result. \newcommand{\finishpaper}{\end{document}}' '\n\n'
                r'Chosen $\ifdraft x\else y\fi$. Final conclusion.')
        self.main.write_text(article(body, pre), encoding='utf-8')
        subprocess.run(['pdflatex', '-no-shell-escape', '-halt-on-error', '-interaction=batchmode',
                        self.main.name], cwd=self.project, capture_output=True, check=True, timeout=60)
        reference = subprocess.check_output(['pdftotext', 'main.pdf', '-'], cwd=self.project).split()
        self.assertEqual(b' '.join(reference), b'First result. Chosen x. Final conclusion.')
        for canonical in (False, True):
            with self.subTest(canonical=canonical):
                if canonical:
                    norm = self.out / 'normalized.tex'
                    self.script('normalize', self.main, '--tex-engine', 'pdflatex', '-o', norm)
                    self.script('latex2nl', norm, '--canonical', '-o', self.out)
                else:
                    self.script('latex2nl', self.main, '--tex-engine', 'pdflatex', '-o', self.out)
                self.script('verify_output', self.out, '--strict')
                self.assertEqual((self.out / 'sentences.txt').read_text().splitlines(),
                                 ['First result.', 'Chosen MATH1.', 'Final conclusion.'])
                self.assertEqual(self.read('formulas.json')[0]['raw'], 'x')
                report = self.read('execution.json')
                self.assertEqual(report['status'], 'observed')
                self.assertIn('then', [e['event'] for e in report['events']])

    def test_math_observation_uses_each_invocation(self):
        pre = r'\newif\ifdraft{\aftergroup\drafttrue}'
        part = r'\(\ifdraft\iftrue x\else z\fi\else y\fi\)'
        (self.project / 'part.tex').write_text(part, encoding='utf-8')
        body = r'First \input{part}. \draftfalse Second \input{part}.'
        self.main.write_text(article(body, pre), encoding='utf-8')
        self.script('latex2nl', self.main, '--tex-engine', 'pdflatex', '-o', self.out)
        self.script('verify_output', self.out, '--strict')
        self.assertEqual([f['raw'] for f in self.read('formulas.json')], ['x', 'y'])
        branches = [e for e in self.read('execution.json')['events'] if e['event'] in ('then', 'else')]
        self.assertEqual([e['event'] for e in branches], ['then', 'then', 'else'])
        self.assertEqual(branches[0]['site'], branches[2]['site'])

    def test_condition_in_math_argument_preserves_the_entire_formula(self):
        formula = r'$\frac{\ifdraft x\else y\fi}{z}$'
        self.main.write_text(article('Before. Value ' + formula + '. After.', r'\newif\ifdraft'),
                             encoding='utf-8')
        norm = self.out / 'normalized.tex'
        self.script('normalize', self.main, '--tex-engine', 'pdflatex', '-o', norm)
        self.script('latex2nl', norm, '--canonical', '-o', self.out)
        self.script('verify_output', self.out, '--strict', code=3)
        self.assertEqual(self.read('execution.json')['status'], 'partial')
        item, = self.read('content_todo.json')['items']
        self.assertEqual((item['kind'], item['raw']), ('conditional_formula', formula))
        self.assertEqual((self.out / 'sentences.txt').read_text().splitlines(),
                         ['Before.', 'Value REF1.', 'After.'])
        blocks = self.read('content_blocks.json')
        blocks[0]['kind'] = 'footnote'
        (self.out / 'content_blocks.json').write_text(json.dumps(blocks), encoding='utf-8')
        self.script('verify_output', self.out, '--strict', code=1)

    def test_both_entry_points_and_real_file_entries(self):
        shutil.copytree(FIXTURE, self.project, dirs_exist_ok=True)
        before = {p.name: p.read_bytes() for p in self.project.iterdir()}
        for normalize_first in (False, True):
            with self.subTest(normalize_first=normalize_first):
                if normalize_first:
                    norm = self.out / 'normalized.tex'
                    self.script('normalize', self.main, '--tex-engine', 'pdflatex', '-o', norm)
                    self.script('latex2nl', norm, '--canonical', '-o', self.out)
                else:
                    self.script('latex2nl', self.main, '--tex-engine', 'pdflatex', '-o', self.out)
                self.script('verify_output', self.out, '--strict')
                sentences = (self.out / 'sentences.txt').read_text().splitlines()
                self.assertEqual(sentences, ['Draft claim.', 'Included result for MATH1.', 'Final conclusion.'])
                self.assertEqual(self.read('formulas.json')[0]['raw'], 'x^2')
                report = self.read('execution.json')
                self.assertEqual(report['status'], 'observed')
                files = {s['id']: s['file'] for s in report['sites']}
                entered = [files[e['site']] for e in report['events'] if e['event'] == 'enter']
                self.assertEqual(entered, ['main.tex', 'first.tex'])
                self.assertEqual(sum(e['event'] == 'excluded' for e in report['events']), 1)
                plan = trace.Plan(self.project, 'TEST')
                plan.load('main.tex')
                for required in [('first.tex', 'enter'), ('main.tex', 'excluded')]:
                    events = [(e['site'], e['event']) for e in report['events']
                              if (files[e['site']], e['event']) != required]
                    with self.assertRaises(trace.Unobserved):
                        trace.Replay(plan, events).document('main.tex')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.project.iterdir()})

    def test_repeated_input_and_nested_conditions(self):
        part = r'\ifdraft\iftrue Draft.\else Never.\fi\else Published.\fi'
        (self.project / 'part.tex').write_text(part, encoding='utf-8')
        pre = r'\newif\ifdraft'
        body = r'\drafttrue\input{part}\draftfalse\input{part}'
        self.main.write_text(article(body, pre), encoding='utf-8')
        self.script('latex2nl', self.main, '--tex-engine', 'pdflatex', '-o', self.out)
        self.script('verify_output', self.out, '--strict')
        self.assertEqual((self.out / 'sentences.txt').read_text().splitlines(), ['Draft.', 'Published.'])

    def test_execution_dependent_switches(self):
        (self.project / 'settings.tex').write_text('\\drafttrue\n\\endinput\n\\draftfalse\n', encoding='utf-8')
        (self.project / 'localopts.sty').write_text(
            r'\ProvidesPackage{localopts}\DeclareOption{draft}{\drafttrue}\ProcessOptions\relax', encoding='utf-8')
        for setup in [r'{\aftergroup\drafttrue}', r'{\globaldefs=1 \drafttrue}', r'\input{settings}',
                      r'\AddToHook{begindocument}{\drafttrue}', r'\usepackage[draft]{localopts}']:
            with self.subTest(setup=setup):
                self.main.write_text(article(r'\ifdraft Correct.\else Wrong.\fi', r'\newif\ifdraft' + setup),
                                     encoding='utf-8')
                self.script('latex2nl', self.main, '--tex-engine', 'pdflatex', '-o', self.out)
                self.script('verify_output', self.out, '--strict')
                self.assertEqual((self.out / 'sentences.txt').read_text().strip(), 'Correct.')
        self.main.write_text(article(r'\ifx\leftword\rightword Correct.\else Wrong.\fi',
                                     r'\def\leftword{abc}\def\rightword{abc}'), encoding='utf-8')
        self.script('latex2nl', self.main, '--tex-engine', 'pdflatex', '-o', self.out)
        self.script('verify_output', self.out, '--strict')
        self.assertEqual((self.out / 'sentences.txt').read_text().strip(), 'Correct.')

    def test_unobserved_test_keeps_both_branches_and_other_prose(self):
        raw = r'\ifnum 2>1 First.\else Second.\fi'
        self.main.write_text(article('Before.\n\n' + raw + '\n\nAfter.'), encoding='utf-8')
        self.script('normalize', self.main, '--tex-engine', 'pdflatex', '-o', self.out / 'normalized.tex')
        self.script('latex2nl', self.out / 'normalized.tex', '--canonical', '-o', self.out)
        self.script('verify_output', self.out, '--strict', code=3)
        self.assertEqual(self.read('execution.json')['status'], 'partial')
        self.assertEqual(self.read('content_todo.json')['items'][0]['raw'], raw)
        self.assertEqual((self.out / 'sentences.txt').read_text().splitlines(), ['Before.', 'REF1', 'After.'])

    def test_missing_package_and_rerun_replace_previous_status(self):
        for body, pre, expected in [('Good.', '', 'observed'), ('Unknown.', r'\usepackage{nlprepmissingxyz}', 'pending'),
                                    ('Again.', '', 'observed')]:
            self.main.write_text(article(body, pre), encoding='utf-8')
            self.script('latex2nl', self.main, '--tex-engine', 'pdflatex', '-o', self.out)
            self.assertEqual(self.read('execution.json')['status'], expected)
            self.script('verify_output', self.out, '--strict', code=3 if expected == 'pending' else 0)
            self.assertEqual((self.out / 'content_todo.json').exists(), expected == 'pending')
        self.script('latex2nl', self.main, '-o', self.out)
        self.assertFalse((self.out / 'execution.json').exists())

    def test_same_line_endinput_cannot_silently_drop_formula(self):
        (self.project / 'child.tex').write_text('Start.\\endinput $x$.\nLater.', encoding='utf-8')
        self.main.write_text(article(r'\input{child}'), encoding='utf-8')
        self.script('latex2nl', self.main, '--tex-engine', 'pdflatex', '-o', self.out)
        self.script('verify_output', self.out, '--strict', code=3)
        self.assertIn(r'\endinput $x$', self.read('content_todo.json')['items'][0]['input_sources']['child.tex'])

    def test_reference_rerun_and_pdf_disagreement(self):
        self.main.write_text(article(r'\section{Result}\label{r}See Section~\ref{r}.'), encoding='utf-8')
        self.observe()
        report = self.read('execution.json')
        self.assertEqual(report['status'], 'observed', report)
        self.assertEqual(report['reference_passes'], 2)
        self.assertEqual(report['observed_passes'], 2)
        with patch('tex_execution.pdf_text', side_effect=[b'Original.', b'Changed.']):
            self.observe()
        self.assertEqual(self.read('execution.json')['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
