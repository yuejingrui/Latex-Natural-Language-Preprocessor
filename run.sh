#!/bin/sh
# run.sh PAPER.tex OUT   -- normalize, split, verify; one exit code.
#   0 clean / 1 hard error / 2 broken artifact / 3 pending
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PYTHON:-python3}
PAPER=${1:?usage: run.sh PAPER.tex OUT}
OUT=${2:?usage: run.sh PAPER.tex OUT}
mkdir -p "$OUT" || exit 2
"$PY" "$HERE/scripts/normalize.py" "$PAPER" -o "$OUT/normalized.tex" \
    --stats "$OUT/normalize-stats.json" --paper-id "$(basename "$PAPER" .tex)" || exit 2
"$PY" "$HERE/scripts/latex2nl.py" "$OUT/normalized.tex" -o "$OUT" --canonical \
    --paper-id "$(basename "$PAPER" .tex)" || exit 2
exec "$PY" "$HERE/scripts/verify_output.py" "$OUT" --strict
