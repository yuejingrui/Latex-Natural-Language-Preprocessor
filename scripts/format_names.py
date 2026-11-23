r"""Check initial LaTeX name bindings for an otherwise undecidable \ifdefined.

The reference job contains only a standard format/class and literal name
queries. No document, local style, command body or class option is executed.
"""
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=64)
def _probe(engine, profile, names):
    job = [r'\documentclass{' + profile + '}'] if profile != 'kernel' else []
    for i, name in enumerate(names):
        job.append(r'\ifcsname ' + name + r'\endcsname\typeout{NLDEF-' + str(i)
                   + r'-1}\else\typeout{NLDEF-' + str(i) + r'-0}\fi')
    job.append(r'\csname @@end\endcsname')
    try:
        with tempfile.TemporaryDirectory(prefix='nlprep-format-') as tmp:
            source = Path(tmp, 'names.tex')
            source.write_text('\n'.join(job), encoding='ascii')
            result = subprocess.run([engine, '-no-shell-escape', '-halt-on-error',
                                     '-interaction=batchmode', source.name],
                                    cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=10, check=False)
            if result.returncode:
                return {}
            log = source.with_suffix('.log').read_text('ascii', errors='replace')
    except (OSError, subprocess.TimeoutExpired):
        return {}
    rows = re.findall(r'^NLDEF-(\d+)-([01])$', log, re.M)
    return {names[int(i)]: bit == '1' for i, bit in rows if int(i) < len(names)}


def lookup(profile, names, name):
    if profile not in ('kernel', 'article', 'report', 'book') or name not in names:
        return None
    # Never emit user-supplied TeX tokens into the probe.
    if not all(re.fullmatch('[A-Za-z@]+', n) for n in names):
        return None
    # Without a specified engine, an engine-dependent name (e.g. \directlua)
    # is not evidence for either branch. A failed probe is not a negative.
    answers = {_probe(path, profile, names).get(name)
               for engine in ('pdflatex', 'xelatex', 'lualatex')
               if (path := shutil.which(engine))}
    return answers.pop() if len(answers) == 1 else None
