"""Structure oracle for T8. Test fixture only, never part of the submission.

The contract from spec section 5:

  configuration time  may read the sealed store, to set thresholds from the true
                      generative parameters
  inference time      may not. It never looks up a label for a row it scores.

So `configure()` takes the sealed store and the generator's own constants and
returns a plain settings object. `score()` takes only the event stream. An oracle
that looked up labels would trivially score 1.0 and measure nothing; this one
answers how well a perfectly informed detector could recover the planted pattern
from observable data, which is the achievability ceiling.
"""

import bisect
import collections
import statistics

from src.generator import config as C


class OracleConfig:
    """Thresholds derived at configuration time. Carries no per-row labels."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):
        return "OracleConfig(" + ", ".join(
            f"{k}={v!r}" for k, v in sorted(self.__dict__.items())) + ")"


def configure(events, sealed_by_id):
    """Read the sealed store ONCE, to learn what was planted. No row scoring here."""
    ct = [e for e in events if sealed_by_id[e["id"]].get("attack_type") == "card_testing"]
    rg = [e for e in events if sealed_by_id[e["id"]].get("attack_type") == "ring"]
    mc = lambda e: e["merchant_context"]

    ct_ms = sorted(mc(e)["checkout_ms"] for e in ct) or [C.ATTACK_CHECKOUT_MODE]
    ct_amt = sorted(e["amount"] for e in ct) or [50000]

    # Ring cluster sizes: how many distinct accounts share a ring's drop pincode.
    pin_accounts = collections.defaultdict(set)
    for e in rg:
        p = mc(e)["shipping_pincode"]
        if p and mc(e)["account_id"]:
            pin_accounts[p].add(mc(e)["account_id"])
    ring_sizes = sorted(len(v) for v in pin_accounts.values()) or [3]

    return OracleConfig(
        # card testing
        window_s=600,
        ct_ms_p90=ct_ms[int(len(ct_ms) * 0.90)],
        ct_amt_p90=ct_amt[int(len(ct_amt) * 0.90)],
        ct_min_iins=C.BURST_IIN_COUNT[1],
        ct_min_devices=C.BURST_DEVICE_COUNT[1],
        ct_decline_base=C.ATTACK_DECLINE_BASE,
        # ring
        ring_min_accounts=max(2, min(ring_sizes)),
        ring_device_subset=C.RING_DEVICE_SUBSET[0],
    )


def _window_stats(events, window_s):
    """Per-event neighbourhood statistics. Event stream only."""
    ts = [e["created_at"] for e in events]
    mc = lambda e: e["merchant_context"]
    n = len(events)
    lo_idx = [bisect.bisect_left(ts, t - window_s) for t in ts]
    hi_idx = [bisect.bisect_right(ts, t + window_s) for t in ts]

    out = []
    for i in range(n):
        lo, hi = lo_idx[i], hi_idx[i]
        seg = events[lo:hi]
        cnt = len(seg)
        iins = collections.Counter(e["card"]["iin"] for e in seg if e.get("card"))
        devs = collections.Counter(mc(e)["device_id"] for e in seg)
        fails = sum(1 for e in seg if e["status"] == "failed")
        out.append({
            "n": cnt,
            "iin_conc": (iins.most_common(1)[0][1] / max(sum(iins.values()), 1)) if iins else 0.0,
            "n_card": sum(iins.values()),
            "dev_conc": devs.most_common(1)[0][1] / max(cnt, 1),
            "decline": fails / max(cnt, 1),
        })
    return out


def score(events, cfg):
    """Score every event. Reads ONLY the event stream.

    Returns (score_all, score_ct, score_ring), each a list aligned with events.
    """
    mc = lambda e: e["merchant_context"]
    W = _window_stats(events, cfg.window_s)

    # ---- card testing: density plus instrument and device concentration ----
    ct_scores = []
    for e, w in zip(events, W):
        s = 0.0
        # A burst converges on very few IINs while running many cards.
        if w["n_card"] >= 8:
            s += 2.0 * w["iin_conc"]
        s += 1.5 * w["dev_conc"]
        s += 1.2 * min(w["n"] / 60.0, 1.0)
        s += 1.5 * max(0.0, (w["decline"] - 0.20) / 0.75)
        if mc(e)["checkout_ms"] <= cfg.ct_ms_p90:
            s += 0.6
        if e["amount"] <= cfg.ct_amt_p90:
            s += 0.4
        if mc(e)["account_id"] is None:
            s += 0.3
        if mc(e)["shipping_pincode"] is None:
            s += 0.3
        ct_scores.append(s)

    # ---- ring: entity graph over drop address, device and phone ----
    pin_accounts = collections.defaultdict(set)
    dev_accounts = collections.defaultdict(set)
    con_accounts = collections.defaultdict(set)
    for e in events:
        a = mc(e)["account_id"]
        if not a:
            continue
        if mc(e)["shipping_pincode"]:
            pin_accounts[mc(e)["shipping_pincode"]].add(a)
        dev_accounts[mc(e)["device_id"]].add(a)
        con_accounts[e["contact"]].add(a)

    # A pincode's normal population, used to judge a cluster against how busy
    # that pincode usually is rather than against a global percentile. The old
    # rule fired only above the 99.5th percentile (112 accounts); once drop
    # pincodes were drawn unweighted the clusters were 7, 11 and 8, so it never
    # fired at all and ring scoring collapsed.
    pin_total = sum(len(v) for v in pin_accounts.values()) or 1
    n_pins = len(pin_accounts) or 1
    typical_pin = pin_total / n_pins

    # The conjunction the account-level diagnostic identified as the only feature
    # that separates a ring member from its innocent neighbours on a pincode:
    # accounts that share BOTH a drop pincode and a device.
    acct_pins = collections.defaultdict(set)
    acct_devs = collections.defaultdict(set)
    for e in events:
        a = mc(e)["account_id"]
        if not a:
            continue
        if mc(e)["shipping_pincode"]:
            acct_pins[a].add(mc(e)["shipping_pincode"])
        acct_devs[a].add(mc(e)["device_id"])

    conj = {}
    for a in acct_pins:
        best = 0
        for p in acct_pins[a]:
            peers = pin_accounts[p]
            for d in acct_devs[a]:
                best = max(best, len(peers & dev_accounts[d]))
        conj[a] = best

    ring_scores = []
    for e in events:
        a = mc(e)["account_id"]
        if not a:
            ring_scores.append(0.0)
            continue
        ring_scores.append(_ring_account_score(
            a, mc(e)["shipping_pincode"], mc(e)["device_id"], e["contact"],
            pin_accounts, dev_accounts, con_accounts, conj, typical_pin, cfg))

    combined = [max(a, b) for a, b in zip(ct_scores, ring_scores)]
    return combined, ct_scores, ring_scores


def _ring_account_score(a, pin, dev, con, pin_accounts, dev_accounts,
                        con_accounts, conj, typical_pin, cfg):
    """Score one account's ring evidence. Event stream only."""
    s = 0.0
    # The conjunction dominates: several accounts on one pincode who ALSO share
    # a device. A busy pincode on its own is not evidence.
    k = conj.get(a, 0)
    if k >= 2:
        s += 4.0 + 0.5 * min(k, 10)
    if pin:
        cluster = len(pin_accounts[pin])
        # Judged against how populous a pincode normally is, not a global cut.
        if cluster >= max(cfg.ring_min_accounts, 3):
            s += 1.5 * min(cluster / max(typical_pin, 1.0), 4.0)
    kd = len(dev_accounts.get(dev, ()))
    if kd >= 2:
        s += 1.8 * min(kd, 8) / 8.0
    if len(con_accounts.get(con, ())) >= 2:
        s += 1.0
    return s


def account_scores(events, cfg):
    """Ring scoring at the ACCOUNT level, the natural unit for a ring.

    Returns (account_ids, scores). Card testing is deliberately not scored here:
    90% of its rows are guest checkout with no account to aggregate to.
    """
    mc = lambda e: e["merchant_context"]
    pin_accounts = collections.defaultdict(set)
    dev_accounts = collections.defaultdict(set)
    con_accounts = collections.defaultdict(set)
    acct_pins = collections.defaultdict(set)
    acct_devs = collections.defaultdict(set)
    acct_cons = collections.defaultdict(set)
    for e in events:
        a = mc(e)["account_id"]
        if not a:
            continue
        p = mc(e)["shipping_pincode"]
        if p:
            pin_accounts[p].add(a)
            acct_pins[a].add(p)
        dev_accounts[mc(e)["device_id"]].add(a)
        acct_devs[a].add(mc(e)["device_id"])
        con_accounts[e["contact"]].add(a)
        acct_cons[a].add(e["contact"])

    pin_total = sum(len(v) for v in pin_accounts.values()) or 1
    typical_pin = pin_total / (len(pin_accounts) or 1)

    conj = {}
    for a in acct_pins:
        best = 0
        for p in acct_pins[a]:
            peers = pin_accounts[p]
            for d in acct_devs[a]:
                best = max(best, len(peers & dev_accounts[d]))
        conj[a] = best

    ids = sorted(acct_devs)
    out = []
    for a in ids:
        best = 0.0
        for p in (acct_pins[a] or {None}):
            for d in acct_devs[a]:
                for c in acct_cons[a]:
                    best = max(best, _ring_account_score(
                        a, p, d, c, pin_accounts, dev_accounts, con_accounts,
                        conj, typical_pin, cfg))
        out.append(best)
    return ids, out
