"""Observe literal TeX execution sites and replay their original source in order."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile

import conditionals as C
import latex2nl as L
import preserved

ENGINES = ('pdflatex', 'xelatex', 'lualatex')
FILE_ARG = re.compile(r'\s*\{([\w./ -]+)\}')
# Stand-in for a known formula with conditional control flow. The outer
# reader can skip either branch without inspecting tokens inside its maths.
FORMULA = '\ue320'
COUNTER = re.compile(L.COUNTER_TOKEN)
TOKEN = re.compile(C.TOKEN.pattern + '|' + FORMULA)
HOOK_PATTERN = '|'.join(sorted(C.HOOKS))
HIDDEN_FLOW = re.compile(r'\\(?:input|include|subfile|endinput|excludecomment|includecomment|' +
                         HOOK_PATTERN + r')(?![A-Za-z@])')
IF_WORD = re.compile(r'\\if[A-Za-z@]*')
DELIMITER = re.compile(r'[ \t]*(?:\n[ \t]*)?')
WORD_END = re.compile(r'(?<!\\)(?:\\\\)*\\[A-Za-z@]+$')
NOISE = {'__pycache__', '.git', '.hg', '.svn'}
GENERATED = {'.aux', '.log', '.fls', '.toc', '.lof', '.lot', '.nav', '.snm', '.out'}


class Unobserved(ValueError):
    """A trace cannot determine which source to release to the splitter."""


def hide_spans(text, spans):
    pieces, pos = [], 0
    for a, b in spans:
        pieces.extend([text[pos:a], re.sub(r'[^\n]', ' ', text[a:b])])
        pos = b
    return ''.join(pieces) + text[pos:]


def join_source(pieces):
    # Removing a conditional can put a letter directly after a control word.
    # TeX had already tokenized these separately in the original file.
    output = []
    for piece in pieces:
        if piece:
            if output and re.match(r'[A-Za-z@]', piece) and WORD_END.search(output[-1]):
                output.append(' ')
            output.append(piece)
    return ''.join(output)


@dataclass
class Site:
    id: int
    kind: str
    start: int
    end: int
    parts: list = field(default_factory=list)
    target: str = ''


@dataclass
class Source:
    name: str
    raw: str
    view: str
    formula_view: str
    formulas: dict = field(default_factory=dict)
    list_items: set = field(default_factory=set)
    edits: dict = field(default_factory=lambda: defaultdict(list))
    nodes: list = field(default_factory=list)


class Plan:
    def __init__(self, root, prefix):
        self.root = root
        self.prefix = prefix
        self.files = {}
        self.sites = {}
        self.switches = set()
        self.comment_envs = set()
        self.hooked_envs = set()
        self.token_registers = set()
        self.count_registers = set()
        self.math_witnesses = {}

    def site(self, src, kind, start, end):
        site = Site(len(self.sites) + 1, kind, start, end)
        self.sites[site.id] = (src, site)
        return site

    def mark(self, src, pos, site, event):
        # \typeout makes temporary assignments that leak under positive \globaldefs.
        src.edits[pos].append(r'\immediate\write16{' + f'{self.prefix}:{site.id}:{event}' + '}')

    def load(self, name):
        if name in self.files:
            return
        is_main = not self.files
        raw = (self.root / name).read_text(encoding='utf-8-sig')
        view = L.lexical_view(raw)
        # Protect macro definitions and ordinary mathematical notation from probes.
        # Input or deferred content hidden in a definition needs a call-site mapping.
        defs = L.definition_spans(view)
        hidden = [a for a, b in defs if HIDDEN_FLOW.search(view, a, b)
                  or any(m[0] != r'\iff' for m in IF_WORD.finditer(view, a, b))]
        view = hide_spans(view, defs)
        spans = L.find_math_spans(view, clean=False, inspect_arguments=True, report_unclosed=False)
        masked = hide_spans(view, [(s.start, s.end) for s in spans])
        self.switches.update(re.findall(r'\\newif\s*\\(if[A-Za-z@]+)', masked))
        outer = list(masked)
        formulas = {}
        for span in spans:
            observe = bool(COUNTER.search(view, span.start, span.end))
            if IF_WORD.search(view, span.start, span.end):
                words = {m[0][1:] for m in C.TOKEN.finditer(view, span.start, span.end)}
                # A user command such as \ifactor and the connective \iff are
                # valid notation. An if-prefix alone is not a branch boundary.
                if (words & (C.BRACED_IFS - {'iff'}) or
                        (words & {'fi', 'else', 'or'} and any(C.opener(w) for w in words)) or
                        words & self.switches):
                    observe = True
            if observe:
                formulas[span.start] = span
                outer[span.start] = FORMULA
        src = Source(name, raw, ''.join(outer), view, formulas)
        # Probe only ordinary items in enumerations whose numbering is changed.
        # Nested itemize/description environments keep their own labels.
        for listing in re.finditer(r"\\begin\s*\{enumerate\}", view):
            end = L.find_env_end(view, listing.end(), "enumerate")
            if end and re.search(r"\\(?:setcounter|addtocounter)\s*\{enum[iv]+\}", view[listing.end():end.start()]):
                nested = 0
                for token in re.finditer(r"\\(?:begin|end)\s*\{(?:enumerate|description|itemize)\}|\\item(?![a-zA-Z@])",
                                         view[listing.end():end.start()]):
                    if token[0] == r"\item" and not nested:
                        src.list_items.add(listing.end()+token.start())
                    elif token[0].startswith(r"\begin"):
                        nested += 1
                    elif token[0].startswith(r"\end"):
                        nested -= 1
        self.files[name] = src
        entry = self.site(src, 'file', 0, 0)
        self.mark(src, 0, entry, 'enter')
        try:
            if hidden:
                line = raw.count('\n', 0, hidden[0]) + 1
                raise Unobserved(f'control flow inside definition on line {line}')
            src.nodes = self.scan(src, 0, len(raw))
            self.witness_inline(src, spans)
        except Unobserved as exc:
            # A file mentioned by an excluded chapter may never execute.
            # Keep its entry probe and defer the error until an actual visit.
            src.edits.clear()
            self.mark(src, 0, entry, 'enter')
            src.nodes = [self.site(src, str(exc), 0, len(raw))]
        if is_main and any(site.kind == 'include' for _, site in self.sites.values()):
            self.main_entry = entry
            src.edits[0].append(r'\ifdefined\AddToHook\AddToHook{include/excluded}{')
            self.mark(src, 0, entry, 'excluded')
            src.edits[0].append(r'}\fi')

    def witness_inline(self, src, spans):
        # Probe standalone literal relations only. Exclude headings, floats,
        # stored arguments and unknown environments with different output order.
        allowed = {"document","proof","lemma","theorem","example","remark","quote","quotation",
                   "equation","corollary","proposition","definition","itemize","enumerate","center"}
        tokens = re.compile(r"\\(begin|end)\s*\{([\w*]+)\}|\\[a-zA-Z@]+|\\.|[{}[\]]")
        cursor, depth, square, stack = 0, 0, 0, []
        for span in spans:
            for token in tokens.finditer(src.formula_view, cursor, span.start):
                if token[1] == "begin":
                    stack.append(token[2])
                elif token[1] == "end":
                    if stack and stack[-1] == token[2]:
                        stack.pop()
                elif token[0] == "{":
                    depth += 1
                elif token[0] == "}":
                    depth = max(0, depth-1)
                elif token[0] == "[":
                    square += 1
                elif token[0] == "]":
                    square = max(0, square-1)
            cursor = span.end
            if (span.kind != "inline" or depth or square or not stack or "document" not in stack
                    or any(e.rstrip("*").lower() not in allowed for e in stack)
                    or not re.fullmatch(r"\s*[a-zA-Z0-9]+\s*(?:=|<|>)\s*[+-]?\s*\d+\s*", span.body)):
                continue
            ident = len(self.math_witnesses)+1
            self.math_witnesses[ident] = dict(file=src.name, offset=span.start, latex=span.body)
            src.edits[span.start].append(r"\immediate\write16{" + f"{self.prefix}-W:{ident}" + "}")

    def scan_formula(self, src, span):
        view, start, end = src.formula_view, span.start, span.end
        if not IF_WORD.search(view, start, end):
            nodes = []
            for arg in COUNTER.finditer(view, start, end):
                # Script operands and unbraced command arguments are single tokens.
                prefix = view[start:arg.start()].rstrip()
                if prefix.endswith(("^", "_")) or re.search(r"\\[a-zA-Z@]+$", prefix):
                    continue
                site = self.site(src, 'counter', arg.start(), arg.end())
                self.mark(src, site.start, site, 'value=' + arg[0])
                nodes.append(site)
            return nodes
        lo, hi = start, end
        ensure = re.match(r'\\ensuremath\s*\{', view[start:end])
        if ensure:
            lo, hi = start + ensure.end(), end - 1
        # A macro argument may be saved, expanded repeatedly or never read.
        # Preserve the complete formula if its test cannot be probed in place.
        depth = 0
        for token in C.TOKEN.finditer(view, lo, hi):
            name = token[0][1:]
            if token[0] == '{':
                depth += 1
            elif token[0] == '}':
                depth -= 1
            elif name.startswith('if') and name != 'iff' and (depth or name in C.BRACED_IFS):
                return [self.site(src, 'unobserved formula', start, end)]
        return self.scan(src, lo, hi, view=view)

    def scan(self, src, start, stop, view=None):
        view = src.view if view is None else view
        nodes, pos = [], start
        while m := TOKEN.search(view, pos, stop):
            name, pos = m[0][1:], m.end()
            if m[0] == FORMULA and m.start() in src.formulas:
                span = src.formulas[m.start()]
                nodes.extend(self.scan_formula(src, span))
                pos = span.end
                continue
            if m.start() in src.list_items and name == "item" and not view[pos:stop].lstrip().startswith("["):
                site = self.site(src, "list_label", m.start(), pos)
                self.mark(src, pos, site, r"value=\csname label\csname @enumctr\endcsname\endcsname")
                nodes.append(site)
                continue
            if (COUNTER.fullmatch(m[0]) and
                    (src is not next(iter(self.files.values())) or r'\begin{document}' in view[:m.start()]) and
                    not re.search(r"\\[A-Za-z@]+\s*$", view[:m.start()])):
                site = self.site(src, 'counter', m.start(), pos)
                self.mark(src, m.start(), site, 'value=' + m[0])
                nodes.append(site)
                continue
            if name == 'newif':
                arg = re.match(r'\s*\\if[A-Za-z@]+', view[pos:stop])
                if arg:
                    pos += arg.end()
                continue
            if name in ('newtoks', 'newcount') and (arg := C.REGISTER_DECL.match(view, pos, stop)):
                registers = self.token_registers if name == 'newtoks' else self.count_registers
                registers.add(arg[1] or arg[2])
                pos = arg.end()
                continue
            if name in self.token_registers and (arg := re.compile(r'\s*=?\s*\{').match(view, pos, stop)):
                end = L.find_group_end(view, arg.end()-1)
                if end is None or end > stop:
                    raise Unobserved('unbounded token register assignment')
                pos = end  # the RHS is stored without executing any of its tokens
                continue
            if name == 'globaldefs':
                arg = re.compile(r'\s*=?\s*\\([A-Za-z@]+)').match(view, pos, stop)
                if arg and arg[1] in self.count_registers:
                    pos = arg.end()
                    site = self.site(src, 'scope', m.start(), pos)
                    nodes.append(site)
                    self.mark(src, pos, site, r'value=\the\globaldefs')
                    continue
            if name == 'IfFileExists':
                args = []
                for _ in range(3):
                    arg = re.compile(r'\s*\{').match(view, pos, stop)
                    end = L.find_group_end(view, arg.end()-1) if arg else None
                    if end is None or end > stop:
                        raise Unobserved('unbounded IfFileExists arguments')
                    args.append((arg.end(), end-1))
                    pos = end
                site = self.site(src, 'condition', m.start(), pos)
                nodes.append(site)
                for (lo, hi), event in zip(args[1:], ('then', 'else')):
                    self.mark(src, lo, site, event)
                    site.parts.append((lo, hi, self.scan(src, lo, hi, view=view)))
                continue
            if m[0] == '{':
                # Arguments may be stored, expanded repeatedly or reordered.
                end = C.group_end(view, m.start())
                group = view[pos:end-1]
                if HIDDEN_FLOW.search(group) or IF_WORD.search(group) or FORMULA in group:
                    nodes.append(self.site(src, 'unsupported argument', m.start(), end))
                elif r'\globaldefs' in group:
                    nodes.extend(self.scan(src, pos, end-1, view=view))
                pos = end
                continue
            if name == 'unless':
                nxt = re.match(r'\s*(\\if[A-Za-z@]*)', view[pos:stop])
                if not nxt:
                    continue
                cond = TOKEN.match(view, pos + nxt.start(1))
            elif C.opener(name):
                cond = m
            else:
                cond = None
            if cond:
                kind = cond[0][1:]
                p = cond.end()
                supported = kind in self.switches or kind in ('iftrue', 'iffalse')
                if kind in ('ifdefined', 'ifx'):
                    # Restrict operands to literal control words. Their extent is exact.
                    count = 2 if kind == 'ifx' else 1
                    args = re.match(r'(?:\s*\\[A-Za-z@]+){%d}' % count, view[p:stop])
                    supported = bool(args)
                    if args:
                        p += args.end()
                cuts, close, end = C.bounds(view, p)
                if end is None or end > stop:
                    raise Unobserved(f'{src.name}: unbalanced {cond[0]}')
                supported = supported and (not cuts or [c[0] for c in cuts] == ['else'])
                site = self.site(src, 'condition' if supported else 'unsupported test', m.start(), end)
                nodes.append(site)
                if supported:
                    branches = [(p, cuts[0][1] if cuts else close, 'then')]
                    if cuts:
                        branches.append((cuts[0][2], close, 'else'))
                    for lo, hi, event in branches:
                        # Preserve the control word's space delimiter before the marker.
                        at = DELIMITER.match(src.raw, lo, hi).end()
                        self.mark(src, at, site, event)
                        site.parts.append((at, hi, self.scan(src, at, hi, view=view)))
                    if not cuts:
                        src.edits[close].append(r'\else')
                        self.mark(src, close, site, 'else')
                pos = end
                continue
            if name in ('input', 'include', 'subfile'):
                arg = FILE_ARG.match(view, pos, stop)
                if not arg or name == 'subfile':
                    nodes.append(self.site(src, 'unsupported input', m.start(), pos))
                    continue
                pos = arg.end()
                site = self.site(src, name, m.start(), pos)
                nodes.append(site)
                path = Path(arg[1].strip())
                if path.name in ('', '..'):
                    site.kind = 'unmapped input'
                    continue
                if not path.suffix:
                    path = path.with_suffix('.tex')
                candidate = (self.root / path).resolve()
                if not candidate.is_relative_to(self.root) or not candidate.is_file():
                    site.kind = 'unmapped input'
                    continue
                site.target = candidate.relative_to(self.root).as_posix()
                self.load(site.target)
                self.mark(src, m.start(), site, 'call')
                self.mark(src, pos, site, 'return')
            elif name == 'endinput':
                rest = src.raw[pos:].partition('\n')[0].lstrip()
                site = self.site(src, 'endinput' if not rest or rest.startswith('%') else 'unsupported endinput line',
                                 m.start(), pos)
                nodes.append(site)
                self.mark(src, m.start(), site, 'stop')
            elif name in C.HOOKS:
                spec = C.hook_spec(name, view, pos, stop)
                if not spec:
                    nodes.append(self.site(src, 'unsupported hook', m.start(), pos))
                    continue
                phase, lo, pos = spec
                if phase.startswith('env/'):
                    self.hooked_envs.add(phase.split('/')[1])
                site = self.site(src, 'hook', m.start(), pos)
                site.target = phase
                nodes.append(site)
                self.mark(src, m.start(), site, 'register')
                self.mark(src, lo, site, 'emit')
                site.parts.append((lo, pos-1, self.scan(src, lo, pos-1)))
            elif name in ('excludecomment', 'includecomment') and (arg := C.ENV_ARG.match(view, pos, stop)):
                pos = arg.end()
                self.comment_envs.add(arg[1])
                site = self.site(src, name, m.start(), pos)
                site.target = arg[1]
                nodes.append(site)
                self.mark(src, m.start(), site, 'declare')
            elif name == 'begin' and (arg := C.ENV_ARG.match(view, pos, stop)) and arg[1] in self.comment_envs:
                extent = C.comment_extent(src.raw, arg.end(), arg[1])
                if not extent or extent[2] > stop:
                    raise Unobserved('cannot locate comment closing line in '+src.name)
                first, last, pos = extent
                site = self.site(src, 'comment', m.start(), pos)
                site.target = arg[1]
                nodes.append(site)
                self.mark(src, m.start(), site, 'call')
                self.mark(src, first, site, 'included')
                src.edits[first].append('%\n')
                site.parts.append((first, last, self.scan(src, first, last)))
                # comment.sty compares the entire closing line literally.
                src.edits[pos].append('\n')
                self.mark(src, pos, site, 'return')
                src.edits[pos].append('%')
            elif name in ('begin', 'end') and (arg := C.ENV_ARG.match(view, pos, stop)) and arg[1] in self.hooked_envs:
                pos = arg.end()
                site = self.site(src, 'environment', m.start(), pos)
                site.target = 'env/'+arg[1]+'/'+name
                nodes.append(site)
                self.mark(src, m.start(), site, name)
            elif name in ('begin', 'end') and (env := re.match(r'\s*\{document\}', view[pos:stop])):
                pos += env.end()
                site = self.site(src, name, m.start(), pos)
                nodes.append(site)
                self.mark(src, m.start(), site, name)
                if name == 'end':
                    break
        return nodes

    def instrument(self, work):
        for src in self.files.values():
            parts, last = [], 0
            for pos, insertions in sorted(src.edits.items()):
                parts.extend([src.raw[last:pos], *insertions])
                last = pos
            (work / src.name).write_text(''.join(parts) + src.raw[last:], encoding='utf-8')

    def inventory(self):
        rows = []
        for i, (src, site) in self.sites.items():
            row = dict(id=i, kind=site.kind, file=src.name, line=src.raw.count('\n', 0, site.start)+1,
                       start=site.start, end=site.end)
            if site.target:
                row['target'] = site.target
            rows.append(row)
        return rows


class Replay:
    def __init__(self, plan, events):
        self.plan = plan
        if any(i not in plan.sites for i, _ in events):
            raise Unobserved('unknown site in TeX log')
        self.events = deque(events)
        self.registered = Counter()
        self.comments = {}
        self.in_body = False
        self.ended = False
        self.pending = []

    def take(self, site, *choices):
        if self.events and self.events[0][0] == site.id and self.events[0][1] in choices:
            return self.events.popleft()[1]
        raise Unobserved(f'site {site.id}: expected {choices}, saw {next(iter(self.events), None)}')

    def file(self, name):
        src = self.plan.files[name]
        if not self.events:
            raise Unobserved(f'{name}: missing file entry')
        origin, entry = self.plan.sites[self.events[0][0]]
        if origin is not src or entry.kind != 'file':
            raise Unobserved(f'{name}: wrong file entry')
        self.take(entry, 'enter')
        return self.render(src, 0, len(src.raw), src.nodes)

    def hooks(self, *phases):
        pieces = []
        while self.events and self.events[0][1] == 'emit':
            src, hook = self.plan.sites[self.events[0][0]]
            if hook.target not in phases or not self.registered[hook.id]:
                raise Unobserved(f'site {hook.id}: unexpected hook execution')
            self.take(hook, 'emit')
            if not hook.target.startswith('env/') or src.raw.startswith(r'\AddToHookNext', hook.start):
                self.registered[hook.id] -= 1
            pieces.append(self.render(src, *hook.parts[0]))
        return join_source(pieces)

    def render(self, src, lo, hi, nodes):
        parts = []
        for site in nodes:
            parts.append(src.raw[lo:site.start])
            lo = site.end
            if site.kind == 'condition':
                branch = 0 if self.take(site, 'then', 'else') == 'then' else 1
                if branch < len(site.parts):
                    parts.append(self.render(src, *site.parts[branch]))
                if src.raw[lo-1:lo] != '}':
                    lo = DELIMITER.match(src.raw, lo, hi).end()
            elif site.kind in ('counter', 'scope', 'list_label'):
                if not self.events or self.events[0][0] != site.id:
                    raise Unobserved(f'site {site.id}: missing counter expansion')
                event = self.events.popleft()[1]
                if not event.startswith('value='):
                    raise Unobserved(f'site {site.id}: missing counter value')
                value = event[6:]
                if site.kind == 'list_label':
                    parts.append(src.raw[site.start:lo])
                    if re.fullmatch(r"[A-Za-z0-9().:\-]+", value):
                        parts.append("[" + value + "]")
                elif site.kind == 'scope':
                    if not re.fullmatch(r'-?[0-9]+', value):
                        raise Unobserved(f'site {site.id}: invalid assignment scope')
                    parts.append(r'\globaldefs=' + value + ' ')
                else:
                    parts.append(value if re.fullmatch(r'[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*|-\d+', value, re.ASCII)
                                 else src.raw[site.start:lo])
            elif site.kind == 'unobserved formula' and self.in_body:
                self.pending.append(site.id)
                parts.append(preserved.pack('conditional_formula', src.raw[site.start:lo],
                                            status='pending', reason='unobserved_formula_condition',
                                            file=src.name))
            elif site.kind == 'unsupported test' and self.in_body:
                raw = src.raw[site.start:lo]
                if HIDDEN_FLOW.search(raw) or re.search(r'\\(?:end|begin)\s*\{document\}', raw):
                    raise Unobserved(f'site {site.id}: unsupported test controls input, hook or document')
                self.pending.append(site.id)
                parts.append(preserved.pack('conditional', raw, status='pending',
                                            reason='unobserved_tex_test', file=src.name))
            elif site.kind in ('input', 'include'):
                self.take(site, 'call')
                if site.kind == 'include' and self.events and self.events[0][1] == 'excluded':
                    self.take(self.plan.main_entry, 'excluded')
                else:
                    content = self.file(site.target)
                    if content and not content.endswith('\n'):
                        content += '\n'
                    parts.append('\n\n' + content + '\n\n' if site.kind == 'include' else content)
                self.take(site, 'return')
            elif site.kind == 'endinput':
                self.take(site, 'stop')
                if hi != len(src.raw):
                    raise Unobserved(f'site {site.id}: endinput crosses source range')
                return join_source(parts)
            elif site.kind == 'hook':
                self.take(site, 'register')
                self.registered[site.id] += 1
            elif site.kind in ('excludecomment', 'includecomment'):
                self.take(site, 'declare')
                self.comments[site.target] = site.kind
            elif site.kind == 'comment':
                self.take(site, 'call')
                if self.events and self.events[0] == (site.id, 'included'):
                    self.take(site, 'included')
                    parts.append(self.render(src, *site.parts[0]))
                elif self.comments.get(site.target) != 'excludecomment':
                    raise Unobserved('missing included body for '+site.target)
                self.take(site, 'return')
            elif site.kind == 'environment':
                self.take(site, site.target.rsplit('/', 1)[1])
                callback = self.hooks(site.target)
                if callback:
                    parts.append(r'\AddToHookNext{' + site.target + '}{' + callback + '}')
                parts.append(src.raw[site.start:lo])
            elif site.kind == 'begin':
                self.take(site, 'begin')
                self.in_body = True
                parts.extend([src.raw[site.start:lo], self.hooks('begin', 'late_begin')])
            elif site.kind == 'end':
                self.take(site, 'end')
                parts.extend([self.hooks('end'), src.raw[site.start:lo]])
                self.ended = True
                return join_source(parts)
            else:
                raise Unobserved(f'{src.name}, site {site.id}: {site.kind}')
        parts.append(src.raw[lo:hi])
        return join_source(parts)

    def document(self, main):
        text = self.file(main)
        missing = any(count and not self.plan.sites[i][1].target.startswith('env/')
                      for i, count in self.registered.items())
        if self.events or missing or not self.ended:
            raise Unobserved('incomplete replay: extra events, unexecuted hook or missing document end')
        return text



def compile_project(engine, work, main):
    cmd = [engine, '-no-shell-escape', '-halt-on-error', '-interaction=nonstopmode',
           '-recorder', './' + main]
    proc = subprocess.run(cmd, cwd=work, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    if proc.returncode:
        log = proc.stdout.decode('utf-8', errors='replace').splitlines()
        message = next((line for line in log if line.startswith('!')), 'see engine output')
        raise Unobserved(f'{Path(engine).name} exit {proc.returncode}: {message}')
    return (work / main).with_suffix('.log').read_text(encoding='utf-8', errors='replace')


def build(engine, work, main):
    for passes in range(1, 4):
        log = compile_project(engine, work, main)
        if re.search(r'Please \(re\)run (?:Biber|BibTeX)', log, re.I):
            raise Unobserved('build requests an external bibliography tool')
        if not re.search(r'(?:rerun (?:LaTeX|to get)|There were undefined (?:references|citations))', log, re.I):
            return log, passes
    raise Unobserved('references or rerun request still outstanding after three TeX passes')


def aux_labels(directory, entry):
    """Read labels from the aux files reached by the independent reference build."""
    directory = Path(directory).resolve()
    labels, visited = {}, set()
    def visit(path):
        path = path.resolve()
        if path in visited or not path.is_relative_to(directory) or not path.is_file():
            return
        visited.add(path)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            include = re.fullmatch(r"\\@input\{([^{}]+\.aux)\}\s*", line)
            if include:
                visit(directory / include[1])
                continue
            match = re.match(r"\\newlabel\{([^{}]+)\}\{\{", line)
            if match:
                end = L.find_group_end(line, match.end()-1)
                if end:
                    labels[match[1]] = line[match.end():end-1]
    visit(directory / entry)
    return labels


def pdf_text(work, main):
    pdf = Path(main).with_suffix('.pdf')
    if not (work / pdf).exists():
        return None  # A document can legitimately produce zero pages.
    return subprocess.run(['pdftotext', '-enc', 'UTF-8', '-layout', str(pdf), '-'], cwd=work,
                          capture_output=True, check=True, timeout=60).stdout


def local_inputs(work, main):
    lines = (work / main).with_suffix('.fls').read_text(encoding='utf-8', errors='replace').splitlines()
    found = set()
    for line in lines:
        if line.startswith('INPUT '):
            path = (work / line[6:]).resolve()
            if path.is_relative_to(work) and path.is_file() and path.suffix.lower() not in GENERATED:
                found.add(path.relative_to(work).as_posix())
    return sorted(found)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
    try:
        tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def clear_report(directory):
    path = Path(directory) / 'execution.json'
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            return
        if isinstance(old, dict) and old.get('schema') == 'nlprep-execution-1':
            path.unlink()


def observe(path, engine, output, sources):
    main = Path(path).resolve()
    original = main.read_text(encoding='utf-8-sig', errors='replace')
    sources[str(main)] = original
    report = dict(schema='nlprep-execution-1', engine=engine, main=main.name, status='pending',
                  stage='prepare', sources={}, sites=[], events=[], recorder_inputs=[])
    prefix = 'NLP' + secrets.token_hex(8)
    plan = None
    try:
        with tempfile.TemporaryDirectory(prefix='nlprep-execution-') as temp:
            base = Path(temp).resolve()
            reference, work = base / 'reference', base / 'observed'

            def ignore(folder, names):
                return [n for n in names if n in NOISE or Path(n).suffix.lower() in GENERATED
                        or (Path(folder) / n).resolve() == base
                        or (Path(folder) == main.parent and n == main.with_suffix('.pdf').name)]

            # Identical clean starting trees. The engine only runs in these copies.
            shutil.copytree(main.parent, reference, ignore=ignore)
            shutil.copytree(reference, work)
            plan = Plan(reference, prefix)
            plan.load(main.name)
            plan.instrument(work)
            report['stage'] = 'reference build'
            _, passes = build(engine, reference, main.name)
            report['reference_passes'] = passes
            report['label_numbers'] = aux_labels(reference, main.stem + '.aux')
            reference_inputs = local_inputs(reference, main.name)
            reference_text = pdf_text(reference, main.name)
            report['stage'] = 'observed build'
            log, observed_passes = build(engine, work, main.name)
            report['observed_passes'] = observed_passes
            inputs = local_inputs(work, main.name)
            report['recorder_inputs'] = inputs
            report['stage'] = 'validation'
            events = [(int(i), event) for i, event in re.findall(
                rf'^{prefix}:(\d+):([a-z]+|value=[^\r\n]*)$', log, re.M)]
            report['events'] = [dict(site=i, event=event) for i, event in events]
            report['visible_inline'] = [plan.math_witnesses[int(i)] for i in
                                        re.findall(rf'^{prefix}-W:(\d+)$', log, re.M)]
            text_pdf = pdf_text(work, main.name)
            if reference_text != text_pdf or reference_inputs != inputs or passes != observed_passes:
                raise Unobserved('probes changed PDF text, project input list or number of passes')
            report['pdf_text_sha256'] = digest(reference_text) if reference_text is not None else None
            report['local_dependency_sha256'] = {name: digest((reference / name).read_bytes())
                                                  for name in reference_inputs}
            for name in inputs:
                file = reference / name
                if file.suffix.lower() == '.tex' and name not in plan.files:
                    raise Unobserved(f'{name}: project TeX input has no source mapping')
                if file.suffix.lower() in ('.sty', '.cls'):
                    view = L.lexical_view(file.read_text(encoding='utf-8-sig', errors='replace'))
                    if re.search(r'\\(?:' + HOOK_PATTERN + r'|excludecomment|includecomment)(?![A-Za-z@])', view):
                        raise Unobserved(f'{name}: hook in project style/class has no source mapping')
            replay = Replay(plan, events)
            selected = replay.document(main.name)
            report.update(status='partial' if replay.pending else 'observed',
                          pending_sites=replay.pending, stage='complete',
                          selected_source_sha256=digest(selected.encode('utf-8')))
    except (Unobserved, OSError, subprocess.SubprocessError, UnicodeError, RecursionError) as exc:
        report['reason'] = str(exc)
    if plan:
        report['sites'] = plan.inventory()
        for name, src in plan.files.items():
            sources[str(main.parent / name)] = src.raw
            report['sources'][name] = digest(src.raw.encode('utf-8'))
    if report['status'] == 'pending':
        selected = preserved.pack('tex_execution', original, status='pending',
                                  reason=report['reason'], file=str(main),
                                  input_sources={s.name: s.raw for s in plan.files.values()
                                                 if s.name != main.name} if plan else {})
    write_report(Path(output), report)
    print(f"TeX execution {report['status']}: {output}" +
          (f" ({report['reason']})" if 'reason' in report else ''))
    return selected
