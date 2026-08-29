"""Baseline detectors. These are the bar a real detector has to clear.

They are deliberately built to be good rather than to lose. Each is a standard
operational rule that a competent payments team would actually deploy, and each
is tuned on the train split by the harness before being scored once on test.

Everything here reads the event stream and nothing else. No labels, no answer
key, no ground truth of any kind. Thresholds arrive as arguments; choosing them
is the harness's job, because choosing them requires labels.
"""

import bisect
import collections


# --------------------------------------------------------------------------
# shared windowing
# --------------------------------------------------------------------------

def _window_bounds(ts, window_s):
    """For each event, the index range of the preceding `window_s` seconds.

    Trailing window only. A detector running live cannot see the future, so a
    centred window would flatter these baselines with information they would not
    have had.
    """
    lo = [bisect.bisect_left(ts, t - window_s) for t in ts]
    return lo


def rolling_counts(events, window_s):
    """Attempts in the trailing window, per event."""
    ts = [e["created_at"] for e in events]
    lo = _window_bounds(ts, window_s)
    return [i - lo[i] + 1 for i in range(len(ts))]


def rolling_decline(events, window_s):
    """(decline rate, n) in the trailing window, per event."""
    ts = [e["created_at"] for e in events]
    failed = [1 if e["status"] == "failed" else 0 for e in events]
    cum = [0]
    for f in failed:
        cum.append(cum[-1] + f)
    lo = _window_bounds(ts, window_s)
    out = []
    for i in range(len(ts)):
        n = i - lo[i] + 1
        s = cum[i + 1] - cum[lo[i]]
        out.append((s / n if n else 0.0, n))
    return out


# --------------------------------------------------------------------------
# baseline 1: rolling volume
# --------------------------------------------------------------------------

def score_volume(events, window_s):
    """Continuous score: attempts per minute in the trailing window."""
    counts = rolling_counts(events, window_s)
    per_min = window_s / 60.0
    return [c / per_min for c in counts]


# --------------------------------------------------------------------------
# baseline 2: rolling decline rate
# --------------------------------------------------------------------------

def score_decline(events, window_s, min_events):
    """Continuous score: trailing decline rate, zero until the window has enough
    events to be meaningful. Without the floor a single failed attempt at 03:00
    reads as a 100% decline rate and the baseline drowns in noise."""
    return [(r if n >= min_events else 0.0)
            for r, n in rolling_decline(events, window_s)]


# --------------------------------------------------------------------------
# baseline 3: combined
# --------------------------------------------------------------------------

def score_combined(events, window_s, min_events, vol_ref, dec_ref):
    """Both conditions, as the weaker of the two normalised signals.

    Scored as min(volume / vol_ref, decline / dec_ref) so a burst has to be both
    busy and failing. Taking the minimum rather than a product keeps the score on
    a scale where 1.0 means "both references met", which is what the alert
    threshold is defined against.
    """
    v = score_volume(events, window_s)
    d = score_decline(events, window_s, min_events)
    return [min(vi / vol_ref, di / dec_ref) for vi, di in zip(v, d)]


# --------------------------------------------------------------------------
# ring baseline: account level
# --------------------------------------------------------------------------

def pincode_cluster_sizes(events):
    """Per account, the largest number of OTHER accounts sharing one of its
    shipping pincodes. Accounts, not events, because a ring is a group of
    accounts and counting events would just rank busy accounts."""
    pin_accounts = collections.defaultdict(set)
    acct_pins = collections.defaultdict(set)
    for e in events:
        mc = e["merchant_context"]
        a, p = mc["account_id"], mc["shipping_pincode"]
        if not a or not p:
            continue
        pin_accounts[p].add(a)
        acct_pins[a].add(p)
    out = {}
    for a, pins in acct_pins.items():
        out[a] = max((len(pin_accounts[p]) - 1) for p in pins)
    return out


def score_pincode_sharing(events):
    """Continuous account-level score: peers on the busiest shared pincode."""
    return pincode_cluster_sizes(events)
