"""Actors act, and rows are the consequence (spec 1.1).

A session is one checkout visit. It produces a first attempt and, if that fails,
possibly a retry on the same order_id with attempt_seq incremented. Nothing here
reads a label, because there is no label: this module only produces legitimate
traffic and the sealed store records everything as label 0.
"""

import math

from . import config as C
from .timeline import downtime_multiplier, _ist


def draw_amount(rng) -> int:
    """Paise. Spec 1.4: 10% micro shoulder, 30% round price points, rest lognormal."""
    u = rng.random()
    if u < C.AMOUNT_MICRO_SHARE:
        # A third of the micro shoulder sits below 50 rupees.
        if rng.random() < C.AMOUNT_MICRO_SUB50_FRACTION:
            return rng.randint(100, 4999)        # 1 to 49.99 rupees
        return rng.randint(5000, 9999)           # 50 to 99.99 rupees
    if u < C.AMOUNT_MICRO_SHARE + C.AMOUNT_ROUND_SHARE:
        return rng.choice(C.AMOUNT_ROUND_POINTS)
    mu = math.log(C.AMOUNT_LOGNORM_MEDIAN)
    v = int(math.exp(rng.gauss(mu, C.AMOUNT_LOGNORM_SIGMA)))
    return max(10000, min(v, C.AMOUNT_MAX))


def draw_method(rng) -> str:
    x = rng.random()
    upto = 0.0
    for m, w in C.METHOD_MIX.items():
        upto += w
        if x <= upto:
            return m
    return "upi"


def draw_checkout_ms(rng, actor_class: str) -> int:
    median, sigma = C.CHECKOUT_MS[actor_class]
    v = int(math.exp(rng.gauss(math.log(median), sigma)))
    return max(120, min(v, 600000))


def decline_probability(method, international, ts, tier, downtimes):
    """Compose the decline probability for one attempt.

    Base is the cited per-method rate. Then three multipliers: evening bank load,
    geography, and any active downtime window. The evening coupling is the point
    of spec 1.2, the busiest hour is also the hour with the most legitimate
    declines, so volume alone cannot separate attacks later.
    """
    if method == "card" and international:
        p = C.METHOD_DECLINE["card_intl"]
    else:
        p = C.METHOD_DECLINE[method]

    hour = _ist(ts).hour
    if hour in C.EVENING_HOURS:
        p *= C.EVENING_DECLINE_MULT

    p *= C.TIER_DECLINE_MULT[tier]

    # Undo the double-count: see config.DECLINE_NORMALISER.
    p /= C.DECLINE_NORMALISER

    # Downtime is a genuine excursion above the blended baseline, so it is applied
    # after normalisation rather than folded into it.
    mult, active = downtime_multiplier(downtimes, ts, method)
    p *= mult

    return min(p, 0.95), active


def draw_decline_reason(rng, downtime_active: bool):
    """Reason mix from spec 1.5. During a downtime window the mix shifts toward
    gateway errors, which is what the probe's real downtime records look like."""
    if downtime_active and rng.random() < 0.75:
        return ("gateway_timeout", "GATEWAY_ERROR", "gateway", "payment_authorization")
    x = rng.random()
    upto = 0.0
    for reason, w, code, source, step in C.DECLINE_REASONS:
        upto += w
        if x <= upto:
            return (reason, code, source, step)
    r = C.DECLINE_REASONS[-1]
    return (r[0], r[2], r[3], r[4])


def sessions_for_actor(rng, actor, timeline, window_days):
    """How many checkout sessions this actor starts, and when.

    Sessions before the actor's signup are impossible, so they are dropped rather
    than clamped. That is why actors who sign up mid-window contribute fewer
    events, which is realistic and keeps account_age_days honest.
    """
    expected = actor.monthly_rate * (window_days / 30.0)
    if actor.actor_class == "new":
        expected = max(expected, 1.0)

    # Poisson draw by Knuth's method, using only the shared rng for determinism.
    n = 0
    if expected > 0:
        lim = math.exp(-expected)
        p = 1.0
        while True:
            p *= rng.random()
            if p <= lim:
                break
            n += 1

    out = []
    # An actor who signs up inside the window almost always buys something soon
    # after, so their first session sits near signup rather than uniformly across
    # the window. This is what makes young-account attempts a consequence of
    # behaviour rather than a quota filled in afterwards.
    if actor.signup_ts >= timeline.window_start and n > 0:
        first = actor.signup_ts + rng.randint(60, 5 * 86400)
        if first < timeline.window_end:
            out.append(first)
            n -= 1
    for _ in range(max(n, 0)):
        ts = timeline.sample_ts(rng)
        if ts < actor.signup_ts:
            continue
        out.append(ts)
    return sorted(out)


def attempts_for_session(rng, actor, ts, downtimes):
    """One session becomes one or more attempts.

    Returns a list of dicts describing attempts. Retries share the order_id and
    increment attempt_seq (spec 1.5), which is why attempt_seq > 1 cannot be a
    fraud signal on its own.
    """
    method = draw_method(rng)
    international = (method == "card" and rng.random() < C.CARD_INTERNATIONAL_SHARE)
    amount = draw_amount(rng)
    card = rng.choice(actor.cards) if method in ("card", "emi") else None

    attempts = []
    seq = 1
    cur_ts = ts
    while seq <= C.MAX_ATTEMPTS_PER_SESSION:
        p, dt_active = decline_probability(method, international, cur_ts,
                                           actor.tier, downtimes)
        failed = rng.random() < p
        reason = draw_decline_reason(rng, dt_active) if failed else None
        attempts.append({
            "ts": cur_ts,
            "method": method,
            "international": international,
            "amount": amount,
            "card": card,
            "failed": failed,
            "reason": reason,
            "attempt_seq": seq,
            "checkout_ms": draw_checkout_ms(rng, actor.actor_class),
            "downtime_active": dt_active,
        })
        if not failed:
            break
        if rng.random() >= C.RETRY_PROB:
            break
        cur_ts += rng.randint(*C.RETRY_DELAY_S)
        seq += 1
    return attempts
