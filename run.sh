#!/bin/sh
# run.sh PAPER.tex OUT [--tex-engine ENGINE]
#
# The whole splitting chain in one command:
#   normalize -> latex2nl -> verify_output
# and one exit code: 0 clean / 1 hard error / 2 broken artifact / 3 pending.
#
# Both stages write their own ledger, so issues.json carries the whole paper and
# not just what verify_output noticed.
#
# --tex-engine pdflatex|xelatex|lualatex
#   Let the paper's own TeX decide which branches are active, which files are
#   included and where it ends, instead of reading the source statically.  The
#   engine and pdftotext have to be on PATH.  Without the option the chain stays
#   static and needs nothing but Python.
#
#   If the build or the replay cannot determine the body, the paper is not half
#   split: it is preserved whole as one pending content block, the reason is
#   printed here, and the exit code is 3.  Fix the build (a missing .sty, say)
#   rather than reading the sentence count as a result.
set -u

usage='usage: run.sh PAPER.tex OUT [--tex-engine ENGINE]'
PAPER=""
OUT=""
ENGINE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --tex-engine)
            [ $# -ge 2 ] || { echo "run.sh: --tex-engine needs a value" >&2; exit 2; }
            ENGINE=$2; shift 2 ;;
        --tex-engine=*)
            ENGINE=${1#--tex-engine=}; shift ;;
        -h|--help)
            echo "$usage"; exit 0 ;;
        -*)
            echo "run.sh: unknown option $1" >&2; exit 2 ;;
        *)
            if [ -z "$PAPER" ]; then PAPER=$1
            elif [ -z "$OUT" ]; then OUT=$1
            else echo "run.sh: too many arguments: $1" >&2; exit 2; fi
            shift ;;
    esac
done
if [ -z "$PAPER" ] || [ -z "$OUT" ]; then echo "$usage" >&2; exit 2; fi

HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PYTHON:-python3}
NAME=$(basename "$PAPER" .tex)
mkdir -p "$OUT" || exit 2

if [ -n "$ENGINE" ]; then
    command -v "$ENGINE" >/dev/null 2>&1 || {
        echo "run.sh: --tex-engine $ENGINE: not on PATH" >&2
        echo "run.sh: drop the option to split the source statically" >&2
        exit 2
    }
    command -v pdftotext >/dev/null 2>&1 || {
        echo "run.sh: --tex-engine needs pdftotext on PATH (poppler-utils)" >&2
        exit 2
    }
fi

set -- "$PAPER" -o "$OUT/normalized.tex" \
    --stats "$OUT/normalize-stats.json" \
    --issues "$OUT/issues_normalize.json" \
    --paper-id "$NAME"
[ -n "$ENGINE" ] && set -- "$@" --tex-engine "$ENGINE"
"$PY" "$HERE/scripts/normalize.py" "$@" || exit 2

# A failed build is not an error here -- the body is preserved and marked -- but
# it must not look like a normal run either.
if [ -n "$ENGINE" ] && [ -f "$OUT/execution.json" ]; then
    "$PY" - "$OUT/execution.json" <<'PY' || true
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    report = json.load(fh)
status = report.get("status")
if status == "partial":
    sites = ", ".join(map(str, report.get("pending_sites") or []))
    print("run.sh: TeX execution partial; unresolved sites: " + sites +
          ". See content_todo.json for the preserved passages.", file=sys.stderr)
elif status == "pending":
    print("run.sh: TeX execution %s: %s" % (status, report.get("reason") or "no reason given"),
          file=sys.stderr)
    print("run.sh: the paper is preserved whole as pending content; see content_todo.json",
          file=sys.stderr)
PY
fi

"$PY" "$HERE/scripts/latex2nl.py" "$OUT/normalized.tex" -o "$OUT" --canonical \
    --issues "$OUT/issues_latex2nl.json" --paper-id "$NAME" || exit 2

exec "$PY" "$HERE/scripts/verify_output.py" "$OUT" --strict
