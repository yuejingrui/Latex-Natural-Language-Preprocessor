#!/usr/bin/env python3
"""Run the probe suites and the two unittest modules; non-zero if any fails."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = ["probe_split", "probe_envs", "probe_brace", "probe_part",
          "probe_audit", "probe_review"]
UNITS = ["test_preservation", "test_split_contract"]


def run(path):
    r = subprocess.run([sys.executable, path], capture_output=True, text=True)
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    return r.returncode, (tail[-1] if tail else "")


def main():
    failed = []
    for name in PROBES + UNITS:
        path = os.path.join(HERE, name + ".py")
        code, last = run(path)
        print("%-22s rc=%-3d %s" % (name, code, last))
        if code != 0:
            failed.append(name)
    print("\n%d/%d suites passed" % (len(PROBES) + len(UNITS) - len(failed),
                                     len(PROBES) + len(UNITS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
