"""Exercise the raw and canonical entry points with actual author definitions."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import latex2nl as L
import normalize


class SourceOrderTests(unittest.TestCase):
    def outputs(self, body, pre='', switches=None):
        src = pre + '\n\\begin{document}\n' + body + '\n\\end{document}'
        yield L.LatexToNL(switches=switches).convert(src)
        norm = normalize.Normalizer(switches=switches).normalize(src)
        L.use_canonical_names()
        converter = L.LatexToNL()
        converter.canonical = True
        yield converter.convert(norm)

    def sentences(self, body, expected, pre='', switches=None):
        for result in self.outputs(body, pre, switches):
            self.assertEqual([s['text'] for s in result['sentences']], expected)
            self.assertFalse(result['content_blocks'])

    @patch('format_names.lookup', return_value=False)
    def test_later_definition(self, _initially_unbound):
        self.sentences(r'\ifdefined\later Wrong.\else Correct.\fi\def\later{X}'
                       r'\ifdefined\later Now defined.\fi', ['Correct.', 'Now defined.'])

    def test_group_and_global_branch(self):
        self.sentences(r'{\iftrue\global\drafttrue\fi}\ifdraft Correct.\else Wrong.\fi'
                       r'{\draftfalse}\ifdraft Still correct.\fi',
                       ['Correct.', 'Still correct.'], r'\newif\ifdraft')

    def test_called_setter(self):
        self.sentences(r'\activate\ifdraft Correct.\else Wrong.\fi', ['Correct.'],
                       r'\newif\ifdraft\def\activate{\enable}\newcommand{\enable}{\drafttrue}')

    def test_document_end_is_read_in_context(self):
        declaration = r'\newcommand{\finishpaper}{\end{document}}'
        pre = r'\def\example{\begin{document}Hidden. \end{document}}'
        for body, expected in [
            ('First. ' + declaration + ' Final conclusion.', ['First.', 'Final conclusion.']),
            ('First. ' + declaration + r' \finishpaper After.', ['First.']),
            (r'First. \iffalse\end{document}\fi Final conclusion.', ['First.', 'Final conclusion.']),
        ]:
            with self.subTest(body=body):
                self.sentences(body, expected, pre)
        literal = '\\verb|\\end{document}|\\begin{verbatim}\n\\end{document}\n\\end{verbatim}'
        self.assertEqual(L.split_document(r'\begin{document}' + literal + r' Tail.\end{document}'),
                         ('', literal + ' Tail.'))

    def test_quoted_setter_uses_call_time_binding_and_scope(self):
        pre = r'\newif\ifdraft\edef\enable{\noexpand\drafttrue}'
        for setup, expected in [('', 'y'), (r'\enable', 'x'), (r'{\enable}', 'y'),
                                (r'{\global\enable}', 'x'), (r'{\xdef\go{\noexpand\enable}}\go', 'x'),
                                (r'\let\drafttrue\draftfalse\enable', 'y')]:
            for result in self.outputs(r'Chosen $\ifdraft x\else y\fi$.', pre + setup):
                with self.subTest(setup=setup):
                    self.assertEqual([f['raw'] for f in result['formulas']], [expected])
        # A more complex expanded definition was called, but its side effects
        # were not computed statically. Retain the formula instead of guessing.
        pre = r'\newif\ifdraft\edef\enable{\noexpand\drafttrue\relax}\enable'
        for result in self.outputs(r'Chosen $\ifdraft x\else y\fi$.', pre):
            self.assertEqual(result['content_blocks'][0]['kind'], 'conditional_formula')
            self.assertIn(r'\ifdraft x\else y\fi', result['content_blocks'][0]['raw'])

    def test_unknown_branch_cannot_settle_later_condition(self):
        for result in self.outputs(r'\ifx\a\b\enable\fi\ifdraft True.\else False.\fi',
                                   r'\newif\ifdraft\def\enable{\drafttrue}'):
            self.assertEqual([b['conditional'] for b in result['content_blocks']],
                             [r'\ifx', r'\ifdraft'])

    def test_computed_switch_name(self):
        self.sentences(r'\csname drafttrue\endcsname\ifdraft Active.\else Inactive.\fi',
                       ['Active.'], r'\newif\ifdraft')
        for result in self.outputs(r'\csname\unavailable\endcsname\ifdraft A.\else B.\fi',
                                   r'\newif\ifdraft'):
            self.assertEqual([b['kind'] for b in result['content_blocks']],
                             ['dynamic_command', 'conditional'])

    def test_saved_values_and_delayed_assignments(self):
        pre = (r'\def\v{x}\def\pair#1{#1+#1}\edef\a{\pair{\v}}\edef\f{\noexpand\pair}'
               r'\def\v{y}\edef\b{\v}\def\update{\def\v{z}}{\aftergroup\update}'
               r'{\globaldefs=1 \def\last{w}}')
        for result in self.outputs(r'Values $\a$, $\b$, $\v$, $\f{u}$ and $\last$.', pre):
            self.assertEqual([f['expanded'] for f in result['formulas']], ['x+x','y','z','u+u','w'])
        pre = r'\def\val{x}\def\valz{wrong}\def\suffix#1{#1z}\edef\saved{\suffix{\val}}'
        for result in self.outputs(r'Value $\saved$.', pre):
            self.assertEqual(result['formulas'][0]['expanded'], 'xz')

    def test_real_and_stored_document_end(self):
        pre = r'\newtoks\savedtokens\def\finish{\end{document}}\AtEndDocument{Final $\v$.}'
        body = r'First. \savedtokens={\end{document}}Kept. \def\v{b}\finish \def\v{c}Lost.'
        for result in self.outputs(body, pre):
            self.assertEqual([s['text'] for s in result['sentences']], ['First.', 'Kept.', 'Final MATH1.'])
            self.assertEqual([f['raw'] for f in result['formulas']], ['b'])

    def test_box_typesetting_and_local_reassignment(self):
        pre = r'\newsavebox{\mybox}\sbox{\mybox}{Stored $w$. }'
        body = r'{\sbox{\mybox}{Local $x$. }\usebox{\mybox}}\usebox{\mybox}'
        for result in self.outputs(body, pre):
            self.assertEqual([f['raw'] for f in result['formulas']], ['x', 'w'])
        for result in self.outputs(r'\usebox{\mybox}', r'\newsavebox{\mybox}\sbox{\mybox}{$\thesection$}'):
            self.assertEqual(result['content_blocks'][0]['status'], 'pending')

    def test_named_switch_configuration(self):
        for value, expected in [(True, 'Draft.'), (False, 'Final.')]:
            self.sentences(r'\ifdraft Draft.\else Final.\fi', [expected],
                           switches={'ifdraft': value})
        self.sentences(r'\draftfalse\ifdraft Draft.\else Final.\fi', ['Final.'],
                       r'\newif\ifdraft', {'ifdraft': True})

    def test_text_and_math_redefinition(self):
        for result in self.outputs(r'We use \op{x}{y}. {\renewcommand{\op}[2]{\ensuremath{#1+#2}}'
                                   r'We add \op{x}{y}.} We use \op{x}{y}.',
                                   r'\newcommand{\op}[2]{#1 and #2}'):
            self.assertEqual([s['text'] for s in result['sentences']],
                             ['We use x and y.', 'We add MATH1.', 'We use x and y.'])
            self.assertEqual(result['formulas'][0]['raw'], 'x+y')

    def test_alias_forwards_formula_arguments(self):
        for result in self.outputs(r'Let \show{x} grow.',
                                   r'\def\show{\render}\def\render#1{\ensuremath{#1}}'):
            self.assertEqual(result['sentences'][0]['text'], 'Let MATH1 grow.')
            self.assertEqual(result['formulas'][0]['raw'], 'x')

    def test_opaque_wrapped_math(self):
        for result in self.outputs(r'Let \myFormula{x} grow.',
                                   r'\newcommand{\myFormula}[1]{\ensuremath{\unlisted{#1}}}'):
            self.assertEqual(result['sentences'][0]['text'], 'Let MATH1 grow.')
            self.assertEqual(result['formulas'][0]['raw'], r'\unlisted{x}')
            self.assertFalse(result['content_blocks'])

    def test_missing_call_keeps_tokens_inside_its_arguments(self):
        raw = r'\mySpecialOperator*[opt]{\inside{x}} {y}'
        for result in self.outputs('We use '+raw+' here.', r'\def\inside#1{changed}'):
            self.assertEqual(result['sentences'][0]['text'], 'We use REF1 here.')
            self.assertEqual(result['content_blocks'][0]['raw'], raw)
            self.assertEqual(result['content_blocks'][0]['status'], 'pending')

    def test_undecided_call_shields_note_and_condition(self):
        raw = r'\missing{\footnote{abc}}{\ifthenelse{\equal{x}{x}}{A}{B}}'
        for result in self.outputs('We use '+raw+' here.'):
            self.assertEqual([b['raw'] for b in result['content_blocks']], [raw])

    def test_inactive_definition(self):
        for result in self.outputs(r'\iffalse\def\notYet{Wrong}\fi '
                                   r'We use \notYet{x} here.'):
            self.assertEqual(result['content_blocks'][0]['raw'], r'\notYet{x}')

    @patch('format_names.lookup', return_value=False)
    def test_let_and_operator_are_defined_in_order(self, _initially_unbound):
        self.sentences(r'\ifdefined\paperMarker Wrong.\else First.\fi \let\paperMarker\relax'
                       r'\ifdefined\paperMarker Second.\fi \ifdefined\op Wrong.\else Third.\fi '
                       r'\DeclareMathOperator{\op}{Op}\ifdefined\op Fourth.\fi',
                       ['First.', 'Second.', 'Third.', 'Fourth.'])

    def test_unloaded_package_does_not_prove_name_absent(self):
        for result in self.outputs(r'\ifdefined\external A.\else B.\fi', r'\usepackage{absent}'):
            self.assertEqual(result['content_blocks'][0]['raw'], r'\ifdefined\external A.\else B.\fi')

    def test_braced_conditional_executes_selected_assignment(self):
        self.sentences(r'\ifthenelse{\equal{x}{x}}{\enable}{}\ifdraft Yes.\else No.\fi',
                       ['Yes.'], r'\newif\ifdraft\def\enable{\drafttrue}')

    def test_local_files_and_rebuild(self):
        def run(script, *args, expected=0):
            proc = subprocess.run([sys.executable, '-X', 'utf8', ROOT/'scripts'/script, *args],
                                  capture_output=True, encoding='utf-8')
            self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root/'out'
            out.mkdir()
            source = root/'main.tex'
            raw = r'\mySpecialOperator{x}{y}'
            source.write_text(r'\usepackage{local}\begin{document}\input{body}\end{document}', 'utf-8')
            child = root/'body.tex'
            child.write_text('We use '+raw+' here.\n'+r'Let \myFormula{x} grow.', 'utf-8')
            sty = root/'local.sty'
            sty.write_text(r'\newcommand{\myFormula}[1]{\ensuremath{#1}}','utf-8')
            def split():
                run('normalize.py', source, '-o', out/'normalized.tex')
                run('latex2nl.py', out/'normalized.tex', '--canonical', '-o', out)
            split()
            run('verify_output.py', out, '--strict', expected=3)
            item = json.loads((out/'content_todo.json').read_text('utf-8'))['items'][0]
            self.assertEqual(item['raw'], raw)
            self.assertEqual(item['source_matches'], [dict(file=str(child), line=1, column=8)])
            with sty.open('a', encoding='utf-8') as handle:
                handle.write('\n'+r'\newcommand{\mySpecialOperator}[2]{#1 and #2}')
            split()
            run('verify_output.py', out, '--strict')
            self.assertFalse((out/'content_todo.json').exists())
            self.assertEqual((out/'sentences.txt').read_text('utf-8').splitlines(),
                             ['We use x and y here.', 'Let MATH1 grow.'])


if __name__ == '__main__':
    unittest.main()
