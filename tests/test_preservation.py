"""Regression tests for content retention, branch semantics, and strict gating.

During repairs run selected methods; the complete suite is a final acceptance run.
"""
from pathlib import Path
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import normalize
import latex2nl as L
import issues
import verify_output as V
import preserved
import conditionals


def pipeline(body, preamble=''):
    issues.reset()
    source = '\\documentclass{article}\n'+preamble+'\n\\begin{document}\n'+body+'\n\\end{document}'
    normalized = normalize.Normalizer().normalize(source)
    L.use_canonical_names()
    converter = L.LatexToNL(); converter.canonical = True
    return normalized, converter.convert(normalized)


class ContentTests(unittest.TestCase):
    def test_formula_and_chinese(self):
        _, result = pipeline(r'\begin{theorem}\[x^2\geq0.\]\end{theorem}'+'若 $x>0$，则 $x^2>0$。结论成立。')
        self.assertEqual(len(result['sentences']), 3)
        self.assertEqual(result['sentences'][0]['content_kind'], 'formula_only')
        self.assertFalse(any(f.get('orphan') for f in result['formulas']))
        for s in result['sentences']:
            self.assertEqual(''.join(s['tokens']), ''.join(s['text'].split()))

    def test_note_anchor_and_math(self):
        norm, result = pipeline(r'Let $x$ be real.\footnote{Assume also $x>0$.} Then $x^2>0$.')
        b = result['content_blocks'][0]
        self.assertEqual(b['status'], 'parsed')
        self.assertEqual(b['anchor_sids'], ['s1'])
        self.assertIn('Assume also', result['sentences'][-1]['text'])
        self.assertIn('x>0', [f['expanded'] for f in result['formulas']])
        self.assertEqual(normalize.Normalizer().normalize(norm), norm)

    def test_layout_and_caption(self):
        body = r'Before.\begin{table}\begin{tabular}{c}Critical $x>0$\end{tabular}\caption{Assumptions apply.}\end{table} After.'
        _, result = pipeline(body)
        b = result['content_blocks'][0]
        self.assertEqual(b['status'], 'pending')
        self.assertIn(r'Critical $x>0$', b['raw'])
        self.assertIn('Assumptions apply.', [s['text'] for s in result['sentences']])
        self.assertTrue(b['preview_sentence_ids'])

    def test_accents(self):
        _, result = pipeline(r'We study $\accentset{\ast}{W}$ and $\underaccent{\bar}{V}$.')
        self.assertEqual([f['expanded'] for f in result['formulas']], [r'\accentset{\ast}{W}', r'\underaccent{\bar}{V}'])

    def test_raw_route(self):
        result = L.LatexToNL().convert(r'Let $x$ be real.\footnote{Assume $x>0$.}\begin{minipage}{2cm}All values matter.\end{minipage}')
        self.assertEqual([b['status'] for b in result['content_blocks']], ['parsed', 'pending'])

    def test_source_linebreak_is_not_sentence_end(self):
        self.assertEqual(len(L.split_sentences('A long\nsentence continues.')),1)
        self.assertEqual(len(L.split_sentences('结论成立。”下一句。')),2)

    def test_nested_and_unclosed_notes(self):
        _,result=pipeline(r'A claim.\footnote{Outer.\footnote{Inner $x>0$.} End.}')
        self.assertEqual(len(result['content_blocks']),2)
        self.assertTrue(all(b['status']=='parsed' and b['anchor_sids'] for b in result['content_blocks']))
        _,result=pipeline(r'Before.\footnote{Unclosed $x>0$. Tail.')
        self.assertEqual(result['content_blocks'][0]['status'],'pending')
        self.assertIn('Tail.',result['content_blocks'][0]['raw'])


class ConditionalTests(unittest.TestCase):
    def test_literals(self):
        for expression in (r'\ifnum 1>2', r'\ifodd 2', r'\ifdim 1pt>2pt'):
            with self.subTest(expression=expression):
                norm, result = pipeline(expression+r' Wrong branch.\else Correct branch.\fi')
                self.assertEqual([s['text'] for s in result['sentences']], ['Correct branch.'])
        self.assertEqual(conditionals.resolve(r'\ifcase 8 zero\or one\else other\fi').strip(), 'other')
        self.assertEqual(conditionals.resolve(r'\ifcase -1 zero\or one\fi'), '')

    def test_sequential_nested_switches(self):
        _, result = pipeline(r'\drafttrue\ifdraft\iftrue First branch.\else Wrong.\fi\else Wrong.\fi\draftfalse\ifdraft Wrong.\else Fourth branch.\fi',r'\newif\ifdraft')
        self.assertEqual([s['text'] for s in result['sentences']], ['First branch.', 'Fourth branch.'])
        out = conditionals.resolve(r'\newif\ifdraft{\drafttrue\ifdraft Yes\fi}\ifdraft Wrong\else No\fi')
        self.assertIn('Yes',out); self.assertIn('No',out); self.assertNotIn('Wrong',out)

    def test_unknown_complete_and_stable(self):
        raw = r'\ifx\foo\bar '+('First branch. '*50)+r'\else Second branch.\fi'
        norm, result = pipeline(raw+' After.',r'\def\foo{A}\def\bar{B}')
        b = result['content_blocks'][0]
        self.assertEqual(b['raw'], raw)
        self.assertEqual(b['status'], 'pending')
        self.assertFalse(any('First branch' in s['text'] for s in result['sentences']))
        self.assertEqual(normalize.Normalizer().normalize(norm), norm)
        self.assertIn('After.', result['sentences'][-1]['text'])

    def test_nested_unknown_and_state(self):
        src = r'\newif\ifdraft\ifmystery\drafttrue\iftrue A\fi\else B\fi\ifdraft C\else D\fi Tail.'
        norm, result = pipeline(src)
        self.assertEqual(len(result['content_blocks']), 2)
        self.assertIn(r'\iftrue A\fi', result['content_blocks'][0]['raw'])
        self.assertIn('Tail.', result['sentences'][-1]['text'])

    def test_connective_and_conditional_operands(self):
        _,result=pipeline(r'We have $P\iff Q$. After.')
        self.assertEqual(result['content_blocks'],[])
        self.assertIn(r'\Leftrightarrow',result['formulas'][0]['expanded'])
        out=conditionals.resolve(r'\ifdefined\ifdraft Yes\else No\fi', lambda name: True)
        self.assertEqual(out.strip(),'Yes')
        raw=r'\ifx\ifdraft\iftrue Yes\else No\fi'
        out=conditionals.resolve(raw)
        self.assertEqual(preserved.unpack(preserved.MARKER.search(out))['raw'],raw)
        out=conditionals.resolve(r'\newif\ifdraft\begingroup\drafttrue\endgroup\ifdraft Wrong\else Correct\fi')
        self.assertIn('Correct',out); self.assertNotIn('Wrong',out)

    def test_process_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp/'input.tex'
            source.write_text(r'\begin{document}\ifx\a\b First.\else Second.\fi After.\end{document}',encoding='utf-8')
            for script,args in [('normalize',[source,'-o',tmp/'normalized.tex']),('latex2nl',[tmp/'normalized.tex','-o',tmp,'--canonical'])]:
                p = subprocess.run([sys.executable,'-X','utf8',ROOT/'scripts'/(script+'.py'),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(p.returncode,0,p.stderr)
            todo=json.loads((tmp/'conditional_todo.json').read_text(encoding='utf-8'))
            self.assertIn('Second.',todo['items'][0]['raw'])

    def test_unknown_inside_formula(self):
        norm,result=pipeline(r'We have $x=\ifx\a\b 1\else 2\fi$.')
        self.assertEqual(result['content_blocks'][0]['kind'],'conditional_formula')
        self.assertIn(r'\else 2',result['content_blocks'][0]['raw'])
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'normalized.tex').write_text(norm,encoding='utf-8')
            L.write_outputs(result,tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(V.run(tmp,True),3)

    def test_ifthenelse_macro_and_unknown(self):
        norm,_=pipeline(r'$\norm[p]{x}$ and $\k{b}$.',r'\newcommand{\norm}[2][]{\ifthenelse{\isempty{#1}}{\|#2\|}{\|#2\|_{#1}}}\newcommand{\k}[1]{\ifthenelse{\equal{#1}{a}}{one}{other}}')
        self.assertIn('_{p}',norm); self.assertIn('other',norm)
        raw=r'\ifthenelse{\unknown}{First.}{Second.}'
        _,result=pipeline(raw)
        self.assertEqual(result['content_blocks'][0]['raw'],raw)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        norm, result = pipeline(r'Let $x>0$.')
        (self.out/'normalized.tex').write_text(norm,encoding='utf-8')
        L.write_outputs(result,str(self.out))

    def write(self,name,data):
        (self.out/name).write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')

    def verify(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return V.run(str(self.out), True)

    def test_clean(self):
        self.assertEqual(self.verify(),0)

    def test_missing_formula(self):
        self.write('formulas.json',[])
        self.assertEqual(self.verify(),1)

    def test_null_parse(self):
        self.write('parses.jsonl',{'sid':'s1','tree':None})
        self.assertEqual(self.verify(),1)

    def test_merged_hard(self):
        self.write('issues_extra.json',{'entries':[{'kind':'parse:failed','site':'fixture','gate':'soft'}]})
        self.assertEqual(self.verify(),1)

    def test_invalid_ledgers(self):
        for data in ({'entries':[{'kind':'unregistered'}]}, {'counts':{}}, []):
            self.write('issues_extra.json',data)
            self.assertEqual(self.verify(),2)
        (self.out/'issues_extra.json').write_text('{bad',encoding='utf-8')
        self.assertEqual(self.verify(),2)

    def test_missing_required_file(self):
        (self.out/'formulas.json').unlink()
        self.assertEqual(self.verify(),2)

    def test_missing_status_manifest(self):
        (self.out/'split_status.json').unlink()
        self.assertEqual(self.verify(),2)

    def test_tokens_and_duplicates(self):
        rows=[json.loads(l) for l in (self.out/'sentences.jsonl').read_text(encoding='utf-8').splitlines()]
        rows[0]['tokens']=['Let']
        (self.out/'sentences.jsonl').write_text('\n'.join(json.dumps(r) for r in rows*2),encoding='utf-8')
        self.assertEqual(self.verify(),1)
        counts=json.loads((self.out/'issues.json').read_text(encoding='utf-8'))['counts']
        self.assertIn('check:tokens',counts); self.assertIn('check:linkage',counts)

    def test_pending_not_bypassed_by_todo_deletion(self):
        norm,result=pipeline(r'Before.\begin{table}Critical condition.\end{table}')
        (self.out/'normalized.tex').write_text(norm,encoding='utf-8')
        L.write_outputs(result,str(self.out))
        (self.out/'content_todo.json').unlink()
        self.assertEqual(self.verify(),3)
        self.write('content_blocks.json',[])
        self.assertEqual(self.verify(),1)

    def test_braces_and_reference_kinds(self):
        self.assertEqual(V.check_c7({'formulas':[{'id':'MATH1','kind':'inline','raw':r'a\}'}]}),[])
        self.assertTrue(V.check_c7({'formulas':[{'id':'MATH1','kind':'display','raw':'}{'}]}))
        fs=[{'id':'REF1','kind':'ref','ref_kind':'cite','ref_target':'article'},
            {'id':'REF2','kind':'ref','ref_kind':'content','ref_target':'content1'},
            {'id':'REF3','kind':'ref','ref_kind':'ref','ref_target':'a{b}'}]
        self.assertEqual(V.check_c13({'formulas':fs},r'\label{a{b}}'),[])

    def test_counts_not_truncated(self):
        rows=[{'sid':'s'+str(i),'unit':'test','text':r'Unknown \bad.', 'tokens':['Unknown',r'\bad','.'],'formulas':[]} for i in range(405)]
        (self.out/'sentences.jsonl').write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
        self.write('parses.jsonl',{'sid':'s0','tree':None})
        self.assertEqual(self.verify(),1)
        counts=json.loads((self.out/'issues.json').read_text(encoding='utf-8'))['counts']
        self.assertEqual(counts['check:C2'],405)
        self.assertEqual(counts['parse:coverage'],404)


if __name__ == '__main__':
    unittest.main()
