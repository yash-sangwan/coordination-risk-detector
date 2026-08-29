"""Graph detector vs the three baselines. Tune on train, score test once.

    python -m tests.detector.evaluate_graph data/sample

Labels live here, never in src/detector/. Thresholds and window sizes are chosen
on the train split and frozen before the test split is touched.
"""

import collections
import sys

import numpy as np
from sklearn.metrics import average_precision_score

from src.detector.baselines import score_combined, score_decline, score_volume
from src.detector.graph import (CONVERGE, DIVERGE, components,
                                convergence_only, score_card_testing)
from tests.baselines.evaluate import best_threshold, burst_latency, flash_windows, in_flash, prf
from tests.fixtures import load_events, load_manifest, labels_by_id

SPLIT = 0.70


def evaluate(name, scores, y, cut, events, lab, manifest, thr=None):
    s = np.asarray(scores, dtype=float)
    if thr is None:
        thr, _ = best_threshold(s[:cut], y[:cut])
    alert = s >= thr
    p, r, f1, tp, fp = prf(y[cut:], alert[cut:])
    ap = average_precision_score(y[cut:], s[cut:]) if y[cut:].sum() else 0.0
    fw = flash_windows(manifest)
    fp_idx = [i for i in range(cut, len(events)) if alert[i] and y[i] == 0]
    fp_flash = sum(1 for i in fp_idx if in_flash(events[i]["created_at"], fw))
    lat = burst_latency(events, alert, lab, (cut, len(events)))
    return dict(name=name, thr=float(thr), precision=p, recall=r, ap=ap,
                tp=tp, fp=len(fp_idx), fp_flash=fp_flash, latency=lat, alert=alert)


def tune_graph(events, y, cut):
    """Sweep window and the Herfindahl floor on TRAIN only."""
    best = None
    for w in (60, 180, 300, 600, 900):
        comp = components(events, w)
        for m in (4, 8, 15, 30):
            s = score_card_testing(events, w, m, comp=comp)
            _, f1 = best_threshold(np.asarray(s)[:cut], y[:cut])
            if best is None or f1 > best[0]:
                best = (f1, {"window_s": w, "min_events": m}, s, comp)
    return best[1], best[2], best[3], best[0]


def main(path):
    events = load_events(path)
    manifest = load_manifest(path)
    lab = labels_by_id(path)
    cut = int(len(events) * SPLIT)
    y = np.array([1 if lab[e["id"]].get("attack_type") == "card_testing" else 0
                  for e in events])

    print("=" * 78)
    print(f"GRAPH DETECTOR vs BASELINES   data={path}")
    print("=" * 78)
    print(f"  events {len(events)}  train {cut} / test {len(events)-cut}   "
          f"card-testing: train {int(y[:cut].sum())}, test {int(y[cut:].sum())}")

    gp, gs, comp, gf1 = tune_graph(events, y, cut)
    print(f"  graph tuned on TRAIN: {gp} (train F1 {gf1:.4f})")

    # baselines, tuned on train exactly as in tests.baselines.evaluate
    vs = score_volume(events, 180)
    ds = score_decline(events, 60, 5)
    vthr, _ = best_threshold(np.asarray(vs)[:cut], y[:cut])
    dthr, _ = best_threshold(np.asarray(ds)[:cut], y[:cut])
    cs = score_combined(events, 180, 5, max(vthr, 1e-9), max(dthr, 1e-9))

    rows = [
        evaluate("baseline 1: rolling volume", vs, y, cut, events, lab, manifest),
        evaluate("baseline 2: rolling decline", ds, y, cut, events, lab, manifest),
        evaluate("baseline 3: combined", cs, y, cut, events, lab, manifest),
        evaluate("GRAPH: fanout vs overlap", gs, y, cut, events, lab, manifest),
    ]

    print("\n" + "=" * 78)
    print("TEST SPLIT, scored once")
    print("=" * 78)
    print(f"  {'detector':<30} {'prec':>7} {'recall':>7} {'PR AUC':>8} {'TP':>6} "
          f"{'FP':>6} {'FP in sale':>11}")
    for r in rows:
        print(f"  {r['name']:<30} {r['precision']:>7.4f} {r['recall']:>7.4f} "
              f"{r['ap']:>8.4f} {r['tp']:>6} {r['fp']:>6} {r['fp_flash']:>11}")

    print("\n  DETECTION LATENCY, per burst (minutes / attempts missed)")
    bursts = sorted({b for b, *_ in rows[0]["latency"]})
    print(f"  {'detector':<30} " + "".join(f"{b:>22}" for b in bursts))
    for r in rows:
        cells = []
        for b in bursts:
            row = next((x for x in r["latency"] if x[0] == b), None)
            if row is None or row[2] is None:
                cells.append(f"{'NOT DETECTED':>22}")
            else:
                cells.append(f"{row[2]:>10.2f}m /{row[3]:>4} att ".rjust(22))
        print(f"  {r['name']:<30} " + "".join(cells))

    ablations(events, y, cut, lab, manifest, gp)
    pre_decline(events, y, cut, lab, gs, ds, rows)


def ablations(events, y, cut, lab, manifest, gp):
    """Which edges actually carry the detection."""
    print("\n" + "=" * 78)
    print("WHICH EDGES CARRY THE DETECTION (test split, each tuned on train)")
    print("=" * 78)
    w, m = gp["window_s"], gp["min_events"]
    comp = components(events, w)

    variants = [("full (iin + device vs email/contact/last4)", CONVERGE, DIVERGE)]
    for f in CONVERGE:
        variants.append((f"converge on {f} only", (f,), DIVERGE))
    variants.append(("no divergence term (concentration alone)", CONVERGE, ()))
    for f in DIVERGE:
        rest = tuple(x for x in DIVERGE if x != f)
        variants.append((f"diverge without {f}", CONVERGE, rest))

    print(f"  {'variant':<44} {'prec':>7} {'recall':>7} {'PR AUC':>8}")
    for label, conv, div in variants:
        s = score_card_testing(events, w, m, comp=comp,
                               use_converge=conv, use_diverge=div)
        r = evaluate(label, s, y, cut, events, lab, manifest)
        print(f"  {label:<44} {r['precision']:>7.4f} {r['recall']:>7.4f} {r['ap']:>8.4f}")

    print("\n  Single attribute concentration alone, no fanout term:")
    print(f"  {'attribute':<44} {'prec':>7} {'recall':>7} {'PR AUC':>8}")
    for f in CONVERGE + DIVERGE + ("pincode", "vpa"):
        s = convergence_only(events, w, f, comp=comp)
        r = evaluate(f, s, y, cut, events, lab, manifest)
        print(f"  {'  ' + f + ' HHI':<44} {r['precision']:>7.4f} {r['recall']:>7.4f} "
              f"{r['ap']:>8.4f}")


def pre_decline(events, y, cut, lab, gs, ds, rows):
    """What is visible BEFORE the declines arrive.

    The decline baseline cannot fire until failures have accumulated. Convergence
    on IIN and device is present from the second attempt of a burst, because it is
    a property of who is attempting, not of how the bank replied.
    """
    print("\n" + "=" * 78)
    print("WHAT THE GRAPH SEES BEFORE THE DECLINES ARRIVE")
    print("=" * 78)
    g = next(r for r in rows if r["name"].startswith("GRAPH"))
    d = next(r for r in rows if "decline" in r["name"])

    by_burst = collections.defaultdict(list)
    for i in range(cut, len(events)):
        b = lab[events[i]["id"]].get("burst_id")
        if b:
            by_burst[b].append(i)

    print(f"  {'burst':>6} {'graph fires at':>16} {'decline fires at':>18} "
          f"{'graph earlier by':>18}")
    for b in sorted(by_burst):
        idxs = by_burst[b]
        gi = next((k for k, i in enumerate(idxs) if g["alert"][i]), None)
        di = next((k for k, i in enumerate(idxs) if d["alert"][i]), None)
        gs_ = "att %d" % gi if gi is not None else "never"
        ds_ = "att %d" % di if di is not None else "never"
        if gi is not None and di is not None:
            delta = di - gi
            secs = events[idxs[di]]["created_at"] - events[idxs[gi]]["created_at"]
            gap = f"{delta:+d} att / {secs/60:+.2f} min"
        else:
            gap = "n/a"
        print(f"  {b:>6} {gs_:>16} {ds_:>18} {gap:>18}")

    print("\n  Structural convergence inside each burst, first 20 attempts:")
    print(f"  {'burst':>6} {'att':>5} {'distinct IIN':>13} {'distinct dev':>13} "
          f"{'distinct email':>15} {'decline so far':>15}")
    for b in sorted(by_burst):
        idxs = by_burst[b][:20]
        for k in (1, 2, 3, 5, 10, 20):
            if k > len(idxs):
                continue
            sl = [events[i] for i in idxs[:k]]
            iins = {(e.get("card") or {}).get("iin") for e in sl}
            devs = {e["merchant_context"]["device_id"] for e in sl}
            mails = {e["email"] for e in sl}
            dec = sum(1 for e in sl if e["status"] == "failed") / k
            print(f"  {b:>6} {k:>5} {len(iins):>13} {len(devs):>13} "
                  f"{len(mails):>15} {dec:>14.0%}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/sample")
