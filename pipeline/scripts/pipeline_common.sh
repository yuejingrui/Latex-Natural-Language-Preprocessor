# Shared setup and acceptance for the two paper runners. Sourced before cd.
prepare_pipeline() {
    PY=${PYTHON:-python3}
    export PYTHONUTF8=1
    HERE=$(cd "$(dirname "$0")" && pwd -P) || return 2
    ROOT=$(cd "$HERE/../.." && pwd -P) || return 2
    [ -f "$PAPER" ] || { echo "missing input: $PAPER" >&2; return 2; }
    PAPER_DIR=$(cd "$(dirname "$PAPER")" && pwd -P) || return 2
    PAPER="$PAPER_DIR/$(basename "$PAPER")"
    mkdir -p "$OUT" || return 2
    OUT=$(cd "$OUT" && pwd -P) || return 2
    [ ! "$PAPER" -ef "$OUT/normalized.tex" ] || {
        echo "output would overwrite the input: $PAPER" >&2; return 2;
    }
    if [ -n "${NL_PREP_MODEL_CONFIG:-}" ]; then
        NL_PREP_MODEL_CONFIG=$("$PY" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$NL_PREP_MODEL_CONFIG") || return 2
        export NL_PREP_MODEL_CONFIG
    fi
    cd "$ROOT" || return 2

    if [ -n "$ENGINE" ]; then
        case "$ENGINE" in pdflatex|xelatex|lualatex) ;; *) echo "unknown TeX engine: $ENGINE" >&2; return 2 ;; esac
        command -v "$ENGINE" >/dev/null 2>&1 || { echo "$ENGINE is unavailable" >&2; return 2; }
    else
        for candidate in pdflatex xelatex lualatex; do
            if command -v "$candidate" >/dev/null 2>&1; then ENGINE=$candidate; break; fi
        done
    fi
    if [ -n "$ENGINE" ]; then
        command -v pdftotext >/dev/null 2>&1 || { echo "$ENGINE needs pdftotext on PATH" >&2; return 2; }
        echo "TeX execution: $ENGINE"
    else
        echo "No TeX engine on PATH; using static source analysis." >&2
    fi
}

normalize_paper() {
    set -- "$PAPER" -o "$OUT/normalized.tex" --stats "$OUT/normalize-stats.json" \
        --hash-paper --issues "$OUT/issues_normalize.json"
    [ -z "$ENGINE" ] || set -- "$@" --tex-engine "$ENGINE"
    "$PY" scripts/normalize.py "$@" || return 1
    NAME=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["paper_id"])' \
        "$OUT/normalize-stats.json") || return 1
    if [ -f "$OUT/names_todo.json" ]; then
        "$PY" - "$OUT/names_todo.json" "$NAME" <<'PYTHON' || return 1
import json, os, sys
path, paper = sys.argv[1:]
with open(path, encoding="utf-8") as fh:
    items = json.load(fh)["items"]
if not items or any(it.get("paper") != paper for it in items):
    os.remove(path)
PYTHON
    fi
}

pipeline_has_pending() {
    for queue in construction slot sentence role names human content conditional; do
        [ ! -f "$OUT/${queue}_todo.json" ] || return 0
    done
    return 1
}


check_model_config() {
    "$PY" pipeline/scripts/pending_names.py --check-config >"$OUT/backend.txt" 2>&1
    rc=$?
    cat "$OUT/backend.txt"
    return "$rc"
}
