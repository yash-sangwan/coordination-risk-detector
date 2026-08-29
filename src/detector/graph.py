"""Graph detector for card testing. Relationships between events, not columns.

The structure spec 2.1 describes is **fanout against overlap**. A burst runs many
throwaway identities across very few instruments and devices: 1 to 3 IINs, 1 to 5
fingerprints, while email, contact and PAN are fresh every attempt. So the thing
to measure is not any field's value but the *disagreement* between two kinds of
concentration in the same window:

    instruments and devices converge   AND   identities do not

Either half alone is ambiguous. High device concentration on its own is just one
customer retrying. High identity fanout on its own is just a busy minute, which
is exactly what a flash sale looks like. Their conjunction is the burst.

Concentration is measured as a Herfindahl index, sum of squared shares. It is
scale-free, so a window of 12 events and a window of 900 are on the same footing
and no volume threshold is smuggled in. Every statistic here comes from the event
stream. No labels, no answer key, no outcome fields.
"""

import bisect
import collections


def sliding_hhi(keys, ts, window_s):
    """Herfindahl concentration of `keys` over a trailing window, per event.

    Returns (hhi, n_distinct, n) per position. Maintains the sum of squared
    counts incrementally, so adding one observation is O(1) rather than a
    recount over the window.

    Trailing window only. A live detector cannot see the future.
    """
    n = len(keys)
    lo_idx = [bisect.bisect_left(ts, t - window_s) for t in ts]
    counts = collections.Counter()
    sumsq = 0
    left = 0
    out = []
    for i in range(n):
        k = keys[i]
        if k is not None:
            c = counts[k]
            sumsq += 2 * c + 1
            counts[k] = c + 1
        while left < lo_idx[i]:
            k0 = keys[left]
            if k0 is not None:
                c = counts[k0]
                sumsq -= 2 * c - 1
                counts[k0] = c - 1
                if counts[k0] == 0:
                    del counts[k0]
            left += 1
        tot = sum(counts.values())
        hhi = (sumsq / (tot * tot)) if tot else 0.0
        out.append((hhi, len(counts), tot))
    return out


def _keys(events, field):
    mc = lambda e: e["merchant_context"]
    if field == "iin":
        return [(e.get("card") or {}).get("iin") for e in events]
    if field == "device":
        return [mc(e)["device_id"] for e in events]
    if field == "email":
        return [e["email"] for e in events]
    if field == "contact":
        return [e["contact"] for e in events]
    if field == "last4":
        return [(e.get("card") or {}).get("last4") for e in events]
    if field == "pincode":
        return [mc(e)["shipping_pincode"] for e in events]
    if field == "vpa":
        v = [e.get("vpa") for e in events]
        return [x.split("@")[0] if x else None for x in v]
    raise KeyError(field)


CONVERGE = ("iin", "device")     # a burst collapses onto these
DIVERGE = ("email", "contact", "last4")   # and scatters across these


def components(events, window_s):
    """Per-event concentration for every linking attribute in the window."""
    ts = [e["created_at"] for e in events]
    return {f: sliding_hhi(_keys(events, f), ts, window_s)
            for f in CONVERGE + DIVERGE + ("pincode", "vpa")}


def score_card_testing(events, window_s=300, min_events=8, comp=None,
                       use_converge=CONVERGE, use_diverge=DIVERGE):
    """Fanout against overlap.

    For each converging attribute, its concentration is credited only to the
    extent that identities in the same window are NOT concentrated. That product
    is what separates a burst from both a single retrying customer and a flash
    sale, neither of which shows the disagreement.

    `min_events` guards the Herfindahl at tiny window sizes, where two events
    sharing a device is not yet evidence of anything.
    """
    comp = comp or components(events, window_s)
    n = len(events)
    # Identity fanout: how spread the identities are, averaged over the
    # diverging attributes present in this window.
    scores = []
    for i in range(n):
        tot = comp[use_converge[0]][i][2]
        if tot < min_events:
            scores.append(0.0)
            continue
        div = []
        for f in use_diverge:
            hhi, _, m = comp[f][i]
            if m >= min_events:
                div.append(1.0 - hhi)
        fanout = (sum(div) / len(div)) if div else 0.0
        s = 0.0
        for f in use_converge:
            hhi, _, m = comp[f][i]
            if m >= min_events:
                s += hhi * fanout
        scores.append(s)
    return scores


def convergence_only(events, window_s, field, comp=None):
    """Concentration of one attribute alone, for ablation."""
    comp = comp or components(events, window_s)
    return [h for h, _, _ in comp[field]]
