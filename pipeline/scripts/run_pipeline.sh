#!/bin/sh
# run_pipeline.sh PAPER.tex [OUTDIR] [--no-ask] [--no-names]
#
# One paper, whole chain: normalise -> split -> lexicon -> ask what is open
# (constructions, slots, boundaries) -> apply -> names -> roles -> gate.
# Every asking step resumes from its answer file, so re-running is cheap.
set -u

PAPER=""
OUT=""
ASK=1
NAMES=1
for arg in "$@"; do
  case "$arg" in
    --no-ask)   ASK=0 ;;
    --no-names) NAMES=0 ;;
    -*)         echo "run_pipeline: unknown option $arg" >&2; exit 2 ;;
    *)          if [ -z "$PAPER" ]; then PAPER="$arg"
                elif [ -z "$OUT" ]; then OUT="$arg"
                else echo "run_pipeline: too many arguments: $arg" >&2; exit 2; fi ;;
  esac
done
[ -n "$PAPER" ] || { echo "usage: run_pipeline.sh PAPER.tex [OUTDIR] [--no-ask] [--no-names]" >&2; exit 2; }

NAME=$(basename "$PAPER" .tex)
OUT="${OUT:-runs/$NAME}"
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
cd "$ROOT" || exit 1
PY=python3

step() { printf '\n=== %s\n' "$*"; }
need() { [ -f "$1" ] || { echo "  (no $1: skipped)"; return 1; }; return 0; }

mkdir -p "$OUT"

pipeline() {
  step "normalize"
  $PY scripts/normalize.py "$PAPER" -o "$OUT/normalized.tex" \
      --stats "$OUT/normalize-stats.json" --paper-id "$NAME" \
      --issues "$OUT/issues_normalize.json" || return 1

  step "latex2nl --canonical (applying the answers already on disk)"
  set -- --canonical --paper-id "$NAME"
  if [ -f "$OUT/sentence_answers.json" ]; then
    set -- "$@" --merge-sentence-answers "$OUT/sentence_answers.json"
  fi
  if [ -f "$OUT/slot_answers.json" ]; then
    [ -f "$OUT/slot_roles.json" ] || cp data/slot_roles.json "$OUT/slot_roles.json"
    set -- "$@" --merge-slot-answers "$OUT/slot_answers.json" \
                 --slot-table "$OUT/slot_roles.json"
  fi
  $PY scripts/latex2nl.py "$OUT/normalized.tex" -o "$OUT" \
      --issues "$OUT/issues_latex2nl.json" "$@" || {
    _rc=$?
    # 3 means "content kept, pending"; 1/2 are real failures
    [ "$_rc" -eq 3 ] || return 1
  }

  step "lexicon"
  set -- "$OUT/formulas.json" -o "$OUT/paper_lexicon.json" --paper-id "$NAME"
  if [ -f "$OUT/construction_answers.json" ]; then
    set -- "$@" --merge-answers "$OUT/construction_answers.json"
  fi
  $PY pipeline/scripts/lexicon.py "$@" || return 1
}

pipeline || exit 1

if [ "$ASK" -eq 1 ]; then
  step "ask: construction readings"
  if need "$OUT/construction_todo.json"; then
    $PY pipeline/scripts/ask_lexicon.py "$OUT/construction_todo.json" || true
  fi
  step "ask: text-slot roles"
  if need "$OUT/slot_todo.json"; then
    $PY pipeline/scripts/ask_slots.py "$OUT/slot_todo.json" || true
  fi
  step "ask: doubtful sentence boundaries"
  if need "$OUT/sentence_todo.json"; then
    $PY pipeline/scripts/ask_boundaries.py "$OUT/sentence_todo.json" \
        -o "$OUT/sentence_answers.json" || true
  fi
  # apply what was just produced before judging anything downstream
  if [ -f "$OUT/construction_answers.json" ] || [ -f "$OUT/slot_answers.json" ] \
     || [ -f "$OUT/sentence_answers.json" ]; then
    step "apply the answers"
    pipeline || exit 1
  fi
fi

# ---- the name layer ------------------------------------------------------
if [ "$ASK" -eq 1 ] && [ "$NAMES" -eq 1 ]; then
  step "names: what neither TeX nor the paper defines"
  if $PY - "$OUT" "$NAME" <<'PY'
import json, os, re, sys
out, paper = sys.argv[1], sys.argv[2]
lex = json.load(open(os.path.join(out, "paper_lexicon.json"), encoding="utf-8"))
doc = json.load(open(os.path.join(out, "formulas.json"), encoding="utf-8"))
recs = doc["formulas"] if isinstance(doc, dict) else doc
by_id = {r["id"]: (r.get("expanded") or "") for r in recs}

# R1: a name can show up in a formula, in the prose, or in the unresolved log;
# the question used to read only the formulas, so \step (16 uses of prose) was
# never asked.
found = {}                      # name -> {"count", "scope", "examples"}


def add(name, count, scope, example):
    key = name.lstrip("\\")
    slot = found.setdefault(key, {"count": 0, "scope": set(), "examples": []})
    slot["count"] += count
    slot["scope"].add(scope)
    if example and len(slot["examples"]) < 2:
        slot["examples"].append(example)


for name, sites in (lex.get("unknown_commands") or {}).items():
    add(name, len(sites), "math", by_id.get(sites[0], ""))
stats_path = os.path.join(out, "normalize-stats.json")
if os.path.exists(stats_path):
    stats = json.load(open(stats_path, encoding="utf-8"))
    for name, count in (stats.get("outside_vocabulary") or {}).items():
        add(name, count, "text", "")
# a macro kept unexpanded because its body reaches an unknown name: the name is
# the only thing downstream can ask about (OPEN-PROBLEMS.md 7.1 item 3)
kept = (json.load(open(stats_path, encoding="utf-8")).get("kept_macros") or {}
        if os.path.exists(stats_path) else {})
for name, body in kept.items():
    add("\\" + name, 1, "text", body)

unres_path = os.path.join(out, "unresolved.log")
if os.path.exists(unres_path):
    for line in open(unres_path, encoding="utf-8"):
        m = re.search(r"commands not understood: (.+)$", line)
        if not m:
            continue
        for name in m.group(1).split(","):
            add(name.strip(), 1, "math", line.split(":")[0])

if not found:
    print("  none")
    sys.exit(1)
items = []
for name, slot in sorted(found.items(), key=lambda kv: -kv[1]["count"]):
    items.append({
        "name": name,
        "count": slot["count"],
        "scope": ",".join(sorted(slot["scope"])),
        "papers": 1,
        "paper": paper,          # one paper: keep the answer local to it
        "shape": "",
        "examples": slot["examples"],
        "action": None, "class": None, "body": None, "note": None,
    })
payload = {
    "allowed_actions": ["allow", "font", "unwrap", "ignore", "expand", "unknown"],
    "allowed_classes": ["const", "letter", "font", "named_operator", "fence",
                        "accent", "text", "binop", "relation", "bigop",
                        "open", "close", "separator", ""],
    "items": items,
}
json.dump(payload, open(os.path.join(out, "names_todo.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
print("  %d name(s): %s" % (len(items), ", ".join("\\" + i["name"] for i in items)))
sys.exit(0)
PY
  then
    $PY pipeline/scripts/pending_names.py --answer-with-model "$OUT/names_todo.json" || true
    $PY pipeline/scripts/pending_names.py --merge "$OUT/names_todo.json" || true
    step "re-run the pipeline so the name answers take effect"
    pipeline || exit 1
    # Q1: the queue is removed only when the names are really gone, and the
    # ones the model could not decide go to a person
    $PY pipeline/scripts/human.py --from-names "$OUT/names_todo.json" \
        "$OUT/human_todo.json" || true
    if $PY - "$OUT" <<'PY'
import json, os, sys
out = sys.argv[1]
lex = json.load(open(os.path.join(out, "paper_lexicon.json"), encoding="utf-8"))
stats = {}
path = os.path.join(out, "normalize-stats.json")
if os.path.exists(path):
    stats = json.load(open(path, encoding="utf-8")).get("outside_vocabulary") or {}
sys.exit(0 if (lex.get("unknown_commands") or {}) or stats else 1)
PY
    then
      echo "  names still open: keeping names_todo.json"
    else
      rm -f "$OUT/names_todo.json"
    fi
  fi
fi

step "formula roles"
$PY pipeline/scripts/roles.py "$OUT/formulas.json" "$OUT/sentences.jsonl" \
    -o "$OUT/roles.json" --todo "$OUT/role_todo.json" --paper-id "$NAME" || exit 1
if [ "$ASK" -eq 1 ] && need "$OUT/role_todo.json"; then
  $PY pipeline/scripts/ask_roles.py --answer "$OUT/role_todo.json" || true
  # one retry: a block the model skipped often comes back the second time
  if $PY - "$OUT/role_todo.json" <<'PY'
import json, sys
items = (json.load(open(sys.argv[1], encoding="utf-8")).get("items") or [])
sys.exit(0 if any(not i.get("answer") for i in items) else 1)
PY
  then
    $PY pipeline/scripts/ask_roles.py --answer "$OUT/role_todo.json" || true
  fi
  $PY pipeline/scripts/roles.py "$OUT/formulas.json" "$OUT/sentences.jsonl" \
      -o "$OUT/roles.json" --todo "$OUT/role_todo.json" --paper-id "$NAME" \
      --merge-answers "$OUT/role_todo.json" || true
  $PY pipeline/scripts/human.py --from-roles "$OUT/role_todo.json" \
      "$OUT/human_todo.json" || true
fi


step "fidelity gate"
# this run's directory, not its parent: the parent holds every other run too
$PY pipeline/scripts/verify_formulas.py "$OUT" >/dev/null 2>&1
VF=$?
echo "  verify_formulas rc=$VF"

step "output checks (GAPS.md C1-C14)"
$PY scripts/verify_output.py "$OUT" --strict >"$OUT/output_checks.txt" 2>&1
VO=$?
tail -3 "$OUT/output_checks.txt"

$PY pipeline/scripts/human.py --from-issues "$OUT/issues.json" \
    "$OUT/human_todo.json" || true

# an empty question file is not a pending question
if [ -f "$OUT/human_todo.json" ]; then
  H=$($PY -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print(len(d.get("items") or []))' "$OUT/human_todo.json" 2>/dev/null || echo 0)
  [ "$H" = "0" ] && rm -f "$OUT/human_todo.json"
fi

PEND=0
for f in construction_todo.json slot_todo.json sentence_todo.json \
         role_todo.json names_todo.json human_todo.json \
         content_todo.json conditional_todo.json; do
  [ -f "$OUT/$f" ] && PEND=1
done

case "$VO" in
  0) ;;
  3) PEND=1 ;;
  *) VO_BAD=1 ;;
esac
VO_BAD=${VO_BAD:-0}
if [ "$VF" -ne 0 ] || [ "$VO_BAD" -ne 0 ]; then
  STATUS="failed"
elif [ "$PEND" -ne 0 ]; then
  STATUS="questions pending"
else
  STATUS="clean"
fi

step "summary"
echo "  status         : $STATUS"
$PY - "$OUT" <<'PY'
import json, os, sys
out = sys.argv[1]
sents = sum(1 for _ in open(os.path.join(out, "sentences.jsonl"), encoding="utf-8"))
doc = json.load(open(os.path.join(out, "formulas.json"), encoding="utf-8"))
recs = doc["formulas"] if isinstance(doc, dict) else doc
lex = json.load(open(os.path.join(out, "paper_lexicon.json"), encoding="utf-8"))
cons = lex.get("constructions") or {}
def count(name):
    path = os.path.join(out, name)
    if not os.path.exists(path):
        return "-"
    d = json.load(open(path, encoding="utf-8"))
    return len(d.get("items") or d.get("formulas") or [])
open_cons = sum(1 for c in cons.values() if not c.get("reading"))
open_total = open_cons + sum(
    len(json.load(open(os.path.join(out, n), encoding="utf-8")).get("items") or [])
    for n in ("construction_todo.json", "slot_todo.json", "sentence_todo.json",
              "role_todo.json", "names_todo.json")
    if os.path.exists(os.path.join(out, n)))
print("  open questions :", open_total, "(provisional readings below)")
print("  sentences      :", sents)
print("  formulas       :", len(recs))
print("  constructions  :", len(cons),
      "| open:", sum(1 for c in cons.values() if not c.get("reading")))
print("  answers        : constructions %s, slots %s, boundaries %s"
      % (count("construction_answers.json"), count("slot_answers.json"),
         count("sentence_answers.json")))
print("  unknown names  :", ", ".join(lex.get("unknown_commands") or {}) or "none")
human_path = os.path.join(out, "human_todo.json")
if os.path.exists(human_path):
    h = json.load(open(human_path, encoding="utf-8"))
    print("  human table    :", sum((h.get("counts") or {}).values()), "item(s)",
          h.get("counts") or {})
unresolved = os.path.join(out, "unresolved.log")
if os.path.exists(unresolved):
    print("  unresolved     :", sum(1 for _ in open(unresolved, encoding="utf-8")))
for name in ("construction_todo.json", "slot_todo.json", "sentence_todo.json",
             "role_todo.json", "names_todo.json", "human_todo.json"):
    print("  %-22s %s" % (name, "pending" if os.path.exists(os.path.join(out, name)) else "-"))
PY

case "$STATUS" in
failed) exit 1 ;;
questions\ pending) exit 3 ;;
esac
exit 0
