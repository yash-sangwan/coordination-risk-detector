"""Acceptance tests T1 to T8, spec section 5. Measures and reports only.

    python -m tests.acceptance.runner data/sample

Nothing here fixes, tunes or adjusts anything. A failure is reported as a
failure. Thresholds are exactly as written in the spec.
"""

import bisect
import collections
import json
import math
import os
import subprocess
import sys

import numpy as np
from scipy import stats as sps
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, precision_recall_curve

from src.generator import config as C
from src.generator.report import pair_collision, account_share_rate
from tests.fixtures import load_events, load_manifest, labels_by_id
from tests.acceptance.isolation import check_import_isolation
from tests.oracle.structure_oracle import configure, score

SPLIT = 0.70          # chronological, same protocol across every test that splits
RESULTS = []


def record(name, ok, detail):
    RESULTS.append((name, ok, detail))


def _fit_auc(X, y, cut, categorical=None, seed=0):
    """Chronological fit. Returns None when a split has no positives."""
    if y[:cut].sum() < 5 or y[cut:].sum() < 5:
        return None
    m = HistGradientBoostingClassifier(max_iter=150, random_state=seed,
                                       categorical_features=categorical)
    m.fit(X[:cut], y[:cut])
    return roc_auc_score(y[cut:], m.predict_proba(X[cut:])[:, 1])


# ---------------------------------------------------------------- field table

def flatten(e):
    mc = e["merchant_context"]
    card = e.get("card") or {}
    return {
        "created_at": e["created_at"],
        "amount": e["amount"],
        "currency": e["currency"],
        "international": e["international"],
        "method": e["method"],
        "card.iin": card.get("iin"),
        "card.last4": card.get("last4"),
        "card.network": card.get("network"),
        "card.type": card.get("type"),
        "card.issuer": card.get("issuer"),
        "vpa": e.get("vpa"),
        "bank": e.get("bank"),
        "wallet": e.get("wallet"),
        "email": e["email"],
        "contact": e["contact"],
        "status": e["status"],
        "error_code": e.get("error_code"),
        "error_source": e.get("error_source"),
        "error_step": e.get("error_step"),
        "error_reason": e.get("error_reason"),
        "notes": json.dumps(e["notes"], sort_keys=True),
        "mc.account_id": mc["account_id"],
        "mc.device_id": mc["device_id"],
        "mc.session_id": mc["session_id"],
        "mc.attempt_seq": mc["attempt_seq"],
        "mc.checkout_ms": mc["checkout_ms"],
        "mc.shipping_pincode": mc["shipping_pincode"],
        "mc.account_age_days": mc["account_age_days"],
        "id": e["id"],
        "order_id": e["order_id"],
    }


NULL_KEY = chr(0) + "NULL"

NUMERIC = {"created_at", "amount", "mc.attempt_seq", "mc.checkout_ms",
           "mc.account_age_days", "international"}


MAX_CAT = 255   # sklearn HistGradientBoosting categorical limit


def encode(rows, field):
    """Returns (X, categorical_mask, how).

    Low-cardinality fields are passed as true categoricals. High-cardinality
    identifiers exceed sklearn's 255-category limit, so they are frequency
    encoded: each value becomes how often it occurs. That encoding is label-free
    and answers what the test actually asks, which is whether "this value is
    rare" leaks the label. Ordinal-by-first-appearance was rejected because it
    smuggles a time correlation into an identifier field.
    """
    vals = [r[field] for r in rows]
    if field in NUMERIC:
        x = np.array([np.nan if v is None else float(v) for v in vals])
        return x.reshape(-1, 1), None, "numeric"
    keys = [NULL_KEY if v is None else str(v) for v in vals]
    card = len(set(keys))
    if card <= MAX_CAT:
        cats = {}
        out = np.empty(len(keys))
        for i, k in enumerate(keys):
            out[i] = cats.setdefault(k, len(cats))
        return out.reshape(-1, 1), [True], "categorical(%d)" % card
    freq = collections.Counter(keys)
    out = np.array([float(freq[k]) for k in keys])
    return out.reshape(-1, 1), None, "freq-encoded(%d)" % card


# ---------------------------------------------------------------------- tests

def t1(rows, y, cut):
    print("\n--- T1: single-feature ceiling (every field, chronological split) ---")
    print(f"  {'field':<22} {'AUC':>8}   {'verdict':<42} encoding")
    fields = list(flatten_keys)
    results = {}
    hows = {}
    for f in fields:
        X, cat, how = encode(rows, f)
        auc = _fit_auc(X, y, cut, categorical=cat)
        if auc is None:
            continue
        results[f] = auc
        hows[f] = how
    fails, investigate, noise = [], [], []
    for f, auc in sorted(results.items(), key=lambda kv: -kv[1]):
        if auc > 0.75:
            v = "FAIL  > 0.75"
            fails.append((f, auc))
        elif auc > 0.70:
            v = "investigate"
            investigate.append((f, auc))
        elif abs(auc - 0.5) < 0.005:
            v = "pass, but ~0.500 (spec: cut, do not ship)"
            noise.append((f, auc))
        else:
            v = "pass"
        print(f"  {f:<22} {auc:>8.4f}   {v:<42} {hows[f]}")
    record("T1 single-feature ceiling", not fails,
           f"{len(fails)} field(s) above 0.75"
           + (f", worst {fails[0][0]}={fails[0][1]:.4f}" if fails else "")
           + f"; {len(investigate)} in 0.70-0.75; {len(noise)} at ~0.500")
    return results, fails, investigate, noise


def t2(rows, y, cut, rng):
    Xs, cats = [], []
    for f in flatten_keys:
        X, c, _ = encode(rows, f)
        Xs.append(X)
        cats.append(bool(c))
    X = np.hstack(Xs)
    catmask = [c for c in cats]
    aucs = []
    ytr = y[:cut].copy()
    for i in range(20):
        sh = ytr.copy()
        rng.shuffle(sh)
        m = HistGradientBoostingClassifier(max_iter=100, random_state=i,
                                           categorical_features=catmask)
        m.fit(X[:cut], sh)
        aucs.append(roc_auc_score(y[cut:], m.predict_proba(X[cut:])[:, 1]))
    mean = float(np.mean(aucs))
    lo, hi = float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))
    ok = (0.48 <= mean <= 0.52) and (lo <= 0.50 <= hi) and max(aucs) <= 0.55
    record("T2 label shuffle", ok,
           f"mean {mean:.4f} (need 0.48-0.52), 95% [{lo:.4f},{hi:.4f}] "
           f"must contain 0.50, max {max(aucs):.4f} (fail above 0.55)")
    return mean, lo, hi


def t3(events, lab):
    mc = lambda e: e["merchant_context"]
    ben = [e for e in events if lab[e["id"]]["label"] == 0]
    atk = [e for e in events if lab[e["id"]]["label"] == 1]
    n = len(ben)

    def ap(R, v):
        return [(mc(e)["account_id"], v(e)) for e in R
                if mc(e)["account_id"] is not None and v(e) is not None]

    doms = [e["email"].split("@")[1] for e in ben]
    contact_rate = account_share_rate(ap(ben, lambda e: e["contact"]))
    vpa_target = contact_rate * 0.92 ** 2 * 0.72

    checks = [
        ("card.iin", pair_collision([e["card"]["iin"] for e in ben if e.get("card")]),
         (0.08, 0.15)),
        ("device_id", account_share_rate(ap(ben, lambda e: mc(e)["device_id"])),
         (0.06 * 0.8, 0.06 * 1.2)),
        ("contact", contact_rate, (0.015 * 0.8, 0.015 * 1.2)),
        ("email top-3 domains",
         sum(c for _, c in collections.Counter(doms).most_common(3)) / n,
         (0.70 * 0.8, 0.70 * 1.2)),
        ("vpa local part",
         account_share_rate(ap(ben, lambda e: e["vpa"].split("@")[0] if e.get("vpa") else None)),
         (vpa_target * 0.8, vpa_target * 1.2)),
        ("shipping_pincode",
         pair_collision([mc(e)["shipping_pincode"] for e in ben
                         if mc(e)["shipping_pincode"] is not None]),
         (0.001468 * 0.8, 0.001468 * 1.2)),
    ]

    # Ratio leg, on a single consistent measure (pair collision) so the two
    # populations are comparable.
    def pc(R, v):
        return pair_collision([v(e) for e in R if v(e) is not None])
    ratio_fields = [
        ("card.iin", lambda e: (e.get("card") or {}).get("iin")),
        ("device_id", lambda e: mc(e)["device_id"]),
        ("contact", lambda e: e["contact"]),
        ("email domain", lambda e: e["email"].split("@")[1]),
        ("vpa local part", lambda e: e["vpa"].split("@")[0] if e.get("vpa") else None),
        ("shipping_pincode", lambda e: mc(e)["shipping_pincode"]),
    ]

    print("\n--- T3: benign collision rates (label-0 only) ---")
    print(f"  {'attribute':<20} {'observed':>10} {'band':>20}   verdict")
    band_fail, zero_fail = [], []
    for name, v, (lo, hi) in checks:
        ok = lo <= v <= hi
        if v <= 0:
            zero_fail.append(name)
        elif not ok:
            band_fail.append((name, v, lo, hi))
        print(f"  {name:<20} {v*100:>9.3f}% {lo*100:>9.3f}-{hi*100:.3f}%   "
              f"{'pass' if ok else 'FAIL'}")

    print(f"\n  {'attribute':<20} {'benign':>10} {'attack':>10} {'ratio':>9}   verdict (<50x)")
    ratio_fail = []
    for name, fn in ratio_fields:
        b, a = pc(ben, fn), pc(atk, fn)
        r = (a / b) if b > 0 else float("inf")
        ok = math.isfinite(r) and r < 50
        if not ok:
            ratio_fail.append((name, r))
        print(f"  {name:<20} {b*100:>9.3f}% {a*100:>9.3f}% {r:>8.1f}x   "
              f"{'pass' if ok else 'FAIL'}")

    ok = not band_fail and not zero_fail and not ratio_fail
    record("T3 benign collisions", ok,
           f"{len(band_fail)} outside band, {len(zero_fail)} at zero, "
           f"{len(ratio_fail)} ratio >= 50x"
           + (f" (worst {ratio_fail[0][0]} {ratio_fail[0][1]:.0f}x)" if ratio_fail else ""))
    return band_fail, zero_fail, ratio_fail


def t4(events, lab, manifest, y, cut):
    n = len(events)
    ts = [e["created_at"] for e in events]
    ids = [e["id"] for e in events]

    tau = sps.kendalltau(np.arange(n), np.array(ts)).statistic
    a1 = tau >= 0.999

    ridx = np.arange(n, dtype=float).reshape(-1, 1)
    auc_row = _fit_auc(ridx, y, cut)
    a2 = auc_row is not None and auc_row <= 0.52

    mono = ids == sorted(ids)
    suffix = [e["id"].split("_", 1)[1] for e in events]
    idfeat = np.column_stack([
        np.arange(n, dtype=float),
        np.array([sum(ord(c) for c in s) for s in suffix], dtype=float),
        np.array([ord(s[-1]) for s in suffix], dtype=float),
        np.array([ord(s[-2]) for s in suffix], dtype=float),
    ])
    auc_id = _fit_auc(idfeat, y, cut)
    a3 = mono and (auc_id is not None and auc_id <= 0.52)

    labels = [lab[e["id"]]["label"] for e in events]
    longest, cur = 0, 0
    for v in labels:
        cur = cur + 1 if v == 1 else 0
        longest = max(longest, cur)
    per_burst = collections.Counter(
        lab[e["id"]].get("burst_id") for e in events if lab[e["id"]].get("burst_id"))
    largest_burst = max(per_burst.values(), default=0)
    a4a = longest <= largest_burst

    pos = [i for i, v in enumerate(labels) if v == 1]
    gaps = [pos[i + 1] - pos[i] for i in range(len(pos) - 1)]
    med_gap = float(np.median(gaps)) if gaps else 0.0
    a4c = med_gap > 1

    ok = a1 and a2 and a3 and a4a and a4c
    record("T4 ordering and identity", ok,
           f"tau {tau:.6f} (>=0.999); row_index AUC {auc_row:.4f} (<=0.52); "
           f"ids monotonic {mono}, id-feature AUC {auc_id:.4f} (<=0.52); "
           f"longest attack run {longest} <= largest burst {largest_burst}; "
           f"median attack row gap {med_gap:.1f} (>1)")
    return dict(tau=tau, auc_row=auc_row, mono=mono, auc_id=auc_id,
                longest=longest, largest_burst=largest_burst, med_gap=med_gap)


def t5(events, lab, y, cut):
    mc = lambda e: e["merchant_context"]
    notes = collections.Counter(json.dumps(e["notes"], sort_keys=True) for e in events)
    deny_meta = ("seed", "archetype", "persona", "actor", "class", "row_index", "label")
    meta_hits = [k for k in notes if any(d in k.lower() for d in deny_meta)]
    n1_const = len(notes) == 1

    deny = ("attack", "bot", "fraud", "ring", "test", "legit")
    def blob(e):
        return (e["id"] + mc(e)["device_id"] + e["email"] + (mc(e)["account_id"] or "")).lower()
    atk = [e for e in events if lab[e["id"]]["label"] == 1]
    ben = [e for e in events if lab[e["id"]]["label"] == 0]
    enriched = []
    for d in deny:
        ra = sum(1 for e in atk if d in blob(e)) / max(len(atk), 1)
        rb = sum(1 for e in ben if d in blob(e)) / max(len(ben), 1)
        if ra > 0 and (rb == 0 or ra / rb > 2.0):
            enriched.append((d, ra, rb))

    locals_ = [e["email"].split("@")[0] for e in events]
    grams = sorted({l[i:i + 3] for l in locals_ for i in range(max(len(l) - 2, 0))})
    gi = {g: i for i, g in enumerate(grams)}
    Xg = np.zeros((len(locals_), len(grams)), dtype=np.float32)
    for r, l in enumerate(locals_):
        for i in range(max(len(l) - 2, 0)):
            Xg[r, gi[l[i:i + 3]]] += 1
    auc_gram = _fit_auc(Xg, y, cut)

    fmt = np.array([1 if e["contact"].startswith("+") else 0 for e in events])
    tab = np.array([[int(((fmt == f) & (y == c)).sum()) for f in (0, 1)] for c in (0, 1)])
    if (tab.sum(axis=0) == 0).any() or (tab.sum(axis=1) == 0).any():
        p_fmt, fmt_const = None, True
    else:
        p_fmt = sps.chi2_contingency(tab).pvalue
        fmt_const = False

    ok = (not meta_hits) and (not enriched) and \
         (auc_gram is not None and auc_gram <= 0.55) and \
         (fmt_const or p_fmt > 0.01)
    record("T5 string and metadata hygiene", ok,
           f"notes metadata hits {len(meta_hits)}; denylist enriched {len(enriched)}; "
           f"email char-3gram AUC {auc_gram:.4f} (<=0.55); "
           f"contact format {'CONSTANT, chi-squared undefined' if fmt_const else f'p={p_fmt:.4f}'}")
    return dict(notes_values=len(notes), notes_const=n1_const, meta_hits=meta_hits,
                enriched=enriched, auc_gram=auc_gram, fmt_const=fmt_const, p_fmt=p_fmt)


def t6(events, lab, manifest):
    days = manifest["days"]
    months = days / 30.0
    bursts = manifest.get("bursts", [])
    sales = manifest.get("flash_sales", [])
    downs = manifest.get("downtimes", [])
    ts = [e["created_at"] for e in events]

    def dens(lo, hi):
        n = bisect.bisect_left(ts, hi) - bisect.bisect_left(ts, lo)
        return n / max((hi - lo) / 60.0, 1e-9)

    burst_rates = [dens(b["start"], b["end"]) for b in bursts]
    blo, bhi = (min(burst_rates), max(burst_rates)) if burst_rates else (0, 0)
    sale_rates = [dens(s["start"], s["end"]) for s in sales]
    in_range = [r for r in sale_rates if blo <= r <= bhi]
    c1 = len(sales) >= 2 * months and len(in_range) >= 2 * months

    atk_ts = [e["created_at"] for e in events if lab[e["id"]]["label"] == 1]
    clean_downs = 0
    for d in downs:
        if not any(d["start"] <= t < d["end"] for t in atk_ts):
            clean_downs += 1
    c2 = clean_downs >= 1 * months

    # window-level model on volume and decline rate only
    W = 900
    t0 = manifest["window_start"]
    buckets = collections.defaultdict(lambda: [0, 0, 0])
    for e in events:
        b = (e["created_at"] - t0) // W
        buckets[b][0] += 1
        if e["status"] == "failed":
            buckets[b][1] += 1
        if lab[e["id"]]["label"] == 1:
            buckets[b][2] += 1
    keys = sorted(buckets)
    Xw = np.array([[buckets[k][0], buckets[k][1] / max(buckets[k][0], 1)] for k in keys])
    yw = np.array([1 if buckets[k][2] > 0 else 0 for k in keys], dtype=np.int8)
    cw = int(len(keys) * SPLIT)
    auc_w = _fit_auc(Xw, yw, cw)
    c3 = auc_w is not None and auc_w <= 0.80

    ok = c1 and c2 and c3
    record("T6 confounder survival", ok,
           f"flash sales {len(sales)} ({len(in_range)} inside burst density "
           f"{blo:.1f}-{bhi:.1f}/min, need >={2*months:.0f}); "
           f"clean downtime windows {clean_downs} (need >={1*months:.0f}); "
           f"window volume+decline AUC {auc_w:.4f} (<=0.80)")
    return dict(sale_rates=sale_rates, burst_rates=burst_rates, in_range=len(in_range),
                clean_downs=clean_downs, auc_w=auc_w, n_windows=len(keys))


def t7(path, manifest):
    out = os.path.join(os.path.dirname(path.rstrip("/\\")) or ".", "_t7_rerun")
    cmd = [sys.executable, "-m", "src.generator.run",
           "--seed", str(manifest["seed"]), "--days", str(manifest["days"]),
           "--actors", str(manifest["n_actors"]), "--out", out]
    subprocess.run(cmd, check=True, capture_output=True)
    import hashlib
    same = {}
    for f in ("events.jsonl", "sealed.jsonl", "manifest.json"):
        h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        same[f] = h(os.path.join(path, f)) == h(os.path.join(out, f))
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    ok = all(same.values())
    record("T7 determinism", ok,
           "byte-identical: " + ", ".join(f"{k}={v}" for k, v in same.items()))
    return same


def _recall_at_precision(y, s, target):
    p, r, _ = precision_recall_curve(y, s)
    best = 0.0
    for pi, ri in zip(p, r):
        if pi >= target:
            best = max(best, ri)
    return best


def t8(events, lab, sealed_by_id, t6res):
    cfg = configure(events, sealed_by_id)
    comb, ct_s, rg_s = score(events, cfg)
    y_all = np.array([lab[e["id"]]["label"] for e in events])
    y_ct = np.array([1 if lab[e["id"]].get("attack_type") == "card_testing" else 0
                     for e in events])
    y_rg = np.array([1 if lab[e["id"]].get("attack_type") == "ring" else 0
                     for e in events])

    auc_all = roc_auc_score(y_all, comb)
    rec_ct = _recall_at_precision(y_ct, np.array(ct_s), 0.80)
    rec_rg = _recall_at_precision(y_rg, np.array(rg_s), 0.70)
    gap = auc_all - (t6res["auc_w"] or 0.0)

    f1, f2, f3, f4 = auc_all >= 0.85, rec_ct >= 0.90, rec_rg >= 0.60, gap >= 0.10
    ok = f1 and f2 and f3 and f4
    record("T8 signal floor (oracle ceiling)", ok,
           f"event AUC {auc_all:.4f} (>=0.85); card-testing recall@P0.80 {rec_ct:.4f} "
           f"(>=0.90); ring recall@P0.70 {rec_rg:.4f} (>=0.60); "
           f"gap vs T6 window model {gap:+.4f} (>=0.10)")
    return dict(cfg=cfg, auc_all=auc_all, rec_ct=rec_ct, rec_rg=rec_rg, gap=gap)


def main(path):
    events = load_events(path)
    manifest = load_manifest(path)
    lab = labels_by_id(path)
    y = np.array([lab[e["id"]]["label"] for e in events], dtype=np.int8)
    rows = [flatten(e) for e in events]
    global flatten_keys
    flatten_keys = list(rows[0].keys())
    cut = int(len(rows) * SPLIT)

    print("=" * 78)
    print(f"ACCEPTANCE TESTS  spec section 5   data={path}")
    print(f"events {len(events)}  attack {int(y.sum())} ({y.mean()*100:.2f}%)  "
          f"chronological split {SPLIT:.0%} -> train {cut} / test {len(rows)-cut}")
    print("=" * 78)

    t1res = t1(rows, y, cut)
    rng = np.random.default_rng(0)
    t2res = t2(rows, y, cut, rng)
    t3res = t3(events, lab)
    t4res = t4(events, lab, manifest, y, cut)
    t5res = t5(events, lab, y, cut)
    t6res = t6(events, lab, manifest)
    t7res = t7(path, manifest)
    t8res = t8(events, lab, lab, t6res)

    bad_imp, bad_sealed = check_import_isolation()
    record("T8 oracle isolation", not bad_imp and not bad_sealed,
           f"src modules importing tests/: {len(bad_imp)}; "
           f"detector modules touching sealed: {len(bad_sealed)}")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<34} {detail}")
    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n  {len(RESULTS)-n_fail} passed, {n_fail} failed")
    return t1res, t5res, t6res, t8res


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/sample")
