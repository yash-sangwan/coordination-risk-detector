"""Generator constants.

Every number here traces to a section of notes/generator-spec.md. Where the spec
gives a citable figure it is marked CITED; where the spec marks it an assumption
it is marked ASSUMPTION. Where two spec numbers cannot both hold, the conflict is
named in SPEC_CONFLICTS and the resolution is explicit, never silent.
"""

# --------------------------------------------------------------------------
# Spec conflicts found while building. Reported, not silently resolved.
# --------------------------------------------------------------------------
SPEC_CONFLICTS = [
    {
        "id": "C1-pincode-collision",
        "spec": "4",
        "conflict": (
            "Section 4 asks for BOTH top-50 pincodes carrying ~25% of orders AND "
            "any two random orders colliding 2-4% of the time."
        ),
        "arithmetic": (
            "Pair-collision probability is sum(p_i^2). A realistic 19,000-pincode "
            "shape with top-50 at 25% yields 0.147%. Reaching 2-4% requires an "
            "effective population of only ~25-50 pincodes total, i.e. every order "
            "from ~33 postcodes, which contradicts the stated 19,000."
        ),
        "resolution": (
            "RESOLVED. Kept the realistic shape and corrected the spec: section 4 "
            "now states the analytic 0.147% instead of the unreachable 2-4%. No "
            "switch to a concentrated shape exists. Low benign collision is the "
            "right outcome here, because pincode is the ring edge and a rare "
            "innocent collision makes an observed one stronger evidence."
        ),
    },
    {
        "id": "C2-overall-decline",
        "spec": "1.5",
        "conflict": (
            "Per-method success rates (UPI 99.2%, cards 85-90%, netbanking 90-95%) "
            "blend to 94.6% success given the section 1.3 method mix. The same "
            "section cites overall D2C success at 68-74%. These are 5.4x apart in "
            "decline terms."
        ),
        "arithmetic": "0.55*0.992 + 0.28*0.875 + 0.09*0.925 + 0.06*0.90 + 0.02*0.90 = 0.946",
        "resolution": (
            "Different denominators. The 68-74% figure is end-to-end and includes "
            "pre-gateway checkout abandonment that never becomes a payment attempt. "
            "Our record is a gateway attempt, so per-method rates govern. Achieved "
            "decline is reported; it will NOT be ~28%."
        ),
    },
    {
        "id": "C3-retry-recovery",
        "spec": "1.5",
        "conflict": (
            "Section 1.5 states a failed attempt is retried ~35% of the time AND "
            "that retries recover 15-20% of failed transactions."
        ),
        "arithmetic": "retry_prob 0.35 * retry success ~0.93 => ~33% recovery, not 15-20%.",
        "resolution": (
            "Followed the explicit behavioural instruction (35% retry) since it is "
            "the one that shapes the stream, and report the achieved recovery rate."
        ),
    },
    {
        "id": "C4-geo-absolutes",
        "spec": "1.5",
        "conflict": (
            "Absolute tier success rates (metro 78-82%, T3 55-62%) cannot coexist "
            "with per-method rates: UPI alone is 99.2% and is 55% of traffic."
        ),
        "arithmetic": "A T3 actor paying by UPI cannot be at 58% success if UPI is 99.2%.",
        "resolution": (
            "Geography implemented as a multiplier on decline probability that "
            "preserves the metro < T2 < T3 ordering, not as absolute levels."
        ),
    },
]

# --------------------------------------------------------------------------
# 1.1 Actor population
# --------------------------------------------------------------------------
ACTOR_CLASSES = {                      # ASSUMPTION (spec 1.1)
    "returning":  {"share": 0.55, "monthly_purchases": (1, 4)},
    "occasional": {"share": 0.35, "monthly_purchases": (0, 1)},
    "new":        {"share": 0.10, "monthly_purchases": (0, 1)},
}

# Fraction of actors whose account is created *during* the window rather than
# before it. Drives the ">=10% of attempts from accounts <7 days old" target in
# section 4 causally, by letting account_age_days be derived at attempt time.
SIGNUP_DURING_WINDOW = 0.30            # ASSUMPTION

# --------------------------------------------------------------------------
# 1.2 Arrival pattern
# --------------------------------------------------------------------------
HOURLY_WEIGHTS = [                     # ASSUMPTION, shape anchored on cited 19-22 peak
    0.15, 0.15, 0.15, 0.15, 0.15, 0.15,   # 00-05
    0.45, 0.45, 0.45,                     # 06-08
    0.80, 0.80, 0.80,                     # 09-11
    1.05, 1.05, 1.05,                     # 12-14
    0.85, 0.85, 0.85,                     # 15-17
    1.35, 1.35,                           # 18-19
    1.80, 1.80,                           # 20-21
    0.90, 0.90,                           # 22-23
]
DOW_WEIGHTS = {0: 0.90, 1: 1.00, 2: 1.00, 3: 1.00, 4: 1.00, 5: 1.25, 6: 1.25}  # Mon=0
PAYDAY_DAYS = set([1, 2, 3, 25, 26, 27, 28, 29, 30, 31])
PAYDAY_MULT = 1.30                     # ASSUMPTION

FLASH_SALES_PER_MONTH = (2, 4)         # spec 1.2
FLASH_SALE_MINUTES = (60, 180)
FLASH_SALE_MULT = (6.0, 15.0)

# --------------------------------------------------------------------------
# 1.3 Method mix
# --------------------------------------------------------------------------
METHOD_MIX = {                         # ASSUMPTION (spec 1.3)
    "upi": 0.55, "card": 0.28, "netbanking": 0.09, "wallet": 0.06, "emi": 0.02,
}
CARD_DEBIT_SHARE = 0.60                # spec 1.3
CARD_INTERNATIONAL_SHARE = 0.02        # spec 1.3

# --------------------------------------------------------------------------
# 1.4 Amounts (paise)
# --------------------------------------------------------------------------
AMOUNT_MICRO_SHARE = 0.10              # spec 1.4, raised 4%->10% by spec section 7
AMOUNT_MICRO_SUB50_FRACTION = 1 / 3    # "of which a third are below 50 rupees"
AMOUNT_ROUND_SHARE = 0.30              # spec 1.4
AMOUNT_ROUND_POINTS = [9900, 19900, 29900, 49900, 79900, 99900, 149900, 199900, 249900]
AMOUNT_LOGNORM_MEDIAN = 85000          # 850 rupees
AMOUNT_LOGNORM_SIGMA = 0.95            # ASSUMPTION, gives a tail to ~50k rupees
AMOUNT_MAX = 5000000                   # 50,000 rupees

# --------------------------------------------------------------------------
# 1.5 Declines
# --------------------------------------------------------------------------
# CITED per-method success (spec 1.5). wallet/emi are ASSUMPTION: the spec gives
# no figure for them.
METHOD_DECLINE = {
    "upi": 0.008,            # CITED  NPCI technical decline <1%
    "card": 0.125,           # CITED  85-90% success
    "card_intl": 0.250,      # CITED  70-80% success
    "netbanking": 0.075,     # CITED  90-95% success
    "wallet": 0.080,         # ASSUMPTION
    "emi": 0.100,            # ASSUMPTION
}

# Evening coupling. The spec cites an 8-12 percentage-point drop, but that figure
# lives in the 68-74% denominator (see C2). Applied here as a decline multiplier
# so UPI does not jump from 0.8% to 10.8%. Achieved pp shift is measured, not assumed.
EVENING_HOURS = (19, 20, 21)
EVENING_DECLINE_MULT = 2.5             # ASSUMPTION, see C2

# Flash sales strain the banks the same way the evening peak does, so a high
# volume window with elevated declines is NORMAL rather than suspicious. Without
# this, "busy and failing" would point straight at an attack. Applied after
# normalisation, like downtime, because a sale is an excursion above the blended
# baseline rather than part of it.
FLASH_DECLINE_STRAIN_MAX = 2.4         # ASSUMPTION, at the top sale multiplier
FLASH_STRAIN_REFERENCE_MULT = 15.0     # sale multiplier at which the max applies

# Geography as relative modifier, not absolute level (see C4).
TIER_DECLINE_MULT = {"metro": 1.0, "tier2": 1.6, "tier3": 2.4}   # ASSUMPTION
TIER_SHARE = {"metro": 0.45, "tier2": 0.33, "tier3": 0.22}       # ASSUMPTION

DECLINE_REASONS = [                    # ASSUMPTION on split, categories from spec 1.5
    ("insufficient_funds",   0.30, "BAD_REQUEST_ERROR", "customer", "payment_authorization"),
    ("incorrect_pin",        0.22, "BAD_REQUEST_ERROR", "customer", "payment_authentication"),
    ("payment_cancelled",    0.20, "BAD_REQUEST_ERROR", "customer", "payment_authentication"),
    ("gateway_timeout",      0.18, "GATEWAY_ERROR",     "gateway",  "payment_authorization"),
    ("card_expired",         0.10, "BAD_REQUEST_ERROR", "customer", "payment_initiation"),
]

RETRY_PROB = 0.35                      # spec 1.5 explicit, see C3
RETRY_DELAY_S = (30, 180)              # spec 1.5
MAX_ATTEMPTS_PER_SESSION = 3

DOWNTIME_PER_MONTH = (1, 3)            # spec 1.5
DOWNTIME_MINUTES = (90, 480)           # ASSUMPTION. Widened from (45,240): real bank
                                       # outages run for hours, and short windows caught
                                       # almost no events, leaving the confounder absent.
DOWNTIME_DECLINE_MULT = (5.0, 10.0)    # spec 1.5

# --------------------------------------------------------------------------
# Guest checkout and non-shipping goods (legitimate)
# --------------------------------------------------------------------------
# These exist because spec 2.1 says card testing leaves account_id and
# shipping_pincode "usually null". The legitimate stream previously emitted a
# value for both on every single row, which would have made nullness a perfect
# label. Guest checkout and digital goods are both real, so they are modelled
# rather than left as a hole for the attack to fall through.
GUEST_CHECKOUT_SHARE = 0.12            # ASSUMPTION. Sessions with no account.
NON_SHIPPING_SHARE = 0.18              # ASSUMPTION. Digital goods, recharges,
                                       # top-ups and subscriptions do not ship.

# --------------------------------------------------------------------------
# 2.1 Card testing bursts
# --------------------------------------------------------------------------
# Spec 2.1 gives bursts of 10-90 minutes at 20-200 attempts/min. At this
# merchant's volume (~2,016 events/day, ~1.4/min average, ~3/min at peak) the
# top of that range is incoherent: a single 90-minute burst at 200/min is 18,000
# events, or 22.9% of the entire 30-day stream on its own. The cited anchor is a
# merchant whose carding PEAKED at 8% of transactions, so we sit at the bottom of
# both spec ranges. This narrowing is arithmetic, not preference, and it is the
# merchant that is small rather than the spec that is wrong.
BURST_MINUTES = (10, 45)               # bottom of spec's 10-90
BURST_RATE_PER_MIN = (20, 60)          # bottom of spec's 20-200
BURSTS_PER_CAMPAIGN = (4, 7)           # ASSUMPTION. Spec says bursts "recur over days
                                       # or weeks" and gives no count, so this is the one
                                       # lever here that is ours rather than the spec's.
                                       # At 8-13 bursts prevalence reached 12.4%, far above
                                       # the cited 8%-at-peak anchor, so the count came down
                                       # rather than the spec's rate or duration bands.
BURST_GAP_DAYS = (1.5, 5.0)            # ASSUMPTION, spacing between bursts

# Campaign envelope, matching the cited slow rise / plateau / decline shape.
CAMPAIGN_RISE_FRACTION = 0.35
CAMPAIGN_PLATEAU_FRACTION = 0.40       # decline takes the remaining 0.25
CAMPAIGN_ENVELOPE_FLOOR = 0.20         # a burst at the very start is still real

BURST_IIN_COUNT = (1, 3)               # spec 2.1
BURST_DEVICE_COUNT = (1, 5)            # spec 2.1

# Card testing is testing whether stolen cards are live, so most attempts fail.
# The point of the exercise is the small fraction that do not.
ATTACK_DECLINE_BASE = 0.88             # ASSUMPTION
ATTACK_DECLINE_BLOCKED = 0.99          # when an issuer or the merchant blocks

# Concentrated CVV/expiry class, unlike the broad legitimate mix.
ATTACK_DECLINE_REASONS = [
    ("incorrect_cvv",   0.62, "BAD_REQUEST_ERROR", "customer", "payment_authentication"),
    ("card_expired",    0.24, "BAD_REQUEST_ERROR", "customer", "payment_initiation"),
    ("card_declined",   0.14, "BAD_REQUEST_ERROR", "customer", "payment_authorization"),
]

ATTACK_CHECKOUT_MS = (150, 2500)       # spec 2.1, heavy mode near 300ms
ATTACK_CHECKOUT_MODE = 300

# Amount mixture from spec 2.1, deliberately not a single band.
ATTACK_AMOUNT_MICRO = 0.55             # 1-50 rupees
ATTACK_AMOUNT_LOW = 0.30               # 50-500 rupees
# remaining 0.15 is drawn from the LEGITIMATE amount distribution (blending)

ATTACK_ATTEMPT_SEQ1_SHARE = 0.92       # "frequently 1", fresh session each time
ATTACK_ACCOUNT_ID_NULL_SHARE = 0.90    # "usually null", guest checkout
ATTACK_PINCODE_NULL_SHARE = 0.93       # "usually null", nothing ships

# Endings, spec 2.1 proportions
BURST_ENDINGS = [("exhausted", 0.50), ("blocked", 0.35), ("moves_on", 0.15)]
BURST_DECAY_MINUTES = (10, 20)         # for the moves_on ending

# --------------------------------------------------------------------------
# 2.2 Rings
# --------------------------------------------------------------------------
# The inverse of a burst: low fanout, high overlap, weeks rather than minutes.
RING_COUNT = (3, 5)                    # ASSUMPTION
RING_SIZE = (3, 15)                    # spec 2.2
RING_SIGNUP_SPREAD_DAYS = (5, 25)      # "created over days or weeks"
RING_DORMANCY_DAYS = (5, 20)           # spec 2.2: rings have a dormancy period
RING_DEVICE_SUBSET = (0.30, 0.60)      # ASSUMPTION, a subset shares a device
RING_CONTACT_REUSE_PROB = 0.25         # ASSUMPTION, "occasional carelessness"
RING_SESSIONS_PER_DAY = (0.15, 0.45)   # ASSUMPTION. Low rate, never bursty.
RING_CAUGHT_PROB = 0.70                # ASSUMPTION
RING_CAUGHT_AFTER_DAYS = (4, 16)       # ASSUMPTION

# --------------------------------------------------------------------------
# 4. Benign collision structure
# --------------------------------------------------------------------------
# card.iin: 10 issuers x 2 IINs each, 80/20 intra-issuer split.
# Analytic pair-collision = sum(p^2) = 0.0868, inside the 8-15% target.
ISSUERS = [
    ("HDFC", 0.20), ("SBIN", 0.18), ("ICIC", 0.14), ("UTIB", 0.11), ("KKBK", 0.08),
    ("PUNB", 0.07), ("BARB", 0.06), ("CNRB", 0.05), ("INDB", 0.05), ("YESB", 0.06),
]
IIN_PRIMARY_SHARE = 0.80               # intra-issuer split, keeps sum(p^2) in range
IIN_TARGET_RANGE = (0.08, 0.15)        # spec 4

DEVICE_SHARE_RATE = 0.072              # assigned rate. Only ~62% of actors transact in a
                                       # 30-day window, so both members of a household are
                                       # both observed less often than they are assigned.
                                       # This lands the OBSERVED rate on the spec 4 target of 6%.
DEVICE_HOUSEHOLD_SIZE = (2, 3)

CONTACT_SHARE_RATE = 0.025             # assigned rate, same observability correction.
                                       # Lands observed on the spec 4 target of 1.5%.

# vpa local part is derived from the phone for most actors, so it inherits the
# phone collision rate rather than being tuned independently (spec 4 mechanism).
VPA_FROM_PHONE_SHARE = 0.92            # ASSUMPTION. Most Indian UPI handles are the
                                       # phone number, which is the spec 4 mechanism for
                                       # vpa-local tracking contact rather than being
                                       # calibrated as an independent edge.
VPA_HANDLES = [                        # deliberately few: handle collision >40%
    ("okhdfcbank", 0.22), ("okicici", 0.18), ("oksbi", 0.17), ("okaxis", 0.12),
    ("ybl", 0.11), ("paytm", 0.10), ("kotak811", 0.05), ("apl", 0.05),
]

EMAIL_TOP_DOMAINS = [                  # spec 4: ~70% on top-3
    ("gmail.com", 0.52), ("yahoo.com", 0.10), ("outlook.com", 0.08),
    ("rediffmail.com", 0.06), ("hotmail.com", 0.05), ("protonmail.com", 0.03),
]
EMAIL_OTHER_DOMAIN_SHARE = 0.16        # long tail of ISP/company domains

# Pincode shape. Realistic 19,000-pincode distribution, top-50 carrying 25%.
# The resulting pair-collision rate is 0.147%, which is what section 4 now states.
# There is deliberately no switch to a concentrated shape: reaching the old 2-4%
# target needs an effective population of 25-50 pincodes, which is not India.
PINCODE_TIERS = [(50, 0.25), (950, 0.45), (18000, 0.30)]
PINCODE_PAIR_COLLISION_ANALYTIC = 0.001468   # sum(p^2) for the tiers above

CHECKOUT_MS_FAST_TARGET = 0.30         # spec 4: >=30% under 1000ms
ACCOUNT_AGE_YOUNG_TARGET = 0.10        # spec 4: >=10% of attempts from accounts <7d

# checkout_ms by actor class, milliseconds (ASSUMPTION on the distributions;
# the >=30% fast target in spec 4 is the constraint they must satisfy)
# The cited per-method rates are already blended across geography and time of day.
# Applying the tier and evening multipliers raw would double-count them, so both
# are normalised to mean 1 over the population and the hourly profile. This keeps
# the blended per-method decline equal to the cited figure while preserving the
# metro<T2<T3 ordering and the evening coupling.
def _expected_multiplier():
    e_tier = sum(TIER_SHARE[t] * TIER_DECLINE_MULT[t] for t in TIER_SHARE)
    total_w = sum(HOURLY_WEIGHTS)
    evening_w = sum(HOURLY_WEIGHTS[h] for h in EVENING_HOURS)
    frac_evening = evening_w / total_w
    e_evening = frac_evening * EVENING_DECLINE_MULT + (1 - frac_evening) * 1.0
    return e_tier * e_evening

DECLINE_NORMALISER = _expected_multiplier()

CHECKOUT_MS = {
    "returning":  (1100, 0.95),   # median ms, lognormal sigma. Saved instrument, one-tap.
    "occasional": (6000, 0.75),   # reads the page, picks a method
    "new":        (18000, 0.70),  # types card details for the first time
}
