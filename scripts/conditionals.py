"""Bounded TeX conditional reader. Unknown tests are transported, never guessed.

This is not a TeX engine: registers, expansion in tests, modes, and ifx meanings
are deliberately unresolved. State is read in execution order, including groups.
"""
import re
from fractions import Fraction
import preserved

TOKEN = re.compile(r'\\[A-Za-z@]+|\\[^A-Za-z@]|[{}]')
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


def test(text, m, switches, defined):
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
    if name in ('ifnum','ifdim'):
        if name == 'ifnum':
            arg = re.match(r'\s*([+-]?\d+)\s*([<=>])\s*([+-]?\d+)(?![\d.])', text[pos:])
            if arg: a, op, b = int(arg[1]), arg[2], int(arg[3])
        else:
            arg = re.match(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(pt|pc|in|bp|cm|mm|sp)\s*([<=>])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(pt|pc|in|bp|cm|mm|sp)(?![A-Za-z])', text[pos:])
            if arg:
                # TeX dimensions are integer scaled points. Round each operand.
                a = round(Fraction(arg[1])*UNITS[arg[2]]*65536)
                b = round(Fraction(arg[4])*UNITS[arg[5]]*65536)
                op = arg[3]
        if arg: return {'<':a<b, '>':a>b, '=':a==b}[op], pos+arg.end()
    if name in ('ifodd','ifcase'):
        arg = re.match(r'\s*([+-]?\d+)(?![\d.])', text[pos:])
        if arg:
            n = int(arg[1])
            return (bool(n % 2) if name == 'ifodd' else n), pos+arg.end()
    return None, pos


def resolve(text, defined=lambda name: False, switches=None):
    state = switches if switches is not None else {}
    groups, out, pos, cursor = [], [], 0, 0
    global_next = False
    while (m := TOKEN.search(text, pos)):
        pos = m.end(); token = m.group(); name = token[1:]
        if token == '{' or name in ('begingroup','bgroup'):
            groups.append(dict(state)); continue
        if token == '}' or name in ('endgroup','egroup'):
            if groups: state.clear(); state.update(groups.pop())
            continue
        if name in DEFS:
            pos = definition_end(text, m); continue
        if name == 'global': global_next = True; continue
        if name == 'newif':
            arg = re.match(r'\s*\\(if[A-Za-z@]+)', text[pos:])
            if arg:
                state[arg[1]] = False
                out.append(text[cursor:m.start()]); cursor = pos = pos+arg.end()
            continue
        setting = re.fullmatch(r'([A-Za-z@]+)(true|false)', name)
        if setting and 'if'+setting[1] in state:
            key, value = 'if'+setting[1], setting[2] == 'true'
            state[key] = value
            if global_next:
                for group in groups: group[key] = value
            global_next = False
            out.append(text[cursor:m.start()]); cursor = pos
            continue
        global_next = False
        if not opener(name): continue
        decision, body_start = test(text, m, state, defined)
        cuts, end, close = bounds(text, body_start)
        raw = text[m.start():close if close is not None else len(text)]
        malformed = close is None or sum(c[0]=='else' for c in cuts)>1
        if name != 'ifcase' and any(c[0]=='or' for c in cuts): malformed = True
        out.append(text[cursor:m.start()])
        if decision is None or malformed:
            out.append(preserved.pack('conditional', raw, status='pending',
                       reason='unbalanced_conditional' if malformed else 'undecidable_test',
                       conditional='\\'+name, source_stage='conditional_input', offset=m.start()))
            # An unresolved branch can alter subsequent state. Never use an old
            # switch value as though those assignments could not have executed.
            for key in list(state):
                if re.search(r'\\'+re.escape(key[2:])+r'(?:true|false)(?![A-Za-z@])',raw):
                    state[key] = None
            for decl in re.finditer(r'\\newif\s*\\(if[A-Za-z@]+)', raw):
                state[decl[1]] = None
        else:
            if name == 'ifcase':
                ors = [c for c in cuts if c[0]=='or']
                otherwise = next((c for c in cuts if c[0]=='else'), None)
                stop = otherwise[1] if otherwise else end
                starts = [body_start]+[c[2] for c in ors if c[1]<stop]
                ends = [c[1] for c in ors if c[1]<stop]+[stop]
                inner = (text[starts[decision]:ends[decision]] if 0<=decision<len(starts)
                         else text[otherwise[2]:end] if otherwise else '')
            else:
                otherwise = next((c for c in cuts if c[0]=='else'), None)
                inner = (text[body_start:otherwise[1] if otherwise else end] if decision
                         else text[otherwise[2]:end] if otherwise else '')
            out.append(resolve(inner, defined, state))
        cursor = pos = close if close is not None else len(text)
    out.append(text[cursor:])
    return ''.join(out)


def read_switches(preamble):
    state = {}
    resolve(preamble, switches=state)
    return state
