"""Paper-scoped decisions shared by the local reviewer and pipeline stages."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

FILENAME = "manual_reviews.sqlite3"
ROLE_FIELDS = ("sid", "unit", "sentence", "window", "formula")


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def role_key(fid, row):
    return digest([fid, {k: row.get(k) for k in ROLE_FIELDS}])


def block_key(row):
    return digest([row.get(k) for k in ("id", "kind", "raw", "reason", "anchor_ref")])


def construction_keys(entries):
    # The bare two-letter word and the adjacency at that very position describe
    # one decision. A confirmation must not leave the other view contradictory.
    pairs = {(v.get("location"), v.get("shape", k)): v.get("anchor") for k,v in entries.items()}
    keys = {}
    for k, row in entries.items():
        word = row.get("left", "")
        anchor = row.get("anchor")
        if row.get("form") == "bare word" and len(word) == 2:
            anchor = pairs.get((row.get("location"), word[0] + " · " + word[1]), anchor)
        keys[k] = anchor
    return keys


def records(directory, paper):
    path = Path(directory) / FILENAME
    if not path.is_file() or not paper:
        return {}
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reviews'").fetchone():
            return {}
        rows = db.execute("SELECT category, target, version, payload FROM reviews WHERE paper=?", (paper,))
        return {(cat, target): (version, json.loads(payload) if payload else None)
                for cat, target, version, payload in rows}


def decisions(directory, paper, category=None):
    rows = records(directory, paper)
    return {(cat, key) if category is None else key: payload["decision"]
            for (cat, key), (_, payload) in rows.items()
            if payload and (category is None or category == cat)}


class Changed(ValueError):
    pass


def save(directory, paper, category, target, version, payload):
    with closing(sqlite3.connect(Path(directory) / FILENAME, timeout=8)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS reviews (paper TEXT, category TEXT, target TEXT, "
                   "version INTEGER NOT NULL, payload TEXT, PRIMARY KEY(paper, category, target))")
        db.execute("BEGIN IMMEDIATE")
        key = (paper, category, target)
        row = db.execute("SELECT version FROM reviews WHERE paper=? AND category=? AND target=?", key).fetchone()
        if version != (row[0] if row else 0):
            raise Changed("此条目已在其他页面修改，请刷新后重试。")
        db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?)",
                   (*key, version + 1, json.dumps(payload, ensure_ascii=False) if payload else None))


def restore(record):
    """Automatic baseline under the last web decision, including an unanswered baseline."""
    if record and "before_manual" in record:
        return record["before_manual"]
    return record


def refresh_names(directory, paper, lexicon, stats_path):
    """Remove resolved name referrals offline and reopen revoked names still in use."""
    name_rows = {key: payload for (cat, key), (_, payload) in records(directory, paper).items()
                 if cat == "names"}
    if not name_rows:
        return
    path = Path(directory) / "names_todo.json"
    doc = json.loads(path.read_text(encoding="utf8")) if path.exists() else {"items": []}
    entries = {r["name"]: r for r in doc["items"] if r.get("paper") == paper}
    stats = json.loads(Path(stats_path).read_text(encoding="utf8")) if Path(stats_path).exists() else {}
    seen = {k.lstrip("\\") for k in lexicon.get("unknown_commands", {})}
    for field in ("outside_vocabulary", "kept_macros"):
        seen.update(k.lstrip("\\") for k in stats.get(field, {}))
    for name, active in name_rows.items():
        if active:
            entries.pop(name, None)
        elif name in seen:
            entries.setdefault(name, {"paper": paper, "name": name, "action": None})
    if entries:
        doc["items"] = list(entries.values())
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf8")
        tmp.replace(path)
    elif path.exists():
        path.unlink()
