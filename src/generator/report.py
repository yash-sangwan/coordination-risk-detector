"""Describe a generated stream. Read-only, no tuning, no pass/fail.

This is not an acceptance test. It prints what the data looks like so a human can
judge whether it resembles real traffic before attacks go in. Section 5 tests are
deliberately not implemented here.

    python -m src.generator.report data/sample
"""

import collections
import datetime as dt
import json
import sys

IST_OFFSET = 19800


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def pct(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def pair_collision(values):
    """Probability two random draws from this multiset share a value = sum(p^2)."""
    if not values:
        return 0.0
    n = len(values)
    c = collections.Counter(values)
    return sum((v / n) ** 2 for v in c.values())


def account_share_rate(pairs):
    """Fraction of distinct accounts whose attribute value is also used by at
    least one other account. pairs: iterable of (account_id, value)."""
    by_value = collections.defaultdict(set)
    accounts = set()
    for acct, val in pairs:
        if val is None:
            continue
        by_value[val].add(acct)
        accounts.add(acct)
    shared = set()
    for val, accts in by_value.items():
        if len(accts) > 1:
            shared |= accts
    return (len(shared) / len(accounts)) if accounts else 0.0


def main(path):
    events = load(f"{path}/events.jsonl")
    sealed = load(f"{path}/sealed.jsonl")
    manifest = json.load(open(f"{path}/manifest.json", encoding="utf-8"))

    n = len(events)
    print("=" * 74)
    print(f"LEGITIMATE TRAFFIC SAMPLE   seed={manifest['seed']}  "
          f"days={manifest['days']}  actors={manifest['n_actors']}")
    print("=" * 74)

    print(f"\nROW COUNT: {n}")
    print(f"  sealed rows      : {len(sealed)}  (labels all 0: "
          f"{all(s['label'] == 0 for s in sealed)})")
    print(f"  distinct accounts: {len({e['merchant_context']['account_id'] for e in events})}")
    print(f"  distinct sessions: {len({e['merchant_context']['session_id'] for e in events})}")
    print(f"  distinct orders  : {len({e['order_id'] for e in events})}")
    span = (manifest['window_end'] - manifest['window_start']) / 86400
    print(f"  events/day       : {n/span:.0f}")

    print("\nMETHOD MIX (spec 1.3 target in brackets)")
    target = {"upi": .55, "card": .28, "netbanking": .09, "wallet": .06, "emi": .02}
    mc = collections.Counter(e["method"] for e in events)
    for m, t in target.items():
        got = mc.get(m, 0) / n
        print(f"  {m:<12} {got*100:6.2f}%   [{t*100:.0f}%]")
    intl = sum(1 for e in events if e["international"])
    cards = sum(1 for e in events if e["method"] == "card")
    print(f"  international     {intl/max(cards,1)*100:6.2f}% of card   [2%]")

    print("\nAMOUNT PERCENTILES (rupees)")
    amts = [e["amount"] for e in events]
    for p in (1, 5, 10, 25, 50, 75, 90, 99):
        print(f"  p{p:<3} {pct(amts, p)/100:12,.2f}")
    print(f"  below Rs 50      : {sum(1 for a in amts if a < 5000)/n*100:5.2f}%   [~3.3%]")
    print(f"  below Rs 100     : {sum(1 for a in amts if a < 10000)/n*100:5.2f}%   [10%]")
    print(f"  round price point: {sum(1 for a in amts if a in (9900,19900,29900,49900,79900,99900,149900,199900,249900))/n*100:5.2f}%   [30%]")

    print("\nDECLINE RATE")
    failed = [e for e in events if e["status"] == "failed"]
    print(f"  overall          : {len(failed)/n*100:5.2f}%")
    print("  by method:")
    for m in target:
        sub = [e for e in events if e["method"] == m]
        if sub:
            f = sum(1 for e in sub if e["status"] == "failed")
            print(f"    {m:<12} {f/len(sub)*100:6.2f}%  (n={len(sub)})")
    print("  by tier:")
    tier_of = {s["id"]: s["tier"] for s in sealed}
    for tier in ("metro", "tier2", "tier3"):
        sub = [e for e in events if tier_of.get(e["id"]) == tier]
        if sub:
            f = sum(1 for e in sub if e["status"] == "failed")
            print(f"    {tier:<12} {f/len(sub)*100:6.2f}%  (n={len(sub)})")

    print("\nDECLINE RATE BY HOUR (IST)   evening peak 19-21 shaded with *")
    by_hour = collections.defaultdict(lambda: [0, 0])
    for e in events:
        h = dt.datetime.fromtimestamp(e["created_at"] + IST_OFFSET, dt.timezone.utc).hour
        by_hour[h][0] += 1
        if e["status"] == "failed":
            by_hour[h][1] += 1
    print(f"  {'hr':>3}  {'events':>7}  {'decline':>8}   {'volume':<26}")
    peak = max(v[0] for v in by_hour.values()) or 1
    for h in range(24):
        tot, f = by_hour.get(h, [0, 0])
        rate = (f / tot * 100) if tot else 0.0
        bar = "#" * int(24 * tot / peak)
        star = "*" if h in (19, 20, 21) else " "
        print(f"  {h:>3}{star} {tot:>7}  {rate:>7.2f}%   {bar}")

    ev = [e for e in events if dt.datetime.fromtimestamp(e["created_at"]+IST_OFFSET, dt.timezone.utc).hour in (19,20,21)]
    ot = [e for e in events if dt.datetime.fromtimestamp(e["created_at"]+IST_OFFSET, dt.timezone.utc).hour not in (19,20,21)]
    evr = sum(1 for e in ev if e["status"]=="failed")/max(len(ev),1)*100
    otr = sum(1 for e in ot if e["status"]=="failed")/max(len(ot),1)*100
    print(f"\n  evening 19-21 decline {evr:.2f}%  vs rest {otr:.2f}%  "
          f"-> shift {evr-otr:+.2f} pp")

    print("\nBENIGN COLLISION RATES, the six load-bearing linking attributes")
    print("  (spec 4 target in brackets. every one must be non-zero)")
    diag = manifest["population_diagnostics"]

    # 1 card.iin  -- spec defines this over card-attempt PAIRS
    iins = [e["card"]["iin"] for e in events if e.get("card")]
    print(f"\n  1. card.iin")
    print(f"     pair collision, card attempts : {pair_collision(iins)*100:6.2f}%   [8-15%]")
    print(f"     analytic from config          : {diag['iin_pair_collision_analytic']*100:6.2f}%")
    print(f"     distinct IINs seen            : {len(set(iins))}")

    # 2 device_id -- spec defines this over ACCOUNTS
    dev = [(e["merchant_context"]["account_id"], e["merchant_context"]["device_id"])
           for e in events]
    print(f"\n  2. merchant_context.device_id")
    print(f"     accounts sharing a device     : {account_share_rate(dev)*100:6.2f}%   [6%]")
    print(f"     pair collision, events        : {pair_collision([d for _, d in dev])*100:6.2f}%")

    # 3 contact -- accounts
    con = [(e["merchant_context"]["account_id"], e["contact"]) for e in events]
    print(f"\n  3. contact")
    print(f"     accounts sharing a phone      : {account_share_rate(con)*100:6.2f}%   [1.5%]")
    print(f"     pair collision, events        : {pair_collision([c for _, c in con])*100:6.2f}%")

    # 4 email
    doms = [e["email"].split("@")[1] for e in events]
    top3 = sum(c for _, c in collections.Counter(doms).most_common(3)) / n
    print(f"\n  4. email")
    print(f"     share on top-3 domains        : {top3*100:6.2f}%   [~70%]")
    print(f"     domain pair collision         : {pair_collision(doms)*100:6.2f}%")
    locals_ = [e["email"].split("@")[0] for e in events]
    print(f"     local-part pair collision     : {pair_collision(locals_)*100:6.4f}%")
    print(f"     distinct domains              : {len(set(doms))}")

    # 5 vpa local part
    vl = [(e["merchant_context"]["account_id"], e["vpa"].split("@")[0])
          for e in events if e.get("vpa")]
    vh = [e["vpa"].split("@")[1] for e in events if e.get("vpa")]
    print(f"\n  5. vpa local part")
    print(f"     accounts sharing a local part : {account_share_rate(vl)*100:6.2f}%   [~1.5%]")
    print(f"     handle pair collision         : {pair_collision(vh)*100:6.2f}%   [>40%, deliberately weak]")

    # 6 shipping_pincode
    pins = [e["merchant_context"]["shipping_pincode"] for e in events]
    cnt = collections.Counter(pins)
    top50 = sum(c for _, c in cnt.most_common(50)) / n
    print(f"\n  6. merchant_context.shipping_pincode   [shape: {diag['pincode_shape']}]")
    print(f"     top-50 share                  : {top50*100:6.2f}%   [~25%]")
    print(f"     pair collision                : {pair_collision(pins)*100:6.2f}%   [2-4%  SEE CONFLICT C1]")
    print(f"     distinct pincodes seen        : {len(set(pins))}")

    print("\nOTHER SPEC 4 CONSTRAINTS")
    cms = [e["merchant_context"]["checkout_ms"] for e in events]
    print(f"  checkout_ms under 1000ms        : {sum(1 for c in cms if c < 1000)/n*100:6.2f}%   [>=30%]")
    print(f"  checkout_ms median              : {pct(cms,50):.0f} ms")
    ages = [e["merchant_context"]["account_age_days"] for e in events]
    print(f"  account_age_days under 7        : {sum(1 for a in ages if a < 7)/n*100:6.2f}%   [>=10%]")
    print(f"  account_age_days median         : {pct(ages,50):.0f} days")
    seqs = [e["merchant_context"]["attempt_seq"] for e in events]
    print(f"  attempts with attempt_seq > 1   : {sum(1 for s in seqs if s > 1)/n*100:6.2f}%")
    firsts = [e for e in events if e["merchant_context"]["attempt_seq"] == 1]
    ff = [e for e in firsts if e["status"] == "failed"]
    rec = 0
    by_order = collections.defaultdict(list)
    for e in events:
        by_order[e["order_id"]].append(e)
    for e in ff:
        later = [x for x in by_order[e["order_id"]]
                 if x["merchant_context"]["attempt_seq"] > 1 and x["status"] == "authorized"]
        if later:
            rec += 1
    print(f"  failed-then-recovered by retry  : {rec/max(len(ff),1)*100:6.2f}%   "
          f"[15-20% cited, SEE CONFLICT C3]")

    print("\nCONFOUNDERS PRESENT (no attacks in this data)")
    print(f"  flash sales scheduled           : {len(manifest['flash_sales'])}")
    fs = sum(1 for s in sealed if s["in_flash_sale"])
    print(f"  events inside a flash sale      : {fs} ({fs/n*100:.2f}%)")
    print(f"  downtime windows scheduled      : {len(manifest['downtimes'])}")
    dtc = sum(1 for s in sealed if s["in_downtime"])
    print(f"  events inside a downtime window : {dtc} ({dtc/n*100:.2f}%)")

    print("\nSTRUCTURE CHECKS")
    ts = [e["created_at"] for e in events]
    print(f"  rows sorted by created_at       : {ts == sorted(ts)}")
    ids = [e["id"] for e in events]
    print(f"  ids monotonic with created_at   : {ids == sorted(ids)}")
    print(f"  ids unique                      : {len(set(ids)) == n}")
    print(f"  cut fields absent from every row: "
          f"{all(k not in e for e in events for k in ('acquirer_data','error_description','entity'))}")
    print(f"  no ip_prefix / user_agent_hash  : "
          f"{all(k not in e['merchant_context'] for e in events for k in ('ip_prefix','user_agent_hash'))}")
    print(f"  no card.sub_type                : "
          f"{all('sub_type' not in (e.get('card') or {}) for e in events)}")
    print(f"  notes empty array everywhere    : {all(e['notes'] == [] for e in events)}")
    print()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/sample")
