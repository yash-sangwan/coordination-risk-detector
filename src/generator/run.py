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
from .behaviour import attempts_for_session, sessions_for_actor
from .timeline import Timeline, schedule_downtimes, schedule_flash_sales

WALLETS = ["paytm", "phonepe", "amazonpay", "mobikwik", "freecharge"]

# Fixed window anchor so output does not depend on wall-clock time.
DEFAULT_WINDOW_END = 1787000000


def generate(seed: int, days: int, n_actors: int):
    master = random.Random(seed)
    rng_pop = random.Random(master.getrandbits(64))
    rng_cal = random.Random(master.getrandbits(64))
    rng_beh = random.Random(master.getrandbits(64))

    window_end = DEFAULT_WINDOW_END
    window_start = window_end - days * 86400

    actors, diag = build_population(rng_pop, n_actors, window_start, window_end)

    flash_sales = schedule_flash_sales(rng_cal, window_start, window_end)
    downtimes = schedule_downtimes(rng_cal, window_start, window_end)
    timeline = Timeline(window_start, window_end, flash_sales)

    # Actors act. Collect (ts, actor, attempt) then sort by time, so the stream is
    # time-ordered and no population occupies a contiguous block.
    pending = []
    for actor in actors:
        for ts in sessions_for_actor(rng_beh, actor, timeline, days):
            attempts = attempts_for_session(rng_beh, actor, ts, downtimes)
            wallet = rng_beh.choice(WALLETS)
            sid = f"sess_{rng_beh.getrandbits(48):012x}"
            for a in attempts:
                a["session_id"] = sid
                a["wallet"] = wallet
                pending.append((a["ts"], actor, a))

    pending.sort(key=lambda t: (t[0], t[1].actor_id, t[2]["attempt_seq"]))

    minter = IdMinter()

    # Account ids are minted from the same monotonic sequence, at first sighting.
    account_ids = {}
    for ts, actor, _ in pending:
        if actor.actor_id not in account_ids:
            account_ids[actor.actor_id] = minter.mint("acct", ts)
    for actor in actors:
        actor.account_id = account_ids.get(actor.actor_id, "")

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
        sealed.append(sealed_record(row, actor, a, timeline.in_flash_sale(ts)))

    manifest = {
        "seed": seed,
        "days": days,
        "n_actors": n_actors,
        "window_start": window_start,
        "window_end": window_end,
        "n_events": len(rows),
        "contains_attacks": False,
        "label_note": "legitimate traffic only, every sealed label is 0",
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
