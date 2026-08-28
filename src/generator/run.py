"""Generator entry point. Legitimate traffic only.

    python -m src.generator.run --seed 42 --days 14 --actors 4000 --out data/sample

Determinism: one seeded random.Random drives everything, and named sub-streams
are derived from it so that changing one stage does not reshuffle another. Same
seed gives byte-identical output.
"""

import argparse
import os
import random

from . import config as C
from .emit import build_row, sealed_record, write_manifest, write_stream, CUT_FIELDS
from .ids import IdMinter
from .population import build_population
from .rings import build_rings, ring_active, ring_extra_sessions
from .attacks import burst_attempts, schedule_campaign
from .behaviour import attempts_for_session, draw_amount, sessions_for_actor
from .timeline import (Timeline, flash_sale_extra_sessions,
                       schedule_downtimes, schedule_flash_sales)

WALLETS = ["paytm", "phonepe", "amazonpay", "mobikwik", "freecharge"]

# Fixed window anchor so output does not depend on wall-clock time.
DEFAULT_WINDOW_END = 1787000000


def generate(seed: int, days: int, n_actors: int, with_attacks: bool = True):
    master = random.Random(seed)
    rng_pop = random.Random(master.getrandbits(64))
    rng_cal = random.Random(master.getrandbits(64))
    rng_beh = random.Random(master.getrandbits(64))
    rng_atk = random.Random(master.getrandbits(64))

    window_end = DEFAULT_WINDOW_END
    window_start = window_end - days * 86400

    actors, diag = build_population(rng_pop, n_actors, window_start, window_end)

    rng_ring = random.Random(seed ^ 0x5217)
    rings, membership = ([], {})
    if with_attacks:
        rings, membership = build_rings(rng_ring, actors, window_start, window_end)
    actors_by_id = {a.actor_id: a for a in actors}

    flash_sales = schedule_flash_sales(rng_cal, window_start, window_end)
    downtimes = schedule_downtimes(rng_cal, window_start, window_end)
    timeline = Timeline(window_start, window_end, flash_sales)

    # Actors act. Collect (ts, actor, attempt) then sort by time, so the stream is
    # time-ordered and no population occupies a contiguous block.
    # Phase 1: baseline demand. Phase 2: the extra sessions a flash sale creates
    # on top of it. Sales used to be folded into the intensity profile, which only
    # moved a fixed session count around and never reached the stated multiplier.
    base_sessions = []
    for actor in actors:
        for ts in sessions_for_actor(rng_beh, actor, timeline, days):
            base_sessions.append((ts, actor))

    extra_sessions = flash_sale_extra_sessions(rng_cal, flash_sales,
                                               base_sessions, actors)
    # Ring activity: low rate, spread across weeks, never bursty.
    extra_sessions += ring_extra_sessions(rng_ring, rings, actors_by_id, window_end)

    pending = []
    for ts, actor in base_sessions + extra_sessions:
        fm = timeline.flash_multiplier(ts)
        attempts = attempts_for_session(rng_beh, actor, ts, downtimes, fm)
        wallet = rng_beh.choice(WALLETS)
        sid = f"sess_{rng_beh.getrandbits(48):012x}"
        # A ring member's events are fraudulent only while the ring is live.
        # Everything before activation is that member building normal history.
        ring = membership.get(actor.actor_id)
        for a in attempts:
            a["session_id"] = sid
            a["wallet"] = wallet
            if ring is not None and ring_active(ring, a["ts"], window_end):
                a["label"] = 1
                a["attack_type"] = "ring"
                a["ring_id"] = ring.ring_id
            else:
                a["label"] = 0
            pending.append((a["ts"], actor, a))

    # Card testing bursts. Attack attempts join the same list before it is sorted,
    # so they interleave by created_at and never occupy a contiguous block.
    bursts = []
    if with_attacks:
        bursts = schedule_campaign(rng_atk, window_start, window_end)
        for b in bursts:
            for ts_a, ident, a in burst_attempts(rng_atk, b, draw_amount):
                # session_id is new per attempt: card testing does not browse.
                a["session_id"] = f"sess_{rng_atk.getrandbits(48):012x}"
                a["label"] = 1
                a["attack_type"] = "card_testing"
                pending.append((ts_a, ident, a))

    pending.sort(key=lambda t: (t[0], t[1].actor_id, t[2]["attempt_seq"]))

    minter = IdMinter()

    # Account ids are minted from the same monotonic sequence, at first sighting.
    account_ids = {}
    for ts, actor, _ in pending:
        if actor.actor_id == "-":
            continue
        if actor.actor_id not in account_ids:
            account_ids[actor.actor_id] = minter.mint("acct", ts)
    for actor in actors:
        actor.account_id = account_ids.get(actor.actor_id, "")

    # The minority of attack identities that registered an account get one from
    # the same monotonic sequence, so an attack account id is indistinguishable
    # from a legitimate one.
    for ts, ident, _ in pending:
        if getattr(ident, "wants_account", False) and not ident.account_id:
            ident.account_id = minter.mint("acct", ts)

    # Order ids: one per session, minted at the session's first attempt.
    order_ids = {}
    for ts, actor, a in pending:
        if a["session_id"] not in order_ids:
            order_ids[a["session_id"]] = minter.mint("order", ts)
        a["order_id"] = order_ids[a["session_id"]]

    rows, sealed = [], []
    for ts, actor, a in pending:
        row = build_row(minter, actor, a)
        rows.append(row)
        sealed.append(sealed_record(row, actor, a, timeline.in_flash_sale(ts),
                                    label=a.get("label", 0)))

    manifest = {
        "seed": seed,
        "days": days,
        "n_actors": n_actors,
        "window_start": window_start,
        "window_end": window_end,
        "n_events": len(rows),
        "contains_attacks": bool(bursts),
        "n_attack_events": sum(1 for s in sealed if s["label"] == 1),
        "n_card_testing_events": sum(1 for s in sealed if s["attack_type"] == "card_testing"),
        "n_ring_events": sum(1 for s in sealed if s["attack_type"] == "ring"),
        "rings": [
            {"ring_id": r.ring_id, "size": len(r.member_ids),
             "drop_pincode": r.drop_pincode, "activation": r.activation_ts,
             "caught": r.caught_ts,
             "active_days": round(((r.caught_ts or window_end) - r.activation_ts) / 86400, 1),
             "never_caught": r.caught_ts is None}
            for r in rings
        ],
        "label_note": ("card testing bursts present, labels in the sealed store only"
                       if bursts else "legitimate traffic only, every sealed label is 0"),
        "bursts": [
            {"burst_id": b.burst_id, "start": b.start, "end": b.end,
             "minutes": (b.end - b.start) // 60, "rate_per_min": round(b.rate_per_min, 1),
             "n_iins": len(b.iins), "n_devices": len(b.device_ids),
             "ending": b.ending, "envelope": round(b.envelope, 3)}
            for b in bursts
        ],
        "cut_fields_not_generated": CUT_FIELDS,
        "flash_sales": flash_sales,
        "downtimes": downtimes,
        "population_diagnostics": diag,
        "spec_conflicts": C.SPEC_CONFLICTS,
    }
    return rows, sealed, manifest


def main():
    ap = argparse.ArgumentParser(description="Legitimate traffic generator")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--actors", type=int, default=4000)
    ap.add_argument("--out", default="data/sample")
    args = ap.parse_args()

    rows, sealed, manifest = generate(args.seed, args.days, args.actors)

    os.makedirs(args.out, exist_ok=True)
    write_stream(os.path.join(args.out, "events.jsonl"), rows)
    write_stream(os.path.join(args.out, "sealed.jsonl"), sealed)
    write_manifest(os.path.join(args.out, "manifest.json"), manifest)

    print(f"wrote {len(rows)} events to {args.out}")


if __name__ == "__main__":
    main()
