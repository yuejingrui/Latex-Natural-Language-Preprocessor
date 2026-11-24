"""Distinguish optional formula labels from blocking questions in pipeline output."""
import json
from pathlib import Path

CONSTRUCTIONS = ("apply", "multiply", "label", "index")
ROLES = ("constraint", "proposition")


def read(out, name, default):
    path = Path(out) / name
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def label_values(kind, row, record):
    """Return (applied label, provisional suggestion). A queued answer alone is unapplied."""
    if kind == "construction":
        if row.get("conflict") or record.get("conflict"):
            return "", ""
        current = record.get("reading")
        suggested = record.get("provisional_reading") or record.get("default") or row.get("default_if_unanswered")
        allowed = CONSTRUCTIONS
    elif kind == "role":
        current = record.get("final")
        suggested = row.get("role_now") or record.get("role_now")
        allowed = ROLES
    elif kind == "slot":
        from latex2nl import SLOT_ANSWERS
        current = record.get("role")
        suggested = row.get("rule_answer") or row.get("default_if_unanswered")
        allowed = SLOT_ANSWERS
    else:
        return "", ""
    if current in allowed:
        return current, ""
    return "", suggested if suggested in allowed else ""


def queue_counts(out, *, before_ledger=False, splitting_only=False):
    """Counts per queue after applying the same label policy as the review page."""
    out = Path(out)
    queues = {}
    for path in sorted(out.glob("*_todo.json")):
        data = read(out, path.name, {})
        rows = data.get("items") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("invalid todo: " + path.name)
        queues[path.stem[:-5]] = rows

    entries = read(out, "paper_lexicon.json", {}).get("constructions", {}) if "construction" in queues else {}
    roles = read(out, "roles.json", {}).get("roles", {}) if "role" in queues else {}
    slots = {}
    if "slot" in queues and not splitting_only:
        forms = read(out, "formulas.json", [])
        for form in forms.get("formulas", []) if isinstance(forms, dict) else forms:
            for slot in form.get("text_slots", []):
                if slot.get("anchor"):
                    slots[slot["anchor"]] = slot
    counts = {}
    optional_roles = set()
    for kind, rows in queues.items():
        if kind == "human":
            continue
        required = 0
        for row in rows:
            if kind == "slot" and splitting_only:
                continue
            if kind == "role":
                record = roles.get(row.get("fid"), {})
            elif kind == "construction":
                record = entries.get(row.get("key"), {})
            else:
                record = slots.get(row.get("anchor"), {}) if kind == "slot" else {}
            if any(label_values(kind, row, record)):
                if kind == "role" and row.get("fid"):
                    optional_roles.add(row["fid"])
            else:
                required += 1
        counts[kind + "_todo.json"] = (required, len(rows) - required)
    if "human" in queues:
        rows = queues["human"]
        required = sum(1 for row in rows if not (
            (before_ledger and row.get("source") == "issues.json") or
            (row.get("source") == "role_todo" and row.get("kind") == "role"
             and row.get("detail") in optional_roles)))
        counts["human_todo.json"] = (required, len(rows) - required)
    return counts
