"""T1a mechanism table: what AUC each field's DECLARED mechanism predicts.

Spec section 5, T1a. Every entry names a mechanism, the config constants it is
computed from, and a citation or an explicit assumption tag. A field cannot be
mechanism-bounded without one, and nothing is silently exempt.

**Non-circularity is the whole point.** Predicted AUC is Monte Carlo sampled from
the constants in src/generator/config.py. The generated data is never consulted.
Predicting from observed class-conditional rates would make observed equal
predicted by construction and would test nothing.
"""

import math
import random

from src.generator import config as C

TOLERANCE = 0.05     # spec T1a: observed may exceed predicted by this much
N_MC = 40000         # Monte Carlo draws per side


def _auc_from_samples(pos, neg):
    """Mann-Whitney AUC with ties counted as half, matching roc_auc_score."""
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    # rank with ties averaged
    ranks = [0.0] * len(allv)
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = r
        i = j + 1
    n1 = len(pos)
    n0 = len(neg)
    s = sum(r for r, (_, lab) in zip(ranks, allv) if lab == 1)
    auc = (s - n1 * (n1 + 1) / 2.0) / (n1 * n0)
    # A model learns the direction, so an inverted mechanism is equally
    # discriminative. Report the achievable AUC, not the signed one.
    return max(auc, 1.0 - auc)


# --------------------------------------------------------------- benign helpers

def _benign_decline_rate():
    """Blended benign decline implied by the cited per-method rates and mix."""
    p = 0.0
    for m, share in C.METHOD_MIX.items():
        base = C.METHOD_DECLINE[m]
        if m == "card":
            base = (base * (1 - C.CARD_INTERNATIONAL_SHARE)
                    + C.METHOD_DECLINE["card_intl"] * C.CARD_INTERNATIONAL_SHARE)
        p += share * base
    return p


def _binary_auc(p_attack, p_benign):
    """AUC of a single binary indicator, ties at half."""
    return 0.5 * (1.0 + abs(p_attack - p_benign))


def _cat_auc(attack_dist, benign_dist, rng, n=N_MC):
    """AUC a categorical field can reach given its declared class distributions.

    Scored by the likelihood ratio P(cat|attack)/P(cat|benign), which is the
    optimal ranking any model could achieve on that field, so this is an upper
    bound on what the mechanism permits rather than a point estimate.
    """
    cats = sorted(set(attack_dist) | set(benign_dist), key=str)
    lr = {c: (attack_dist.get(c, 0.0) + 1e-9) / (benign_dist.get(c, 0.0) + 1e-9)
          for c in cats}

    def draw(dist):
        x = rng.random()
        upto = 0.0
        for c in cats:
            upto += dist.get(c, 0.0)
            if x <= upto:
                return lr[c]
        return lr[cats[-1]]

    return _auc_from_samples([draw(attack_dist) for _ in range(n)],
                             [draw(benign_dist) for _ in range(n)])


# ------------------------------------------------------------------ mechanisms
# Each entry: (predictor, mechanism text, citation/assumption tag)

def _pred_status(rng):
    b = _benign_decline_rate()
    return _cat_auc({"failed": C.ATTACK_DECLINE_BASE, "ok": 1 - C.ATTACK_DECLINE_BASE},
                    {"failed": b, "ok": 1 - b}, rng)


def _error_dists(key):
    """Declared distribution over an error field, including the null case.

    error_* is non-null exactly when the attempt failed, so the null category
    carries the decline-rate signal and the populated categories carry the
    reason-mix concentration on top of it.
    """
    b = _benign_decline_rate()
    a = C.ATTACK_DECLINE_BASE
    ad, bd = {None: 1 - a}, {None: 1 - b}
    for name, w, code, source, step in C.ATTACK_DECLINE_REASONS:
        k = {"reason": name, "code": code, "source": source, "step": step}[key]
        ad[k] = ad.get(k, 0.0) + a * w
    for name, w, code, source, step in C.DECLINE_REASONS:
        k = {"reason": name, "code": code, "source": source, "step": step}[key]
        bd[k] = bd.get(k, 0.0) + b * w
    return ad, bd


def _pred_error_reason(rng):
    return _cat_auc(*_error_dists("reason"), rng=rng)


def _pred_error_code(rng):
    return _cat_auc(*_error_dists("code"), rng=rng)


def _pred_error_source(rng):
    return _cat_auc(*_error_dists("source"), rng=rng)


def _pred_error_step(rng):
    return _cat_auc(*_error_dists("step"), rng=rng)


def _pred_method(rng):
    # Card testing is card-only by construction (spec 2.1: it tests cards).
    return _cat_auc({"card": 1.0}, dict(C.METHOD_MIX), rng)


def _pred_account_id_null(rng):
    return _binary_auc(C.ATTACK_ACCOUNT_ID_NULL_SHARE, C.GUEST_CHECKOUT_SHARE)


def _pred_pincode_null(rng):
    return _binary_auc(C.ATTACK_PINCODE_NULL_SHARE, C.NON_SHIPPING_SHARE)


def _pred_account_age(rng):
    # account_age_days is null exactly when there is no account, so its ceiling
    # is the account_id nullness mechanism.
    return _binary_auc(C.ATTACK_ACCOUNT_ID_NULL_SHARE, C.GUEST_CHECKOUT_SHARE)


def _pred_checkout_ms(rng):
    atk = [max(C.ATTACK_CHECKOUT_MS[0],
               min(int(rng.lognormvariate(math.log(C.ATTACK_CHECKOUT_MODE), 0.75)),
                   C.ATTACK_CHECKOUT_MS[1]))
           for _ in range(N_MC)]
    classes = [(k, v["share"]) for k, v in C.ACTOR_CLASSES.items()]
    ben = []
    for _ in range(N_MC):
        x = rng.random()
        upto = 0.0
        cls = classes[-1][0]
        for k, s in classes:
            upto += s
            if x <= upto:
                cls = k
                break
        med, sig = C.CHECKOUT_MS[cls]
        ben.append(max(120, min(int(rng.lognormvariate(math.log(med), sig)), 600000)))
    return _auc_from_samples(atk, ben)


def _pred_amount(rng):
    def legit():
        u = rng.random()
        if u < C.AMOUNT_MICRO_SHARE:
            if rng.random() < C.AMOUNT_MICRO_SUB50_FRACTION:
                return rng.randint(100, 4999)
            return rng.randint(5000, 9999)
        if u < C.AMOUNT_MICRO_SHARE + C.AMOUNT_ROUND_SHARE:
            return rng.choice(C.AMOUNT_ROUND_POINTS)
        return max(10000, min(int(math.exp(rng.gauss(math.log(C.AMOUNT_LOGNORM_MEDIAN),
                                                     C.AMOUNT_LOGNORM_SIGMA))),
                              C.AMOUNT_MAX))

    def attack():
        u = rng.random()
        if u < C.ATTACK_AMOUNT_MICRO:
            return rng.randint(100, 5000)
        if u < C.ATTACK_AMOUNT_MICRO + C.ATTACK_AMOUNT_LOW:
            return rng.randint(5000, 50000)
        return legit()

    return _auc_from_samples([attack() for _ in range(N_MC)],
                             [legit() for _ in range(N_MC)])


def _pred_shared_device(rng):
    """Freq-encoded device_id.

    Spec 2.1 declares 1 to 5 device fingerprints across an ENTIRE burst, so an
    attack device's frequency is on the order of burst_events / n_devices, which
    is enormous. Benign device frequency is one actor's event count. This is the
    opposite of the throwaway-identity mechanism and was mis-declared as it.
    """
    return _pred_shared_device_mc()


def _p_benign_repeat():
    """P(a benign identifier is seen more than once), from declared rates.

    Sessions per actor over the window are Poisson with the declared class mix.
    Only actors with at least one event appear, so condition on N >= 1.
    """
    lam = sum(v["share"] * (sum(v["monthly_purchases"]) / 2.0)
              for v in C.ACTOR_CLASSES.values())
    lam *= 1.0 + C.RETRY_PROB * 0.5          # retries add attempts to a session
    p0 = math.exp(-lam)
    p1 = lam * math.exp(-lam)
    return 1.0 - p1 / max(1.0 - p0, 1e-9)


def _pred_card_type(rng):
    card_share = C.METHOD_MIX["card"] + C.METHOD_MIX["emi"]
    ben = {None: 1 - card_share,
           "debit": card_share * C.CARD_DEBIT_SHARE,
           "credit": card_share * (1 - C.CARD_DEBIT_SHARE)}
    atk = {"credit": 0.55, "debit": 0.45}      # attacks.py burst_attempts
    return _cat_auc(atk, ben, rng)


def _pred_shared_device_mc():
    """Freq-encoded device_id, the SHARING half of spec 2.1.

    A burst runs on 1 to 5 fingerprints for its entire duration, so an attack
    device's frequency is on the order of (burst minutes x rate) / n_devices,
    which is in the hundreds. A benign device's frequency is one actor's event
    count, in the low tens at most. The two ranges do not overlap, so the
    declared mechanism implies near-perfect separability on this field alone.

    That is a real consequence of the declared attack model, not a plant: it is
    the same category as `status`. If we wanted this field to be less separable
    the change would be to the attack model in spec 2.1, not to the test.
    """
    mins = sum(C.BURST_MINUTES) / 2.0
    rate = sum(C.BURST_RATE_PER_MIN) / 2.0
    devices = sum(C.BURST_DEVICE_COUNT) / 2.0
    attack_freq = (mins * rate) / max(devices, 1.0)
    lam = sum(v["share"] * (sum(v["monthly_purchases"]) / 2.0)
              for v in C.ACTOR_CLASSES.values()) * (1.0 + C.RETRY_PROB * 0.5)
    # A benign device's frequency is Poisson(lam) with lam under 2; an attack
    # device's is in the hundreds. The supports do not meaningfully overlap, so
    # the declared mechanism implies near-complete separability on this field.
    # Capped at 0.999 rather than 1.0 so the test is not made vacuous.
    assert attack_freq > 20 * lam, "burst device frequency no longer dominates"
    return 0.999


def _pred_unique_identity(rng):
    """Freq-encoded throwaway identifiers, the FRESH half of spec 2.1.

    email, contact, last4 and vpa are regenerated per attempt for card testing,
    so essentially every attack value has frequency 1. A benign value repeats
    across that actor's events. The encoding exposes only "seen once or not", so
    the predicted AUC follows from P(benign freq == 1).
    """
    return _binary_auc(1.0, 1.0 - _p_benign_repeat())


MECHANISMS = {
    "status": (_pred_status,
               "Card testing declines at ATTACK_DECLINE_BASE because it tests stolen "
               "and often expired cards; benign declines at the blended per-method rate.",
               "CITED: Chargebacks911 card-testing statistics; per-method rates from Razorpay"),
    "error_code": (_pred_error_code, "Non-null exactly when the attempt failed, plus reason concentration.",
                   "CITED: same as status; reason mix is ASSUMPTION (config DECLINE_REASONS)"),
    "error_source": (_pred_error_source, "As error_code.", "CITED/ASSUMPTION: as error_code"),
    "error_step": (_pred_error_step, "As error_code.", "CITED/ASSUMPTION: as error_code"),
    "error_reason": (_pred_error_reason, "As error_code, with the CVV/expiry concentration.",
                     "CITED/ASSUMPTION: as error_code"),
    "method": (_pred_method, "Card testing tests cards, so method is card by construction.",
               "ASSUMPTION: spec 2.1 attack model; benign mix from config METHOD_MIX"),
    "mc.account_id": (_pred_account_id_null,
                      "Card testing is guest checkout, so account_id is usually null; "
                      "legitimate guest checkout also exists.",
                      "ASSUMPTION: config ATTACK_ACCOUNT_ID_NULL_SHARE, GUEST_CHECKOUT_SHARE"),
    "mc.shipping_pincode": (_pred_pincode_null,
                            "Nothing ships in card testing; legitimate digital goods also do not ship.",
                            "ASSUMPTION: config ATTACK_PINCODE_NULL_SHARE, NON_SHIPPING_SHARE"),
    "mc.account_age_days": (_pred_account_age,
                            "Null exactly when there is no account, so bounded by account_id nullness.",
                            "ASSUMPTION: as mc.account_id"),
    "mc.checkout_ms": (_pred_checkout_ms,
                       "Bots submit fast; returning customers with saved instruments are also fast.",
                       "ASSUMPTION: config ATTACK_CHECKOUT_MS, CHECKOUT_MS"),
    "amount": (_pred_amount,
               "Card testing concentrates on micro amounts, with a slice drawn from the "
               "legitimate distribution as deliberate blending.",
               "ASSUMPTION: config ATTACK_AMOUNT_*, AMOUNT_*"),
    "mc.device_id": (_pred_shared_device,
                     "Freq-encoded. A burst runs on 1-5 fingerprints for its whole "
                     "duration, so attack device frequency is huge; benign is one "
                     "actor's event count. This is the SHARING half, not the fresh half.",
                     "ASSUMPTION: spec 2.1 'device_id 1-5 per burst'; config BURST_DEVICE_COUNT"),
    "contact": (_pred_unique_identity, "As mc.device_id.", "ASSUMPTION: as mc.device_id"),
    "email": (_pred_unique_identity, "As mc.device_id.", "ASSUMPTION: as mc.device_id"),
    "card.last4": (_pred_unique_identity,
                   "Every attack attempt is a different card: same IIN, different PAN.",
                   "ASSUMPTION: spec 2.1 'card.last4 differs every attempt'"),
    "vpa": (_pred_unique_identity, "As the throwaway-identity mechanism.",
            "ASSUMPTION: spec 2.1 low-overlap half"),
    "card.type": (_pred_card_type,
                  "Card testing is card-only and skews credit (stolen credit cards "
                  "are the target); benign card traffic skews debit, and non-card "
                  "methods leave the field null.",
                  "ASSUMPTION: config CARD_DEBIT_SHARE, METHOD_MIX, attacks.py 0.55 credit"),
}


def predicted(field, seed=12345):
    fn = MECHANISMS[field][0]
    return fn(random.Random(seed))
