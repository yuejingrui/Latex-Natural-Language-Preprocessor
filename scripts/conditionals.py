"""Bounded TeX conditional reader. Unknown tests are transported, never guessed.

This is not a TeX engine: registers, expansion in tests, modes, and ifx meanings
are deliberately unresolved. State is read in execution order, including groups.
"""
import re
from dataclasses import dataclass
from bisect import bisect_right
from fractions import Fraction
import preserved

TOKEN = re.compile(r'\\[A-Za-z@]+|\\[^A-Za-z@]|[{}]')
EXEC_TOKEN = re.compile(TOKEN.pattern + r'|\$\$?')
MATH_CLOSE = {'$': '$', '$$': '$$', r'\(': r'\)', r'\[': r'\]'}
REGISTER_DECL = re.compile(r'\s*(?:\{\s*\\([A-Za-z@]+)\s*\}|\\([A-Za-z@]+))')
ALLOCATORS = {'newcount', 'newdimen', 'newskip', 'newmuskip', 'newtoks', 'newbox',
              'newread', 'newwrite', 'newlength', 'newsavebox'}
BRACED_IFS = {'iff', 'ifthenelse', 'ifstrequal', 'ifstrempty', 'ifblank', 'ifboolexpr', 'ifdefstring'}
DEFS = {'newcommand', 'renewcommand', 'providecommand', 'DeclareRobustCommand',
        'newenvironment', 'renewenvironment', 'def', 'gdef', 'edef', 'xdef'}
UNITS = {'pt': Fraction(1), 'pc': Fraction(12), 'in': Fraction(7227,100),
         'bp': Fraction(7227,7200), 'cm': Fraction(7227,254),
         'mm': Fraction(7227,2540), 'sp': Fraction(1,65536)}


def group_end(text, start):
    depth = 0
    for m in TOKEN.finditer(text, start):
        if m.group() == '{': depth += 1
        elif m.group() == '}':
            depth -= 1
            if depth == 0: return m.end()
    return len(text)


def definition_end(text, match):
    """Skip definition bodies, which are not executed at declaration time."""
    pos = match.end()
    primitive = match.group()[1:] in {'def', 'gdef', 'edef', 'xdef'}
    if primitive:
        first = text.find('{', pos)
        return group_end(text, first) if first >= 0 else len(text)
    head = re.match(r'\*?\s*(?:\{\\[A-Za-z@]+\}|\\[A-Za-z@]+|\{[^{}]+\})', text[pos:])
    if not head: return pos
    pos += head.end()
    for _ in range(2):
        opt = re.match(r'\s*\[[^\]]*\]', text[pos:])
        if opt: pos += opt.end()
    for _ in range(2 if 'environment' in match.group() else 1):
        ws = re.match(r'\s*', text[pos:]); pos += ws.end()
        if text[pos:pos+1] == '{': pos = group_end(text, pos)
    return pos


def opener(name):
    return name.startswith('if') and name not in BRACED_IFS


def bounds(text, start):
    depth, cuts, pos = 1, [], start
    while (m := TOKEN.search(text, pos)):
        pos = m.end(); name = m.group()[1:]
        if name in DEFS:
            pos = definition_end(text, m)
        elif name == 'newif':
            nxt = re.match(r'\s*\\if[A-Za-z@]+', text[pos:])
            if nxt: pos += nxt.end()
        elif opener(name):
            depth += 1
            _, pos = test(text, m, {}, lambda name: None)
        elif name == 'fi':
            depth -= 1
            if depth == 0: return cuts, m.start(), m.end()
        elif depth == 1 and name in ('else','or'):
            cuts.append((name, m.start(), m.end()))
    return cuts, len(text), None


def integer(text, pos, values):
    """Read decimal digits and simple digit macros. An expandable suffix can
    still supply digits, so an unsupported suffix invalidates the whole test."""
    number = ''
    pos += re.match(r'\s*', text[pos:]).end()
    while pos < len(text):
        literal = re.match(r'[+-]?[0-9]+' if not number else r'[0-9]+', text[pos:])
        if literal:
            number += literal[0]
            pos += literal.end()
        elif text[pos] == '\\':
            cmd = TOKEN.match(text, pos)
            if cmd is None:
                return None
            name = cmd[0][1:]
            value = values.get(name)
            if value is None and name in ('relax', 'else', 'fi', 'or'):
                break
            if not isinstance(value, Macro) or value.nargs or not re.fullmatch(
                    r'[+-]?[0-9]+' if not number else r'[0-9]+', value.body):
                return None
            number += value.body
            pos = cmd.end()
            # TeX discards the separator after a control word at tokenization.
            pos += re.match(r'[ \t]*(?:\n[ \t]*)?', text[pos:]).end()
        else:
            break
        if len(number.lstrip('+-').lstrip('0')) > 10:
            return None
    if not number:
        return None
    digits = number.lstrip('+-').lstrip('0') or '0'
    value = int(digits)
    if value > 2147483647:
        return None
    return (-value if number.startswith('-') else value), pos


def test(text, m, switches, defined, values=None):
    name, pos = m.group()[1:], m.end()
    if name == 'iftrue': return True, pos
    if name == 'iffalse': return False, pos
    if name in switches: return switches[name], pos
    if name == 'ifdefined':
        arg = re.match(r'\s*\\([A-Za-z@]+)', text[pos:])
        if arg: return defined(arg.group(1)), pos+arg.end()
    if name == 'ifx':
        for _ in range(2):
            arg = re.match(r'\s*(?:\\[A-Za-z@]+|\\.|[^\s])', text[pos:])
            if arg: pos += arg.end()
        return None, pos
    if name == 'ifdim':
        arg = re.match(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(pt|pc|in|bp|cm|mm|sp)\s*([<=>])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(pt|pc|in|bp|cm|mm|sp)(?![A-Za-z])', text[pos:])
        if arg:
            # Restrict decisions to exact conversions. TeX truncates at
            # intermediate steps; Python's round() gives different results.
            nums = []
            for i in (1, 4):
                if len(arg[i]) > 32:
                    return None, pos
                scaled = Fraction(arg[i]) * 65536
                if scaled.denominator != 1:
                    return None, pos
                sp = int(scaled * UNITS[arg[i+1]])
                if abs(sp) >= 2**30:
                    return None, pos
                nums.append(sp)
            a, b = nums
            op = arg[3]
        if arg: return {'<':a<b, '>':a>b, '=':a==b}[op], pos+arg.end()
    if name in ('ifnum', 'ifodd', 'ifcase'):
        values = values or {}
        first = integer(text, pos, values)
        if first is None:
            return None, pos
        a, stop = first
        if name != 'ifnum':
            return (bool(a % 2) if name == 'ifodd' else a), stop
        relation = re.match(r'\s*([<=>])', text[stop:])
        if relation:
            last = integer(text, stop + relation.end(), values)
            if last:
                b, end = last
                return {'<': a < b, '>': a > b, '=': a == b}[relation[1]], end
    return None, pos






@dataclass(frozen=True)
class Switch:
    value: object


@dataclass(frozen=True)
class Setter:
    switch: str
    value: bool


@dataclass(frozen=True)
class ExternalAlias:
    token: str
    defined: bool | None


@dataclass(frozen=True)
class Macro:
    nargs: int
    body: str
    default: str | None


UNKNOWN = object()
ABSENT = object()
DECLARED = object()
SIMPLE_CMD = re.compile(r'\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)'
                        r'\*?\s*(?:\{\s*\\([A-Za-z@]+)\s*\}|\\([A-Za-z@]+))'
                        r'\s*(?:\[(\d)\]\s*)?(?:\[([^\]]*)\]\s*)?\{')
SIMPLE_DEF = re.compile(r'\\(?:g?def)\s*\\([A-Za-z@]+)((?:\s*#[1-9])*)\s*\{')


def ordinary_definition(text, match, end):
    head = SIMPLE_CMD.match(text, match.start())
    if head:
        name = head[1] or head[2]
        nargs, default = int(head[3] or 0), head[4]
    else:
        head = SIMPLE_DEF.match(text, match.start())
        if not head:
            return None
        name, default = head[1], None
        params = re.findall(r'#([1-9])', head[2])
        nargs = len(params)
        if params != list('123456789'[:nargs]):
            return None
    if head.end() > end or text[end-1:end] != '}':
        return None
    return name, Macro(nargs, text[head.end():end-1], default)


def alternatives(text, start, cuts, end):
    stops = [c[1] for c in cuts] + [end]
    starts = [start] + [c[2] for c in cuts]
    branches = [text[a:b] for a, b in zip(starts, stops)]
    if not cuts or cuts[-1][0] != 'else':
        branches.append('')
        starts.append(end)
    return list(zip(starts, branches))


class Execution:
    """One source-order state, shared by preamble, selected branches and calls."""

    def __init__(self, defined=lambda name: False, skip=(), keep=None, packages=None, defer=None, switch_defaults=None, braced_test=None, format_defined=None):
        self.fallback_defined = defined
        self.packages = packages or {}
        self.braced_test = braced_test
        self.format_defined = format_defined
        self.defer = defer
        self.held = []
        self.switch_defaults = switch_defaults or {}
        self.skip = set(skip)
        self.keep = keep or (lambda name, spec, execution: False)
        self.values = {}
        self.groups = []
        self.managed = set()
        self.expansions = 0
        for key, value in self.switch_defaults.items():
            self.declare_switch(key, value)

    def declare_switch(self, key, value, globally=False):
        self.assign(key, Switch(value), globally)
        for suffix, setting in [('true', True), ('false', False)]:
            self.assign(key[2:]+suffix, Setter(key, setting), globally)


    def assign(self, name, value, globally=False):
        self.values[name] = value
        if globally:
            for group in self.groups:
                group[name] = value

    def defined(self, name, query_format=False):
        value = self.values.get(name, ABSENT)
        if value is UNKNOWN:
            return None
        if isinstance(value, ExternalAlias):
            return value.defined
        if name in self.values:
            return value is not ABSENT
        answer = self.fallback_defined(name)
        if query_format and self.format_defined and answer is not True:
            if self.values.get('unread_source'):
                return None
            return self.format_defined(self.values.get('class_profile', 'kernel'), name)
        return None if answer is False and self.values.get('unread_source') else answer

    def switches(self):
        return {k: v.value for k, v in self.values.items() if isinstance(v, Switch)}

    def merge_branches(self, branches, active):
        """Propagate only state common to all outcomes, including saved groups."""
        original = [self.values, *self.groups]
        outcomes = []
        for branch in branches:
            self.values, *self.groups = [d.copy() for d in original]
            self.run(branch, active=active)
            outcomes.append([self.values, *self.groups])
        self.values, *self.groups = original
        for i, frame in enumerate(original):
            frames = [outcome[i] if i < len(outcome) else {} for outcome in outcomes]
            for key in set().union(*frames, frame):
                choices = [d.get(key, ABSENT) for d in frames]
                if all(v == choices[0] for v in choices):
                    if choices[0] is ABSENT:
                        frame.pop(key, None)
                    else:
                        frame[key] = choices[0]
                else:
                    frame[key] = Switch(None) if all(isinstance(v, Switch) for v in choices) else UNKNOWN

    def load_package(self, name):
        key = 'package:' + name
        if key not in self.values:
            self.assign(key, True, globally=True)
            self.run(self.packages[name])

    def begin_document(self, preamble, body):
        self.run(preamble)
        hooks = self.values.pop('begin_hooks', ())
        if hooks is UNKNOWN:
            # Different possible preambles registered different hooks. Their
            # side effects and any text they produce belong to the document.
            for key, value in list(self.values.items()):
                self.values[key] = Switch(None) if isinstance(value, Switch) else UNKNOWN
            self.values['unread_source'] = True
            return preserved.pack('conditional', preamble, status='pending',
                                  reason='undecidable_begin_document_hook') + self.run(body)
        return ''.join(self.run(hook) for hook in hooks) + self.run(body)

    def stateful_environment(self, text, match, pos, env, spec, active, offset, math):
        """Execute state-changing begin/end code around the actual body."""
        import latex2nl as L
        nargs, default, begin, finish = spec
        parsed = L.macro_arguments(text, pos, nargs, default)
        if parsed is None:
            return None
        args, start = parsed
        begin = re.sub(r'#([1-9])', lambda h: args[int(h[1])-1]
                       if int(h[1]) <= len(args) else h[0], begin)
        if not any(isinstance(self.values.get(n), (Setter, Macro)) or n in DEFS
                   or n in ('global', 'newif', 'let', 'csname') or opener(n)
                   for n in L.control_words(begin+' '+finish)):
            return None  # Layout-only wrappers retain the existing rendering rules.
        closing = L.find_env_end(text, start, env)
        guard = 'env:'+env
        if closing is None or guard in active:
            end = closing.end() if closing else len(text)
            return end, preserved.pack('environment', text[match.start():end], status='pending',
                                       reason='unresolved_environment_execution')
        self.groups.append(self.values.copy())
        replacement = self.run('\n'.join((begin, text[start:closing.start()], finish)),
                               active+(guard,), offset+start, math)
        self.values = self.groups.pop()
        return closing.end(), replacement

    def conditional(self, text, match, active, offset, math, unless=None):
        token = match[0]
        name = token[1:]
        decision, body_start = test(text, match, self.switches(),
                                    lambda key: self.defined(key, query_format=True), self.values)
        start = match.start() if unless is None else unless
        if unless is not None:
            decision = not decision if isinstance(decision, bool) else None
        cuts, end, close = bounds(text, body_start)
        branches = alternatives(text, body_start, cuts, end)
        separators = [cut[0] for cut in cuts]
        malformed = (close is None or separators.count('else') > 1 or
                     ('else' in separators and separators[-1] != 'else') or
                     ('or' in separators and name != 'ifcase'))
        pos = len(text) if close is None else close
        if decision is None or malformed:
            replacement = preserved.pack('conditional', text[start:pos], status='pending',
                reason='unbalanced_conditional' if malformed else 'undecidable_test',
                conditional=token, test=text[start:body_start], source_stage='conditional_input',
                offset=offset+start)
            self.merge_branches([branch for _, branch in branches], active)
        else:
            if name == 'ifcase':
                index = decision if 0 <= decision < len(branches)-1 else -1
            else:
                index = 0 if decision else -1
            branch_offset, branch = branches[index]
            replacement = self.run(branch, active, offset+branch_offset, math)
        return pos, replacement

    def run(self, text, active=(), offset=0, math=False):
        import latex2nl as L
        out, pos, cursor = [], 0, 0
        global_start = None
        spans = starts = None
        def in_math(at):
            nonlocal spans, starts
            if math:
                return True
            if spans is None:
                spans = L.find_math_spans(text, clean=False, inspect_arguments=True, report_unclosed=False)
                starts = [span.start for span in spans]
            i = bisect_right(starts, at)-1
            return i >= 0 and at < spans[i].end

        while (m := EXEC_TOKEN.search(text, pos)):
            pos = m.end()
            token, name = m[0], m[0][1:]
            start = m.start()
            globally = global_start is not None and not text[cursor:start].strip()
            if globally:
                start = global_start
            global_start = None
            replacement = None
            if token in MATH_CLOSE or token in (r'\)', r'\]'):
                if (token, len(self.groups)) == self.values.get('math_closer') and self.groups:
                    self.values = self.groups.pop()
                elif token in MATH_CLOSE:
                    self.groups.append(self.values.copy())
                    self.values['math_closer'] = (MATH_CLOSE[token], len(self.groups))
                continue
            if token == '{' or name in ('begingroup', 'bgroup'):
                self.groups.append(self.values.copy())
                continue
            if token == '}' or name in ('endgroup', 'egroup'):
                if self.groups:
                    self.values = self.groups.pop()
                continue
            if name in ('begin', 'end'):
                env = re.match(r'\s*\{([\w*@-]+)\}', text[pos:])
                if env:
                    pos += env.end()
                    if name == 'begin':
                        spec = self.values.get('env:'+env[1])
                        if isinstance(spec, tuple):
                            result = self.stateful_environment(text, m, pos, env[1], spec,
                                                               active, offset, in_math(m.start()))
                            if result:
                                pos, rendered = result
                                out.extend((text[cursor:m.start()], rendered))
                                cursor = pos
                                continue
                        self.groups.append(self.values.copy())
                    elif self.groups:
                        self.values = self.groups.pop()
                continue
            if name == 'global':
                out.append(text[cursor:start])
                cursor = m.end()
                global_start = start
                continue
            if name == 'DeclareMathOperator':
                head = L.MacroTable.DECLOP_RE.match(text, m.start())
                end = L.find_group_end(text, head.end()-1) if head else None
                if end is None:
                    continue
                key = head[1]
                value = Macro(0, r'\operatorname{' + text[head.end():end], None)
                self.assign(key, value, globally)
                self.managed.add(key)
                pos, replacement = end, ''
            elif name in DEFS:
                pos = definition_end(text, m)
                parsed = ordinary_definition(text, m, pos)
                if not parsed or parsed[0] in self.skip:
                    if name in ('newenvironment', 'renewenvironment'):
                        from normalize import read_env_decls
                        for key, spec in read_env_decls(text[m.start():pos]).items():
                            self.assign('env:'+key, spec)
                        env = re.match(r'\*?\s*\{([A-Za-z@]+)\}', text[m.end():])
                        if env:
                            self.assign(env[1], DECLARED)
                            self.assign('end'+env[1], DECLARED)
                    # An unsupported replacement or parameter syntax can still
                    # introduce a known binding, e.g. \edef or a delimited \def.
                    if name in ('def', 'gdef', 'edef', 'xdef'):
                        key = re.match(r'\s*\\([A-Za-z@]+)', text[m.end():])
                        if key:
                            self.assign(key[1], DECLARED, globally or name in ('gdef', 'xdef'))
                    continue
                key, spec = parsed
                self.managed.add(key)
                previous = self.defined(key, query_format=True) if name == 'providecommand' else False
                if previous is False:
                    self.assign(key, spec, globally or name == 'gdef')
                elif previous is None:
                    # It exists afterwards whichever binding won.
                    self.assign(key, DECLARED, globally)
                replacement = ''
            elif name == 'AtBeginDocument':
                hook = re.match(r'\s*\{', text[pos:])
                if hook:
                    first = pos + hook.end()
                    end = L.find_group_end(text, first-1)
                    if end is not None:
                        hooks = self.values.get('begin_hooks', ())
                        if hooks is not UNKNOWN:
                            self.assign('begin_hooks', hooks+(text[first:end-1],), globally=True)
                        pos, replacement = end, ''
            elif name == 'documentclass':
                arg = re.match(r'\s*(\[[^\]]*\]\s*)?\{([^{}]+)\}', text[pos:])
                if arg:
                    self.assign('class_profile', None if arg[1] else arg[2])
                    pos += arg.end()
                continue
            elif name in ('usepackage', 'RequirePackage'):
                arg = re.match(r'\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}', text[pos:])
                if arg:
                    for package in (v.strip() for v in arg[1].split(',')):
                        if package in self.packages:
                            self.load_package(package)
                        else:
                            self.assign('unread_source', True)
                    pos += arg.end()
                continue
            elif name in ('input', 'include', 'subfile'):
                # An include still present here was unavailable (or not inlined).
                self.assign('unread_source', True)
                pos = preserved.call_end(text, pos)
                replacement = preserved.pack('source_file', text[m.start():pos],
                    status='pending', reason='unread_include', source_stage='conditional_input')
            elif name == 'csname':
                depth = 1
                end = None
                for nested in TOKEN.finditer(text, pos):
                    if nested[0] == r'\csname':
                        depth += 1
                    elif nested[0] == r'\endcsname':
                        depth -= 1
                        if depth == 0:
                            end = nested
                            break
                key = text[pos:end.start()].strip() if end else ''
                pos = end.end() if end else len(text)
                target = self.values.get(key)
                if isinstance(target, Setter):
                    self.assign(target.switch, Switch(target.value), globally)
                    replacement = ''
                else:
                    # A computed control sequence can change state. A later
                    # branch must not use the old switch value as evidence.
                    for switch in self.switches():
                        self.assign(switch, Switch(None), globally=True)
                    replacement = preserved.pack('dynamic_command', text[m.start():pos],
                        status='pending', reason='unresolved_csname',
                        source_stage='conditional_input', offset=offset+m.start())
            elif name == 'let':
                alias = L.MacroTable.LET_RE.match(text, m.start())
                if alias and alias[1] not in self.skip:
                    key, target = alias[1], alias[2]
                    value = self.values.get(target, ABSENT)
                    if target not in self.values:
                        defined = self.defined(target, query_format=True)
                        if defined is not False:
                            value = ExternalAlias('\\'+target, defined)
                    self.assign(key, value, globally)
                    self.managed.add(key)
                    pos, replacement = alias.end(), ''
                else:
                    alias = L.MacroTable.LET_CHAR_RE.match(text, m.start())
                    if not alias:
                        continue
                    self.assign(alias[1], Macro(0, alias[2], None), globally)
                    self.managed.add(alias[1])
                    pos, replacement = alias.end(), ''
            elif name in ALLOCATORS:
                arg = REGISTER_DECL.match(text, pos)
                if arg:
                    key = arg[1] or arg[2]
                    self.assign(key, DECLARED, globally=True)
                    pos, replacement = arg.end(), ''
            elif name == 'newcounter':
                counter = re.match(r'\s*\{([A-Za-z@]+)\}', text[pos:])
                if counter:
                    for prefix in ('c@', 'the', 'p@', 'cl@'):
                        self.assign(prefix+counter[1], DECLARED, globally=True)
                    pos += counter.end()
            elif name == 'newtheorem':
                theorem = re.match(r'\*?\s*\{([A-Za-z@]+)\}', text[pos:])
                if theorem:
                    key = theorem[1]
                    self.assign(key, DECLARED, globally=True)
                    self.assign('end'+key, DECLARED, globally=True)
                    pos += theorem.end()
            elif name == 'newif':
                arg = re.match(r'\s*\\(if[A-Za-z@]+)', text[pos:])
                if not arg:
                    continue
                key = arg[1]
                self.declare_switch(key, self.switch_defaults.get(key, False), globally)
                pos += arg.end()
                replacement = ''
            elif isinstance(value := self.values.get(name), Setter):
                self.assign(value.switch, Switch(value.value), globally)
                replacement = ''
            elif isinstance(value, ExternalAlias):
                replacement = value.token
                if text[pos:pos+1].isalpha():
                    replacement += ' '
            elif isinstance(value, Macro):
                args = L.macro_arguments(text, pos, value.nargs, value.default)
                if args is None or self.keep(name, value, self) or name in active or len(active) >= 32 or self.expansions >= 50000:
                    # Keep the invocation together until its math/prose context is known.
                    if args is not None:
                        pos = args[1]
                    continue
                actuals, pos = args
                self.expansions += 1
                expanded = re.sub(r'#([1-9])', lambda h: actuals[int(h[1])-1]
                                  if int(h[1]) <= len(actuals) else h[0], value.body)
                # A zero-argument alias can forward the caller's remaining
                # arguments to another ordinary macro (\def\show{\render}).
                alias_names = set()
                while (alias := re.fullmatch(r'\\([A-Za-z@]+)', expanded)):
                    target = self.values.get(alias[1])
                    if not isinstance(target, Macro) or alias[1] in alias_names:
                        break
                    alias_names.add(alias[1])
                    if target.nargs == 0:
                        if not re.fullmatch(r"\\[A-Za-z@]+", target.body):
                            break
                        expanded = target.body
                        continue
                    forwarded = L.macro_arguments(text, pos, target.nargs, target.default)
                    if forwarded is not None:
                        _, after = forwarded
                        expanded += text[pos:after]
                        pos = after
                    break
                if globally:
                    expanded = r'\global ' + expanded
                replacement = self.run(expanded, active+(name,), offset+m.start(), in_math(m.start()))
                if replacement and replacement[0].isalpha() and re.search(r'\\[A-Za-z@]+$', text[:start]):
                    replacement = ' '+replacement
                if text[pos:pos+1].isalpha() and re.search(r'\\[A-Za-z@]+$', replacement):
                    replacement += ' '
            elif value is UNKNOWN and not opener(name):
                pos = preserved.call_end(text, pos)
                replacement = preserved.pack('prose_macro', text[m.start():pos], status='pending',
                    command=token, reason='conditional_definition', source_stage='conditional_input',
                    offset=offset+m.start())
            elif name == 'ifthenelse' and self.braced_test:
                args = L.macro_arguments(text, pos, 3, None)
                if args is None:
                    pos = len(text)
                    replacement = preserved.pack('conditional', text[m.start():], status='pending',
                        reason='malformed_ifthenelse', conditional=token)
                else:
                    (predicate, yes, no), pos = args
                    decision = self.braced_test(predicate)
                    if decision is None:
                        self.merge_branches([yes, no], active)
                        replacement = preserved.pack('conditional', text[m.start():pos], status='pending',
                            reason='undecidable_test', test=predicate, conditional=token)
                    else:
                        replacement = self.run(yes if decision == 1 else no, active,
                                               offset+m.start(), in_math(m.start()))
            elif name == 'unless':
                nxt = TOKEN.search(text, pos)
                if nxt and not text[pos:nxt.start()].strip() and opener(nxt[0][1:]):
                    pos, replacement = self.conditional(text, nxt, active, offset,
                                                        in_math(m.start()), m.start())
            elif opener(name):
                pos, replacement = self.conditional(text, m, active, offset, in_math(m.start()))
            elif (self.defer and name and name[0].isalpha() and self.defer(name)
                  and not in_math(m.start())):
                stop = preserved.call_end(text, pos)
                if stop > pos:
                    raw = text[m.start():stop]
                    # The argument of a missing macro might never execute.
                    # Keep it intact and merge the observable switch effects.
                    if any(isinstance(self.values.get(n), (Setter, Macro))
                           or n in DEFS for n in L.control_words(text[pos:stop])):
                        tail = text[pos:stop]
                        arguments = []
                        end_arg = 0
                        for group in re.finditer(r'[\[{]', tail):
                            if group.start() < end_arg:
                                continue
                            end_arg = L.find_group_end(tail, group.start()) or len(tail)
                            arguments.append(tail[group.end():end_arg-1])
                        self.merge_branches(['', *arguments, ''.join(arguments)], active)
                    replacement = '\ue200CALL'+str(len(self.held))+'\ue201'
                    self.held.append(raw)
                    pos = stop
            if replacement is not None:
                out.extend((text[cursor:m.start()] if not globally else '', replacement))
                cursor = pos
        out.append(text[cursor:])
        return ''.join(out)


def switch_option(value):
    match = re.fullmatch(r'(if[A-Za-z@]+)=(true|false)', value)
    if not match:
        raise ValueError('expected ifNAME=true or ifNAME=false')
    return match[1], match[2] == 'true'


def preserve_late_conditions(text):
    """A layout/legacy expansion can reveal a test after its scope is gone."""
    out, pos, cursor = [], 0, 0
    while (m := TOKEN.search(text, pos)):
        name = m[0][1:]
        pos = m.end()
        if name in DEFS:
            pos = definition_end(text, m)
            continue
        start = m.start()
        if name == 'unless':
            nxt = TOKEN.search(text, pos)
            if nxt and not text[pos:nxt.start()].strip() and opener(nxt[0][1:]):
                m, name = nxt, nxt[0][1:]
        if not opener(name):
            continue
        _, after = test(text, m, {}, lambda key: None)
        _, _, close = bounds(text, after)
        pos = close or len(text)
        out.extend((text[cursor:start], preserved.pack(
            'conditional', text[start:pos], conditional=m[0], source_stage='late_expansion',
            status='pending', reason='conditional_after_ordered_execution')))
        cursor = pos
    return ''.join(out) + text[cursor:]


def resolve(text, defined=lambda name: False, switches=None):
    execution = Execution(defined)
    for key, value in (switches or {}).items():
        execution.declare_switch(key, value)
    result = execution.run(text)
    if switches is not None:
        switches.update(execution.switches())
    return result


def read_switches(preamble):
    execution = Execution()
    execution.run(preamble)
    return execution.switches()
