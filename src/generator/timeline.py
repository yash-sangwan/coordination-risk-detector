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
    """Hourly intensity profile with an inverse-CDF sampler."""

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
            for s in flash_sales:
                if s["start"] <= hour_start < s["end"]:
                    w *= s["multiplier"]
                    break
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


def downtime_multiplier(downtimes, ts: int, method: str):
    """Returns (multiplier, active) for a method at a timestamp."""
    for d in downtimes:
        if d["start"] <= ts < d["end"] and d["method"] == method:
            return d["multiplier"], True
    return 1.0, False
