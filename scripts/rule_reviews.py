"""Record an independent model opinion alongside a decisive rule."""
import json
from pathlib import Path


def compare(rule, decision, answer, source, model=None):
    return {"rule": rule, "rule_answer": decision, "model_answer": answer,
            "model": model or source,
            "status": "confirmed" if decision == answer else
                      "undecided" if answer == "unsure" else "disagreement"}


def from_model(source):
    return (source or "").lower().startswith(("model", "llm", "assistant:"))


def report(directory):
    out = Path(directory)
    def read(name):
        with (out / name).open(encoding="utf-8") as fh:
            return json.load(fh)
    rows = []
    def append(kind, location, record):
        if record.get("model_review"):
            rows.append({"kind": kind, "location": location, **record["model_review"]})
    for formula in read("formulas.json"):
        for slot in formula.get("text_slots", []):
            append("slot", slot["id"], slot)
    for key, entry in read("paper_lexicon.json")["constructions"].items():
        append("construction", entry.get("location", key), entry)
    for fid, record in read("roles.json")["roles"].items():
        append("role", fid, record)
    path = out / "rule_reviews.json"
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"items": rows}, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)
    for row in rows:
        if row["status"] == "disagreement":
            applied = row["model_answer"] if row.get("applied_source") == "model" else row["rule_answer"]
            print("WARNING %s %s: rule %s proposed %s; model proposed %s; applied %s. Check this occurrence."
                  % (row["kind"], row["location"], row["rule"], row["rule_answer"], row["model_answer"], applied))
    print("Rule/model comparisons: %d confirmed, %d disagreements, %d undecided -> %s"
          % (*(sum(row["status"] == status for row in rows)
               for status in ("confirmed", "disagreement", "undecided")), path))


if __name__ == "__main__":
    import sys
    report(sys.argv[1])
