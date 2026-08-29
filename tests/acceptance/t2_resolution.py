"""Is a T2 failure a leak, or is it the test's own Monte Carlo noise?

    python -m tests.acceptance.t2_resolution data/evasive/v050

T2 declares the null empirically: permute the train labels, fit, score the test
split, and require the median over 50 permutations to sit within 0.03 of 0.50.
That threshold is only meaningful if the median is stable to better than 0.03
under a change of permutation seed. This measures that, and nothing else.

It changes no threshold and fixes nothing. It answers one question: how far does
the statistic T2 tests move when only the permutation seed changes?
"""

import sys

import numpy as np

from tests.acceptance.runner import SPLIT, encode, flatten
from tests.fixtures import load_events, labels_by_id

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

N_PERM = 50


def medians(path, seeds):
    events = load_events(path)
    lab = labels_by_id(path)
    y = np.array([lab[e["id"]]["label"] for e in events], dtype=np.int8)
    rows = [flatten(e) for e in events]
    fields = list(rows[0].keys())
    cut = int(len(rows) * SPLIT)

    Xs, cats = [], []
    for f in fields:
        X, c, _ = encode(rows, f)
        Xs.append(X)
        cats.append(bool(c))
    X = np.hstack(Xs)

    out = []
    for s in seeds:
        rng = np.random.default_rng(s)
        aucs = []
        ytr = y[:cut].copy()
        for i in range(N_PERM):
            sh = ytr.copy()
            rng.shuffle(sh)
            m = HistGradientBoostingClassifier(max_iter=100, random_state=i,
                                               categorical_features=cats)
            m.fit(X[:cut], sh)
            aucs.append(roc_auc_score(y[cut:], m.predict_proba(X[cut:])[:, 1]))
        med = float(np.median(aucs))
        out.append((s, med, float(np.percentile(aucs, 2.5)),
                    float(np.percentile(aucs, 97.5))))
        print(f"  permutation seed {s:>3}: median {med:.4f}  "
              f"|median-0.50| {abs(med-0.50):.4f}  "
              f"{'PASS' if abs(med-0.50) <= 0.03 else 'FAIL'}", flush=True)
    return out


def main(path, seeds=(0, 1, 2, 3, 4)):
    print("=" * 74)
    print(f"T2 RESOLUTION CHECK   data={path}   {N_PERM} permutations per seed")
    print("=" * 74)
    print("  T2 asks whether the empirical-null median is within 0.03 of 0.50.")
    print("  Only the permutation seed changes below. The data is identical.")
    print()
    out = medians(path, seeds)
    meds = [m for _, m, _, _ in out]
    spread = max(meds) - min(meds)
    print()
    print(f"  median across seeds ranges {min(meds):.4f} to {max(meds):.4f}, "
          f"spread {spread:.4f}")
    print(f"  T2 threshold on the same statistic:            0.0300")
    print(f"  verdict: the threshold is {'FINER' if spread > 0.06 else 'comparable to or coarser than'} "
          f"the statistic's own seed-to-seed movement")
    n_fail = sum(1 for m in meds if abs(m - 0.50) > 0.03)
    print(f"  {n_fail} of {len(meds)} seeds would fail T2 on this identical data")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/evasive/v050")
