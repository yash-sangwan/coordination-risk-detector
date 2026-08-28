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
    print(f"\n  6. merchant_context.shipping_pincode")
    print(f"     top-50 share                  : {top50*100:6.2f}%   [~25%]")
    print(f"     pair collision                : {pair_collision(pins)*100:6.3f}%   [0.147% analytic]")
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


def attack_report(path, benign_baseline=None):
    """Attack-side view. Read-only, no pass/fail, no acceptance tests."""
    import datetime as _dt
    events = load(f"{path}/events.jsonl")
    sealed = load(f"{path}/sealed.jsonl")
    manifest = json.load(open(f"{path}/manifest.json", encoding="utf-8"))
    lab = {s["id"]: s for s in sealed}
    atk = [e for e in events if lab[e["id"]]["label"] == 1]
    ben = [e for e in events if lab[e["id"]]["label"] == 0]
    n = len(events)

    print("=" * 78)
    print("CARD TESTING BURSTS")
    print("=" * 78)
    print(f"\nATTACK SHARE")
    print(f"  total events            : {n}")
    print(f"  attack events           : {len(atk)}  ({len(atk)/n*100:.2f}%)")
    print(f"  legitimate events       : {len(ben)}  ({len(ben)/n*100:.2f}%)")

    print(f"\nBURSTS ({len(manifest['bursts'])})")
    print(f"  {'id':>4} {'start (IST)':>16} {'min':>4} {'rate/min':>9} {'events':>7} "
          f"{'IINs':>5} {'devs':>5} {'envelope':>9}  ending")
    per_burst = collections.Counter(lab[e["id"]]["burst_id"] for e in atk)
    for b in manifest["bursts"]:
        st = _dt.datetime.fromtimestamp(b["start"] + IST_OFFSET, _dt.timezone.utc)
        print(f"  {b['burst_id']:>4} {st.strftime('%a %d %H:%M'):>16} {b['minutes']:>4} "
              f"{b['rate_per_min']:>9.1f} {per_burst[b['burst_id']]:>7} "
              f"{b['n_iins']:>5} {b['n_devices']:>5} {b['envelope']:>9.2f}  {b['ending']}")
    endings = collections.Counter(b["ending"] for b in manifest["bursts"])
    tot = sum(endings.values()) or 1
    print("  endings:", ", ".join(f"{k} {v}/{tot} ({v/tot*100:.0f}%)"
                                  for k, v in endings.most_common()),
          "  [spec: exhausted 50%, blocked 35%, moves_on 15%]")

    print("\nCAMPAIGN SHAPE (attack share of each day's events)")
    day0 = manifest["window_start"]
    daily = collections.defaultdict(lambda: [0, 0])
    for e in events:
        d = (e["created_at"] - day0) // 86400
        daily[d][0] += 1
        if lab[e["id"]]["label"] == 1:
            daily[d][1] += 1
    peak = max((a / max(t, 1) for t, a in daily.values()), default=0) or 1
    for d in sorted(daily):
        t, a = daily[d]
        share = a / max(t, 1)
        bar = "#" * int(46 * share / peak)
        print(f"  day {d:>2}  {t:>5} ev  {share*100:>5.2f}%  {bar}")

    print("\nSIX LINKING ATTRIBUTES: within-attack vs benign")
    print("  (benign column is the legitimate-only run from the last commit)")
    mc = lambda e: e["merchant_context"]

    def acct_pairs(rows, val):
        return [(mc(e)["account_id"], val(e)) for e in rows
                if mc(e)["account_id"] is not None and val(e) is not None]

    rowsets = [("card.iin", "pair",
                lambda R: pair_collision([e["card"]["iin"] for e in R if e.get("card")])),
               ("device_id", "pair",
                lambda R: pair_collision([mc(e)["device_id"] for e in R])),
               ("contact", "pair",
                lambda R: pair_collision([e["contact"] for e in R])),
               ("email domain", "pair",
                lambda R: pair_collision([e["email"].split("@")[1] for e in R])),
               ("vpa local part", "pair",
                lambda R: pair_collision([e["vpa"].split("@")[0] for e in R if e.get("vpa")])),
               ("shipping_pincode", "pair",
                lambda R: pair_collision([mc(e)["shipping_pincode"] for e in R
                                          if mc(e)["shipping_pincode"] is not None]))]
    print(f"  {'attribute':<20} {'within-attack':>14} {'benign':>12} {'ratio':>9}")
    print("  " + "-" * 58)
    for name, _, fn in rowsets:
        a = fn(atk)
        b = fn(ben)
        ratio = (a / b) if b else float("inf")
        a_s = f"{a*100:.2f}%" if a else "n/a"
        print(f"  {name:<20} {a_s:>14} {b*100:>11.3f}% "
              f"{(f'{ratio:.1f}x' if b and a else '-'):>9}")

    print("\nTHE LOW-OVERLAP HALF (what a burst does NOT share)")
    def uniq(rows, val):
        vals = [val(e) for e in rows if val(e) is not None]
        return (len(set(vals)) / len(vals)) if vals else 0.0
    for name, val in (("card.last4", lambda e: (e.get("card") or {}).get("last4")),
                      ("email", lambda e: e["email"]),
                      ("contact", lambda e: e["contact"]),
                      ("session_id", lambda e: mc(e)["session_id"])):
        print(f"  {name:<14} distinct/total  attack {uniq(atk, val)*100:6.2f}%   "
              f"benign {uniq(ben, val)*100:6.2f}%")
    for name, val in (("account_id", lambda e: mc(e)["account_id"]),
                      ("shipping_pincode", lambda e: mc(e)["shipping_pincode"])):
        an = sum(1 for e in atk if val(e) is None) / max(len(atk), 1)
        bn = sum(1 for e in ben if val(e) is None) / max(len(ben), 1)
        print(f"  {name:<14} null rate       attack {an*100:6.2f}%   benign {bn*100:6.2f}%")

    print("\nCATEGORY E LEAK CHECKS")
    ids = [e["id"] for e in events]
    ts = [e["created_at"] for e in events]
    print(f"  rows sorted by created_at        : {ts == sorted(ts)}")
    print(f"  ids monotonic with created_at    : {ids == sorted(ids)}")
    labels = [lab[e["id"]]["label"] for e in events]
    runs, cur = [], labels[0]
    ln = 0
    for x in labels:
        if x == cur:
            ln += 1
        else:
            runs.append((cur, ln)); cur, ln = x, 1
    runs.append((cur, ln))
    longest = max((l for v, l in runs if v == 1), default=0)
    print(f"  longest consecutive attack run   : {longest} rows "
          f"(largest burst is {max(per_burst.values(), default=0)} events)")
    hours = {_dt.datetime.fromtimestamp(e["created_at"] + IST_OFFSET, _dt.timezone.utc).hour
             for e in atk}
    print(f"  distinct hours containing attack : {len(hours)}/24")
    # A substring like "bad" occurs by chance in hex and base62 identifiers, so a
    # raw hit is meaningless. What matters is whether it occurs MORE often in
    # attack identifiers than in legitimate ones.
    tells = ("attack", "bot", "fraud", "ring", "test", "legit", "evil", "bad", "atk")
    # Only fields the generator CHOOSES are checked. `id` and `order_id` come from
    # one monotonic base62 sequence that never sees a label, so substrings like
    # "bot" turn up in them by chance (pay_0000000DJboT3o) and mean nothing.
    def tell_rate(rows, needle):
        if not rows:
            return 0.0
        return sum(1 for e in rows
                   if needle in (e["email"] + mc(e)["device_id"]
                                 + mc(e)["session_id"]).lower()) / len(rows)
    flagged = []
    for needle in tells:
        ra, rb = tell_rate(atk, needle), tell_rate(ben, needle)
        if ra > 0 and (rb == 0 or ra / rb > 2.0):
            flagged.append(f"{needle} (attack {ra*100:.3f}% vs benign {rb*100:.3f}%)")
    print(f"  string tells enriched in attack  : {flagged or 'NONE'}")
    adom = {e["email"].split('@')[1] for e in atk}
    bdom = {e["email"].split('@')[1] for e in ben}
    print(f"  attack email domains not seen in benign: {sorted(adom - bdom) or 'NONE'}")
    aiin = {e["card"]["iin"] for e in atk if e.get("card")}
    biin = {e["card"]["iin"] for e in ben if e.get("card")}
    print(f"  attack IINs not seen in benign   : {sorted(aiin - biin) or 'NONE'}")
    print()
