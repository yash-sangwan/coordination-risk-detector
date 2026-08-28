"""Card testing bursts (spec 2.1). No ring, no acceptance tests.

The shape that matters is **high fanout, low overlap**: many distinct throwaway
identities converging on very few instruments and devices. Both halves are
modelled here, because a generator that only gets the sharing half right makes
the attack trivially separable on the fields it forgot to vary.

Nothing in this module writes a marker into the event stream. An attack attempt
produces exactly the same field set as a legitimate one; only the sealed store
knows the difference. Identities are drawn from the *same* helpers the legitimate
population uses, so there is no string tell to find.
"""

import math
from dataclasses import dataclass, field
from typing import Optional

from . import config as C
from .population import (_email_local, _weighted, build_iin_table,
                         build_pincode_table)


@dataclass
class AttackIdentity:
    """A throwaway identity, fresh per attempt.

    Deliberately shaped like an Actor so emit.build_row does not need to know
    whether it is rendering legitimate or attack traffic.
    """
    account_id: Optional[str]
    device_id: str
    pincode: Optional[str]
    email: str
    contact: str
    vpa: Optional[str] = None
    signup_ts: Optional[int] = None
    wants_account: bool = False
    actor_id: str = "-"
    actor_class: str = "-"
    tier: str = "-"
    cards: list = field(default_factory=list)


@dataclass
class Burst:
    burst_id: str
    start: int
    end: int
    rate_per_min: float
    iins: list          # 1-3 (iin, issuer, network) tuples
    device_ids: list    # 1-5 fingerprints
    ending: str
    envelope: float


def _campaign_envelope(frac: float) -> float:
    """Slow rise, plateau, decline. Matches the cited airline campaign shape."""
    rise = C.CAMPAIGN_RISE_FRACTION
    plateau_end = rise + C.CAMPAIGN_PLATEAU_FRACTION
    floor = C.CAMPAIGN_ENVELOPE_FLOOR
    if frac <= rise:
        return floor + (1.0 - floor) * (frac / rise)
    if frac <= plateau_end:
        return 1.0
    tail = (frac - plateau_end) / max(1e-9, 1.0 - plateau_end)
    return max(floor, 1.0 - (1.0 - floor) * tail)


def schedule_campaign(rng, window_start: int, window_end: int):
    """One campaign of recurring bursts across the window.

    Burst start times are drawn without reference to the legitimate hourly
    profile: a bot does not care that it is 04:00. Restricting bursts to quiet
    hours would make time-of-day the label, so they land wherever they land and
    frequently overlap real traffic.
    """
    iin_pairs, _ = build_iin_table()

    n_bursts = rng.randint(*C.BURSTS_PER_CAMPAIGN)
    span = window_end - window_start

    # The campaign occupies most of the window, so the rise, plateau and decline
    # all fit inside it. Computing the envelope against the window instead of the
    # campaign's own span left every burst on the rising limb and the decline was
    # never generated.
    camp_start = window_start + int(span * rng.uniform(0.06, 0.12))
    camp_end = window_start + int(span * rng.uniform(0.88, 0.96))
    camp_span = max(camp_end - camp_start, 3600)

    bursts = []
    for i in range(n_bursts):
        # One burst per interval, jittered inside it. Advancing a cursor by whole
        # days instead put every burst at roughly the same time of day.
        lo = camp_start + int(camp_span * i / n_bursts)
        hi = camp_start + int(camp_span * (i + 1) / n_bursts)
        cursor = rng.randint(lo, max(lo + 1, hi - 1))
        if cursor >= window_end - 3600:
            break
        frac = (cursor - camp_start) / camp_span
        env = _campaign_envelope(frac)

        minutes = rng.randint(*C.BURST_MINUTES)
        # Rate is drawn from the spec band then scaled by the campaign envelope,
        # so early and late bursts are genuinely weaker than the plateau ones.
        rate = rng.uniform(*C.BURST_RATE_PER_MIN) * env

        # Stolen cards are real cards from real issuers, so attack IINs come from
        # the same pool legitimate customers use. A novel IIN would BE the label.
        k_iin = rng.randint(*C.BURST_IIN_COUNT)
        iins = [_weighted(rng, iin_pairs) for _ in range(k_iin)]

        k_dev = rng.randint(*C.BURST_DEVICE_COUNT)
        devices = [f"dev_{rng.getrandbits(48):012x}" for _ in range(k_dev)]

        bursts.append(Burst(
            burst_id=f"b{i:02d}",
            start=cursor,
            end=cursor + minutes * 60,
            rate_per_min=rate,
            iins=iins,
            device_ids=devices,
            ending=_weighted(rng, C.BURST_ENDINGS),
            envelope=env,
        ))

    return bursts


def _attack_amount(rng, legit_amount_fn):
    u = rng.random()
    if u < C.ATTACK_AMOUNT_MICRO:
        return rng.randint(100, 5000)          # 1 to 50 rupees
    if u < C.ATTACK_AMOUNT_MICRO + C.ATTACK_AMOUNT_LOW:
        return rng.randint(5000, 50000)        # 50 to 500 rupees
    # Deliberate blending: a slice drawn from the legitimate distribution, so an
    # amount threshold alone cannot separate the populations.
    return legit_amount_fn(rng)


def _attack_checkout_ms(rng):
    """Low but not degenerate. Real bot frameworks add randomised delays, so a
    single tight spike would be both unrealistic and trivially separable."""
    lo, hi = C.ATTACK_CHECKOUT_MS
    v = int(rng.lognormvariate(math.log(C.ATTACK_CHECKOUT_MODE), 0.75))
    return max(lo, min(v, hi))


def _attack_decline_reason(rng):
    return _weighted(rng, [(r, w) for r, w, _, _, _ in C.ATTACK_DECLINE_REASONS]), None


def burst_attempts(rng, burst: Burst, legit_amount_fn, minter_free=True):
    """Generate one burst's attempts.

    Returns a list of (ts, AttackIdentity, attempt_dict). Timestamps are Poisson
    arrivals at the burst rate; the caller merges them into the main stream and
    sorts by created_at, so attack rows never occupy a contiguous block.
    """
    reason_lookup = {r[0]: r for r in C.ATTACK_DECLINE_REASONS}
    pin_pairs, _, _ = build_pincode_table()
    # Attack emails are drawn from the SAME weighted domain distribution as
    # legitimate ones. Drawing them uniformly gave attack traffic higher domain
    # entropy than real traffic, which is a tell in the opposite direction: real
    # throwaway addresses are gmail-heavy too.
    domain_pairs = list(C.EMAIL_TOP_DOMAINS) + [("__other__", C.EMAIL_OTHER_DOMAIN_SHARE)]

    out = []
    t = float(burst.start)
    duration = max(burst.end - burst.start, 60)
    decay_start = burst.end - rng.randint(*C.BURST_DECAY_MINUTES) * 60

    while t < burst.end:
        # Exponential inter-arrivals at the burst rate.
        gap = rng.expovariate(max(burst.rate_per_min, 1e-6) / 60.0)
        t += gap
        if t >= burst.end:
            break
        ts = int(t)
        progress = (ts - burst.start) / duration

        # Endings. The three are modelled separately because a detector that only
        # ever sees one of them will overfit to it.
        decline_p = C.ATTACK_DECLINE_BASE
        if burst.ending == "blocked" and progress > 0.75:
            # Attempts continue briefly at a rising decline rate, then stop.
            ramp = (progress - 0.75) / 0.25
            decline_p = C.ATTACK_DECLINE_BASE + ramp * (
                C.ATTACK_DECLINE_BLOCKED - C.ATTACK_DECLINE_BASE)
        elif burst.ending == "moves_on" and ts > decay_start:
            # Rate decays: thin the attempts out toward the end.
            keep = 1.0 - (ts - decay_start) / max(burst.end - decay_start, 1)
            if rng.random() > keep:
                continue
        # "exhausted" needs no modification: it simply stops at burst.end.

        iin, issuer, network = rng.choice(burst.iins)

        # --- the low-overlap half. Everything below is fresh per attempt. ---
        dom = _weighted(rng, domain_pairs)
        if dom == "__other__":
            dom = rng.choice(["airtelmail.in", "bsnl.in", "acme-corp.in", "vsnl.net",
                              "zoho.in", "icloud.com", "gmx.com", "mail.in"])
        email = f"{_email_local(rng, rng.randint(0, 4))}@{dom}"
        contact = "9" + "".join(str(rng.randint(0, 9)) for _ in range(9))

        # "Usually" null, not always. A bot that registers an account, or one
        # buying something that ships, both happen. If attack traffic were 100%
        # null on either field, nullness alone would be the label.
        wants_account = rng.random() >= C.ATTACK_ACCOUNT_ID_NULL_SHARE
        has_pincode = rng.random() >= C.ATTACK_PINCODE_NULL_SHARE

        ident = AttackIdentity(
            account_id=None,
            device_id=rng.choice(burst.device_ids),
            pincode=_weighted(rng, pin_pairs) if has_pincode else None,
            email=email,
            contact=contact,
            wants_account=wants_account,
        )

        failed = rng.random() < decline_p
        reason = None
        if failed:
            name, _ = _attack_decline_reason(rng)
            r = reason_lookup[name]
            reason = (r[0], r[2], r[3], r[4])

        out.append((ts, ident, {
            "ts": ts,
            "method": "card",
            "international": False,
            "amount": _attack_amount(rng, legit_amount_fn),
            # last4 differs every attempt: same IIN, different PAN.
            "card": type("C", (), {
                "iin": iin, "last4": f"{rng.randint(0, 9999):04d}",
                "network": network, "type": "credit" if rng.random() < 0.55 else "debit",
                "issuer": issuer,
            })(),
            "failed": failed,
            "reason": reason,
            "attempt_seq": 1 if rng.random() < C.ATTACK_ATTEMPT_SEQ1_SHARE else 2,
            "checkout_ms": _attack_checkout_ms(rng),
            "downtime_active": False,
            "burst_id": burst.burst_id,
            "wallet": None,
        }))
    return out
