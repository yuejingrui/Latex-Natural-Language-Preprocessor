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
ENV_HOOKS = {'AtBeginEnvironment': 'begin', 'AtEndEnvironment': 'end'}
HOOKS = {'AtBeginDocument', 'AtEndDocument', 'AfterEndPreamble', 'AddToHook', 'AddToHookNext',
         'BeforeBeginEnvironment', 'AfterEndEnvironment', *ENV_HOOKS}
ENV_ARG = re.compile(r'\s*\{([\w*@-]+)\}')


def hook_spec(name, text, pos, stop):
    """Return the target and exact source range of a literal callback body."""
    phase = {'AtBeginDocument': 'begin', 'AtEndDocument': 'end',
             'AfterEndPreamble': 'late_begin'}.get(name)
    if phase is None:
        arg = re.compile(r'\s*\{([^{}]+)\}').match(text, pos, stop)
        if not arg:
            return None
        pos = arg.end()
        if name in ENV_HOOKS and re.fullmatch(r'[\w*@-]+', arg[1]):
            phase = 'env/' + arg[1] + '/' + ENV_HOOKS[name]
        elif name in ('AddToHook', 'AddToHookNext'):
            phase = {'begindocument':'begin', 'begindocument/end':'late_begin',
                     'enddocument':'end'}.get(arg[1])
            if re.fullmatch(r'env/[\w*@-]+/(?:begin|end)', arg[1]):
                phase = arg[1]
    group = re.compile(r'\s*\{').match(text, pos, stop)
    if phase and group:
        import latex2nl as L
        end = L.find_group_end(text, group.end()-1)
        if end is not None and end <= stop:
            return phase, group.end(), end
    return None


def comment_extent(text, start, env):
    """comment.sty reads physical lines and requires a literal closing line."""
    newline = text.find('\n', start)
    if newline >= 0:
        closing = re.search(r'(?m)^\\end\{' + re.escape(env) + r'\}[ \t]*$', text[newline+1:])
        if closing:
            return newline+1, newline+1+closing.start(), newline+1+closing.end()
    return None


class UnknownExecution(ValueError):
    """The uncertainty can affect the remainder of the document."""


@dataclass(frozen=True)
class UnexpandedDef:
    source: str


@dataclass(frozen=True)
class TokenRegister:
    body: str = ''


@dataclass(frozen=True)
class SavedBox:
    body: str = ''


SIMPLE_CMD = re.compile(r'\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)'
                        r'\*?\s*(?:\{\s*\\([A-Za-z@]+)\s*\}|\\([A-Za-z@]+))'
                        r'\s*(?:\[(\d)\]\s*)?(?:\[([^\]]*)\]\s*)?\{')
SIMPLE_DEF = re.compile(r'\\(?:g?def)\s*\\([A-Za-z@]+)((?:\s*#[1-9])*)\s*\{')
EXPANDED_HEAD = re.compile(r'\\[ex]def\s*\\([A-Za-z@]+)\s*\{')
CONTROL_WORD = re.compile(r'\\[A-Za-z@]+')
TOKEN_SPACE = re.compile(r'[ \t]*(?:\n[ \t]*)?')
WORD_END = re.compile(r'(?<!\\)(?:\\\\)*\\[A-Za-z@]+$')


def substitute(body, args):
    """Substitute already-tokenized arguments without fusing control words."""
    parts, pos = [], 0
    def append(piece):
        if piece:
            if parts and re.match(r'[A-Za-z@]', piece) and WORD_END.search(parts[-1]):
                parts.append(' ')
            parts.append(piece)
    for m in re.finditer(r'\\.|#([1-9])', body):
        if m[1] and int(m[1]) <= len(args):
            append(body[pos:m.start()])
            append(args[int(m[1])-1])
            pos = m.end()
    append(body[pos:])
    return ''.join(parts)


def freeze_tokens(text, values, active=(), budget=None):
    r"""Expand ordinary macros at definition time; \noexpand quotes one token."""
    import latex2nl as L
    if '#' in text or len(active) >= 32:
        return None
    budget = [0] if budget is None else budget
    out, pos = [], 0
    while m := TOKEN.search(text, pos):
        out.append(text[pos:m.start()])
        token, pos = m[0], m.end()
        if not token.startswith('\\'):
            out.append(token)
            continue
        if CONTROL_WORD.fullmatch(token):
            pos = TOKEN_SPACE.match(text, pos).end()
        if token == r'\noexpand':
            if pos == len(text):
                return None
            nxt = TOKEN.match(text, pos)
            token = nxt[0] if nxt else text[pos]
            pos += len(token)
            if CONTROL_WORD.fullmatch(token):
                pos = TOKEN_SPACE.match(text, pos).end()
                token += ' '  # token delimiter when the stored body is read back
            out.append(token)
            continue
        value = values.get(token[1:])
        budget[0] += 1
        if not isinstance(value, Macro) or token in active or budget[0] > 1024:
            return None
        body = value.body
        if value.nargs:
            args = L.macro_arguments(text, pos, value.nargs, value.default)
            if args is None:
                return None
            actuals, pos = args
            if any(int(k) > len(actuals) for k in re.findall(r'#([1-9])', body)):
                return None
            body = substitute(body, actuals)
            if len(body) > 100000:
                return None
        expansion = freeze_tokens(body, values, active+(token,), budget)
        if expansion is None:
            return None
        out.append(expansion)
    return ''.join(out) + text[pos:]


def ordinary_definition(text, match, end, values):
    if match[0] in (r'\edef', r'\xdef'):
        head = EXPANDED_HEAD.match(text, match.start())
        if head and text[end-1:end] == '}':
            body = freeze_tokens(text[head.end():end-1], values)
            if body is not None:
                return head[1], Macro(0, body, None)
        return None
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
        self.document_active = False
        self.ended = False
        self.hook_source = ''
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

    def enter_group(self):
        self.groups.append(self.values.copy())
        self.values.pop('tex:aftergroup', None)

    def leave_group(self):
        delayed = self.values.get('tex:aftergroup', ())
        self.values = self.groups.pop()
        if delayed is UNKNOWN:
            return preserved.pack('conditional', r'\aftergroup', status='pending',
                                  reason='undecidable_delayed_tokens')
        return ''.join(t + ' ' if CONTROL_WORD.fullmatch(t) else t for t in delayed)

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
            if self.ended:
                raise UnknownExecution('uncertain_document_end')
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

    def begin_document(self, preamble, body, initial_packages=(), source=None):
        self.hook_source = preamble or body
        try:
            for package in initial_packages:
                self.load_package(package)
            self.run(preamble)
            self.document_active = True
            opening = self.fire_hooks('begin') + self.fire_hooks('late_begin')
            content = self.run(body)
            self.ended = False
            self.document_active = False
            return opening + content + self.fire_hooks('end')
        except UnknownExecution as exc:
            return preserved.pack('tex_execution', source if source is not None else preamble + '\n' + body, status='pending',
                                  reason=str(exc))

    def fire_hooks(self, phase):
        out = []
        for prefix in ('hook:', 'next_hook:'):
            key = prefix + phase
            hooks = self.values.get(key, ())
            if prefix == 'next_hook:' and hooks:
                self.assign(key, (), globally=True)
            if hooks is UNKNOWN:
                for name in self.switches():
                    self.assign(name, Switch(None), globally=True)
                out.append(preserved.pack('conditional', self.hook_source, status='pending',
                                          reason='undecidable_hook_registration'))
            else:
                out.extend(self.run(hook) for hook in hooks)
        return ''.join(out)

    def stateful_environment(self, text, match, pos, env, spec, active, offset, math, callback):
        """Execute state-changing begin/end code around the actual body."""
        import latex2nl as L
        nargs, default, begin, finish = spec
        parsed = L.macro_arguments(text, pos, nargs, default)
        if parsed is None:
            return None
        args, start = parsed
        begin = substitute(begin, args)
        literal_wrapper = bool(nargs or begin.strip() or finish.strip()) and not L.control_words(spec[2] + finish)
        if not literal_wrapper and not any(isinstance(self.values.get(n), (Setter, Macro)) or n in DEFS
                   or n in ('global', 'newif', 'let', 'csname') or opener(n)
                   for n in L.control_words(begin+' '+finish)):
            return None  # Layout-only wrappers retain the existing rendering rules.
        closing = L.find_env_end(text, start, env)
        guard = 'env:'+env
        if closing is None or guard in active:
            end = closing.end() if closing else len(text)
            return end, preserved.pack('environment', text[match.start():end], status='pending',
                                       reason='unresolved_environment_execution')
        replacement = callback + self.run(begin + '\n' + text[start:closing.start()],
                               active+(guard,), offset+start, math)
        replacement += self.fire_hooks('env/'+env+'/end')
        replacement += self.run(finish, active+(guard,), offset+closing.start(), math)
        if literal_wrapper:
            replacement = text[match.start():pos] + '\n' + replacement + '\n' + closing[0]
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
        def insert_delayed(at, delayed):
            nonlocal text, spans, starts
            if delayed:
                text = text[:at] + delayed + text[at:]
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

        while not self.ended and (m := EXEC_TOKEN.search(text, pos)):
            pos = m.end()
            token, name = m[0], m[0][1:]
            start = m.start()
            globally = global_start is not None and not text[cursor:start].strip()
            if globally:
                start = global_start
            global_start = None
            replacement = None
            mode = self.values.get('tex:globaldefs', 0)
            if mode is UNKNOWN:
                raise UnknownExecution('uncertain_assignment_scope')
            global_assignment = mode > 0 or (mode == 0 and globally)
            if token in MATH_CLOSE or token in (r'\)', r'\]'):
                if (token, len(self.groups)) == self.values.get('math_closer') and self.groups:
                    insert_delayed(pos, self.leave_group())
                elif token in MATH_CLOSE:
                    self.enter_group()
                    self.values['math_closer'] = (MATH_CLOSE[token], len(self.groups))
                continue
            if token == '{' or name in ('begingroup', 'bgroup'):
                self.enter_group()
                continue
            if token == '}' or name in ('endgroup', 'egroup'):
                if self.groups:
                    insert_delayed(pos, self.leave_group())
                continue
            if name in ('begin', 'end'):
                env = re.match(r'\s*\{([\w*@-]+)\}', text[pos:])
                if env:
                    pos += env.end()
                    if name == 'end' and env[1] == 'document' and self.document_active:
                        self.ended = True
                        out.append(text[cursor:start])
                        break
                    if name == 'begin':
                        mode = self.values.get('comment:'+env[1])
                        if mode is not None:
                            extent = comment_extent(text, pos, env[1])
                            first, last, pos = extent or (pos, len(text), len(text))
                            if mode is UNKNOWN or not extent:
                                contents = preserved.pack('environment', text[start:pos], status='pending',
                                                          reason='unresolved_comment_environment')
                            else:
                                contents = self.run(text[first:last], active, offset+first, math) if mode else ''
                            out.extend((text[cursor:start], contents))
                            cursor = pos
                            continue
                        phase = 'env/'+env[1]+'/begin'
                        has_hook = self.values.get('hook:'+phase) or self.values.get('next_hook:'+phase)
                        self.enter_group()
                        callback = self.fire_hooks(phase) if has_hook else ''
                        spec = self.values.get('env:'+env[1])
                        if isinstance(spec, tuple):
                            result = self.stateful_environment(text, m, pos, env[1], spec,
                                                               active, offset, in_math(m.start()), callback)
                            if result:
                                pos, rendered = result
                                insert_delayed(pos, self.leave_group())
                                out.extend((text[cursor:m.start()], rendered))
                                cursor = pos
                                continue
                        if callback:
                            if isinstance(spec, tuple) and spec[0] == 0:
                                out.extend((text[cursor:pos], callback))
                                cursor = pos
                            else:
                                out.extend((text[cursor:start], callback))
                                cursor = start
                    elif self.groups:
                        callback = self.fire_hooks('env/'+env[1]+'/end')
                        if callback:
                            out.extend((text[cursor:start], callback))
                            cursor = start
                        insert_delayed(pos, self.leave_group())
                continue
            if name == 'global':
                out.append(text[cursor:start])
                cursor = m.end()
                global_start = start
                continue
            if name == 'aftergroup':
                pos = TOKEN_SPACE.match(text, pos).end()
                if pos < len(text):
                    arg = TOKEN.match(text, pos)
                    delayed = arg[0] if arg else text[pos]
                    pos += len(delayed)
                    if CONTROL_WORD.fullmatch(delayed):
                        pos = TOKEN_SPACE.match(text, pos).end()
                    if self.groups:
                        queue = self.values.get('tex:aftergroup', ())
                        if queue is not UNKNOWN:
                            self.values['tex:aftergroup'] = queue+(delayed,)
                replacement = ''
            elif name == 'globaldefs':
                arg = re.match(r'\s*=?\s*([+-]?\d{1,10})(?!\d)', text[pos:])
                if not arg:
                    raise UnknownExecution('unresolved_globaldefs_value')
                pos += arg.end()
                following = CONTROL_WORD.match(text, pos)
                if following and following[0][1:] not in (DEFS | {'global', 'relax', 'let', 'globaldefs'}):
                    raise UnknownExecution('unresolved_globaldefs_numeric_suffix')
                self.assign('tex:globaldefs', int(arg[1]), global_assignment)
                replacement = ''
            elif name == 'DeclareMathOperator':
                head = L.MacroTable.DECLOP_RE.match(text, m.start())
                end = L.find_group_end(text, head.end()-1) if head else None
                if end is None:
                    continue
                key = head[1]
                value = Macro(0, r'\operatorname{' + text[head.end():end], None)
                self.assign(key, value, global_assignment)
                self.managed.add(key)
                pos, replacement = end, ''
            elif name in DEFS:
                pos = definition_end(text, m)
                parsed = ordinary_definition(text, m, pos, self.values)
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
                            value = UnexpandedDef(text[m.start():pos]) if name in ('edef', 'xdef') else DECLARED
                            if isinstance(value, UnexpandedDef):
                                self.managed.add(key[1])
                            self.assign(key[1], value, global_assignment or
                                        (name in ('gdef', 'xdef') and mode != UNKNOWN and mode >= 0))
                    continue
                key, spec = parsed
                self.managed.add(key)
                previous = self.defined(key, query_format=True) if name == 'providecommand' else False
                if previous is False:
                    self.assign(key, spec, global_assignment or
                                (name in ('gdef', 'xdef') and mode != UNKNOWN and mode >= 0))
                elif previous is None:
                    # It exists afterwards whichever binding won.
                    self.assign(key, DECLARED, global_assignment)
                replacement = ''
            elif name in HOOKS:
                spec = hook_spec(name, text, pos, len(text))
                if not spec:
                    raise UnknownExecution('unmapped_hook_registration')
                phase, first, pos = spec
                key = ('next_hook:' if name == 'AddToHookNext' else 'hook:') + phase
                hooks = self.values.get(key, ())
                if hooks is not UNKNOWN:
                    self.assign(key, hooks+(text[first:pos-1],), globally=True)
                replacement = ''
            elif name in ('excludecomment', 'includecomment'):
                arg = ENV_ARG.match(text, pos)
                if arg:
                    self.assign('comment:'+arg[1], name == 'includecomment', global_assignment)
                    pos, replacement = arg.end(), ''
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
                import issues
                pos = preserved.call_end(text, pos)
                issues.record('human:source_error', 'conditionals.Execution.run',
                              where=text[m.start():pos], size=0)
                if not self.document_active:
                    raise UnknownExecution('unread_setup_include')
                self.assign('unread_source', True)
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
                    self.assign(target.switch, Switch(target.value), global_assignment)
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
                    self.assign(key, value, global_assignment)
                    self.managed.add(key)
                    pos, replacement = alias.end(), ''
                else:
                    alias = L.MacroTable.LET_CHAR_RE.match(text, m.start())
                    if not alias:
                        continue
                    self.assign(alias[1], Macro(0, alias[2], None), global_assignment)
                    self.managed.add(alias[1])
                    pos, replacement = alias.end(), ''
            elif name in ALLOCATORS:
                arg = REGISTER_DECL.match(text, pos)
                if arg:
                    key = arg[1] or arg[2]
                    binding = {'newtoks': TokenRegister(), 'newsavebox': SavedBox()}.get(name, DECLARED)
                    self.assign(key, binding, globally=True)
                    pos, replacement = arg.end(), ''
            elif name in ('sbox', 'usebox') and (arg := REGISTER_DECL.match(text, pos)):
                key = arg[1] or arg[2]
                box = self.values.get(key)
                if not isinstance(box, SavedBox):
                    raise UnknownExecution('unresolved_box_register')
                pos = arg.end()
                if name == 'usebox':
                    replacement = box.body
                else:
                    contents = L.macro_arguments(text, pos, 1, None)
                    if not contents:
                        raise UnknownExecution('unbounded_box_contents')
                    (body,), pos = contents
                    # A box is typeset at storage time. Reusing literal text and
                    # math is exact; replaying executable commands at use time is not.
                    if re.search(r'[\\#%&~]|[\r\n]\s*[\r\n]', body):
                        raise UnknownExecution('unresolved_box_construction')
                    self.assign(key, SavedBox(body), global_assignment)
                    replacement = ''
            elif isinstance(self.values.get(name), SavedBox):
                raise UnknownExecution('unmapped_box_operation')
            elif isinstance(self.values.get(name), TokenRegister):
                arg = re.compile(r'\s*=?\s*\{').match(text, pos)
                end = L.find_group_end(text, arg.end()-1) if arg else None
                if end is None:
                    raise UnknownExecution('unresolved_token_register_assignment')
                self.assign(name, TokenRegister(text[arg.end():end-1]), global_assignment)
                pos, replacement = end, ''
            elif name == 'the' and (arg := re.compile(r'\s*\\([A-Za-z@]+)').match(text, pos)):
                stored = self.values.get(arg[1])
                if isinstance(stored, TokenRegister):
                    if arg[1] in active:
                        raise UnknownExecution('recursive_token_register')
                    pos, replacement = arg.end(), self.run(stored.body, active+(arg[1],), offset+start, in_math(start))
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
                self.declare_switch(key, self.switch_defaults.get(key, False), global_assignment)
                pos += arg.end()
                replacement = ''
            elif isinstance(value := self.values.get(name), Setter):
                self.assign(value.switch, Switch(value.value), global_assignment)
                replacement = ''
            elif isinstance(value, UnexpandedDef):
                # Its invocation may contain a global setter. A stale initial
                # value cannot decide later branches, even after group exit.
                for key in self.switches():
                    self.assign(key, Switch(None), globally=True)
                pos = preserved.call_end(text, pos)
                replacement = preserved.pack('prose_macro', text[m.start():pos], status='pending',
                                             reason='unresolved_expanded_definition', definition=value.source)
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
                expanded = substitute(value.body, actuals)
                # A zero-argument alias can forward the caller's remaining
                # arguments to another ordinary macro (\def\show{\render}).
                alias_names = set()
                while (alias := re.fullmatch(r'\\([A-Za-z@]+)\s*', expanded)):
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
            elif name == 'IfFileExists':
                args = L.macro_arguments(text, pos, 3, None)
                if args is None:
                    raise UnknownExecution('unbounded_file_test')
                (_, yes, no), pos = args
                self.merge_branches([yes, no], active)
                replacement = preserved.pack('conditional', text[start:pos], conditional=token,
                                             status='pending', reason='unobserved_file_lookup')
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
        if not self.ended:
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
