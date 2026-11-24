#!/bin/sh
# run_pipeline_llm.sh PAPER.tex OUTDIR [--dry-run] [--limit N] [--no-ask]
#
# One paper through the whole chain *including* the model questions:
#
#   normalize -> latex2nl -> lexicon -> ask -> apply -> formula roles -> checks
#
# DepCCG is not part of this chain and is never called here.
#
#   --dry-run   print the questions and write no answer; makes no request
#   --limit N   ask at most N questions per class (a cheap trial run)
#   --no-ask    split only, no request (the offline chain)
#
# Every stage resumes: an answer file already in OUTDIR is reused and its
# questions are not asked again.  Delete the file to ask that class again.
#
# Exit code: 0 no hard error and nothing open, 3 questions pending, 1 failed,
# 2 bad usage, missing key or missing artefact.
#
# The name layer ("a command neither TeX nor the paper defines") is built by
# run_pipeline.sh collects those names from the lexicon, the normalize stats
# and unresolved.log before asking; this script asks the four question classes
# that have an asker of their own.  Use run_pipeline.sh when a paper leaves
# name questions open (see the summary line names_todo.json).
set -u

USAGE='usage: run_pipeline_llm.sh PAPER.tex OUTDIR [--dry-run] [--limit N] [--no-ask]'
PAPER=""
OUT=""
DRY=0
LIMIT=0
ASK=1
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --no-ask)  ASK=0 ;;
        --limit)   shift; LIMIT=${1:-0} ;;
        --limit=*) LIMIT=${1#--limit=} ;;
        -h|--help) echo "$USAGE"; exit 0 ;;
        -*)        echo "run_pipeline_llm: unknown option $1" >&2; exit 2 ;;
        *)         if [ -z "$PAPER" ]; then PAPER=$1
                   elif [ -z "$OUT" ]; then OUT=$1
                   else echo "run_pipeline_llm: too many arguments: $1" >&2; exit 2; fi ;;
    esac
    shift
done
if [ -z "$PAPER" ] || [ -z "$OUT" ]; then echo "$USAGE" >&2; exit 2; fi
case "$LIMIT" in ''|*[!0-9]*) LIMIT=0 ;; esac

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
cd "$ROOT" || exit 2
PY=${PYTHON:-python3}
NAME=$(basename "$PAPER" .tex)
mkdir -p "$OUT" || exit 2

step() { printf '\n=== %s\n' "$*"; }
open_questions() {   # how many items in a question file are still unanswered
    $PY - "$1" <<'PY' 2>/dev/null || echo "?"
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        items = (json.load(fh) or {}).get("items") or []
except (OSError, ValueError):
    print("?"); raise SystemExit
print(sum(1 for i in items if not i.get("answer")))
PY
}
LIMIT_ARG=""
[ "$LIMIT" -gt 0 ] && LIMIT_ARG="--limit $LIMIT"
DRY_ARG=""
[ "$DRY" -eq 1 ] && DRY_ARG="--dry-run"

# ---- 0. the backend, before anything is asked -----------------------------
if [ "$ASK" -eq 1 ] && [ "$DRY" -eq 0 ]; then
    step "model backend"
    if ! $PY pipeline/scripts/pending_names.py --check-config >"$OUT/backend.txt" 2>&1; then
        echo "  cannot read the backend configuration; see $OUT/backend.txt" >&2
        exit 2
    fi
    if ! grep -q 'NL_PREP_API_KEY=sk-' "$OUT/backend.txt"; then
        echo "  no API key: set NL_PREP_API_KEY or write it to ~/.config/nlprep/env" >&2
        echo "  (--dry-run prints the questions and needs no key)" >&2
        exit 2
    fi
    sed -E 's/(NL_PREP_API_KEY=sk-..)[^ ]*/\1***/' "$OUT/backend.txt" | sed -n '1,3p' | sed 's/^/  /'
fi

# ---- 1. split, then the word tables --------------------------------------
pipeline() {
    step "normalize"
    $PY scripts/normalize.py "$PAPER" -o "$OUT/normalized.tex" \
        --stats "$OUT/normalize-stats.json" --paper-id "$NAME" \
        --issues "$OUT/issues_normalize.json" || return 1

    step "latex2nl --canonical"
    set -- --canonical --paper-id "$NAME" --issues "$OUT/issues_latex2nl.json"
    if [ -f "$OUT/sentence_answers.json" ]; then
        set -- "$@" --merge-sentence-answers "$OUT/sentence_answers.json"
    fi
    if [ -f "$OUT/slot_answers.json" ]; then
        [ -f "$OUT/slot_roles.json" ] || cp data/slot_roles.json "$OUT/slot_roles.json"
        set -- "$@" --merge-slot-answers "$OUT/slot_answers.json" \
                     --slot-table "$OUT/slot_roles.json"
    fi
    $PY scripts/latex2nl.py "$OUT/normalized.tex" -o "$OUT" "$@" || return 1

    step "lexicon"
    set -- "$OUT/formulas.json" -o "$OUT/paper_lexicon.json" --paper-id "$NAME"
    if [ -f "$OUT/construction_answers.json" ]; then
        set -- "$@" --merge-answers "$OUT/construction_answers.json"
    fi
    $PY pipeline/scripts/lexicon.py "$@" || return 1
    return 0
}

pipeline || exit 1

# ---- 2. the three splitter questions -------------------------------------
if [ "$ASK" -eq 1 ]; then
    step "ask: construction readings"
    if [ -f "$OUT/construction_todo.json" ]; then
        echo "  open: $(open_questions "$OUT/construction_todo.json")"
        $PY pipeline/scripts/ask_lexicon.py "$OUT/construction_todo.json" \
            $DRY_ARG $LIMIT_ARG || true
    else
        echo "  (no construction_todo.json: skipped)"
    fi

    step "ask: text-slot roles"
    if [ -f "$OUT/slot_todo.json" ]; then
        echo "  open: $(open_questions "$OUT/slot_todo.json")"
        $PY pipeline/scripts/ask_slots.py "$OUT/slot_todo.json" \
            $DRY_ARG $LIMIT_ARG || true
    else
        echo "  (no slot_todo.json: skipped)"
    fi

    step "ask: doubtful sentence boundaries"
    if [ -f "$OUT/sentence_todo.json" ]; then
        echo "  open: $(open_questions "$OUT/sentence_todo.json")"
        $PY pipeline/scripts/ask_boundaries.py "$OUT/sentence_todo.json" \
            -o "$OUT/sentence_answers.json" $DRY_ARG $LIMIT_ARG || true
    else
        echo "  (no sentence_todo.json: skipped)"
    fi

    if [ "$DRY" -eq 0 ] && { [ -f "$OUT/construction_answers.json" ] \
         || [ -f "$OUT/slot_answers.json" ] || [ -f "$OUT/sentence_answers.json" ]; }; then
        step "apply the answers: the pipeline runs again"
        pipeline || exit 1
    fi
fi

# ---- 3. formula roles ------------------------------------------------------
if [ "$ASK" -eq 1 ]; then
    step "formula roles"
    $PY pipeline/scripts/roles.py "$OUT/formulas.json" "$OUT/sentences.jsonl" \
        -o "$OUT/roles.json" --todo "$OUT/role_todo.json" --paper-id "$NAME" || exit 1
    if [ "$DRY" -eq 0 ]; then
        if [ -f "$OUT/role_todo.json" ] && [ "$(open_questions "$OUT/role_todo.json")" != "0" ]; then
            echo "  open: $(open_questions "$OUT/role_todo.json")"
            $PY pipeline/scripts/ask_roles.py --answer "$OUT/role_todo.json" \
                $LIMIT_ARG || true
            $PY pipeline/scripts/roles.py "$OUT/formulas.json" "$OUT/sentences.jsonl" \
                -o "$OUT/roles.json" --todo "$OUT/role_todo.json" --paper-id "$NAME" \
                --merge-answers "$OUT/role_todo.json" || true
        else
            echo "  (nothing to ask)"
        fi
    else
        $PY pipeline/scripts/ask_roles.py --export "$OUT/roles_preview.json" \
            "$OUT/formulas.json" >/dev/null 2>&1 || true
        echo "  (dry run: the questions are in $OUT/roles_preview.json)"
    fi
fi

# ---- 4. the two gates ------------------------------------------------------
step "fidelity gate: the formula table itself"
$PY pipeline/scripts/verify_formulas.py "$OUT"
VF=$?
echo "  verify_formulas rc=$VF"

step "output checks"
$PY scripts/verify_output.py "$OUT" --strict >"$OUT/output_checks.txt" 2>&1
VO=$?
echo "  verify_output --strict rc=$VO   (0 clean / 1 hard / 2 broken / 3 pending)"
tail -3 "$OUT/output_checks.txt" | sed 's/^/  /'

step "sentences that end without a terminal mark, by cause"
if $PY scripts/split_audit.py --run "$OUT" -o "$OUT/split-audit.md" >/dev/null 2>&1; then
    sed -n '/^## 汇总/,/^## /p' "$OUT/split-audit.md" \
        | grep -E '^\| (论文|句子|无句末标点|标点|容器|标题|句末|裸花括号|疑似|原文缺|其它)' \
        | sed 's/^/  /'
else
    echo "  (split_audit failed; the run itself is not affected)"
fi

# ---- 5. what came out -----------------------------------------------------
step "summary"
$PY - "$OUT" <<'PY'
import json, os, sys
out = sys.argv[1]

def load(name):
    try:
        with open(os.path.join(out, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None

state = load("split_status.json") or {}
counts = state.get("counts") or {}
sents = counts.get("sentences")
if sents is None:
    try:
        sents = sum(1 for _ in open(os.path.join(out, "sentences.jsonl"), encoding="utf-8"))
    except OSError:
        sents = "-"
print("  sentences      : %s" % sents)
print("  formulas       : %s" % counts.get("formulas", "-"))
print("  content blocks : %s" % counts.get("content_blocks", "-"))
for name in ("construction_todo.json", "slot_todo.json", "sentence_todo.json",
             "role_todo.json", "names_todo.json"):
    data = load(name)
    if not isinstance(data, dict):
        continue
    items = data.get("items") or []
    done = sum(1 for i in items if i.get("answer"))
    print("  %-22s %d answered, %d open" % (name, done, len(items) - done))
issues = load("issues.json") or {}
pending = issues.get("pending") or []
print("  ledger         : status=%s hard=%s" % (issues.get("status"), issues.get("hard")))
print("  pending        : %s" % (", ".join(pending) if pending else "nothing"))
PY

VO_BAD=0
case "$VO" in
    0|3) ;;
    *) VO_BAD=1 ;;
esac
if [ "$VF" -ne 0 ] || [ "$VO_BAD" -ne 0 ]; then
    echo "  status         : FAILED"
    exit 1
fi
if [ "$VO" -eq 3 ]; then
    echo "  status         : questions pending"
    exit 3
fi
echo "  status         : clean"
exit 0
