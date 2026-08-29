"""Ring detector, account level. The conjunction, and what follows from it.

A ring is the inverse shape of a card testing burst: a handful of real accounts
sharing a drop address and some devices, over weeks, never bursty. Volume sees
nothing. The drop address alone is worthless, because hot urban pincodes carry
over 130 innocent accounts and flagging a pincode flags all of them.

The account-level diagnostic found exactly one feature that separates a ring
member from its innocent neighbours on the same pincode: **accounts that share
BOTH a pincode and a device**. This is the detector that follows from that.

It has two stages, and the second is where the interesting part is.

  Stage 1, the conjunction. Build a graph whose edge is "these two accounts
  share a pincode AND share a device", and take connected components. This is
  strong evidence and it is also narrowly scoped: only the members who actually
  share a device can be reached this way, which caps recall at the device
  sharing rate.

  Stage 2, the drop address. A conjunction component sitting on a pincode is
  evidence about the PINCODE, not only about its members. Once a pincode is
  implicated, every account on it inherits suspicion. That is how an analyst
  reasons, and it is the only route past the stage 1 ceiling.

Stage 2 is also where a naive version destroys itself. Household device sharing
runs at ~6%, so a busy pincode with 130 accounts will contain incidental device
pairs, and propagating from those would flag all 130. What separates the two
cases is not the size of the conjunction but its DENSITY: a ring is a small
pincode cluster of which a large fraction is device-linked, while a hot pincode
is a huge cluster with a couple of coincidental pairs. Density is the whole
defence and it is why the score multiplies by it rather than adding it.

Everything here reads the event stream. No labels, no answer key, no outcome
store. Thresholds arrive as arguments; choosing them needs labels and is the
harness's job.
"""

import collections


def account_attributes(events):
    """Per account, the entities it touches. Event stream only.

    Guest checkout rows carry no account and are skipped: an account-level
    detector has nothing to attach them to. That is a real limitation of the
    unit, not of this implementation, and it is why card testing is not scored
    here at all.
    """
    pins = collections.defaultdict(set)
    devs = collections.defaultdict(set)
    cons = collections.defaultdict(set)
    first_seen = {}
    for e in events:
        mc = e["merchant_context"]
        a = mc["account_id"]
        if not a:
            continue
        if mc["shipping_pincode"]:
            pins[a].add(mc["shipping_pincode"])
        devs[a].add(mc["device_id"])
        cons[a].add(e["contact"])
        if a not in first_seen:
            first_seen[a] = e["created_at"]
    return pins, devs, cons, first_seen


def _invert(d):
    out = collections.defaultdict(set)
    for k, vals in d.items():
        for v in vals:
            out[v].add(k)
    return out


class _DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def conjunction_components(pins, devs):
    """Connected components of "shares a pincode AND shares a device".

    Built by intersecting the two co-membership relations rather than by
    enumerating account pairs, so this stays linear in the number of shared
    entities instead of quadratic in accounts.
    """
    by_pin = _invert(pins)
    by_dev = _invert(devs)

    dsu = _DSU()
    for dev, accounts in by_dev.items():
        if len(accounts) < 2:
            continue
        # Among accounts on this device, link those that also share a pincode.
        by_pin_local = collections.defaultdict(list)
        for a in accounts:
            for p in pins.get(a, ()):
                by_pin_local[p].append(a)
        for p, group in by_pin_local.items():
            if len(group) < 2:
                continue
            first = group[0]
            for other in group[1:]:
                dsu.union(first, other)

    comps = collections.defaultdict(set)
    for a in list(dsu.p):
        comps[dsu.find(a)].add(a)
    return [c for c in comps.values() if len(c) >= 2], by_pin


def pair_share_rate(pins, devs):
    """P(two random accounts share a device), measured on THIS window.

    This is the background the conjunction is judged against. It is a rate, not
    a count, but it is not itself window-invariant: a device carries one to three
    accounts however long you watch, so the number of sharing groups grows with
    the account count A while the pair denominator grows as A^2, leaving this
    quantity falling as ~1/A. That is exactly the factor that cancels the growth
    in cluster size, which is why dividing by it produces a scale-free score.
    """
    by_dev = _invert(devs)
    a = len(devs)
    if a < 2:
        return 0.0
    pairs = sum(len(v) * (len(v) - 1) / 2.0 for v in by_dev.values() if len(v) > 1)
    return pairs / (a * (a - 1) / 2.0)


SCORE_MODES = ("raw", "density", "lift", "bg", "q95", "rank")


def _pin_weight(mode, k, population, p_pair):
    """Evidence attaching to one pincode, in the chosen units.

    raw      k^2 / n. The original. NOT scale invariant: k and n both grow with
             the observation window, so this grows with it too. Kept only so the
             comparison has the old behaviour in it.

    density  k / n. The share of a pincode's accounts that are device linked.
             Dimensionless and exactly invariant, because k and n scale together.
             Says nothing about how surprising that share is.

    lift     observed conjunction rate over the rate this window would produce by
             CHANCE. Under a null where devices are assigned independently of
             pincodes, an account on a pincode of n has about (n-1) chances to be
             device linked to a neighbour, each with probability p_pair, so the
             expected linked count is n(n-1)p_pair. Dividing the observed k by it
             gives a ratio against an analytic background rate. MEASURED, this
             drifts badly and in the opposite direction to raw, for the reason
             below.

    bg       raw, divided by the median raw weight of every candidate cluster in
             the SAME window. See the note below: this is the one that transfers.

    Why the analytic normalisations do not work, measured rather than assumed.
    All of raw, density and lift assume k and n scale together as the window
    grows. They do not. A pincode's population saturates almost immediately,
    since every ring member shows up early, while k, the size of the observed
    device-linked component, keeps growing because a link needs BOTH endpoints
    and their shared device to have been seen. So k accumulates and n does not,
    and the drift is in the EVIDENCE rather than in the units. No closed form in
    (k, n, p_pair) can cancel it. Measured across test windows of 10/20/30/50%:
    raw drifts 1.125 -> 2.286, density 0.375 -> 0.571, lift 2.0e4 -> 3.4e3.

    What does cancel it is a background that accumulates at the same rate as the
    signal, which is the window's own population of clusters. `bg` divides by the
    median candidate weight in the same window, so it reads "this cluster is N
    times the typical cluster this window produces". That is the sentence the
    score was always trying to express, and because it is a single constant per
    window it is a pure rescale: the RANKING within a window is untouched, so
    PR AUC is identical to raw and the fix costs nothing in discrimination. What
    it buys is a threshold that means the same thing on any window.
    """
    if population < 2:
        return 0.0
    if mode in ("raw", "bg", "q95", "rank"):   # all rescaled per window below
        return k * (k / population)
    if mode == "density":
        return k / population
    if mode == "lift":
        expected = population * (population - 1) * p_pair
        return (k / expected) if expected > 0 else 0.0
    raise KeyError(mode)


def score_accounts(events, min_component=2, out_weight=0.35, contact_weight=0.0,
                   min_pin_population=0, score_mode="raw"):
    """Continuous ring score per account. Higher is more suspicious.

    `out_weight` is the discount applied to an account that sits on an
    implicated pincode but is NOT itself in the conjunction component. At 0.0
    the detector is stage 1 only and cannot exceed the device sharing rate. At
    1.0 it trusts the drop address as much as the direct evidence, which floods
    on any pincode carrying an incidental household device pair. It is a
    parameter because the right trade is an empirical question, not because it
    is being fitted to a target.

    `contact_weight` adds the "occasional carelessness" signal, an account whose
    phone number is used by another account.

    `min_pin_population` is the smallest number of accounts a pincode must serve
    before it is treated as a possible drop address. It exists because density
    alone gets the household case backwards: a family of two sharing a device
    and an address is a component of 2 on a pincode of 2, which is density 1.0,
    while a ring of eleven with four device-linked members is density 0.36. Left
    unguarded the score therefore ranks households ABOVE rings. The separating
    idea is not purity but reach: a drop address collects for more people than a
    household contains, so a floor on the pincode's population is what tells the
    two apart. This matters only against a population where households share an
    address, which is the realistic case.
    """
    pins, devs, cons, _ = account_attributes(events)
    comps, by_pin = conjunction_components(pins, devs)
    p_pair = pair_share_rate(pins, devs)

    # Strongest conjunction evidence attaching to each pincode, in whichever
    # units `score_mode` selects. Density and lift are scale free; raw is not.
    pin_evidence = {}
    for comp in comps:
        if len(comp) < min_component:
            continue
        shared_pins = set.intersection(*[pins[a] for a in comp]) if comp else set()
        for p in shared_pins:
            population = len(by_pin.get(p, ()))
            if population < max(2, min_pin_population):
                continue
            weight = _pin_weight(score_mode, len(comp), population, p_pair)
            if weight > pin_evidence.get(p, (0.0,))[0]:
                pin_evidence[p] = (weight, comp)

    if score_mode in ("bg", "q95", "rank") and pin_evidence:
        # Normalise against the window's OWN population of candidate clusters,
        # which is the only background that accumulates evidence at the same rate
        # the signal does. All three are monotone within a window, so the ranking
        # and therefore PR AUC are untouched; only the threshold's meaning moves.
        ws = sorted(w for w, _ in pin_evidence.values())
        n = len(ws)
        if score_mode == "bg":
            # Ratio to the TYPICAL cluster. Median, so a few large clusters
            # cannot inflate their own denominator.
            ref = ws[n // 2] if n % 2 else (ws[n // 2 - 1] + ws[n // 2]) / 2.0
            if ref > 0:
                pin_evidence = {p: (w / ref, c)
                                for p, (w, c) in pin_evidence.items()}
        elif score_mode == "q95":
            # Ratio to the TAIL. A detector's operating point lives in the tail,
            # not the middle, so the tail is the reference that has to be stable.
            ref = ws[min(n - 1, int(0.95 * n))]
            if ref > 0:
                pin_evidence = {p: (w / ref, c)
                                for p, (w, c) in pin_evidence.items()}
        else:
            # Pure quantile position: the share of this window's clusters that
            # this one exceeds. Unitless by construction, so a threshold of 0.99
            # means "top 1% of clusters" on any window of any length.
            below = {}
            for w in ws:
                below.setdefault(w, sum(1 for x in ws if x < w) / n)
            pin_evidence = {p: (below[w], c)
                            for p, (w, c) in pin_evidence.items()}

    by_con = _invert(cons)
    scores = {}
    for a in devs:                      # every account seen in the stream
        best = 0.0
        for p in pins.get(a, ()):
            ev = pin_evidence.get(p)
            if ev is None:
                continue
            weight, comp = ev
            best = max(best, weight if a in comp else weight * out_weight)
        if contact_weight and any(len(by_con[c]) > 1 for c in cons.get(a, ())):
            best += contact_weight
        scores[a] = best
    return scores


def score_pincode_only(events):
    """Stage 2 with no stage 1: trust the drop address on its own.

    Kept so the ablation can show what the conjunction is actually buying. This
    is close to the existing pincode baseline and is expected to be useless for
    the same reason.
    """
    pins, devs, _, _ = account_attributes(events)
    by_pin = _invert(pins)
    return {a: max((len(by_pin[p]) for p in pins.get(a, ())), default=0)
            for a in devs}
