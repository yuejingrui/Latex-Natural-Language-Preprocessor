#!/bin/sh
# run_pipeline_llm.sh PAPER.tex OUTDIR [--dry-run] [--limit N] [--no-ask] [--tex-engine ENGINE] [--model-config FILE]
#
# One paper through the whole chain *including* the model questions:
#
#   normalize -> latex2nl -> lexicon -> ask -> apply -> formula roles -> checks
#
# DepCCG is not part of this chain and is never called here.
#
#   --dry-run   print the questions and write no answer; makes no request
#   --limit N   ask at most N questions per class (a cheap trial run)
#   --no-ask    run all offline stages, including formula roles
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

USAGE='usage: run_pipeline_llm.sh PAPER.tex OUTDIR [--dry-run] [--limit N] [--no-ask] [--tex-engine ENGINE] [--model-config FILE]'
ENGINE=""
PAPER=""
OUT=""
DRY=0
LIMIT=""
ASK=1
while [ $# -gt 0 ]; do
    case "$1" in
        --model-config) shift; [ -n "${1:-}" ] || exit 2; NL_PREP_MODEL_CONFIG=$1 ;;
        --tex-engine) shift; [ -n "${1:-}" ] || exit 2; ENGINE=$1 ;;
        --tex-engine=?*) ENGINE=${1#*=} ;;
        --dry-run) DRY=1 ;;
        --no-ask)  ASK=0 ;;
        --limit)   shift; [ -n "${1:-}" ] || { echo "--limit requires a positive integer" >&2; exit 2; }; LIMIT=$1 ;;
        --limit=) echo "--limit requires a positive integer" >&2; exit 2 ;;
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


. "$(dirname "$0")/pipeline_common.sh" || exit 2
prepare_pipeline || exit 2
if [ -n "$LIMIT" ]; then
    LIMIT=$("$PY" -c 'import argparse
from pipeline.scripts.pending_names import positive_count
parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=positive_count, required=True)
print(parser.parse_args().limit)' --limit="$LIMIT") || exit 2
fi

step() { printf '\n=== %s\n' "$*"; }
open_questions() {   # how many items in a question file are still unanswered
    "$PY" - "$1" <<'PY' 2>/dev/null || echo "?"
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
[ -n "$LIMIT" ] && LIMIT_ARG="--limit $LIMIT"
DRY_ARG=""
[ "$DRY" -eq 1 ] && DRY_ARG="--dry-run"

# ---- 0. the backend, before anything is asked -----------------------------
if [ "$ASK" -eq 1 ] && [ "$DRY" -eq 0 ]; then
    step "model backend"
    check_model_config || exit 2
fi

# ---- 1. split, then the word tables --------------------------------------
pipeline() {
    step "normalize"
    normalize_paper || return 1

    step "latex2nl --canonical"
    set -- --canonical --paper-id "$NAME" --issues "$OUT/issues_latex2nl.json"
    if [ -f "$OUT/sentence_answers.json" ]; then
        set -- "$@" --merge-sentence-answers "$OUT/sentence_answers.json"
    fi
    if [ -f "$OUT/slot_answers.json" ]; then
        set -- "$@" --merge-slot-answers "$OUT/slot_answers.json"
    fi
    "$PY" scripts/latex2nl.py "$OUT/normalized.tex" -o "$OUT" "$@" || return 1

    step "lexicon"
    set -- "$OUT/formulas.json" -o "$OUT/paper_lexicon.json" --paper-id "$NAME"
    if [ -f "$OUT/construction_answers.json" ]; then
        set -- "$@" --merge-answers "$OUT/construction_answers.json"
    fi
    "$PY" pipeline/scripts/lexicon.py "$@" || return 1
    return 0
}

pipeline || exit 1

# ---- 2. the three splitter questions -------------------------------------
if [ "$ASK" -eq 1 ]; then
    step "ask: construction readings"
    if [ -f "$OUT/construction_questions.json" ]; then
        echo "  open: $(open_questions "$OUT/construction_questions.json")"
        "$PY" pipeline/scripts/ask_lexicon.py "$OUT/construction_questions.json" \
            $DRY_ARG $LIMIT_ARG || true
    else
        echo "  (no construction_todo.json: skipped)"
    fi

    step "ask: text-slot roles"
    if [ -f "$OUT/slot_questions.json" ]; then
        echo "  open: $(open_questions "$OUT/slot_questions.json")"
        "$PY" pipeline/scripts/ask_slots.py "$OUT/slot_questions.json" \
            $DRY_ARG $LIMIT_ARG || true
    else
        echo "  (no slot_todo.json: skipped)"
    fi

    step "ask: doubtful sentence boundaries"
    if [ -f "$OUT/sentence_todo.json" ]; then
        echo "  open: $(open_questions "$OUT/sentence_todo.json")"
        "$PY" pipeline/scripts/ask_boundaries.py "$OUT/sentence_todo.json" \
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
step "formula roles"
"$PY" pipeline/scripts/roles.py "$OUT/formulas.json" "$OUT/sentences.jsonl" \
    -o "$OUT/roles.json" --todo "$OUT/role_todo.json" --paper-id "$NAME" || exit 1
if [ "$ASK" -eq 1 ]; then
    if [ "$DRY" -eq 0 ]; then
        if [ -f "$OUT/role_questions.json" ] && [ "$(open_questions "$OUT/role_questions.json")" != "0" ]; then
            echo "  open: $(open_questions "$OUT/role_questions.json")"
            "$PY" pipeline/scripts/ask_roles.py --answer "$OUT/role_questions.json" \
                $LIMIT_ARG || true
            "$PY" pipeline/scripts/roles.py "$OUT/formulas.json" "$OUT/sentences.jsonl" \
                -o "$OUT/roles.json" --todo "$OUT/role_todo.json" --paper-id "$NAME" \
                --merge-answers "$OUT/role_questions.json" || exit 1
        else
            echo "  (nothing to ask)"
        fi
    else
        if [ -f "$OUT/role_questions.json" ]; then
            cp "$OUT/role_questions.json" "$OUT/roles_preview.json" || exit 1
            echo "  (dry run: the questions are in $OUT/roles_preview.json)"
        else
            rm -f "$OUT/roles_preview.json"
        fi
    fi
fi

# ---- 4. the two gates ------------------------------------------------------
"$PY" scripts/rule_reviews.py "$OUT" || exit 1
"$PY" pipeline/scripts/human.py --from-names "$OUT/names_todo.json" "$OUT/human_todo.json" || exit 1
"$PY" pipeline/scripts/human.py --from-roles "$OUT/role_todo.json" "$OUT/human_todo.json" || exit 1

step "fidelity gate: the formula table itself"
"$PY" pipeline/scripts/verify_formulas.py "$OUT"
VF=$?
echo "  verify_formulas rc=$VF"

step "output checks"
"$PY" scripts/verify_output.py "$OUT" --strict >"$OUT/output_checks.txt" 2>&1
VO=$?
echo "  verify_output --strict rc=$VO   (0 clean / 1 hard / 2 broken / 3 pending)"
tail -3 "$OUT/output_checks.txt" | sed 's/^/  /'

"$PY" pipeline/scripts/human.py --from-issues "$OUT/issues.json" "$OUT/human_todo.json" || exit 1

step "sentences that end without a terminal mark, by cause"
if "$PY" scripts/split_audit.py --run "$OUT" -o "$OUT/split-audit.md" >/dev/null 2>&1; then
    sed -n '/^## 汇总/,/^## /p' "$OUT/split-audit.md" \
        | grep -E '^\| (论文|句子|无句末标点|标点|容器|标题|句末|裸花括号|疑似|原文缺|其它)' \
        | sed 's/^/  /'
else
    echo "  (split_audit failed; the run itself is not affected)"
fi

# ---- 5. what came out -----------------------------------------------------
step "summary"
"$PY" - "$OUT" <<'PY'
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
issues = load("issues.json") or {}
pending = issues.get("pending") or []
print("  ledger         : status=%s hard=%s" % (issues.get("status"), issues.get("hard")))
print("  splitter pending: %s" % (", ".join(pending) if pending else "nothing"))
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
pipeline_queue_status
queue_status=$?
case "$queue_status" in 0|3) ;; *) exit 1 ;; esac
if [ "$VO" -eq 3 ] || [ "$queue_status" -eq 3 ]; then
    echo "  status         : questions pending"
    exit 3
fi
echo "  status         : clean"
exit 0
