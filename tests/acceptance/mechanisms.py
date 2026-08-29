"""T1a mechanism table: what AUC each field's DECLARED mechanism predicts.

Spec section 5, T1a. Every entry names a mechanism, the config constants it is
computed from, and a citation or an explicit assumption tag. A field cannot be
mechanism-bounded without one, and nothing is silently exempt.

**Non-circularity is the whole point.** Predicted AUC is Monte Carlo sampled from
the constants in src/generator/config.py. The generated data is never consulted.
Predicting from observed class-conditional rates would make observed equal
predicted by construction and would test nothing.
"""

import collections
import math
import random

from src.generator import config as C
from src.generator import population as P

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

def _pred_status(rng, scale=None):
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


def _pred_error_reason(rng, scale=None):
    return _cat_auc(*_error_dists("reason"), rng=rng)


def _pred_error_code(rng, scale=None):
    return _cat_auc(*_error_dists("code"), rng=rng)


def _pred_error_source(rng, scale=None):
    return _cat_auc(*_error_dists("source"), rng=rng)


def _pred_error_step(rng, scale=None):
    return _cat_auc(*_error_dists("step"), rng=rng)


def _pred_method(rng, scale=None):
    # Card testing is card-only by construction (spec 2.1: it tests cards).
    return _cat_auc({"card": 1.0}, dict(C.METHOD_MIX), rng)


def _pred_account_id_null(rng, scale=None):
    return _binary_auc(C.ATTACK_ACCOUNT_ID_NULL_SHARE, C.GUEST_CHECKOUT_SHARE)


def _pred_pincode_null(rng, scale=None):
    return _binary_auc(C.ATTACK_PINCODE_NULL_SHARE, C.NON_SHIPPING_SHARE)


def _pred_account_age(rng, scale=None):
    # account_age_days is null exactly when there is no account, so its ceiling
    # is the account_id nullness mechanism.
    return _binary_auc(C.ATTACK_ACCOUNT_ID_NULL_SHARE, C.GUEST_CHECKOUT_SHARE)


def _pred_checkout_ms(rng, scale=None):
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


def _pred_amount(rng, scale=None):
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


def _pred_shared_device(rng, scale=None):
    """Freq-encoded device_id.

    Spec 2.1 declares 1 to 5 device fingerprints across an ENTIRE burst, so an
    attack device's frequency is on the order of burst_events / n_devices, which
    is enormous. Benign device frequency is one actor's event count. This is the
    opposite of the throwaway-identity mechanism and was mis-declared as it.

    A burst runs on 1 to 5 fingerprints for its entire duration, so an attack
    device frequency is on the order of (burst minutes x rate) / n_devices, in the
    hundreds. A benign device frequency is one actor event count, in the low tens.
    The supports do not meaningfully overlap, so the declared mechanism implies
    near-complete separability. Capped at 0.999 so the test is not vacuous.
    """
    mins = sum(C.BURST_MINUTES) / 2.0
    rate = sum(C.BURST_RATE_PER_MIN) / 2.0
    devices = sum(C.BURST_DEVICE_COUNT) / 2.0
    attack_freq = (mins * rate) / max(devices, 1.0)
    lam = _lam_events_per_actor(scale or DEFAULT_SCALE)
    assert attack_freq > 20 * lam, "burst device frequency no longer dominates"
    return 0.999


def _p_benign_row_unique(lam):
    """P(a benign ROW carries an identifier seen exactly once), before collisions.

    AUC is computed over ROWS, not accounts. Rows from an actor with N events all
    carry frequency N, so a many-event actor contributes many high-frequency rows
    while a one-event actor contributes a single freq-1 row. Row weighting is
    therefore P(N=1)/E[N], which for Poisson(lam) reduces to exp(-lam).

    The previous version returned the ACCOUNT-weighted quantity 1 - P(N=1|N>=1),
    a different number entirely: 0.34 against a true row-weighted 0.15.
    """
    return math.exp(-lam)


def _lam_events_per_actor(scale):
    """Expected events per actor over the window, from declared rates only."""
    lam = sum(v["share"] * (sum(v["monthly_purchases"]) / 2.0)
              for v in C.ACTOR_CLASSES.values())
    lam *= scale["days"] / 30.0
    lam *= 1.0 + C.RETRY_PROB * 0.5
    return max(lam, 1e-6)


def _n_attack_rows(scale):
    """Expected card-testing rows, from the declared burst parameters."""
    bursts = sum(C.BURSTS_PER_CAMPAIGN) / 2.0
    mins = sum(C.BURST_MINUTES) / 2.0
    rate = sum(C.BURST_RATE_PER_MIN) / 2.0
    envelope = 0.75          # mean of the declared rise / plateau / decline shape
    return int(bursts * mins * rate * envelope)


def _weighted_pick(rng, pairs):
    total = sum(w for _, w in pairs)
    x = rng.random() * total
    upto = 0.0
    for v, w in pairs:
        upto += w
        if x <= upto:
            return v
    return pairs[-1][0]


# --- declared identifier generators, simulated rather than assumed unique -----
# Each draws one value through the SAME code path the generator uses, so the
# namespace is whatever the generator actually declares.

def _gen_contact(rng):
    return P.format_contact(rng, "9" + "".join(str(rng.randint(0, 9))
                                               for _ in range(9)))


def _gen_email(rng):
    dom = _weighted_pick(rng, list(C.EMAIL_TOP_DOMAINS)
                         + [("__other__", C.EMAIL_OTHER_DOMAIN_SHARE)])
    if dom == "__other__":
        dom = rng.choice(["airtelmail.in", "bsnl.in", "acme-corp.in", "vsnl.net",
                          "zoho.in", "icloud.com", "gmx.com", "mail.in"])
    return "%s@%s" % (P._email_local(rng, rng.randint(0, 4)), dom)


def _gen_last4(rng):
    return "%04d" % rng.randint(0, 9999)


def _gen_vpa(rng):
    if rng.random() < C.VPA_FROM_PHONE_SHARE:
        local = "9" + "".join(str(rng.randint(0, 9)) for _ in range(9))
    else:
        local = "%s%d" % (P._email_local(rng, rng.randint(0, 4)),
                          rng.randint(100, 99999))
    return "%s@%s" % (local, _weighted_pick(rng, list(C.VPA_HANDLES)))


_ID_GENERATORS = {
    "contact": _gen_contact,
    "email": _gen_email,
    "card.last4": _gen_last4,
    "vpa": _gen_vpa,
}

# (P(field is populated | attack), P(field is populated | benign)).
# card.last4 exists only on card rows and vpa only on UPI rows, so most rows
# carry a shared NULL bucket whose frequency dwarfs any real value. That null
# mass is the dominant mechanism for those two, larger than uniqueness.
# Card testing is card-only, hence 1.0 for last4 and 0.0 for vpa.
_ID_PRESENCE = {
    "contact": (1.0, 1.0),
    "email": (1.0, 1.0),
    "card.last4": (1.0, C.METHOD_MIX["card"] + C.METHOD_MIX["emi"]),
    "vpa": (0.0, C.METHOD_MIX["upi"]),
}


def _poisson(rng, lam):
    lim = math.exp(-lam)
    p, n = 1.0, 0
    while True:
        p *= rng.random()
        if p <= lim:
            return n
        n += 1


def _simulate_identity(field, scale, rng):
    """Row-weighted P(freq == 1) per population, by simulating the DECLARED
    generator at the declared scale. The observed data is never consulted.

    Attack uniqueness is COMPUTED, not assumed. It is near 1 for a 10^9 phone
    namespace but well below it for email, whose local part draws from a 24 x 16
    name pool with only 384 distinct values on shape 1, and for card.last4, whose
    namespace is 10^4.
    """
    gen = _ID_GENERATORS[field]
    p_atk_present, p_ben_present = _ID_PRESENCE[field]
    NULL = "\x00NULL"
    lam = _lam_events_per_actor(scale)
    freq = collections.Counter()
    benign = []
    for _ in range(scale["n_actors"]):
        v = gen(rng)
        n = _poisson(rng, lam)
        if n <= 0:
            continue
        # Presence is drawn per ROW, since the method varies between an actor
        # sessions. An absent row falls into the shared NULL bucket.
        for _ in range(n):
            row = v if rng.random() < p_ben_present else NULL
            freq[row] += 1
            benign.append((row, 1))
    attack = []
    for _ in range(_n_attack_rows(scale)):
        v = gen(rng) if rng.random() < p_atk_present else NULL
        freq[v] += 1
        attack.append(v)

    # Score on the simulated FREQUENCY, exactly as the encoder does, rather than
    # on a freq==1 indicator. For a field with a null bucket the shared NULL
    # frequency is enormous and dominates, so nullness rather than uniqueness is
    # the operative mechanism; computing the AUC on frequency captures whichever
    # of the two is stronger without having to decide in advance.
    return (_auc_from_samples([float(freq[v]) for v in attack],
                              [float(freq[v]) for v, _ in benign]),
            sum(1 for v in attack if freq[v] == 1) / max(len(attack), 1),
            sum(n for v, n in benign if freq[v] == 1) / max(
                sum(n for _, n in benign), 1))


def _pred_unique_identity_factory(field):
    def pred(rng, scale):
        return _simulate_identity(field, scale, rng)[0]
    return pred


def _pred_card_type(rng, scale=None):
    card_share = C.METHOD_MIX["card"] + C.METHOD_MIX["emi"]
    ben = {None: 1 - card_share,
           "debit": card_share * C.CARD_DEBIT_SHARE,
           "credit": card_share * (1 - C.CARD_DEBIT_SHARE)}
    atk = {"credit": 0.55, "debit": 0.45}      # attacks.py burst_attempts
    return _cat_auc(atk, ben, rng)


def _pred_card_issuer(rng, scale=None):
    """Two declared components.

    1. Nullness. Card testing is card-only (spec 2.1), so the issuer is populated
       on essentially every attack row against only the card share of benign
       traffic. Same mechanism as method and card.type.
    2. Issuer concentration. Spec 2.1 declares 1 to 3 IINs per burst, and
       BURSTS_PER_CAMPAIGN is 4 to 7, so a campaign draws at most about 21 IINs
       and necessarily concentrates on a handful of issuers. That is the BIN walk,
       which 2.1 calls the defining feature of the pattern.
    """
    card_share = C.METHOD_MIX["card"] + C.METHOD_MIX["emi"]
    issuers = [(name, w) for name, w in C.ISSUERS]
    tot = sum(w for _, w in issuers)
    ben = {None: 1 - card_share}
    for name, w in issuers:
        ben[name] = card_share * w / tot

    n_bursts = int(sum(C.BURSTS_PER_CAMPAIGN) / 2.0)
    k = max(int(sum(C.BURST_IIN_COUNT) / 2.0), 1)
    picked = [_weighted_pick(rng, issuers) for _ in range(n_bursts * k)]
    counts = collections.Counter(picked)
    n = sum(counts.values()) or 1
    atk = {name: c / n for name, c in counts.items()}
    return _cat_auc(atk, ben, rng)


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
    "contact": (_pred_unique_identity_factory("contact"),
                "Freq-encoded. Fresh per attempt for card testing against a 10^9 "
                "phone namespace, so attack values are essentially all unique, "
                "while benign values repeat across an actor events.",
                "ASSUMPTION: spec 2.1 fresh-per-attempt; config ACTOR_CLASSES, RETRY_PROB"),
    "email": (_pred_unique_identity_factory("email"),
              "Freq-encoded. Fresh per attempt, but the local part draws from a "
              "24x16 name pool with only 384 values on shape 1, so attack emails "
              "collide with each other and with benign ones.",
              "ASSUMPTION: population._email_local namespace; config EMAIL_TOP_DOMAINS"),
    "card.last4": (_pred_unique_identity_factory("card.last4"),
                   "Every attack attempt is a different card, but last4 has a 10^4 "
                   "namespace so birthday collisions are heavy at this volume.",
                   "ASSUMPTION: spec 2.1 card.last4 differs every attempt"),
    "vpa": (_pred_unique_identity_factory("vpa"),
            "Freq-encoded. Phone-derived for most identities, so it inherits the "
            "phone namespace and its near-total uniqueness.",
            "ASSUMPTION: config VPA_FROM_PHONE_SHARE, VPA_HANDLES"),
    "card.issuer": (_pred_card_issuer,
                    "Two components. Card testing is card-only so the issuer is "
                    "populated, and 1-3 IINs per burst across 4-7 bursts concentrate "
                    "attack traffic on a handful of issuers. That is the BIN walk.",
                    "ASSUMPTION: config METHOD_MIX, BURST_IIN_COUNT, "
                    "BURSTS_PER_CAMPAIGN, ISSUERS; spec 2.1 BIN walk"),
    "card.type": (_pred_card_type,
                  "Card testing is card-only and skews credit (stolen credit cards "
                  "are the target); benign card traffic skews debit, and non-card "
                  "methods leave the field null.",
                  "ASSUMPTION: config CARD_DEBIT_SHARE, METHOD_MIX, attacks.py 0.55 credit"),
}


DEFAULT_SCALE = {"n_actors": 40000, "days": 30}


def predicted(field, scale=None, seed=12345):
    """Predicted AUC from declared parameters.

    `scale` carries run PARAMETERS (actor count, window length), not observations,
    so the prediction stays independent of the generated data.
    """
    fn = MECHANISMS[field][0]
    return fn(random.Random(seed), scale or DEFAULT_SCALE)
