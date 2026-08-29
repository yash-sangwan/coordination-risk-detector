"""T2 alone, at the raised permutation count, across every grade of the sweep.

    python -m tests.acceptance.t2_sweep data/evasive

Runs only T2, because that is the test whose count changed, and reports wall
time per dataset so the cost of the change is on the record next to its benefit.
"""

import os
import re
import sys
import time

import numpy as np

from tests.acceptance import runner as R
from tests.fixtures import load_events, labels_by_id

STEP = re.compile(r"^v\d{3}$")


def grades(root):
    out = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if STEP.match(d) and os.path.isdir(p):
            out.append((int(d[1:]) / 100.0, p))
    return out


def main(root):
    print("=" * 88)
    print(f"T2 AT {R.T2_PERMUTATIONS} PERMUTATIONS, ACROSS THE SWEEP   root={root}")
    print("=" * 88)
    print("  Threshold unchanged: the empirical-null median must sit within 0.03")
    print("  of 0.50, and the 95% band must contain 0.50. Only the count moved.")
    print()

    rows = []
    total = 0.0
    for v, path in grades(root):
        events = load_events(path)
        lab = labels_by_id(path)
        y = np.array([lab[e["id"]]["label"] for e in events], dtype=np.int8)
        flat = [R.flatten(e) for e in events]
        R.flatten_keys = list(flat[0].keys())
        cut = int(len(flat) * R.SPLIT)

        R.RESULTS.clear()
        t0 = time.time()
        med, lo, hi = R.t2(flat, y, cut, np.random.default_rng(0))
        dt = time.time() - t0
        total += dt
        _, ok, detail = R.RESULTS[0]
        rows.append((v, med, lo, hi, ok, dt))
        print(f"  v={v:.2f}  median {med:.4f}  |med-0.50| {abs(med-0.50):.4f}  "
              f"95% [{lo:.4f},{hi:.4f}]  {'PASS' if ok else 'FAIL'}  "
              f"{dt/60:.1f} min", flush=True)

    print("\n" + "=" * 88)
    print("SUMMARY")
    print("=" * 88)
    n_fail = sum(1 for *_, ok, _ in rows if not ok)
    meds = [m for _, m, _, _, _, _ in rows]
    print(f"  {len(rows)-n_fail} of {len(rows)} grades pass")
    print(f"  median ranges {min(meds):.4f} to {max(meds):.4f}, "
          f"spread {max(meds)-min(meds):.4f}  (threshold 0.0300)")
    print(f"  worst |median - 0.50| across the sweep: "
          f"{max(abs(m-0.50) for m in meds):.4f}")
    print(f"  wall time: {total/60:.1f} min total, "
          f"{total/60/len(rows):.1f} min per dataset")
    print(f"  at the old count of 50 this test took about "
          f"{total/60/len(rows)/7:.1f} min per dataset")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/evasive")
