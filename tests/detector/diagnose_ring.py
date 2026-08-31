"""Is the ring detector's precision real, or is the conjunction a planted label?

    python -m tests.detector.diagnose_ring data/sample

Written 2026-08-30, when the detector scored 0.9444 precision against what was
then believed to be an oracle ceiling of 0.4400. A detector beating an informed
oracle by that margin is a claim to disbelieve first and verify second, so this
checks the obvious explanation: that "shares a pincode AND shares a device" has
almost no benign occurrence in the generated population, which would make it
close to a pure label rather than a hard signal.

It did. And the same defect was inflating the oracle: that 0.4400 was the oracle
reading the identical artefact, and it has since been withdrawn. Both numbers in
the paragraph above are historical. See docs/report/what-broke.md, 2026-08-30.

Measures only. Changes nothing.
"""

import collections
import sys

from src.detector.ring import account_attributes, conjunction_components, _invert
from tests.detector.evaluate_ring import SPLIT, ring_accounts
from tests.fixtures import load_events, load_manifest, labels_by_id


def main(path):
    events = load_events(path)
    manifest = load_manifest(path)
    lab = labels_by_id(path)
    cut = int(len(events) * SPLIT)

    print("=" * 88)
    print(f"IS THE CONJUNCTION A SIGNAL OR A PLANTED LABEL?   data={path}")
    print("=" * 88)

    for name, sub in (("train", events[:cut]), ("test", events[cut:]),
                      ("whole window", events)):
        pins, devs, cons, _ = account_attributes(sub)
        comps, by_pin = conjunction_components(pins, devs)
        pos = ring_accounts(sub, lab)
        pure_ring = sum(1 for c in comps if c <= pos)
        pure_benign = sum(1 for c in comps if not (c & pos))
        mixed = len(comps) - pure_ring - pure_benign
        n_acct = len(devs)
        print(f"\n  {name}: {n_acct} accounts, {len(pos)} of them ring members")
        print(f"    conjunction components (share a pincode AND a device): {len(comps)}")
        print(f"      all-ring   {pure_ring}")
        print(f"      all-benign {pure_benign}")
        print(f"      mixed      {mixed}")
        if comps:
            sizes = sorted(len(c) for c in comps)
            print(f"      sizes: {sizes}")

    # ---- why: do benign device-sharers share a pincode? ----
    print("\n" + "=" * 88)
    print("WHY: DO HOUSEHOLDS SHARE AN ADDRESS?")
    print("=" * 88)
    pins, devs, cons, _ = account_attributes(events)
    pos = ring_accounts(events, lab)
    by_dev = _invert(devs)

    shared_dev_groups = [(d, a) for d, a in by_dev.items() if len(a) > 1]
    benign_groups = [(d, a) for d, a in shared_dev_groups if not (a & pos)]
    both = 0
    for d, accounts in benign_groups:
        seen = collections.Counter()
        for a in accounts:
            for p in pins.get(a, ()):
                seen[p] += 1
        if seen and max(seen.values()) > 1:
            both += 1

    print(f"  accounts observed in the window            : {len(devs)}")
    print(f"  device groups with more than one account   : {len(shared_dev_groups)}")
    print(f"  of which contain no ring member            : {len(benign_groups)}")
    print(f"  of THOSE, groups also sharing a pincode    : {both}"
          f"  ({both/max(len(benign_groups),1)*100:.2f}%)")
    print(f"  households formed at generation time       : "
          f"{manifest['population_diagnostics'].get('households_formed')}")

    print("\n  In src/generator/population.py the household loop copies ONLY the")
    print("  device id between members; each actor keeps the pincode it drew")
    print("  independently from the weighted table. So two people modelled as")
    print("  living in one household live at two different postcodes.")
    print("  A real household shares its address by definition.")

    # ---- what it would look like if households shared an address ----
    print("\n" + "=" * 88)
    print("COUNTERFACTUAL: IF HOUSEHOLDS SHARED A PINCODE")
    print("=" * 88)
    obs_households = len(benign_groups)
    ring_comps = [c for c in conjunction_components(pins, devs)[0] if c & pos]
    n_ring_comp = len(ring_comps)
    print(f"  benign conjunction components today          : {both}")
    print(f"  benign conjunction components if households")
    print(f"    shared an address (one per observed group)  : ~{obs_households}")
    print(f"  ring conjunction components                  : {n_ring_comp}")
    if obs_households:
        print(f"  implied precision ceiling for stage 1        : "
              f"~{n_ring_comp/(n_ring_comp+obs_households):.4f}")
    print("\n  Today stage 1 has essentially no benign population to compete with,")
    print("  so its precision is a property of the generator, not of the detector.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/sample")
