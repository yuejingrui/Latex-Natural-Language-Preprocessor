"""Read paper identity from the run, including older normalization outputs."""
import json
from pathlib import Path, PureWindowsPath


def run_paper_id(directory):
    root = Path(directory)
    stats = {}
    for filename, field in (("normalize-stats.json", "paper_id"), ("paper_lexicon.json", "paper")):
        path = root / filename
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                record = json.load(stream)
            if record.get(field):
                return record[field]
            if filename == "normalize-stats.json":
                stats = record
    return PureWindowsPath(stats.get("input") or "").stem
