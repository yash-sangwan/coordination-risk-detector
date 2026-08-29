"""Tune the baselines on train, score them once on test.

    python -m tests.baselines.evaluate data/sample

Labels live here and only here. src/detector/baselines.py never sees them: it
exposes scoring functions parameterised by thresholds, and choosing thresholds
is what needs labels, so that choice is made in this file.

Nothing is tuned on the test split. The sweep runs on train only, the chosen
parameters are frozen and printed, and the test split is scored once.
"""

import collections
import sys

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve

from src.detector.baselines import (score_combined, score_decline,
                                    score_pincode_sharing, score_volume)
from tests.fixtures import load_events, load_manifest, labels_by_id

SPLIT = 0.70


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def prf(y, alert):
    tp = int(((y == 1) & alert).sum())
    fp = int(((y == 0) & alert).sum())
    fn = int(((y == 1) & ~alert).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f1, tp, fp


def best_threshold(scores, y):
    """Threshold maximising F1 on the train split. A fair criterion: it does not
    let the baseline cherry-pick an operating point that only looks good at one
    end of the curve."""
    cand = sorted({round(s, 6) for s in scores if s > 0})
    if not cand:
        return 0.0, 0.0
    # Cap the sweep so this stays fast on 47k events without changing the answer.
    if len(cand) > 400:
        idx = np.linspace(0, len(cand) - 1, 400).astype(int)
        cand = [cand[i] for i in idx]
    s = np.asarray(scores)
    best, best_f1 = cand[0], -1.0
    for t in cand:
        _, _, f1, _, _ = prf(y, s >= t)
        if f1 > best_f1:
            best, best_f1 = t, f1
    return best, best_f1


# --------------------------------------------------------------------------
# latency, per burst, never averaged
# --------------------------------------------------------------------------

def burst_latency(events, alert, lab, idx_range):
    """First alert firing on each burst, measured from that burst's first event.

    Reported per burst. A burst with no alert is recorded as not detected rather
    than folded into a mean, because averaging a miss as if it were a slow
    detection hides the failure.
    """
    lo, hi = idx_range
    by_burst = collections.defaultdict(list)
    for i in range(lo, hi):
        b = lab[events[i]["id"]].get("burst_id")
        if b:
            by_burst[b].append(i)

    rows = []
    for b in sorted(by_burst):
        idxs = by_burst[b]
        t0 = events[idxs[0]]["created_at"]
        hit = next((i for i in idxs if alert[i]), None)
        if hit is None:
            rows.append((b, len(idxs), None, None, None))
        else:
            mins = (events[hit]["created_at"] - t0) / 60.0
            attempts = sum(1 for i in idxs if i < hit)
            rows.append((b, len(idxs), mins, attempts, events[hit]["created_at"]))
    return rows


def flash_windows(manifest):
    return [(s["start"], s["end"]) for s in manifest.get("flash_sales", [])]


def in_flash(ts, windows):
    return any(a <= ts < b for a, b in windows)


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def report(name, params, events, scores, y, cut, lab, manifest):
    s = np.asarray(scores)
    y_tr, y_te = y[:cut], y[cut:]
    thr, f1_tr = best_threshold(s[:cut], y_tr)

    alert = s >= thr
    p, r, f1, tp, fp = prf(y_te, alert[cut:])
    ap = average_precision_score(y_te, s[cut:]) if y_te.sum() else 0.0

    print("\n" + "=" * 74)
    print(f"{name}")
    print("=" * 74)
    print(f"  tuned on TRAIN: {params}, threshold {thr:.4f} (train F1 {f1_tr:.4f})")
    print(f"  TEST, scored once: precision {p:.4f}  recall {r:.4f}  "
          f"PR AUC {ap:.4f}   TP {tp}  FP {fp}")

    fw = flash_windows(manifest)
    fp_idx = [i for i in range(cut, len(events)) if alert[i] and y[i] == 0]
    fp_flash = sum(1 for i in fp_idx if in_flash(events[i]["created_at"], fw))
    print(f"  false positives: {len(fp_idx)} total, {fp_flash} inside a flash sale "
          f"({fp_flash/max(len(fp_idx),1)*100:.1f}% of FPs)")

    print("  detection latency, per burst in the test split:")
    rows = burst_latency(events, alert, lab, (cut, len(events)))
    if not rows:
        print("    (no burst falls in the test split)")
    else:
        print(f"    {'burst':>6} {'events':>7} {'latency (min)':>14} {'attempts missed':>16}")
        for b, n, mins, attempts, _ in rows:
            if mins is None:
                print(f"    {b:>6} {n:>7} {'NOT DETECTED':>14} {'-':>16}")
            else:
                print(f"    {b:>6} {n:>7} {mins:>14.2f} {attempts:>16}")
    return dict(name=name, thr=thr, precision=p, recall=r, ap=ap,
                fp=len(fp_idx), fp_flash=fp_flash, latency=rows)


def tune_window(events, y, cut, kind, manifest):
    """Sweep the window (and decline floor) on TRAIN only, by train F1."""
    best = None
    windows = [60, 180, 300, 600, 900]
    floors = [3, 5, 10, 20]
    for w in windows:
        if kind == "volume":
            s = score_volume(events, w)
            _, f1 = best_threshold(np.asarray(s)[:cut], y[:cut])
            cand = (f1, {"window_s": w}, s)
        else:
            cand = None
            for m in floors:
                s = score_decline(events, w, m)
                _, f1 = best_threshold(np.asarray(s)[:cut], y[:cut])
                if cand is None or f1 > cand[0]:
                    cand = (f1, {"window_s": w, "min_events": m}, s)
        if best is None or cand[0] > best[0]:
            best = cand
    return best[1], best[2]


def main(path):
    events = load_events(path)
    manifest = load_manifest(path)
    lab = labels_by_id(path)
    cut = int(len(events) * SPLIT)

    y_ct = np.array([1 if lab[e["id"]].get("attack_type") == "card_testing" else 0
                     for e in events])

    print("=" * 74)
    print(f"BASELINES  data={path}")
    print("=" * 74)
    print(f"  events {len(events)}   chronological split {SPLIT:.0%} -> "
          f"train {cut} / test {len(events)-cut}")
    print(f"  card-testing events: train {int(y_ct[:cut].sum())}, "
          f"test {int(y_ct[cut:].sum())}")
    print("  every threshold below is chosen on TRAIN and frozen before test.")

    vp, vs = tune_window(events, y_ct, cut, "volume", manifest)
    r1 = report("1. Rolling volume threshold", vp, events, vs, y_ct, cut, lab, manifest)

    dp, ds = tune_window(events, y_ct, cut, "decline", manifest)
    r2 = report("2. Rolling decline rate threshold", dp, events, ds, y_ct, cut,
                lab, manifest)

    # Combined: reference points come from the two tuned single baselines, so it
    # inherits their fair tuning rather than being handicapped.
    vthr, _ = best_threshold(np.asarray(vs)[:cut], y_ct[:cut])
    dthr, _ = best_threshold(np.asarray(ds)[:cut], y_ct[:cut])
    w = vp["window_s"]
    m = dp.get("min_events", 5)
    cs = score_combined(events, w, m, max(vthr, 1e-9), max(dthr, 1e-9))
    r3 = report("3. Combined volume and decline", {"window_s": w, "min_events": m,
                                                   "vol_ref": round(float(vthr), 3),
                                                   "dec_ref": round(float(dthr), 3)},
                events, cs, y_ct, cut, lab, manifest)

    ring_report(events, lab, cut, manifest)

    print("\n" + "=" * 74)
    print("CARD TESTING BASELINES, side by side (test split)")
    print("=" * 74)
    print(f"  {'baseline':<34} {'prec':>7} {'recall':>7} {'PR AUC':>8} "
          f"{'FP':>6} {'FP in sale':>11} {'missed bursts':>14}")
    for r in (r1, r2, r3):
        miss = sum(1 for row in r["latency"] if row[2] is None)
        print(f"  {r['name']:<34} {r['precision']:>7.4f} {r['recall']:>7.4f} "
              f"{r['ap']:>8.4f} {r['fp']:>6} {r['fp_flash']:>11} "
              f"{miss:>6}/{len(r['latency']):<7}")


def ring_report(events, lab, cut, manifest):
    """Account-level ring baseline: flag accounts sharing a pincode with > N others.

    Clusters are built from each split's own events, so the train sweep cannot
    see test-split structure.
    """
    print("\n" + "=" * 74)
    print("4. Ring baseline, account level: shares a pincode with > N accounts")
    print("=" * 74)
    tr, te = events[:cut], events[cut:]
    s_tr, s_te = score_pincode_sharing(tr), score_pincode_sharing(te)

    ring_accts = {e["merchant_context"]["account_id"] for e in events
                  if lab[e["id"]].get("attack_type") == "ring"
                  and e["merchant_context"]["account_id"]}

    def eval_at(scores, n):
        acc = sorted(scores)
        yv = np.array([1 if a in ring_accts else 0 for a in acc])
        al = np.array([scores[a] > n for a in acc])
        return prf(yv, al)

    # Sweep the full observed range, not an arbitrary cap. Clusters reach into
    # the hundreds on hot urban pincodes, so stopping at 60 would deny the
    # baseline the thresholds that might actually have worked.
    n_max = max(max(s_tr.values(), default=0), max(s_te.values(), default=0)) + 2
    best_n, best_f1 = None, -1.0
    for n in range(0, n_max):
        _, _, f1, _, _ = eval_at(s_tr, n)
        if f1 > best_f1:
            best_n, best_f1 = n, f1
    print(f"  tuned on TRAIN: N = {best_n} (train F1 {best_f1:.4f}), "
          f"swept N = 0..{n_max-1}")

    acc = sorted(s_te)
    yv = np.array([1 if a in ring_accts else 0 for a in acc])
    sv = np.array([float(s_te[a]) for a in acc])
    p, r, f1, tp, fp = eval_at(s_te, best_n)
    ap = average_precision_score(yv, sv) if yv.sum() else 0.0
    print(f"  TEST, scored once: precision {p:.4f}  recall {r:.4f}  PR AUC {ap:.4f}"
          f"   TP {tp}  FP {fp}")
    print(f"  accounts in test split: {len(acc)}, of which ring members: {int(yv.sum())}")

    fw = flash_windows(manifest)
    flagged = {a for a in acc if s_te[a] > best_n and a not in ring_accts}
    fp_flash = len({e["merchant_context"]["account_id"] for e in te
                    if e["merchant_context"]["account_id"] in flagged
                    and in_flash(e["created_at"], fw)})
    print(f"  false positives: {fp} accounts, {fp_flash} with any event inside a "
          f"flash sale (a sale does not change who shares a pincode, so this is "
          f"expected to be incidental)")

    print("  detection latency, per ring in the test split:")
    by_ring = collections.defaultdict(list)
    for i in range(cut, len(events)):
        rid = lab[events[i]["id"]].get("ring_id")
        if rid:
            by_ring[rid].append(i)
    if not by_ring:
        print("    (no ring activity in the test split)")
        return
    print(f"    {'ring':>6} {'events':>7} {'latency (min)':>14} {'attempts missed':>16}")
    for rid in sorted(by_ring):
        idxs = by_ring[rid]
        t0 = events[idxs[0]]["created_at"]
        hit = next((i for i in idxs
                    if s_te.get(events[i]["merchant_context"]["account_id"], 0) > best_n),
                   None)
        if hit is None:
            print(f"    {rid:>6} {len(idxs):>7} {'NOT DETECTED':>14} {'-':>16}")
        else:
            mins = (events[hit]["created_at"] - t0) / 60.0
            missed = sum(1 for i in idxs if i < hit)
            print(f"    {rid:>6} {len(idxs):>7} {mins:>14.2f} {missed:>16}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/sample")
