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
            "Implemented the mechanism-grounded number (top-50 = 25%) and report "
            "the achieved pair-collision rate honestly. PINCODE_CONCENTRATED=True "
            "switches to the degenerate ~40-pincode shape if the 2-4% figure is "
            "the one that matters."
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

# Pincode shape. See C1: these two targets are incompatible.
PINCODE_CONCENTRATED = False           # False = realistic 19k shape (top-50 = 25%)
PINCODE_TIERS_REALISTIC = [(50, 0.25), (950, 0.45), (18000, 0.30)]
PINCODE_TIERS_CONCENTRATED = [(8, 0.35), (32, 0.45), (200, 0.20)]

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
