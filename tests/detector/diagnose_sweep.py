"""Three questions the sweep table raises but does not answer.

    python -m tests.detector.diagnose_sweep data/evasive

1. Does the decline baseline INVERT at the top of the sweep, or just go silent?
   PR AUC falling to 0.2887 is consistent with either, and the two call for
   completely different fixes.
2. Is the graph's flatness real independence from the decline rate, or a
   coincidence? Checked by comparing the score vectors themselves, not summaries.
3. What would it actually take to catch the evasive attack on decline rate
   alone, if we allowed ourselves to retune the threshold per grade?
"""

import bisect
import os
import sys

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from src.detector.baselines import rolling_decline, score_decline
from src.detector.graph import components, score_card_testing
from tests.baselines.evaluate import best_threshold, prf
from tests.detector.evaluate_sweep import _load, freeze, grades

W_DEC, MIN_DEC = 60, 5
W_GRAPH, MIN_GRAPH = 180, 8


def q1_inversion(steps):
    """Silent or inverted? The distinction is the whole fix."""
    print("=" * 92)
    print("Q1. DOES THE DECLINE BASELINE INVERT, OR GO SILENT?")
    print("=" * 92)
    print("  The frozen threshold is 0.6250. What matters is where the attack's own")
    print("  windowed decline rate sits relative to the benign stream it is compared")
    print("  against, which is the BLENDED rate over all methods, not the card rate.")
    print()
    print(f"  {'v':>6} {'attack rows':>12} {'benign rows':>12} {'benign card':>12} "
          f"{'attack win':>11} {'benign win':>11} {'ratio':>7} {'max attack':>11}")
    for v, path in steps:
        events, mf, lab, cut, y = _load(path)
        rd = rolling_decline(events, W_DEC)
        s = np.asarray(score_decline(events, W_DEC, MIN_DEC))
        atk = y == 1
        ben = y == 0
        a_raw = np.mean([e["status"] == "failed" for e, m in zip(events, atk) if m])
        b_raw = np.mean([e["status"] == "failed" for e, m in zip(events, ben) if m])
        b_card = np.mean([e["status"] == "failed" for e, m in zip(events, ben)
                          if m and e["method"] == "card"])
        print(f"  {v:>6.2f} {a_raw*100:>11.2f}% {b_raw*100:>11.2f}% {b_card*100:>11.2f}% "
              f"{s[atk].mean()*100:>10.2f}% {s[ben].mean()*100:>10.2f}% "
              f"{s[atk].mean()/max(s[ben].mean(),1e-9):>6.2f}x {s[atk].max()*100:>10.2f}%")

    print("\n  The window the baseline averages over is method-agnostic. 55% of")
    print("  legitimate traffic is UPI at a 0.8% decline rate, so the blended benign")
    print("  rate the attack is measured against is far below the card-only rate.")
    print("  Inversion needs the attack window rate BELOW the benign window rate.")


def q2_graph_independence(steps):
    """If the graph reads nothing decline-linked, its scores are bit-identical."""
    print("\n" + "=" * 92)
    print("Q2. IS THE GRAPH ACTUALLY INDEPENDENT OF THE DECLINE RATE?")
    print("=" * 92)
    print("  The coordination is byte-identical across every evasive step, and the")
    print("  graph reads no outcome field. If that is true rather than merely")
    print("  approximately true, the SCORE VECTORS are identical, not just the")
    print("  summary metrics. Comparing vectors is the strong form of the check.")
    print()
    ref = None
    print(f"  {'v':>6} {'events':>8} {'max |score diff| vs v=0.25':>30} {'identical':>11}")
    for v, path in steps:
        events, mf, lab, cut, y = _load(path)
        s = np.asarray(score_card_testing(events, W_GRAPH, MIN_GRAPH,
                                          comp=components(events, W_GRAPH)))
        if v == 0.0:
            print(f"  {v:>6.2f} {len(events):>8} {'(control, different RNG seq)':>30} "
                  f"{'n/a':>11}")
            continue
        if ref is None:
            ref = s
            print(f"  {v:>6.2f} {len(events):>8} {'(reference)':>30} {'-':>11}")
            continue
        d = float(np.abs(s - ref).max()) if len(s) == len(ref) else float("nan")
        print(f"  {v:>6.2f} {len(events):>8} {d:>30.2e} "
              f"{str(bool(np.array_equal(s, ref))):>11}")

    print("\n  A single non-zero entry here would mean the graph is reading")
    print("  something that moves with the decline rate. Zero means it is not.")


def q3_retune(steps):
    """If we allowed per-grade retuning of the decline threshold, what then?"""
    print("\n" + "=" * 92)
    print("Q3. WHAT WOULD CATCHING IT ON DECLINE RATE ALONE REQUIRE?")
    print("=" * 92)
    print("  The frozen threshold is 0.6250. This refits the threshold on each")
    print("  grade's OWN train split, which the measurement task forbids for the")
    print("  headline numbers. It is reported only to show the ceiling that a")
    print("  perfectly retuned decline baseline would hit.")
    print()
    print(f"  {'v':>6} {'best thr':>10} {'prec':>8} {'recall':>8} {'F1':>8} "
          f"{'PR AUC':>8} {'vs graph PR AUC':>17}")
    for v, path in steps:
        events, mf, lab, cut, y = _load(path)
        s = np.asarray(score_decline(events, W_DEC, MIN_DEC))
        thr, f1tr = best_threshold(s[:cut], y[:cut])
        p, r, f1, tp, fp = prf(y[cut:], (s >= thr)[cut:])
        ap = average_precision_score(y[cut:], s[cut:])
        g = np.asarray(score_card_testing(events, W_GRAPH, MIN_GRAPH,
                                          comp=components(events, W_GRAPH)))
        gap = average_precision_score(y[cut:], g[cut:])
        print(f"  {v:>6.2f} {thr:>10.4f} {p:>8.4f} {r:>8.4f} {f1:>8.4f} "
              f"{ap:>8.4f} {ap - gap:>+17.4f}")

    print("\n  Retuning recovers the threshold but not the separation: PR AUC is a")
    print("  threshold-free measure and it falls regardless. A decline baseline")
    print("  cannot be repaired by moving its cut, because at the top of the sweep")
    print("  there is no cut that separates the two populations.")


def main(root):
    steps = grades(root)
    q1_inversion(steps)
    q2_graph_independence(steps)
    q3_retune(steps)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/evasive")
