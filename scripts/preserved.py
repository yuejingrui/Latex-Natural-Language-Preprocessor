"""Lossless transport for content which must survive normalization passes.

The canonical marker carries UTF-8 JSON as hex (no TeX syntax in the payload).
It travels in normalized.tex itself, so moving the file or crossing process
boundaries cannot silently detach a footnote or an unresolved conditional.
Offsets in payloads refer to the indicated processing stage, not original files.
"""
import json
import re

MARKER = re.compile(r"\\nlprepcontent\{([0-9a-f]+)\}")


def pack(kind, raw, **fields):
    record = dict(version=1, kind=kind, raw=raw, **fields)
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode('utf-8').hex()
    return r'\nlprepcontent{' + encoded + '}'


def unpack(match):
    record = json.loads(bytes.fromhex(match.group(1)).decode('utf-8'))
    if record.get('version') != 1 or not isinstance(record.get('raw'), str):
        raise ValueError('invalid preserved content record')
    return record


def mask(text, prefix='BLOCK'):
    """Shield existing capsules from code that rewrites plain numeric groups."""
    records = []
    def sub(m):
        records.append(m.group(0))
        return '\ue200' + prefix + str(len(records)-1) + '\ue201'
    return MARKER.sub(sub, text), records


def restore(text, records, prefix='BLOCK'):
    return re.sub('\ue200' + re.escape(prefix) + r'(\d+)\ue201',
                  lambda m: records[int(m.group(1))], text)


def restore_calls(text, calls):
    """Restore held calls after notes and floats store their source and previews."""
    if not calls:
        return text
    def in_block(match):
        block = unpack(match)
        for key in ('raw', 'body'):
            if key in block:
                block[key] = restore(block[key], calls, 'CALL')
        if 'previews' in block:
            block['previews'] = [restore(s, calls, 'CALL') for s in block['previews']]
        block.pop('version')
        return pack(**block)
    return restore(MARKER.sub(in_block, text), calls, 'CALL')


def call_end(text, pos):
    """Lexical extent only: arity is unknowable without the definition."""
    from latex2nl import find_group_end
    if text[pos:pos+1] == '*':
        pos += 1
    while True:
        group = re.match(r'[ \t]*(?:\n[ \t]*)?([\[{])', text[pos:])
        if not group:
            return pos
        pos = find_group_end(text, pos + group.end()-1)
        if pos is None:
            return len(text)


def locate(text, sources):
    """Attach literal source matches. Repeated identical calls remain ambiguous."""
    if not sources or not MARKER.search(text):
        return text
    from bisect import bisect_right
    from latex2nl import strip_comments
    indexed = {}
    for path, source in sources.items():
        origins = []
        clean = strip_comments(source, origins)
        starts = [0] + [m.end() for m in re.finditer('\n', clean)]
        indexed[path] = (clean, starts, origins, source.splitlines(keepends=True))

    def annotate(match):
        block = unpack(match)
        if block['kind'] not in ('prose_macro', 'conditional') or 'source_matches' in block:
            return match[0]
        raw = block['raw']
        locations = []
        for path, (clean, starts, origins, lines) in indexed.items():
            for hit in re.finditer(re.escape(raw), clean):
                first = bisect_right(starts, hit.start())-1
                last = bisect_right(starts, hit.end()-1)-1
                col = hit.start()-starts[first]
                end_col = hit.end()-starts[last]
                lo, hi = origins[first]-1, origins[last]-1
                source_raw = (lines[lo][col:end_col] if lo == hi else
                              lines[lo][col:] + ''.join(lines[lo+1:hi]) + lines[hi][:end_col])
                site = dict(file=path, line=lo+1, column=col+1)
                if source_raw != raw:
                    site['source_raw'] = source_raw
                locations.append(site)
        if not locations:
            return match[0]
        block['source_matches'] = locations
        block.pop('version')
        return pack(**block)
    return MARKER.sub(annotate, text)
