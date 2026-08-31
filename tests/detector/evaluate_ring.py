"""Ring detector against the pincode baseline. Account level, test scored once.

    python -m tests.detector.evaluate_ring data/sample

Labels live here, never in src/detector/. Parameters are swept on the train
split and frozen before the test split is touched.

Clusters are rebuilt from each split's own events, matching the protocol the
pincode baseline already uses, so the train sweep cannot see test structure.
"""

import collections
import sys

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve

from src.generator import config as C
from src.detector.baselines import score_pincode_sharing
from src.detector.ring import (SCORE_MODES, account_attributes,
                               conjunction_components, score_accounts,
                               score_pincode_only)
from tests.baselines.evaluate import prf
from tests.fixtures import load_events, load_manifest, labels_by_id

SPLIT = 0.70

# The shipped ring score. `raw` is component_size x density, k^2 / n_p.
#
# Reverted to on 2026-08-30 after the scale-invariance attempt was measured and
# lost. Density bought about a quarter of the drift and cost PR AUC 0.5811 to
# 0.2047, and the diagnosis behind it was wrong: the operating point does not
# fail because of scale. Per ring, n_p is stable across window lengths (r00
# 7/8/8, r02 10/10/11) and so is k. It fails because r01, at density 0.5714,
# sets the train cut and is absent from the test window, so with three rings the
# between-ring variation dominates. See docs/report/decisions.md, 2026-08-30.
#
# The alternative modes stay in src/detector/ring.py and in the stability table
# below, because the evidence for this decision has to stay reproducible. They
# are deliberately NOT swept: re-opening a closed decision every run invites a
# tie-break to silently flip it.
RING_SCORE_MODE = "raw"


def patch_households(events):
    """Give every shared-device account group a common pincode.

    **This is now a no-op, and that is the correct state.** It is kept as a
    regression guard, not as an active correction.

    History. population.py once built households by copying only the device id,
    so two people modelled as sharing a home lived at two different postcodes.
    That left "shares a pincode AND shares a device" with almost no benign
    population, making it close to a pure ring label. Regenerating was refused at
    the time because it would have invalidated the evasive sweep, so this
    function stood in for the generator fix at scoring time. The pipeline
    regenerates from seed, so its first run applied the real fix, and households
    have shared an address ever since. There is nothing left here to repair.

    Why it stays. Running it costs one pass over the events and gives the two
    column comparison in main(), whose delta should now be +0.0000 everywhere.
    If the generator ever loses the shared address again, that delta stops being
    zero. It is the cheapest alarm available for a defect whose only symptom
    last time was a detector quietly beating its own oracle.

    It uses no labels. "Accounts observed on one device" is visible in the event
    stream, and it is applied to every such group, ring and benign alike.
    """
    from src.detector.ring import _invert
    pins, devs, _, _ = account_attributes(events)
    by_dev = _invert(devs)

    canon = {}
    for d in sorted(by_dev):
        accounts = by_dev[d]
        if len(accounts) < 2:
            continue
        cands = [p for a in sorted(accounts) for p in sorted(pins.get(a, ()))]
        if not cands:
            continue
        for a in accounts:
            canon.setdefault(a, cands[0])

    out = []
    for e in events:
        mc = e["merchant_context"]
        a = mc["account_id"]
        if a in canon and mc["shipping_pincode"]:
            mc2 = dict(mc)
            mc2["shipping_pincode"] = canon[a]
            e = dict(e)
            e["merchant_context"] = mc2
        out.append(e)
    return out


def ring_accounts(events, lab):
    out = set()
    for e in events:
        if lab[e["id"]].get("attack_type") == "ring":
            a = e["merchant_context"]["account_id"]
            if a:
                out.add(a)
    return out


def vectors(scores, positives, universe=None):
    """Score vector over a COMMON account universe.

    Without this the pincode baseline is judged over the 10,555 accounts that
    carry a pincode while the ring detector is judged over all 12,482, and the
    two are not comparable. Accounts a detector does not cover score 0.
    """
    ids = sorted(universe if universe is not None else scores)
    y = np.array([1 if a in positives else 0 for a in ids])
    s = np.array([float(scores.get(a, 0.0)) for a in ids])
    return ids, y, s


def best_cut(scores, positives, universe=None):
    """Threshold maximising F1 on this split. Same criterion as the baselines."""
    ids, y, s = vectors(scores, positives, universe)
    cand = sorted({round(v, 6) for v in s if v > 0})
    if not cand:
        return 0.0, 0.0
    best, best_f1 = cand[0], -1.0
    for t in cand:
        _, _, f1, _, _ = prf(y, s >= t)
        if f1 > best_f1:
            best, best_f1 = t, f1
    return best, best_f1


def evaluate(name, scores, positives, thr, universe=None):
    ids, y, s = vectors(scores, positives, universe)
    p, r, f1, tp, fp = prf(y, s >= thr)
    ap = average_precision_score(y, s) if y.sum() else 0.0
    flagged = int((s >= thr).sum())
    return dict(name=name, precision=p, recall=r, f1=f1, ap=ap, tp=tp, fp=fp,
                thr=thr, flagged=flagged, n=len(ids), pos=int(y.sum()),
                ids=ids, y=y, s=s)


def pr_curve(name, y, s):
    """The full curve, because recall saturating early is the thing to see."""
    prec, rec, thr = precision_recall_curve(y, s)
    print(f"\n  {name}: BEST precision at each achievable recall")
    print(f"    {'recall':>8} {'precision':>10} {'threshold':>11}")
    # Best precision per recall level. Keeping an arbitrary point per level made
    # this table contradict the recall-at-fixed-precision table below it.
    best = {}
    for i in range(len(rec)):
        rr = round(float(rec[i]), 4)
        t = float(thr[i]) if i < len(thr) else float("inf")
        if rr not in best or prec[i] > best[rr][0]:
            best[rr] = (float(prec[i]), t)
    for rr in sorted(best):
        p, t = best[rr]
        print(f"    {rr:>8.4f} {p:>10.4f} {t:>11.4f}")
    print(f"    max recall reached: {rec.max():.4f}")


def latency(events, lab, manifest, thr, params, positives):
    """Days from a ring's first event to the first alert on any of its members.

    Replayed daily on everything visible up to that day, which is what a live
    detector has. The THRESHOLD is the frozen one from train; only the evidence
    grows. Reported per ring, never averaged: a ring with no alert is recorded
    as not detected rather than folded into a mean.
    """
    by_ring = collections.defaultdict(list)
    for e in events:
        rid = lab[e["id"]].get("ring_id")
        if rid:
            by_ring[rid].append(e)

    t0 = manifest["window_start"]
    t_end = manifest["window_end"]
    days = sorted({int((e["created_at"] - t0) // 86400) for e in events})

    members = collections.defaultdict(set)
    for rid, evs in by_ring.items():
        for e in evs:
            a = e["merchant_context"]["account_id"]
            if a:
                members[rid].add(a)

    first_ev = {rid: min(e["created_at"] for e in evs) for rid, evs in by_ring.items()}
    hit = {}
    ts_sorted = [e["created_at"] for e in events]
    for d in days:
        cutoff = t0 + (d + 1) * 86400
        if cutoff > t_end + 86400:
            break
        upto = events[:np.searchsorted(ts_sorted, cutoff, side="left")]
        if len(upto) < 50:
            continue
        pending = [r for r in by_ring if r not in hit]
        if not pending:
            break
        sc = score_accounts(upto, **params)
        for rid in pending:
            if any(sc.get(a, 0.0) >= thr for a in members[rid]):
                hit[rid] = cutoff

    print("\n  DETECTION LATENCY PER RING")
    print("    Days from the ring's first FRAUDULENT event to its first alert.")
    print("    Negative means the alert fired BEFORE the ring transacted at all,")
    print("    which is possible because members share the drop address and the")
    print("    device from account setup, so the structure exists throughout the")
    print("    dormancy period and is visible before the ring does anything.")
    print(f"    {'ring':>6} {'members':>8} {'events':>7} {'first fraud':>13} "
          f"{'latency (days)':>15} {'never caught':>13}")
    caught_flags = {r["ring_id"]: r["never_caught"] for r in manifest.get("rings", [])}
    for rid in sorted(by_ring):
        n_ev = len(by_ring[rid])
        fe = first_ev[rid]
        if rid in hit:
            lat = (hit[rid] - fe) / 86400.0
            cell = f"{lat:+.1f}"
        else:
            cell = "NOT DETECTED"
        print(f"    {rid:>6} {len(members[rid]):>8} {n_ev:>7} "
              f"{(fe-t0)/86400.0:>12.1f}d {cell:>15} "
              f"{str(caught_flags.get(rid, '?')):>13}")


def false_positives(events, scores, positives, thr, top=12):
    """Which innocent accounts get flagged, and on what evidence."""
    pins, devs, cons, _ = account_attributes(events)
    comps, by_pin = conjunction_components(pins, devs)
    in_comp = set()
    for c in comps:
        in_comp |= c

    fps = sorted(((s, a) for a, s in scores.items()
                  if s >= thr and a not in positives), reverse=True)
    print(f"\n  FALSE POSITIVES: {len(fps)} innocent accounts flagged")
    if not fps:
        return
    print(f"    {'account':<22} {'score':>8} {'in conj':>8} {'pincode pop':>12} "
          f"{'devices':>8} {'shared phone':>13}")
    for s, a in fps[:top]:
        pop = max((len(by_pin[p]) for p in pins.get(a, ())), default=0)
        shared = any(len(_inv_get(cons, c)) > 1 for c in cons.get(a, ()))
        print(f"    {a:<22} {s:>8.4f} {str(a in in_comp):>8} {pop:>12} "
              f"{len(devs.get(a, ())):>8} {str(shared):>13}")
    if len(fps) > top:
        print(f"    ... and {len(fps)-top} more")

    n_conj = sum(1 for _, a in fps if a in in_comp)
    print(f"\n    of {len(fps)} false positives, {n_conj} are themselves in a "
          f"conjunction component")
    print(f"    and {len(fps)-n_conj} are guilt-by-drop-address only")


_CON_INDEX = {}


def _inv_get(cons, c):
    key = id(cons)
    if key not in _CON_INDEX:
        inv = collections.defaultdict(set)
        for a, vals in cons.items():
            for v in vals:
                inv[v].add(a)
        _CON_INDEX[key] = inv
    return _CON_INDEX[key][c]


def run(events, manifest, lab, tag, note):
    """Tune on train, score test once, report. Shared by the two populations."""
    cut = int(len(events) * SPLIT)
    tr, te = events[:cut], events[cut:]
    pos_tr = ring_accounts(tr, lab)
    pos_te = ring_accounts(te, lab)

    # Common account universe. Every detector is judged over every account seen
    # in the test split, scoring 0 where it has no opinion.
    universe = set()
    for e in te:
        a = e["merchant_context"]["account_id"]
        if a:
            universe.add(a)
    uni_tr = set()
    for e in tr:
        a = e["merchant_context"]["account_id"]
        if a:
            uni_tr.add(a)

    print("\n" + "=" * 88)
    print(f"{tag}")
    print("=" * 88)
    print(f"  {note}")
    print(f"  events {len(events)}  train {cut} / test {len(events)-cut}")
    print(f"  ring accounts: train {len(pos_tr)}, test {len(pos_te)}")
    print(f"  common account universe: train {len(uni_tr)}, test {len(universe)}")

    # -------------------------------------------------------------- tune
    grid = []
    for sm in (RING_SCORE_MODE,):
        for mc in (2, 3):
            for ow in (0.0, 0.15, 0.35, 0.60, 1.0):
                for cw in (0.0,):
                    for mp in (0, 4, 6, 8):
                        p = {"min_component": mc, "out_weight": ow,
                             "contact_weight": cw, "min_pin_population": mp,
                             "score_mode": sm}
                        s = score_accounts(tr, **p)
                        thr, f1 = best_cut(s, pos_tr, uni_tr)
                        grid.append((f1, p, thr))
    # Selection, in three stages, all on TRAIN.
    #   1. train F1, which measures discrimination inside one window
    #   2. among everything tied at the top, train-side TRANSFER
    #   3. still tied, prefer the LARGER min_pin_population
    # Stage 3 is a prior, not a measurement: the gate encodes "a drop address
    # serves more people than a household contains", so where the data cannot
    # separate two gates the stricter one is the more defensible claim. It is
    # fixed here rather than left to dict ordering so that the choice is stated
    # instead of falling out of an arbitrary tie-break, which is how the worst
    # scoring mode got selected on the previous pass.
    top = max(f1 for f1, _, _ in grid)
    tied = [g for g in grid if g[0] >= top - 1e-9]
    scored = []
    for f1, p, thr in tied:
        mean_f1, fired, n = train_transfer(tr, lab, p, thr)
        scored.append((mean_f1, fired, f1, p, thr))
    scored.sort(key=lambda t: (-t[0], -t[1], -t[3]["min_pin_population"]))
    print(f"\n  train F1 ties at {top:.4f} across {len(tied)} configurations, "
          f"so the tie is broken on TRAIN-SIDE TRANSFER:")
    print(f"    {'mode':>9} {'min_pop':>8} {'sub-window F1':>14} {'fired':>7}")
    seen = set()
    for mean_f1, fired, f1, p, thr in scored:
        key = (p["score_mode"], p["min_pin_population"])
        if key in seen:
            continue
        seen.add(key)
        print(f"    {p['score_mode']:>9} {p['min_pin_population']:>8} "
              f"{mean_f1:>14.4f} {fired:>5}/3")
        if len(seen) >= 8:
            break
    _, _, best_f1, P, THR = scored[0]
    grid = [(f1, p, thr) for _, _, f1, p, thr in scored]
    print(f"\n  tuned on TRAIN: {P}, threshold {THR:.4f} (train F1 {best_f1:.4f})")
    print("  top of the train sweep:")
    for f1, p, t in grid[:5]:
        print(f"    F1 {f1:.4f}  {p}  thr {t:.4f}")

    # -------------------------------------------------------- score test once
    s_ours = score_accounts(te, **P)
    s_pin = score_pincode_sharing(te)
    s_stage1 = score_accounts(te, min_component=P["min_component"],
                              out_weight=0.0, contact_weight=0.0,
                              min_pin_population=P["min_pin_population"],
                              score_mode=P["score_mode"])

    pin_thr, _ = best_cut(score_pincode_sharing(tr), pos_tr, uni_tr)
    s1_thr, _ = best_cut(score_accounts(
        tr, min_component=P["min_component"], out_weight=0.0, contact_weight=0.0,
        min_pin_population=P["min_pin_population"],
        score_mode=P["score_mode"]), pos_tr, uni_tr)

    rows = [
        evaluate("pincode baseline (peers on pincode)", s_pin, pos_te, pin_thr, universe),
        evaluate("stage 1 only: conjunction", s_stage1, pos_te, s1_thr, universe),
        evaluate("RING DETECTOR: conj + drop addr", s_ours, pos_te, THR, universe),
    ]

    print("\n" + "-" * 88)
    print("TEST SPLIT, scored once   (account level, common universe)")
    print("-" * 88)
    print(f"  {'detector':<36} {'prec':>7} {'recall':>7} {'F1':>7} {'PR AUC':>8} "
          f"{'TP':>4} {'FP':>6} {'flagged':>8} {'of':>7}")
    for r in rows:
        print(f"  {r['name']:<36} {r['precision']:>7.4f} {r['recall']:>7.4f} "
              f"{r['f1']:>7.4f} {r['ap']:>8.4f} {r['tp']:>4} {r['fp']:>6} "
              f"{r['flagged']:>8} {r['n']:>7}")
    print(f"\n  ring accounts in test: {rows[0]['pos']} of {rows[0]['n']} "
          f"({rows[0]['pos']/rows[0]['n']*100:.3f}%) — that is the base rate")

    print()
    print("  NO ORACLE CEILING IS AVAILABLE FOR RINGS")
    print("    This block used to report a structure-oracle ceiling of 0.4400 at")
    print("    precision 0.70. That ceiling was an artefact and was withdrawn on")
    print("    2026-08-30. The oracle dominant term is the pincode-and-device")
    print("    conjunction, which had ZERO benign instances before the household")
    print("    fix: 3 such components in the whole stream, all three of them rings.")
    print("    Post-fix there are 559, of which 556 are benign, and the oracle")
    print("    falls to 0.0000. Disabling that term leaves it at chance on both")
    print("    populations, 0.0011 and 0.0010 against a 0.0010 base rate, so it")
    print("    never had ring signal of its own.")
    print("    We did NOT give the oracle this detector population gate. An oracle")
    print("    built in the detector image measures our approach rather than the")
    print("    achievable signal. The T8 ring leg is UNBOUNDED, not failed, and how")
    print("    much ring signal is recoverable from this stream is not known.")
    print("    Nothing below depends on the oracle, and nothing ever did.")

    for r in rows:
        pr_curve(r["name"], r["y"], r["s"])

    print("\n" + "=" * 88)
    print("RECALL AT FIXED PRECISION (test split)")
    print("=" * 88)
    print(f"  {'detector':<36} " + "".join(f"{('P>=%.2f' % t):>10}"
                                           for t in (0.30, 0.50, 0.70, 0.90)))
    for r in rows:
        cells = []
        for target in (0.30, 0.50, 0.70, 0.90):
            prec, rec, _ = precision_recall_curve(r["y"], r["s"])
            best = max([ri for pi, ri in zip(prec, rec) if pi >= target],
                       default=0.0)
            cells.append(f"{best:>10.4f}")
        print(f"  {r['name']:<36} " + "".join(cells))

    latency(events, lab, manifest, THR, P, pos_te)
    false_positives(te, s_ours, pos_te, THR)
    return rows, P, THR


def train_transfer(tr, lab, params, thr, fracs=(0.30, 0.50, 0.70)):
    """Does this threshold still fire on shorter windows WITHIN train?

    Train F1 cannot choose between the scale-free modes, and not by accident:
    raw, bg, q95 and rank are monotone transforms of one another inside a single
    window, so they produce identical rankings and therefore identical train F1.
    Selecting on F1 leaves the choice to an arbitrary tie-break, which is how the
    worst-transferring mode got picked first.

    Transfer is the property being bought, so transfer is what has to be
    measured. This applies the full-train threshold to sub-windows OF TRAIN and
    reports how it holds up. It touches no test data.
    """
    scores = []
    fired = 0
    for f in fracs:
        sub = tr[int(len(tr) * (1.0 - f)):]
        pos = ring_accounts(sub, lab)
        uni = {e["merchant_context"]["account_id"] for e in sub
               if e["merchant_context"]["account_id"]}
        if not pos:
            continue
        _, y, s = vectors(score_accounts(sub, **params), pos, uni)
        _, _, f1, _, _ = prf(y, s >= thr)
        scores.append(f1)
        fired += int((s >= thr).sum() > 0)
    return (sum(scores) / len(scores) if scores else 0.0), fired, len(scores)


def stability(events, lab, P, THR, modes=SCORE_MODES):
    """Does the train threshold still mean the same thing on a different window?

    The whole point of the fix. Each mode is scored on test windows of several
    lengths using the threshold tuned on the 70% train split, and we look at
    whether the cut keeps firing and whether the raw score magnitudes drift.
    """
    print("\n" + "=" * 88)
    print("SCALE INVARIANCE: one train threshold, several test window lengths")
    print("=" * 88)
    print("  The threshold is tuned once on the 70% train split of the patched")
    print("  population and then applied unchanged to test windows of different")
    print("  lengths. A scale-free score keeps firing; a scale-dependent one")
    print("  drifts out of range, which is the defect being fixed.")

    cut = int(len(events) * SPLIT)
    tr = events[:cut]
    pos_tr = ring_accounts(tr, lab)
    uni_tr = {e["merchant_context"]["account_id"] for e in tr
              if e["merchant_context"]["account_id"]}

    for mode in modes:
        params = dict(P)
        params["score_mode"] = mode
        thr, _ = best_cut(score_accounts(tr, **params), pos_tr, uni_tr)
        print(f"\n  mode {mode!r}: threshold tuned on train = {thr:.6g}")
        print(f"    {'test window':>13} {'events':>8} {'accounts':>9} "
              f"{'max score':>11} {'flagged':>8} {'prec':>7} {'recall':>7} "
              f"{'PR AUC':>8}")
        for frac in (0.10, 0.20, 0.30, 0.50):
            te = events[int(len(events) * (1.0 - frac)):]
            pos = ring_accounts(te, lab)
            uni = {e["merchant_context"]["account_id"] for e in te
                   if e["merchant_context"]["account_id"]}
            if not pos:
                continue
            s = score_accounts(te, **params)
            r = evaluate(mode, s, pos, thr, uni)
            top = max(s.values()) if s else 0.0
            print(f"    {('last %.0f%%' % (frac*100)):>13} {len(te):>8} "
                  f"{len(uni):>9} {top:>11.4g} {r['flagged']:>8} "
                  f"{r['precision']:>7.4f} {r['recall']:>7.4f} {r['ap']:>8.4f}")


def main(path):
    events = load_events(path)
    manifest = load_manifest(path)
    lab = labels_by_id(path)

    print("=" * 88)
    print(f"RING DETECTOR, ACCOUNT LEVEL   data={path}")
    print("=" * 88)

    a, _, _ = run(events, manifest, lab, "POPULATION AS GENERATED",
                  "Households share a device but NOT a pincode. See the caveat "
                  "below.")

    patched = patch_households(events)
    b, P, THR = run(patched, manifest, lab,
                    "COUNTERFACTUAL: HOUSEHOLDS SHARE AN ADDRESS",
                    "Every shared-device account group given a common pincode, "
                    "using no labels.")

    stability(patched, lab, P, THR)

    print("\n" + "=" * 88)
    print("THE TWO POPULATIONS SIDE BY SIDE (test split, PR AUC)")
    print("=" * 88)
    print(f"  {'detector':<36} {'as generated':>14} {'households fixed':>18} "
          f"{'delta':>9}")
    for x, y in zip(a, b):
        print(f"  {x['name']:<36} {x['ap']:>14.4f} {y['ap']:>18.4f} "
              f"{y['ap']-x['ap']:>+9.4f}")
    print("\n  BOTH COLUMNS ARE NOW THE SAME POPULATION, and the delta above")
    print("  should read +0.0000 throughout. That is the expected result, not a")
    print("  bug: population.py now gives household members a shared address, so")
    print("  patch_households finds nothing left to repair and is a no-op.")
    print()
    print("  History, because the columns are otherwise puzzling. Households")
    print("  once shared a device but not a pincode, which left the conjunction")
    print("  with almost no benign population to compete with and inflated this")
    print("  detector to PR AUC 0.9291. We would not regenerate at the time,")
    print("  because that would have invalidated the evasive sweep, so the right")
    print("  column was a label-free counterfactual standing in for the real")
    print("  fix. The pipeline later regenerated from seed and the fix landed.")
    print()
    print("  The comparison is kept as a REGRESSION GUARD. If the generator ever")
    print("  loses the shared address again, these two columns separate and this")
    print("  delta stops being zero, which is the cheapest available alarm for a")
    print("  defect that took a detector beating its own oracle to notice.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/sample")
