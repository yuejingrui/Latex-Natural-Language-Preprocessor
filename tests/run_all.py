#!/usr/bin/env python3
"""Run every probe suite and unittest module; non-zero if any of them fails.

A module that needs a TeX engine and pdftotext skips those cases instead of
failing, and says so on its last line ("OK (skipped=12)"), so a green run on a
machine without TeX is not mistaken for a full run.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = ["probe_split", "probe_envs", "probe_brace", "probe_part",
          "probe_audit", "probe_review"]
UNITS = ["test_preservation", "test_split_contract", "test_source_order",
         "test_conditional_acceptance", "test_tex_execution"]


def run(path):
    r = subprocess.run([sys.executable, path], capture_output=True, text=True)
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    return r.returncode, (tail[-1] if tail else "")


def main():
    failed, skipped = [], []
    total = len(PROBES) + len(UNITS)
    for name in PROBES + UNITS:
        path = os.path.join(HERE, name + ".py")
        code, last = run(path)
        print("%-24s rc=%-3d %s" % (name, code, last))
        if code != 0:
            failed.append(name)
        elif "skipped" in last:
            skipped.append(name)
    print("\n%d/%d suites passed" % (total - len(failed), total))
    if skipped:
        print("note: %s skipped cases that need a TeX engine and pdftotext"
              % ", ".join(skipped))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
