"""All four detectors across the spec 2.1e sweep, with thresholds frozen.

    python -m tests.detector.evaluate_sweep data/evasive

Every window, floor and threshold is chosen ONCE, on the train split of the
v=0.00 dataset, and then applied unchanged to every grade. Nothing is refitted
per step. That is the whole point: this measures how detectors tuned against one
attack hold up as the attack changes, not how well they can be re-tuned.

v=0.00 is byte-identical to data/sample, so its row here must reproduce the
numbers already recorded for that dataset. It is a consistency check, and it is
also a SECOND scoring of a test split that was scored once before, which is noted
in the output rather than glossed over.
"""

import collections
import json
import os
import sys

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from src.detector.baselines import score_combined, score_decline, score_volume
from src.detector.graph import components, score_card_testing
from tests.baselines.evaluate import (best_threshold, burst_latency,
                                      flash_windows, in_flash, prf, tune_window)
from tests.detector.evaluate_graph import tune_graph
from tests.fixtures import load_events, load_manifest, labels_by_id

SPLIT = 0.70
FROZEN_FROM = 0.00
NL_ = chr(10)   # avoids an escape in the machine-readable block


def _load(path):
    events = load_events(path)
    manifest = load_manifest(path)
    lab = labels_by_id(path)
    cut = int(len(events) * SPLIT)
    y = np.array([1 if lab[e["id"]].get("attack_type") == "card_testing" else 0
                  for e in events])
    return events, manifest, lab, cut, y


def freeze(path):
    """Choose every parameter on the TRAIN split of this dataset. Once."""
    events, manifest, lab, cut, y = _load(path)

    vp, vs = tune_window(events, y, cut, "volume", manifest)
    vthr, _ = best_threshold(np.asarray(vs)[:cut], y[:cut])

    dp, ds = tune_window(events, y, cut, "decline", manifest)
    dthr, _ = best_threshold(np.asarray(ds)[:cut], y[:cut])

    cw, cm = vp["window_s"], dp.get("min_events", 5)
    cs = score_combined(events, cw, cm, max(vthr, 1e-9), max(dthr, 1e-9))
    cthr, _ = best_threshold(np.asarray(cs)[:cut], y[:cut])

    gp, gs, _, gf1 = tune_graph(events, y, cut)
    gthr, _ = best_threshold(np.asarray(gs)[:cut], y[:cut])

    return {
        "volume": {"window_s": vp["window_s"], "thr": float(vthr)},
        "decline": {"window_s": dp["window_s"], "min_events": dp.get("min_events", 5),
                    "thr": float(dthr)},
        "combined": {"window_s": cw, "min_events": cm, "vol_ref": float(max(vthr, 1e-9)),
                     "dec_ref": float(max(dthr, 1e-9)), "thr": float(cthr)},
        "graph": {"window_s": gp["window_s"], "min_events": gp["min_events"],
                  "thr": float(gthr), "train_f1": float(gf1)},
    }


def score_all(events, P):
    """Every detector's continuous score, using ONLY the frozen parameters."""
    v = P["volume"]
    d = P["decline"]
    c = P["combined"]
    g = P["graph"]
    return {
        "baseline 1: rolling volume": (score_volume(events, v["window_s"]), v["thr"]),
        "baseline 2: rolling decline": (
            score_decline(events, d["window_s"], d["min_events"]), d["thr"]),
        "baseline 3: combined": (
            score_combined(events, c["window_s"], c["min_events"],
                           c["vol_ref"], c["dec_ref"]), c["thr"]),
        "GRAPH: fanout vs overlap": (
            score_card_testing(events, g["window_s"], g["min_events"],
                               comp=components(events, g["window_s"])), g["thr"]),
    }


def evaluate(name, scores, thr, y, cut, events, lab, manifest):
    s = np.asarray(scores, dtype=float)
    alert = s >= thr
    p, r, f1, tp, fp = prf(y[cut:], alert[cut:])
    yte, ste = y[cut:], s[cut:]
    ap = average_precision_score(yte, ste) if yte.sum() else 0.0
    # ROC AUC below 0.5 means the score ranks attacks BELOW benign traffic, i.e.
    # the detector has inverted rather than merely gone quiet.
    roc = roc_auc_score(yte, ste) if 0 < yte.sum() < len(yte) else float("nan")
    ap_inv = average_precision_score(yte, -ste) if yte.sum() else 0.0

    fw = flash_windows(manifest)
    fp_idx = [i for i in range(cut, len(events)) if alert[i] and y[i] == 0]
    fp_flash = sum(1 for i in fp_idx if in_flash(events[i]["created_at"], fw))
    lat = burst_latency(events, alert, lab, (cut, len(events)))
    missed = sum(1 for row in lat if row[2] is None)
    return dict(name=name, precision=p, recall=r, ap=ap, roc=roc, ap_inv=ap_inv,
                tp=tp, fp=len(fp_idx), fp_flash=fp_flash, latency=lat,
                missed=missed, n_bursts=len(lat), alert=alert)


def grades(root):
    out = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if d.startswith("v") and d[1:].isdigit() and os.path.isdir(p):
            out.append((int(d[1:]) / 100.0, p))
    return out


def observed_decline(events, lab):
    atk = [e for e in events if lab[e["id"]].get("attack_type") == "card_testing"]
    return sum(1 for e in atk if e["status"] == "failed") / max(len(atk), 1)


def main(root):
    steps = grades(root)
    frozen_path = dict(steps)[FROZEN_FROM]

    print("=" * 100)
    print(f"DETECTORS ACROSS THE SPEC 2.1e SWEEP   root={root}")
    print("=" * 100)
    print(f"  Every parameter frozen on the TRAIN split of v={FROZEN_FROM:.2f} "
          f"({frozen_path}). No refitting per step.")
    P = freeze(frozen_path)
    for k, v in P.items():
        print(f"    {k:<10} {json.dumps(v, sort_keys=True)}")
    print("\n  NOTE: the v=0.00 test split was scored once before, for the graph")
    print("  detector commit. This is a second scoring of it. Its row must")
    print("  reproduce those numbers exactly; it is a check, not a new result.")

    rows = {}
    dec = {}
    for v, path in steps:
        events, manifest, lab, cut, y = _load(path)
        dec[v] = observed_decline(events, lab)
        rows[v] = [evaluate(n, s, t, y, cut, events, lab, manifest)
                   for n, (s, t) in score_all(events, P).items()]
        print(f"\n  scored v={v:.2f}  events {len(events)}  test {len(events)-cut}  "
              f"attack decline {dec[v]*100:.2f}%", flush=True)

    names = [r["name"] for r in rows[steps[0][0]]]

    # ---------------------------------------------------------------- per grade
    for v, _ in steps:
        print("\n" + "=" * 100)
        print(f"v={v:.2f}   observed attack decline {dec[v]*100:.2f}%")
        print("=" * 100)
        print(f"  {'detector':<30} {'prec':>7} {'recall':>7} {'PR AUC':>8} "
              f"{'ROC':>7} {'TP':>6} {'FP':>6} {'FP in sale':>11} {'bursts missed':>14}")
        for r in rows[v]:
            print(f"  {r['name']:<30} {r['precision']:>7.4f} {r['recall']:>7.4f} "
                  f"{r['ap']:>8.4f} {r['roc']:>7.4f} {r['tp']:>6} {r['fp']:>6} "
                  f"{r['fp_flash']:>11} {r['missed']:>6}/{r['n_bursts']:<7}")

        bursts = sorted({b for b, *_ in rows[v][0]["latency"]})
        print(f"\n  LATENCY PER BURST (minutes / attempts before first alert)")
        print(f"  {'detector':<30} " + "".join(f"{b:>22}" for b in bursts))
        for r in rows[v]:
            cells = []
            for b in bursts:
                row = next((x for x in r["latency"] if x[0] == b), None)
                if row is None or row[2] is None:
                    cells.append(f"{'NOT DETECTED':>22}")
                else:
                    cells.append(f"{row[2]:>10.2f}m /{row[3]:>4} att ".rjust(22))
            print(f"  {r['name']:<30} " + "".join(cells))

    # ------------------------------------------------------------------- curves
    print("\n" + "=" * 100)
    print("THE CURVE: detector against observed decline rate")
    print("=" * 100)
    for metric, label in (("ap", "PR AUC"), ("recall", "RECALL"),
                          ("precision", "PRECISION")):
        print(f"\n  {label}")
        print(f"  {'detector':<30} " + "".join(
            f"{('%.0f%%' % (dec[v]*100)):>9}" for v, _ in steps))
        print(f"  {'observed attack decline ->':<30} " + "".join(
            f"{('v=%.2f' % v):>9}" for v, _ in steps))
        for i, n in enumerate(names):
            print(f"  {n:<30} " + "".join(
                f"{rows[v][i][metric]:>9.4f}" for v, _ in steps))

    print("\n  BURSTS MISSED ENTIRELY (of %d in the test split)"
          % rows[steps[0][0]][0]["n_bursts"])
    print(f"  {'detector':<30} " + "".join(f"{('v=%.2f' % v):>9}" for v, _ in steps))
    for i, n in enumerate(names):
        print(f"  {n:<30} " + "".join(
            f"{rows[v][i]['missed']:>9}" for v, _ in steps))

    print("\n  FALSE POSITIVES / OF WHICH INSIDE A FLASH SALE")
    print(f"  {'detector':<30} " + "".join(f"{('v=%.2f' % v):>9}" for v, _ in steps))
    for i, n in enumerate(names):
        print(f"  {n:<30} " + "".join(
            f"{('%d/%d' % (rows[v][i]['fp'], rows[v][i]['fp_flash'])):>9}"
            for v, _ in steps))

    ascii_plot(steps, dec, rows, names)
    inversion(steps, dec, rows, names)
    machine_readable(steps, rows, names)


def ascii_plot(steps, dec, rows, names):
    """PR AUC against observed decline rate, so a crossover is visible."""
    print("\n" + "=" * 100)
    print("PR AUC vs OBSERVED DECLINE RATE   (1.0 at the right edge)")
    print("=" * 100)
    W = 58
    marks = {"baseline 1: rolling volume": "V", "baseline 2: rolling decline": "D",
             "baseline 3: combined": "C", "GRAPH: fanout vs overlap": "G"}
    print("  " + " " * 22 + "0.0" + " " * (W - 8) + "1.0")
    for v, _ in steps:
        print(f"  v={v:.2f} decline {dec[v]*100:>5.1f}%  |"
              + "-" * W + "|")
        line = [" "] * (W + 1)
        for i, n in enumerate(names):
            pos = int(round(max(0.0, min(rows[v][i]["ap"], 1.0)) * W))
            line[pos] = marks[n] if line[pos] == " " else "*"
        print(" " * 22 + " |" + "".join(line) + "|")
    print("\n  V volume   D decline   C combined   G graph   * two or more overlap")


def inversion(steps, dec, rows, names):
    """Has any detector inverted, rather than merely gone quiet?"""
    print("\n" + "=" * 100)
    print("INVERSION CHECK: does the score rank attacks BELOW benign traffic?")
    print("=" * 100)
    print("  ROC AUC < 0.5000 means inverted. PR AUC of the NEGATED score shows")
    print("  what a detector that simply flipped its sign would recover.")
    print(f"  {'detector':<30} " + "".join(f"{('v=%.2f' % v):>10}" for v, _ in steps))
    for i, n in enumerate(names):
        print(f"  {n + ' ROC':<30} " + "".join(
            f"{rows[v][i]['roc']:>10.4f}" for v, _ in steps))
    print()
    for i, n in enumerate(names):
        print(f"  {n + ' PR(-s)':<30} " + "".join(
            f"{rows[v][i]['ap_inv']:>10.4f}" for v, _ in steps))

def burst_decline_by_attempt(events, lab, ks=(1, 2, 3, 5, 10)):
    """Pooled decline rate over the first k attempts of every burst.

    Computed and stored rather than described in prose. The claim that card
    testing is saturated from its first attempt is what makes the graph firing
    later a structural fact rather than a tuning artefact, so k=1 has to be a
    number in the artifact and not a sentence in a README.
    """
    by_burst = collections.defaultdict(list)
    for e in events:
        b = lab[e["id"]].get("burst_id")
        if b:
            by_burst[b].append(e)
    out = []
    for k in ks:
        failed = total = 0
        for b in sorted(by_burst):
            head = by_burst[b][:k]
            failed += sum(1 for e in head if e["status"] == "failed")
            total += len(head)
        out.append((k, (failed / total) if total else 0.0, total))
    return out


def machine_readable(steps, rows, names):
    """Strictly parseable restatement of values computed above.

    The artifact should not depend on the column layout of a table meant for a
    person. Everything here is already computed; this block only writes it in a
    form a parser can read without guessing at padding."""
    print(NL_ + "=" * 100)
    print("MACHINE READABLE")
    print("=" * 100)

    print("LATENCY_ROWS grade|detector|burst|minutes|attempts")
    for v, _ in steps:
        for i, n in enumerate(names):
            for b, n_ev, mins, att, _ts in rows[v][i]["latency"]:
                if mins is None:
                    print("LATENCY_ROW %.2f|%s|%s|NA|NA" % (v, n, b))
                else:
                    print("LATENCY_ROW %.2f|%s|%s|%.4f|%d" % (v, n, b, mins, att))

    print("DECLINE_BY_ATTEMPT grade|k|rate|n")
    for v, path in steps:
        events, mf, lb, cut, y = _load(path)
        for k, rate, n in burst_decline_by_attempt(events, lb):
            print("DECLINE_ROW %.2f|%d|%.6f|%d" % (v, k, rate, n))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/evasive")
