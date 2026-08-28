"""Rings (spec 2.2). Brief, as the spec intends.

The shape is the inverse of a card testing burst. A burst is high fanout and low
overlap: many throwaway identities converging on a few instruments in minutes.
A ring is low fanout and high overlap: a handful of real accounts sharing a drop
address and some devices, over weeks.

Ring members are ordinary actors from the population, not throwaway identities.
They are created over days or weeks, behave normally through a dormancy period,
then their later activity is what carries the label. The shared entities exist
from account setup, so they appear in that member's legitimate rows too, which
is both realistic and what stops "shares a drop address" from being the label.
"""

from dataclasses import dataclass
from typing import Optional

from . import config as C
from .population import _email_local, _weighted, build_iin_table, build_pincode_table


@dataclass
class Ring:
    ring_id: str
    member_ids: list
    drop_pincode: str
    shared_device: str
    activation_ts: int
    caught_ts: Optional[int]      # None means never caught inside the window
    email_shape: int


def build_rings(rng, actors, window_start, window_end):
    """Assemble rings out of existing actors. Returns (rings, membership)."""
    pin_pairs, _, _ = build_pincode_table()
    iin_pairs, _ = build_iin_table()
    span = window_end - window_start

    n_rings = rng.randint(*C.RING_COUNT)
    pool = rng.sample(range(len(actors)), min(len(actors), n_rings * C.RING_SIZE[1]))
    cursor = 0

    rings, membership = [], {}
    for i in range(n_rings):
        size = rng.randint(*C.RING_SIZE)
        idxs = pool[cursor:cursor + size]
        cursor += size
        if len(idxs) < C.RING_SIZE[0]:
            break
        members = [actors[j] for j in idxs]

        # Activation sits far enough into the window to leave weeks of activity.
        activation = window_start + int(span * rng.uniform(0.20, 0.45))
        dormancy = rng.randint(*C.RING_DORMANCY_DAYS) * 86400
        spread = rng.randint(*C.RING_SIGNUP_SPREAD_DAYS) * 86400

        # Exactly one ring is never caught, so recall cannot reach 100% by
        # accident. The rest are caught partway through.
        if i == 0 or rng.random() >= C.RING_CAUGHT_PROB:
            caught = None
        else:
            caught = activation + rng.randint(*C.RING_CAUGHT_AFTER_DAYS) * 86400
            if caught >= window_end:
                caught = None

        drop = _weighted(rng, pin_pairs)
        shared_device = f"dev_{rng.getrandbits(48):012x}"
        shape = rng.randint(0, 4)

        # A subset shares the device, not everyone. Sloppiness is partial.
        k_dev = max(2, int(len(members) * rng.uniform(*C.RING_DEVICE_SUBSET)))
        device_sharers = set(rng.sample(range(len(members)), min(k_dev, len(members))))

        for pos, a in enumerate(members):
            # Accounts created over days or weeks, all before activation.
            a.signup_ts = activation - dormancy - rng.randint(0, spread)
            a.pincode = drop
            if pos in device_sharers:
                a.device_id = shared_device
            # Same local-part SHAPE, different domains. Shape is the tell, not
            # the domain, so the domain distribution is left alone.
            dom = a.email.split("@")[1]
            a.email = f"{_email_local(rng, shape)}@{dom}"
            # Instruments are deliberately varied: a ring does not reuse an IIN.
            for card in a.cards:
                iin, issuer, network = _weighted(rng, iin_pairs)
                card.iin, card.issuer, card.network = iin, issuer, network

        # Occasional carelessness: one pair shares a phone.
        if len(members) >= 2 and rng.random() < C.RING_CONTACT_REUSE_PROB:
            b = rng.randrange(1, len(members))
            members[b].contact = members[0].contact

        r = Ring(ring_id=f"r{i:02d}",
                 member_ids=[a.actor_id for a in members],
                 drop_pincode=drop, shared_device=shared_device,
                 activation_ts=activation, caught_ts=caught, email_shape=shape)
        rings.append(r)
        for a in members:
            membership[a.actor_id] = r
    return rings, membership


def ring_active(ring: Ring, ts: int, window_end: int) -> bool:
    """Is this timestamp inside the ring's fraudulent period?"""
    if ts < ring.activation_ts:
        return False
    end = ring.caught_ts if ring.caught_ts is not None else window_end
    return ts < end


def ring_extra_sessions(rng, rings, actors_by_id, window_end):
    """Low-rate extra activity while a ring is live.

    Deliberately not bursty: sessions are spread uniformly across the whole
    active period, which can run for weeks. This is the opposite of a burst and
    is why volume features should barely see a ring at all.
    """
    out = []
    for r in rings:
        end = r.caught_ts if r.caught_ts is not None else window_end
        days = max((end - r.activation_ts) / 86400.0, 0.0)
        if days <= 0:
            continue
        for aid in r.member_ids:
            actor = actors_by_id[aid]
            n = int(round(days * rng.uniform(*C.RING_SESSIONS_PER_DAY)))
            for _ in range(n):
                out.append((rng.randint(r.activation_ts, max(r.activation_ts, end - 1)),
                            actor))
    return out
