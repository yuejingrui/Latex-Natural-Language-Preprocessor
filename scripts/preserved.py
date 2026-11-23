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
