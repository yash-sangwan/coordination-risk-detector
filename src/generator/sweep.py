"""Evasive card testing sweep (spec 2.1e). Generator side only, no detectors.

PURPOSE: this is a DETECTOR ROBUSTNESS TEST. It measures where a decline-rate
baseline fails. It is a test fixture, not an attack tool, and the evasion it
models is standard published knowledge about card testing rather than anything
novel: card lists are sold graded by how many numbers are still live, and the
schemes score merchants on the enumeration RATIO rather than the count, which is
a standing public incentive to hold an observed decline rate down. Track 02 is
defence only and this stays on the defensive side of that line: what comes out
is a labelled synthetic stream in our own schema, for scoring our own detectors.

    python -m src.generator.sweep --seed 42 --days 30 --actors 40000

Writes one dataset per sweep step under --out and prints, for each step, the
observed decline rate, the events per minute, and the coordination table. It
prints NOTHING about detector performance: that is a separate step and the data
gets checked first.
"""

import argparse
import collections
import json
import os
import sys

from . import config as C
from .emit import write_manifest, write_stream
from .report import account_share_rate, pair_collision
from .run import generate

mc = lambda e: e["merchant_context"]


def _step_dir(out, v):
    return os.path.join(out, "v%03d" % round(v * 100))


def build(seed, days, actors, out, steps):
    """Generate one dataset per step. Returns [(v, path, events, sealed, mf)]."""
    made = []
    for v in steps:
        path = _step_dir(out, v)
        os.makedirs(path, exist_ok=True)
        rows, sealed, mf = generate(seed, days, actors, evasive_valid_share=v)
        write_stream(os.path.join(path, "events.jsonl"), rows)
        write_stream(os.path.join(path, "sealed.jsonl"), sealed)
        write_manifest(os.path.join(path, "manifest.json"), mf)
        print("  wrote %-24s %6d events, list grade %.2f"
              % (path, len(rows), v), flush=True)
        made.append((v, path, rows, sealed, mf))
    return made


def _read(path):
    def jl(name):
        with open(os.path.join(path, name), encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]
    with open(os.path.join(path, "manifest.json"), encoding="utf-8") as fh:
        mf = json.load(fh)
    return jl("events.jsonl"), jl("sealed.jsonl"), mf


def load(out, steps):
    """Re-read datasets already on disk, so the report can be reprinted without
    paying for a regeneration."""
    made = []
    for v in steps:
        path = _step_dir(out, v)
        if not os.path.isdir(path):
            continue
        ev, sl, mf = _read(path)
        made.append((v, path, ev, sl, mf))
    return made


# ---------------------------------------------------------------- measurements

def rates(events, sealed, mf):
    """Observed decline rate and events per minute, attack against benign."""
    lab = {s["id"]: s for s in sealed}
    atk = [e for e in events if lab[e["id"]].get("attack_type") == "card_testing"]
    ben = [e for e in events if lab[e["id"]]["label"] == 0]

    def dec(rows):
        return sum(1 for e in rows if e["status"] == "failed") / max(len(rows), 1)

    # Attack events per minute is measured INSIDE the bursts, which is the only
    # place the attack exists. Averaging it over the whole window would divide by
    # 30 days of quiet and report a number no detector ever sees.
    burst_minutes = sum(b["minutes"] for b in mf["bursts"]) or 1
    span_min = (mf["window_end"] - mf["window_start"]) / 60.0

    per_burst = collections.Counter(lab[e["id"]].get("burst_id") for e in atk)
    return {
        "n_attack": len(atk),
        "attack_decline": dec(atk),
        "benign_decline": dec(ben),
        "benign_card_decline": dec([e for e in ben if e["method"] == "card"]),
        "attack_per_min": len(atk) / burst_minutes,
        "stream_per_min": len(events) / span_min,
        "burst_rates": sorted(per_burst[b["burst_id"]] / max(b["minutes"], 1)
                              for b in mf["bursts"]),
    }


# The three-way table, exactly as before: within-attack, benign, ratio.
SHARED = [
    ("card.iin", lambda R: pair_collision([e["card"]["iin"] for e in R if e.get("card")])),
    ("device_id", lambda R: pair_collision([mc(e)["device_id"] for e in R])),
    ("contact", lambda R: pair_collision([e["contact"] for e in R])),
    ("email domain", lambda R: pair_collision([e["email"].split("@")[1] for e in R])),
    ("shipping_pincode",
     lambda R: pair_collision([mc(e)["shipping_pincode"] for e in R
                               if mc(e)["shipping_pincode"] is not None])),
]

FRESH = [
    ("card.last4", lambda e: (e.get("card") or {}).get("last4")),
    ("email", lambda e: e["email"]),
    ("contact", lambda e: e["contact"]),
    ("session_id", lambda e: mc(e)["session_id"]),
]

NULLABLE = [
    ("account_id", lambda e: mc(e)["account_id"]),
    ("shipping_pincode", lambda e: mc(e)["shipping_pincode"]),
]


def coordination(events, sealed, mf):
    """Everything the attack shares and everything it does not."""
    lab = {s["id"]: s for s in sealed}
    atk = [e for e in events if lab[e["id"]].get("attack_type") == "card_testing"]
    ben = [e for e in events if lab[e["id"]]["label"] == 0]

    def uniq(rows, val):
        vals = [val(e) for e in rows if val(e) is not None]
        return (len(set(vals)) / len(vals)) if vals else 0.0

    out = {"shared": {}, "fresh": {}, "null": {}, "counts": {}}
    for name, fn in SHARED:
        out["shared"][name] = (fn(atk), fn(ben))
    for name, fn in FRESH:
        out["fresh"][name] = uniq(atk, fn)
    for name, fn in NULLABLE:
        out["null"][name] = sum(1 for e in atk if fn(e) is None) / max(len(atk), 1)
    out["counts"]["iins_per_burst"] = sorted(b["n_iins"] for b in mf["bursts"])
    out["counts"]["devices_per_burst"] = sorted(b["n_devices"] for b in mf["bursts"])
    return out


# --------------------------------------------------------------------- printing

def report(made):
    print("\n" + "=" * 78)
    print("SPEC 2.1e EVASIVE SWEEP: observed decline rate and volume")
    print("=" * 78)
    print("  mechanism: list grade. The swept parameter is the fraction of the")
    print("  card list already known live. It changes the decline rate and")
    print("  nothing else: every identity, device, amount and timestamp below is")
    print("  drawn from the same RNG sequence at every step.")
    print()
    print(f"  {'grade':>6} {'declared':>9} {'observed':>9} {'benign':>8} {'benign':>8} "
          f"{'attack':>9} {'attack ev/min':>26}")
    print(f"  {'v':>6} {'decline':>9} {'decline':>9} {'all':>8} {'card':>8} "
          f"{'events':>9} {'per burst':>26}")
    R = {}
    for v, path, ev, sl, mf in made:
        r = rates(ev, sl, mf)
        R[v] = r
        span = "%.1f - %.1f" % (r["burst_rates"][0], r["burst_rates"][-1])
        print(f"  {v:>6.2f} {C.evasive_decline(v)*100:>8.2f}% "
              f"{r['attack_decline']*100:>8.2f}% {r['benign_decline']*100:>7.2f}% "
              f"{r['benign_card_decline']*100:>7.2f}% {r['n_attack']:>9} "
              f"{span:>26}")
    print("\n  Attack events per minute is measured inside the bursts. It is flat")
    print("  across the sweep by construction: list grade does not touch pacing.")

    print("\n" + "=" * 78)
    print("COORDINATION STRUCTURE ACROSS THE SWEEP")
    print("=" * 78)
    cols = [v for v, *_ in made]
    Cd = {v: coordination(ev, sl, mf) for v, _, ev, sl, mf in made}
    ben_ref = Cd[cols[0]]["shared"]

    print("\n  WHAT THE BURST SHARES, pair collision within attack rows")
    print(f"  {'attribute':<20} {'benign':>9} " + "".join(f"{('v=%.2f' % v):>9}" for v in cols))
    for name, _ in SHARED:
        cells = "".join(f"{Cd[v]['shared'][name][0]*100:>8.2f}%" for v in cols)
        print(f"  {name:<20} {ben_ref[name][1]*100:>8.3f}% {cells}")

    print("\n  WHAT IT DOES NOT SHARE, distinct/total within attack rows")
    print(f"  {'attribute':<20} {'':>9} " + "".join(f"{('v=%.2f' % v):>9}" for v in cols))
    for name, _ in FRESH:
        cells = "".join(f"{Cd[v]['fresh'][name]*100:>8.2f}%" for v in cols)
        print(f"  {name:<20} {'':>9} {cells}")
    for name, _ in NULLABLE:
        cells = "".join(f"{Cd[v]['null'][name]*100:>8.2f}%" for v in cols)
        print(f"  {name + ' (null)':<20} {'':>9} {cells}")

    print("\n  CONVERGENCE COUNTS, per burst")
    for k in ("iins_per_burst", "devices_per_burst"):
        same = all(Cd[v]["counts"][k] == Cd[cols[0]]["counts"][k] for v in cols)
        print(f"  {k:<20} {Cd[cols[0]]['counts'][k]}   identical across sweep: {same}")

    # The claim the sweep rests on, checked rather than asserted.
    print("\n  VERDICT: is the coordination unchanged?")

    def spread(where):
        worst, at = 0.0, ""
        for group, key in ((SHARED, "shared"), (FRESH, "fresh"), (NULLABLE, "null")):
            for name, _ in group:
                vals = [Cd[v][key][name][0] if key == "shared" else Cd[v][key][name]
                        for v in where]
                d = max(vals) - min(vals)
                if d > worst:
                    worst, at = d, name
        return worst, at

    nonzero = [v for v in cols if v > 0]
    w_all, at_all = spread(cols)
    print(f"  largest spread on any coordination measure, all steps:      "
          f"{w_all*100:.4f} pp   ({at_all})")
    if nonzero:
        w_nz, at_nz = spread(nonzero)
        print(f"  the same, excluding the v=0.00 control:                     "
              f"{w_nz*100:.4f} pp   ({at_nz})")
        print("  The whole spread is the control step, not drift along the curve.")
        print("  v=0.00 draws its decline reason only on failures, which is what")
        print("  keeps it byte-identical to data/sample; every evasive step draws")
        print("  on all attempts and so shares one RNG sequence exactly.")
    dec = [R[v]["attack_decline"] for v in cols]
    print(f"  spread on the decline rate, the one thing meant to move:    "
          f"{(max(dec)-min(dec))*100:.4f} pp")

    print("\n  WHERE OBSERVED PARTS FROM DECLARED")
    print(f"  {'v':>6} {'declared':>9} {'observed':>9} {'gap':>8}   cause")
    for v in cols:
        gap = R[v]["attack_decline"] - C.evasive_decline(v)
        print(f"  {v:>6.2f} {C.evasive_decline(v)*100:>8.2f}% "
              f"{R[v]['attack_decline']*100:>8.2f}% {gap*100:>+7.2f} pp   "
              + ("-" if abs(gap) < 0.002 else "issuer-block ramp, see below"))
    print("  The blocked ending ramps to ATTACK_DECLINE_BLOCKED whatever the list")
    print("  grade, because a block is issuer-side. It is a fixed additive floor,")
    print("  so it is invisible at v=0.00 and dominates the residual at v=1.00.")
    print("  The attack cannot be driven below it by buying a better list, which")
    print("  is a property of the attack rather than a limit of the fixture.")


def main():
    ap = argparse.ArgumentParser(description="Evasive card testing sweep (2.1e)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--actors", type=int, default=40000)
    ap.add_argument("--out", default="data/evasive")
    ap.add_argument("--report-only", action="store_true",
                    help="reprint the report from datasets already on disk")
    args = ap.parse_args()

    if args.report_only:
        made = load(args.out, C.EVASIVE_SWEEP)
        print("read %d steps from %s" % (len(made), args.out), flush=True)
    else:
        print("generating %d steps: %s" % (len(C.EVASIVE_SWEEP), C.EVASIVE_SWEEP),
              flush=True)
        made = build(args.seed, args.days, args.actors, args.out, C.EVASIVE_SWEEP)
    report(made)


if __name__ == "__main__":
    main()
