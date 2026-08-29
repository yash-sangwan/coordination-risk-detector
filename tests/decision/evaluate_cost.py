"""Money-optimal against F1-optimal, and the price of a frozen threshold.

    python -m tests.decision.evaluate_cost data/evasive

No detector is retuned here. The detector scores are exactly the ones already
committed, with their windows frozen at the v=0.00 train values used everywhere
else. What is chosen here is an OPERATING POINT, and it is chosen twice, once by
F1 and once by rupees, so the two can be compared.

Labels live in this file, never in src/decision/.
"""

import sys

import numpy as np

from src.decision import cost as C
from src.decision.policy import ACTIONS, choose, decide, tier_boundaries
from src.detector.baselines import score_combined, score_decline, score_volume
from src.detector.graph import components, score_card_testing
from tests.baselines.evaluate import best_threshold, prf
from tests.detector.evaluate_sweep import _load, freeze, grades
from tests.fixtures import load_events, load_manifest, labels_by_id

SPLIT = 0.70


def measured_authorize_rate(events, y):
    """MEASURED, per dataset: what share of attack attempts authorise.

    This is the input that makes a missed attempt more expensive as the
    attacker evades, and it is read from the stream rather than assumed.
    """
    atk = [e for e, yy in zip(events, y) if yy]
    if not atk:
        return 0.0
    return sum(1 for e in atk if e["status"] != "failed") / len(atk)


def actions_for(scores, thr, y, amounts, p_auth, p_fraud_at):
    """Map scores to bounded actions. Above threshold, the tier is chosen by
    cost; below it, we monitor."""
    out = []
    for s, amt in zip(scores, amounts):
        if s < thr:
            out.append("MONITOR")
        else:
            a, _ = choose(p_fraud_at(s), amt, p_auth)
            out.append(a)
    return out


def calibrator(scores_tr, y_tr):
    """Score to P(fraud), fitted on TRAIN only.

    Bucketed empirical precision: for a score s, the observed fraud rate among
    train events scoring at least s. Monotone by construction and needs no
    distributional assumption.
    """
    order = np.argsort(-np.asarray(scores_tr, dtype=float))
    s_sorted = np.asarray(scores_tr, dtype=float)[order]
    y_sorted = np.asarray(y_tr)[order]
    cum = np.cumsum(y_sorted) / np.arange(1, len(y_sorted) + 1)

    def p_at(s):
        i = int(np.searchsorted(-s_sorted, -float(s), side="right"))
        if i <= 0:
            return float(cum[0]) if len(cum) else 0.0
        return float(cum[min(i, len(cum)) - 1])
    return p_at


def sweep_thresholds(scores, y, amounts, p_auth, p_fraud_at, n=160):
    """Total rupee cost across candidate thresholds."""
    s = np.asarray(scores, dtype=float)
    cand = np.unique(np.round(s[s > 0], 6))
    if len(cand) == 0:
        return [], []
    if len(cand) > n:
        cand = cand[np.linspace(0, len(cand) - 1, n).astype(int)]
    costs = []
    for t in cand:
        acts = actions_for(s, t, y, amounts, p_auth, p_fraud_at)
        costs.append(C.total_cost(y, acts, amounts, p_auth))
    return list(cand), costs


def evaluate_point(scores, thr, y, amounts, p_auth, p_fraud_at):
    acts = actions_for(scores, thr, y, amounts, p_auth, p_fraud_at)
    money = C.total_cost(y, acts, amounts, p_auth)
    alert = np.asarray(scores, dtype=float) >= thr
    p, r, f1, tp, fp = prf(np.asarray(y), alert)
    from collections import Counter
    return money, p, r, f1, tp, fp, Counter(acts)


def detector_scores(events, P):
    v, d, c, g = P["volume"], P["decline"], P["combined"], P["graph"]
    return {
        "baseline 1: rolling volume": score_volume(events, v["window_s"]),
        "baseline 2: rolling decline": score_decline(events, d["window_s"],
                                                     d["min_events"]),
        "baseline 3: combined": score_combined(events, c["window_s"],
                                               c["min_events"], c["vol_ref"],
                                               c["dec_ref"]),
        "GRAPH: fanout vs overlap": score_card_testing(
            events, g["window_s"], g["min_events"],
            comp=components(events, g["window_s"])),
    }


def print_params():
    print("=" * 96)
    print("COST MODEL. Every parameter tagged CITED, MEASURED or ASSUMPTION.")
    print("=" * 96)
    for name, val, tag, src in C.describe():
        print(f"\n  {name}  =  {val}   [{tag}]")
        for line in _wrap(src, 86):
            print(f"      {line}")
    print("\n  MEASURED per dataset, from the generated stream, not assumed:")
    print("      p_authorize        share of attack attempts that authorise")
    print("      order amounts      taken per event from the stream")
    print("      attack prevalence  the base rate the calibrator sees")


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


def main(root):
    steps = grades(root)
    frozen_path = dict(steps)[0.00]
    P = freeze(frozen_path)

    print_params()

    # ---- the cited asymmetry, as a check on the model rather than an input
    ev0, mf0, lab0, cut0, y0 = _load(frozen_path)
    pa0 = measured_authorize_rate(ev0, y0)
    mean_legit = float(np.mean([e["amount"] for e, yy in zip(ev0, y0) if not yy]))
    mean_atk = float(np.mean([e["amount"] for e, yy in zip(ev0, y0) if yy]))
    ratio = C.implied_decline_ratio(mean_legit, mean_atk, pa0)
    lo, hi = C.p("cited_decline_ratio_lo"), C.p("cited_decline_ratio_hi")
    print("\n" + "=" * 96)
    print("CHECK: does the model reproduce the cited asymmetry?")
    print("=" * 96)
    print(f"  mean legitimate order   Rs {mean_legit/100:,.2f}   [MEASURED]")
    print(f"  mean attack attempt     Rs {mean_atk/100:,.2f}   [MEASURED]")
    print(f"  attack authorise rate   {pa0*100:.2f}%   [MEASURED]")
    print(f"  implied ratio, rupees lost to a false decline per rupee saved by "
          f"blocking fraud: {ratio:.2f}x")
    print(f"  cited band              {lo:.0f}x to {hi:.0f}x   [CITED: Razorpay]")
    print(f"  verdict                 "
          f"{'INSIDE the cited band' if lo <= ratio <= hi else 'OUTSIDE the cited band, see below'}")
    if not (lo <= ratio <= hi):
        clo, chi = C.implied_churn_multiple(mean_legit, mean_atk, pa0)
        print("\n  The check FAILS, and the reason is structural rather than a bad")
        print("  parameter. This model prices the IMMEDIATE order only: one lost")
        print("  margin. The cited figure is the full economic cost of a false")
        print("  decline, which is dominated by the customer not returning. No")
        print("  single-order model reaches 4-6x at any plausible margin.")
        print(f"\n  Solving the other way, the citation implies a false decline costs")
        print(f"  {clo:.1f}x to {chi:.1f}x one order's margin, so roughly "
              f"{clo-1:.1f} to {chi-1:.1f} further")
        print("  orders of lost repeat business. That is derived from the citation,")
        print("  not fitted to it, and it is not used as an input anywhere.")
        print("\n  The direction is the safe one. Counting only the immediate order")
        print("  UNDERSTATES what over-blocking costs, so every operating point")
        print("  below is if anything more aggressive than the citation justifies.")
        print("  It also barely matters in practice: this system never declines,")
        print("  and step-up and hold friction are small next to a lost customer.")

    # ---- money optimal vs F1 optimal, per detector, at v=0.00
    print("\n" + "=" * 96)
    print("MONEY-OPTIMAL AGAINST F1-OPTIMAL OPERATING POINT   (v=0.00, test split)")
    print("=" * 96)
    frozen_money = {}
    for v, path in steps:
        events, mf, lab, cut, y = _load(path)
        amounts = [e["amount"] for e in events]
        p_auth = measured_authorize_rate(events, y)
        scores = detector_scores(events, P)
        if v == 0.00:
            print(f"  {'detector':<30} {'F1 thr':>9} {'money thr':>10} "
                  f"{'F1 cost':>13} {'money cost':>13} {'gap Rs':>12} {'gap %':>7}")
        for name, s in scores.items():
            s = np.asarray(s, dtype=float)
            cal = calibrator(s[:cut], y[:cut])
            f1_thr, _ = best_threshold(s[:cut], y[:cut])
            cand, costs = sweep_thresholds(s[cut:], y[cut:], amounts[cut:],
                                           p_auth, cal)
            if not cand:
                continue
            money_thr = float(cand[int(np.argmin(costs))])
            money_cost = float(min(costs))
            f1_cost, *_ = evaluate_point(s[cut:], f1_thr, y[cut:], amounts[cut:],
                                         p_auth, cal)
            if v == 0.00:
                gap = f1_cost - money_cost
                print(f"  {name:<30} {f1_thr:>9.4f} {money_thr:>10.4f} "
                      f"{f1_cost:>13,.0f} {money_cost:>13,.0f} {gap:>12,.0f} "
                      f"{(gap/f1_cost*100 if f1_cost else 0):>6.2f}%")
                frozen_money[name] = money_thr

    # ---- price of not adapting
    print("\n" + "=" * 96)
    print("THE PRICE OF NOT ADAPTING")
    print("=" * 96)
    print("  A threshold frozen at the v=0.00 money-optimum, against the")
    print("  money-optimum recomputed at each grade. Test split of each dataset.")
    for name in frozen_money:
        print(f"\n  {name}")
        print(f"    {'grade':>6} {'decline':>8} {'p_auth':>8} {'frozen thr':>11} "
              f"{'best thr':>10} {'frozen Rs':>12} {'best Rs':>12} "
              f"{'excess Rs':>12} {'excess %':>9}")
        for v, path in steps:
            events, mf, lab, cut, y = _load(path)
            amounts = [e["amount"] for e in events]
            p_auth = measured_authorize_rate(events, y)
            s = np.asarray(detector_scores(events, P)[name], dtype=float)
            cal = calibrator(s[:cut], y[:cut])
            cand, costs = sweep_thresholds(s[cut:], y[cut:], amounts[cut:],
                                           p_auth, cal)
            if not cand:
                continue
            best_thr = float(cand[int(np.argmin(costs))])
            best_cost = float(min(costs))
            froz_cost, *_ = evaluate_point(s[cut:], frozen_money[name], y[cut:],
                                           amounts[cut:], p_auth, cal)
            dec = sum(1 for e, yy in zip(events, y)
                      if yy and e["status"] == "failed") / max(sum(y), 1)
            excess = froz_cost - best_cost
            print(f"    {v:>6.2f} {dec*100:>7.2f}% {p_auth*100:>7.2f}% "
                  f"{frozen_money[name]:>11.4f} {best_thr:>10.4f} "
                  f"{froz_cost:>12,.0f} {best_cost:>12,.0f} {excess:>12,.0f} "
                  f"{(excess/best_cost*100 if best_cost else 0):>8.2f}%")

    worked_example(frozen_path, P, frozen_money)


def worked_example(path, P, frozen_money):
    """One alert, end to end."""
    print("\n" + "=" * 96)
    print("WORKED EXAMPLE: ONE ALERT RECORD, END TO END")
    print("=" * 96)
    events, mf, lab, cut, y = _load(path)
    amounts = [e["amount"] for e in events]
    p_auth = measured_authorize_rate(events, y)
    name = "GRAPH: fanout vs overlap"
    s = np.asarray(detector_scores(events, P)[name], dtype=float)
    cal = calibrator(s[:cut], y[:cut])
    thr = frozen_money[name]

    idx = next(i for i in range(cut, len(events))
               if s[i] >= thr and y[i] == 1)
    e = events[idx]
    g = P["graph"]
    comp = components(events, g["window_s"])
    hhi_iin = comp["iin"][idx]
    hhi_dev = comp["device"][idx]
    hhi_email = comp["email"][idx]
    evidence = {
        "window": f"{g['window_s']}s trailing",
        "attempts in window": hhi_iin[2],
        "IIN concentration": f"{hhi_iin[0]:.4f} over {hhi_iin[1]} distinct",
        "device concentration": f"{hhi_dev[0]:.4f} over {hhi_dev[1]} distinct",
        "identity fanout": f"{1 - hhi_email[0]:.4f} over {hhi_email[1]} emails",
        "shipping pincode": e["merchant_context"]["shipping_pincode"] or "null",
        "account_id": e["merchant_context"]["account_id"] or "null (guest)",
        "checkout_ms": e["merchant_context"]["checkout_ms"],
    }
    rec = decide(e, name, s[idx], thr, cal(s[idx]), p_auth, evidence,
                 note="card testing burst; converging instrument and device "
                      "against scattering identities")
    print(rec.render())
    print("  The same record as data, for the audit log:")
    import json
    print("   ", json.dumps({
        "event_id": rec.event_id, "action": rec.action,
        "reversible": rec.reversible, "score": round(rec.score, 6),
        "threshold": round(rec.threshold, 6), "p_fraud": round(rec.p_fraud, 6),
        "amount_paise": rec.amount_paise, "rationale": rec.rationale,
        "expected_costs": {k: round(v, 2) for k, v in rec.expected_costs.items()},
    }, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/evasive")
