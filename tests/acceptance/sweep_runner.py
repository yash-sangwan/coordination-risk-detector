"""Run the full acceptance suite over every step of the spec 2.1e sweep.

    python -m tests.acceptance.sweep_runner data/evasive

The evasive variant has to clear the same bar as everything else, so this runs
T1 to T8 unchanged on each dataset and prints one summary matrix at the end. It
fixes nothing and adjusts no threshold: a failure is reported as a failure.
"""

import io
import os
import re
import subprocess
import sys

STEP = re.compile(r"^v\d{3}$")


def steps(root):
    return [os.path.join(root, d) for d in sorted(os.listdir(root))
            if STEP.match(d) and os.path.isdir(os.path.join(root, d))]


def run_one(path):
    """Each dataset runs in its own process: T7 regenerates and rewrites files,
    so sharing an interpreter across steps would invite cross-contamination."""
    p = subprocess.run([sys.executable, "-u", "-m", "tests.acceptance.runner", path],
                       capture_output=True, text=True)
    return p.stdout + p.stderr


def parse_summary(out):
    """Pull the [PASS]/[FAIL] lines out of a runner transcript."""
    rows = []
    for line in out.splitlines():
        m = re.match(r"\s*\[(PASS|FAIL)\]\s+(\S[^ ]*(?: \S+)*?)\s{2,}(.*)$", line)
        if m:
            rows.append((m.group(1), m.group(2).strip(), m.group(3).strip()))
    return rows


def main(root):
    out_dir = os.path.join(root, "_acceptance")
    os.makedirs(out_dir, exist_ok=True)
    results = {}
    for path in steps(root):
        name = os.path.basename(path)
        print(f"=== {name} ===", flush=True)
        txt = run_one(path)
        io.open(os.path.join(out_dir, name + ".txt"), "w",
                encoding="utf-8").write(txt)
        rows = parse_summary(txt)
        results[name] = rows
        for verdict, test, _ in rows:
            print(f"  [{verdict}] {test}", flush=True)
        if not rows:
            print("  NO SUMMARY PARSED, see transcript", flush=True)

    print("\n" + "=" * 78)
    print("ACCEPTANCE ACROSS THE SPEC 2.1e SWEEP")
    print("=" * 78)
    names = sorted(results)
    tests = []
    for n in names:
        for _, t, _ in results[n]:
            if t not in tests:
                tests.append(t)
    print(f"  {'test':<36} " + "".join(f"{n:>8}" for n in names))
    for t in tests:
        cells = []
        for n in names:
            v = next((v for v, tt, _ in results[n] if tt == t), "-")
            cells.append(f"{v:>8}")
        print(f"  {t:<36} " + "".join(cells))
    bad = [(n, t) for n in names for v, t, _ in results[n] if v == "FAIL"]
    print(f"\n  {len(bad)} failing cells across {len(names)} datasets")
    for n, t in bad:
        detail = next(d for v, tt, d in results[n] if tt == t)
        print(f"    {n}  {t}: {detail}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/evasive")
