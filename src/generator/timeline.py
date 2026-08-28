"""Arrival intensity and scheduled disruptions (spec 1.2 and 1.5).

Builds an hourly intensity profile over the window from four multiplicative
components: time of day, day of week, payday, and flash sales. Sessions are then
drawn from that profile, so the arrival shape is a property of the calendar
rather than something stamped onto rows.

Downtime windows live here too. They raise the decline rate for one method with
no attack present, which is the confounder a burst detector has to survive.
"""

import bisect
import datetime as dt

from . import config as C

IST_OFFSET = 19800  # +05:30, seconds


def _ist(ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts + IST_OFFSET, dt.timezone.utc)


def schedule_flash_sales(rng, window_start: int, window_end: int):
    days = (window_end - window_start) / 86400
    months = max(days / 30.0, 0.0)
    lo, hi = C.FLASH_SALES_PER_MONTH
    n = int(round(rng.uniform(lo, hi) * months))
    sales = []
    for _ in range(max(n, 0)):
        # Flash sales are scheduled by a merchant, so they start on the hour and
        # favour the evening, when traffic is already highest.
        start = rng.randint(window_start, max(window_start, window_end - 3600))
        start -= start % 3600
        hour = _ist(start).hour
        if hour < 10 and rng.random() < 0.7:
            start += 10 * 3600
        dur = rng.randint(*C.FLASH_SALE_MINUTES) * 60
        sales.append({
            "start": start,
            "end": min(start + dur, window_end),
            "multiplier": rng.uniform(*C.FLASH_SALE_MULT),
        })
    return sorted(sales, key=lambda s: s["start"])


def schedule_downtimes(rng, window_start: int, window_end: int):
    days = (window_end - window_start) / 86400
    months = max(days / 30.0, 0.0)
    lo, hi = C.DOWNTIME_PER_MONTH
    n = int(round(rng.uniform(lo, hi) * months))
    out = []
    for _ in range(max(n, 0)):
        # Bank outages are largely load-induced, so they cluster where the traffic
        # is rather than uniformly across the clock. Drawing the start hour flat
        # put windows at 04:00 IST, where they touch almost no events and the
        # confounder may as well not exist.
        day = rng.randint(0, max(int((window_end - window_start) // 86400) - 1, 0))
        x = rng.random() * sum(C.HOURLY_WEIGHTS)
        upto = 0.0
        hour = 20
        for h, w in enumerate(C.HOURLY_WEIGHTS):
            upto += w
            if x <= upto:
                hour = h
                break
        # window_start is midnight UTC-ish; shift into IST hours
        start = window_start + day * 86400 + (hour * 3600 - IST_OFFSET) % 86400
        start = min(max(start, window_start), max(window_start, window_end - 3600))
        dur = rng.randint(*C.DOWNTIME_MINUTES) * 60
        # Weight by traffic share: a downtime on a method carrying 2% of volume
        # produces no observable confounder, which defeats the purpose.
        x = rng.random(); upto = 0.0; chosen = "upi"
        for m, w in C.METHOD_MIX.items():
            upto += w
            if x <= upto:
                chosen = m
                break
        out.append({
            "start": start,
            "end": min(start + dur, window_end),
            "method": chosen,
            "multiplier": rng.uniform(*C.DOWNTIME_DECLINE_MULT),
        })
    return sorted(out, key=lambda d: d["start"])


class Timeline:
    """Baseline hourly demand profile with an inverse-CDF sampler.

    Flash sales are deliberately NOT in these weights. Folding a multiplier into
    the profile only redistributes a session count that is fixed by the actor
    population, so a sale concentrated existing demand instead of creating extra
    and never reached its stated multiplier. Sales are additive now, see
    flash_sale_extra_sessions below.
    """

    def __init__(self, window_start: int, window_end: int, flash_sales):
        self.window_start = window_start
        self.window_end = window_end
        self.flash_sales = flash_sales
        self.n_hours = int((window_end - window_start) // 3600)

        self.weights = []
        for h in range(self.n_hours):
            hour_start = window_start + h * 3600
            local = _ist(hour_start)
            w = C.HOURLY_WEIGHTS[local.hour]
            w *= C.DOW_WEIGHTS[local.weekday()]
            if local.day in C.PAYDAY_DAYS:
                w *= C.PAYDAY_MULT
            self.weights.append(w)

        self.total = sum(self.weights)
        self.cum = []
        acc = 0.0
        for w in self.weights:
            acc += w
            self.cum.append(acc)

    def sample_ts(self, rng) -> int:
        x = rng.random() * self.total
        h = bisect.bisect_left(self.cum, x)
        h = min(h, self.n_hours - 1)
        return self.window_start + h * 3600 + rng.randint(0, 3599)

    def in_flash_sale(self, ts: int) -> bool:
        return any(s["start"] <= ts < s["end"] for s in self.flash_sales)

    def flash_multiplier(self, ts: int) -> float:
        for s in self.flash_sales:
            if s["start"] <= ts < s["end"]:
                return s["multiplier"]
        return 1.0


def flash_sale_extra_sessions(rng, flash_sales, base_sessions, actors):
    """Sessions a sale ADDS on top of baseline demand.

    A sale creates incremental purchases; it does not merely move existing ones
    around. The extra count is measured against the baseline that actually landed
    in the window, so a stated multiplier of Nx really produces N times the
    traffic rather than N times the share of a fixed total.

    Buyers are drawn from the same actor population, weighted by how often they
    normally buy, so a sale brings in real customers with their real devices,
    instruments, pincodes and phone numbers. Volume is the only thing that
    changes: what the events SHARE is untouched, which is the whole point of
    keeping this confounder honest.
    """
    if not flash_sales:
        return []

    by_ts = sorted(t for t, _ in base_sessions)
    out = []
    for s in flash_sales:
        lo = bisect.bisect_left(by_ts, s["start"])
        hi = bisect.bisect_left(by_ts, s["end"])
        baseline_n = hi - lo
        if baseline_n <= 0:
            continue
        extra_n = int(round(baseline_n * (s["multiplier"] - 1.0)))

        # Only actors who already exist can shop in the sale.
        eligible = [a for a in actors if a.signup_ts <= s["start"]]
        if not eligible:
            continue
        weights = [max(a.monthly_rate, 0.05) for a in eligible]
        total_w = sum(weights)

        for _ in range(extra_n):
            x = rng.random() * total_w
            upto = 0.0
            pick = eligible[-1]
            for a, w in zip(eligible, weights):
                upto += w
                if x <= upto:
                    pick = a
                    break
            ts = rng.randint(s["start"], max(s["start"], s["end"] - 1))
            out.append((ts, pick))
    return out


def downtime_multiplier(downtimes, ts: int, method: str):
    """Returns (multiplier, active) for a method at a timestamp."""
    for d in downtimes:
        if d["start"] <= ts < d["end"] and d["method"] == method:
            return d["multiplier"], True
    return 1.0, False
