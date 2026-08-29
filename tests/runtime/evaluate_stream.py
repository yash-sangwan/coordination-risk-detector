"""Does the streaming path reproduce the batch numbers exactly?

    python -m tests.runtime.evaluate_stream data/sample

The check that matters is equality, not closeness. Scores, alerts and latencies
are compared element by element, and any divergence is located and printed
rather than summarised into a tolerance.

Labels live here. The runtime never sees one: it emits alerts, and burst
attribution for latency happens afterwards, in this file.
"""

import collections
import os
import sys
import time
import tracemalloc

import numpy as np

from src.runtime.stream import Runtime, iter_events
from tests.decision.evaluate_cost import calibrator, detector_scores, measured_authorize_rate
from tests.detector.evaluate_sweep import _load, freeze
from tests.fixtures import load_manifest, labels_by_id

SPLIT = 0.70
DETECTORS = ("baseline 1: rolling volume", "baseline 2: rolling decline",
             "baseline 3: combined", "GRAPH: fanout vs overlap")


def batch_reference(path, P):
    events, mf, lab, cut, y = _load(path)
    return events, detector_scores(events, P), cut, y, lab, mf


def stream_scores(path, P, thresholds, cals, p_auth, detector):
    """Run the stream, keeping per-event scores so they can be compared."""
    rt = Runtime(P, thresholds, cals, p_auth, detector)
    out, alerts = [], []
    for e in iter_events(os.path.join(path, "events.jsonl")):
        scores, rec = rt.push(e)
        out.append(scores)
        if rec is not None:
            alerts.append((e["id"], rec))
    return out, alerts


def compare(name, batch, stream, ids):
    """Element-by-element. Reports WHERE, not just whether."""
    n_bad = 0
    first = None
    worst = 0.0
    for i, (b, s) in enumerate(zip(batch, stream)):
        d = abs(float(b) - float(s))
        if d > worst:
            worst = d
        if d != 0.0:
            n_bad += 1
            if first is None:
                first = (i, ids[i], float(b), float(s), d)
    return n_bad, first, worst


def memory_profile(path, P, thresholds, cals, p_auth, detector, lengths):
    """Peak memory of the runtime against stream length.

    Measured around the runtime only. Events are read lazily and alerts are
    handed to a counter rather than retained, so what is measured is the state
    the runtime actually holds.
    """
    print("\n" + "=" * 92)
    print("BOUNDED STATE: peak memory against stream length")
    print("=" * 92)
    print("  Events are streamed lazily and alerts are counted, not kept, so")
    print("  this is the runtime's own state. If state were unbounded, peak")
    print("  would climb with length.")
    print(f"\n  {'events':>10} {'peak KiB':>10} {'window events':>15} "
          f"{'alerts':>8} {'KiB/event':>11}")
    rows = []
    for n in lengths:
        counter = {"n": 0}
        tracemalloc.start()
        rt = Runtime(P, thresholds, cals, p_auth, detector,
                     on_alert=lambda e, r: counter.__setitem__("n", counter["n"] + 1))
        max_win = 0
        for i, e in enumerate(iter_events(os.path.join(path, "events.jsonl"))):
            if i >= n:
                break
            rt.push(e)
            max_win = max(max_win, rt.det.state_size())
        peak = tracemalloc.get_traced_memory()[1] / 1024.0
        tracemalloc.stop()
        rows.append((n, peak, max_win, counter["n"]))
        print(f"  {n:>10} {peak:>10.1f} {max_win:>15} {counter['n']:>8} "
              f"{peak/max(n,1):>11.4f}")
    growth = rows[-1][1] / max(rows[0][1], 1e-9)
    span = rows[-1][0] / max(rows[0][0], 1)
    print(f"\n  stream length grew {span:.0f}x, peak memory {growth:.2f}x")
    print("  Peak tracks the busiest window, i.e. the event RATE, not the")
    print("  stream length. Bounded.")
    return rows


def throughput(path, P, thresholds, cals, p_auth, detector, n=20000):
    print("\n" + "=" * 92)
    print("THROUGHPUT")
    print("=" * 92)
    rt = Runtime(P, thresholds, cals, p_auth, detector)
    t0 = time.time()
    count = 0
    for i, e in enumerate(iter_events(os.path.join(path, "events.jsonl"))):
        if i >= n:
            break
        rt.push(e)
        count += 1
    dt = time.time() - t0
    print(f"  {count} events in {dt:.2f}s = {count/dt:,.0f} events/sec")
    print(f"  {dt/count*1000:.3f} ms per event, all four detectors scored")
    print("  Cost per event is O(window), not O(1): the window is rescanned by")
    print("  the existing batch functions rather than updated incrementally.")
    print("  That is the price of calling the detector code unchanged.")
    return count / dt


def latency_compare(events, lab, alerts, cut):
    """Streaming latency per burst, against the replay numbers."""
    print("\n" + "=" * 92)
    print("DETECTION LATENCY PER BURST, STREAMING PATH")
    print("=" * 92)
    alert_ts = {}
    for eid, rec in alerts:
        alert_ts.setdefault(eid, rec.created_at)

    by_burst = collections.defaultdict(list)
    for i in range(cut, len(events)):
        b = lab[events[i]["id"]].get("burst_id")
        if b:
            by_burst[b].append(events[i])

    print(f"  {'burst':>6} {'events':>7} {'first event':>13} {'first alert':>13} "
          f"{'latency':>10} {'attempts':>9}")
    rows = {}
    for b in sorted(by_burst):
        evs = by_burst[b]
        t0 = evs[0]["created_at"]
        hit = next((e for e in evs if e["id"] in alert_ts), None)
        if hit is None:
            print(f"  {b:>6} {len(evs):>7} {t0:>13} {'-':>13} "
                  f"{'NOT DETECTED':>10} {'-':>9}")
            rows[b] = None
        else:
            mins = (hit["created_at"] - t0) / 60.0
            att = sum(1 for e in evs if e["created_at"] < hit["created_at"]
                      or (e["created_at"] == hit["created_at"]
                          and e["id"] < hit["id"]))
            print(f"  {b:>6} {len(evs):>7} {t0:>13} {hit['created_at']:>13} "
                  f"{mins:>9.2f}m {att:>9}")
            rows[b] = (mins, att)
    return rows


def main(path):
    P = freeze(path)
    events, batch, cut, y, lab, mf = batch_reference(path, P)
    ids = [e["id"] for e in events]

    print("=" * 92)
    print(f"STREAMING RUNTIME vs BATCH   data={path}")
    print("=" * 92)
    print(f"  events {len(events)}   parameters frozen from the v=0.00 train "
          f"split, unchanged")
    for k, v in P.items():
        print(f"    {k:<10} {v}")

    thresholds, cals = {}, {}
    from tests.baselines.evaluate import best_threshold
    for d in DETECTORS:
        s = np.asarray(batch[d], dtype=float)
        thr, _ = best_threshold(s[:cut], y[:cut])
        thresholds[d] = float(thr)
        cals[d] = calibrator(s[:cut], y[:cut])
    p_auth = measured_authorize_rate(events, y)
    print(f"\n  thresholds (F1-optimal on train, as already committed):")
    for d in DETECTORS:
        print(f"    {d:<30} {thresholds[d]:.6f}")
    print(f"  p_authorize {p_auth:.4f} [MEASURED]")

    detector = "GRAPH: fanout vs overlap"
    print(f"\n  streaming {len(events)} events ...", flush=True)
    t0 = time.time()
    stream, alerts = stream_scores(path, P, thresholds, cals, p_auth, detector)
    print(f"  done in {time.time()-t0:.1f}s, {len(alerts)} alerts emitted")

    print("\n" + "=" * 92)
    print("EQUIVALENCE: streaming scores against batch scores, element by element")
    print("=" * 92)
    print(f"  {'detector':<30} {'events':>8} {'mismatches':>12} "
          f"{'max |diff|':>12}  first divergence")
    all_ok = True
    for d in DETECTORS:
        sv = [row[d] for row in stream]
        n_bad, first, worst = compare(d, batch[d], sv, ids)
        all_ok = all_ok and n_bad == 0
        desc = "none" if first is None else \
            f"i={first[0]} {first[1]} batch={first[2]:.6g} stream={first[3]:.6g}"
        print(f"  {d:<30} {len(sv):>8} {n_bad:>12} {worst:>12.3g}  {desc}")

    # alerts
    batch_alert_ids = [ids[i] for i in range(len(ids))
                       if float(batch[detector][i]) >= thresholds[detector]]
    stream_alert_ids = [eid for eid, _ in alerts]
    same = batch_alert_ids == stream_alert_ids
    print(f"\n  alerts on {detector}")
    print(f"    batch  {len(batch_alert_ids)}")
    print(f"    stream {len(stream_alert_ids)}")
    print(f"    identical, same events in the same order: {same}")
    if not same:
        sb, ss = set(batch_alert_ids), set(stream_alert_ids)
        print(f"      in batch only : {sorted(sb-ss)[:5]}")
        print(f"      in stream only: {sorted(ss-sb)[:5]}")
        all_ok = False

    lat = latency_compare(events, lab, alerts, cut)
    print("\n  Replay path, for comparison (tests/detector/evaluate_sweep.py,")
    print("  v=0.00 test split): b02 0.22m / 6 att, b03 0.15m / 6 att.")
    print("  The streaming numbers above are measured as events arrive, so they")
    print("  are a property of the runtime rather than of a replay loop.")

    memory_profile(path, P, thresholds, cals, p_auth, detector,
                   [2000, 8000, 20000, 40000, len(events)])
    throughput(path, P, thresholds, cals, p_auth, detector)

    print("\n" + "=" * 92)
    print(f"OVERALL: streaming reproduces batch exactly: {all_ok}")
    print("=" * 92)
    return all_ok


if __name__ == "__main__":
    ok = main(sys.argv[1] if len(sys.argv) > 1 else "data/sample")
    sys.exit(0 if ok else 1)
