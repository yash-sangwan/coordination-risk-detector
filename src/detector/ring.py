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


def score_accounts(events, min_component=2, out_weight=0.35, contact_weight=0.0,
                   min_pin_population=0):
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

    # Strongest conjunction evidence attaching to each pincode, as (size,
    # density). Density is size relative to how populous that pincode is, which
    # is what separates a small drop address from a busy commercial one.
    pin_evidence = {}
    for comp in comps:
        if len(comp) < min_component:
            continue
        shared_pins = set.intersection(*[pins[a] for a in comp]) if comp else set()
        for p in shared_pins:
            population = len(by_pin.get(p, ()))
            if population < max(2, min_pin_population):
                continue
            density = len(comp) / population
            weight = len(comp) * density
            if weight > pin_evidence.get(p, (0.0,))[0]:
                pin_evidence[p] = (weight, comp)

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
