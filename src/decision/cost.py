"""Cost model in rupees. Confusion matrix to money.

Every parameter below carries one of three tags and there are no others:

  CITED       a published figure, with its source, verified in the spec
  MEASURED    computed from our own generated stream, never invented
  ASSUMPTION  a number we chose, with the reasoning that produced it

The asymmetry this whole layer exists to respect is cited: **"For every Rs 100
saved by preventing fraud, brands lose Rs 400-600 to falsely declined legitimate
orders"** (Razorpay, payment success rate optimization, verified 2026-08-28 and
recorded in docs/generator-spec.md section 3). A 4x to 6x penalty against
over-blocking is the reason this system never declines anyone: see
src/decision/policy.py, where the most severe available action is a reversible
hold rather than a decline.

That citation is an aggregate industry ratio, not a per-event price, so it is
used here as a CHECK on the model rather than as an input to it. The model is
built bottom up from per-event quantities, and `implied_decline_ratio()` then
reports what asymmetry it implies, which should land inside the cited 4-6x band.
If it does not, the model is wrong and says so.

Amounts come from the generated stream. Nothing here reads a label: costs are
computed per outcome, and which outcome an event had is the harness's business.
"""

from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Param:
    value: float
    tag: str          # CITED | MEASURED | ASSUMPTION
    source: str

    def __post_init__(self):
        assert self.tag in ("CITED", "MEASURED", "ASSUMPTION"), self.tag


PARAMS = {
    # ---- what a fraudulent attempt costs if we let it through -------------
    "chargeback_fee": Param(
        1500.0, "ASSUMPTION",
        "Fixed per-dispute fee charged to the merchant on top of the reversed "
        "amount. Card schemes and PSPs all levy one; we found no Razorpay "
        "figure we could verify, so this is ours. Rs 1,500 is the order of "
        "magnitude quoted across Indian PSP pricing pages. Sensitivity is "
        "reported rather than assumed away."),

    "enumeration_cost_per_attempt": Param(
        2.0, "ASSUMPTION",
        "Scheme exposure per enumeration attempt. Visa VAMP flags a merchant "
        "above 300,000 enumeration attempts/month at a 20%+ ratio from 1 Oct "
        "2025 [CITED: Chargebacks911, in spec 2.1], but the fine schedule "
        "behind that threshold is not public in any source we verified, so the "
        "per-attempt price is ours. Small by construction: the point is that "
        "attempts are not free even when they decline."),

    # ---- what our own actions cost when the customer was legitimate -------
    "gross_margin": Param(
        0.30, "ASSUMPTION",
        "Merchant gross margin on an order. A lost legitimate order costs the "
        "margin, not the full ticket. 30% is mid-range for Indian D2C retail. "
        "Ours, not cited."),

    "review_cost": Param(
        120.0, "ASSUMPTION",
        "Analyst cost of one manual review. Derived rather than picked: an "
        "analyst at roughly Rs 9,00,000 fully loaded per year works about "
        "1,800 hours, so Rs 500/hour, and a queue review taking about 15 "
        "minutes costs Rs 125. Rounded to Rs 120. The inputs are ours."),

    "stepup_abandon_rate": Param(
        0.08, "ASSUMPTION",
        "Share of legitimate customers who drop out when asked for additional "
        "authentication. Step-up friction is real but modest, since the "
        "customer can complete it. Ours."),

    "hold_abandon_rate": Param(
        0.04, "ASSUMPTION",
        "Share of legitimate customers lost when an order is authorised but "
        "held for review before fulfilment. Lower than step-up because the "
        "customer has already completed checkout and the delay is back "
        "office. Ours."),

    # ---- how well each action actually stops fraud ------------------------
    "stepup_effectiveness": Param(
        0.85, "ASSUMPTION",
        "Share of fraudulent attempts stopped by step-up authentication. A "
        "card tester holding stolen PAN and CVV usually does not hold the "
        "cardholder's OTP, so this is high but not 1.0. Ours."),

    "hold_effectiveness": Param(
        0.97, "ASSUMPTION",
        "Share of fraudulent attempts stopped by holding for review. Higher "
        "than step-up because a person looks at it. Not 1.0, because reviewers "
        "release bad orders. Ours."),

    # ---- the cited asymmetry, used as a check and not as an input ---------
    "cited_decline_ratio_lo": Param(
        4.0, "CITED",
        "Rs 400 lost per Rs 100 saved. Razorpay, payment success rate "
        "optimization; verified 2026-08-28, spec section 3."),
    "cited_decline_ratio_hi": Param(
        6.0, "CITED",
        "Rs 600 lost per Rs 100 saved. Same source."),
}


def p(name):
    return PARAMS[name].value


# --------------------------------------------------------------------------
# Per-event costs
# --------------------------------------------------------------------------

def cost_missed_attack(amount_paise, p_authorize):
    """Rupee cost of letting one card-testing attempt through.

    Two parts. Every attempt carries scheme exposure whether or not it
    authorises, because the enumeration ratio counts attempts. The share that
    DO authorise become disputes, costing the reversed amount plus the fee.

    `p_authorize` is MEASURED per dataset from the generated stream, not
    assumed, which matters: an evasive attacker working a better card list
    authorises far more often, so each missed attempt costs more. That is the
    mechanism that should move the money-optimal threshold as the attack
    changes.
    """
    amount = amount_paise / 100.0
    return (p("enumeration_cost_per_attempt")
            + p_authorize * (amount + p("chargeback_fee")))


def cost_action_on_legitimate(action, amount_paise):
    """Rupee cost of taking `action` against a customer who was legitimate."""
    amount = amount_paise / 100.0
    lost_margin = amount * p("gross_margin")
    if action == "MONITOR":
        return 0.0
    if action == "STEP_UP":
        return p("stepup_abandon_rate") * lost_margin
    if action == "HOLD_REVIEW":
        return p("review_cost") + p("hold_abandon_rate") * lost_margin
    if action == "DECLINE":
        # Not reachable from policy.py. Priced only so the cited asymmetry can
        # be checked against a model that never uses it.
        return lost_margin
    raise KeyError(action)


def cost_action_on_attack(action, amount_paise, p_authorize):
    """Rupee cost of taking `action` against an attempt that really was fraud.

    The residual: what the action fails to stop.
    """
    full = cost_missed_attack(amount_paise, p_authorize)
    if action == "MONITOR":
        return full
    if action == "STEP_UP":
        return full * (1.0 - p("stepup_effectiveness"))
    if action == "HOLD_REVIEW":
        # A held order still costs a review even when the review was right.
        return full * (1.0 - p("hold_effectiveness")) + p("review_cost")
    if action == "DECLINE":
        return 0.0
    raise KeyError(action)


def implied_decline_ratio(mean_legit_amount_paise, mean_attack_amount_paise,
                          p_authorize):
    """What asymmetry does this model imply, against the cited 4-6x?

    The cited figure is rupees lost to false declines per rupee saved by
    blocking fraud. Modelled bottom up: declining a legitimate order costs its
    margin, declining a fraudulent one saves the missed-attack cost.

    MEASURED RESULT: this comes out at about 1.5x against a cited 4-6x, so the
    check FAILS, and the failure is structural rather than a bad parameter. This
    function prices the immediate order only. The citation is the full economic
    cost of a false decline, which is dominated by the customer not coming back.
    A single-order model cannot reach 4-6x at any plausible margin.

    The direction matters and it is the safe one. Counting only the immediate
    order UNDERSTATES what over-blocking costs, so every operating point chosen
    against this model is, if anything, more aggressive than the citation would
    justify. It is a lower bound on the cost of a false positive, never an upper
    one. `implied_churn_multiple` below turns the gap into the quantity the
    citation is actually telling us about.
    """
    saved = cost_missed_attack(mean_attack_amount_paise, p_authorize)
    lost = cost_action_on_legitimate("DECLINE", mean_legit_amount_paise)
    return (lost / saved) if saved else float("inf")


def implied_churn_multiple(mean_legit_amount_paise, mean_attack_amount_paise,
                           p_authorize):
    """How many further orders the citation implies a false decline costs.

    Derived, not fitted. If a false decline really costs 4-6x what blocking a
    fraud attempt saves, and the immediate order accounts for only part of it,
    the remainder is repeat business. This solves for that remainder rather than
    choosing a churn parameter and checking it against the citation, which would
    be fitting the model to its own test.

    Returned as (lo, hi) additional order-equivalents, one per end of the band.
    """
    saved = cost_missed_attack(mean_attack_amount_paise, p_authorize)
    one_order = cost_action_on_legitimate("DECLINE", mean_legit_amount_paise)
    if one_order <= 0:
        return (float("inf"), float("inf"))
    return (p("cited_decline_ratio_lo") * saved / one_order,
            p("cited_decline_ratio_hi") * saved / one_order)


# --------------------------------------------------------------------------
# Confusion matrix to money
# --------------------------------------------------------------------------

def total_cost(labels, actions, amounts_paise, p_authorize):
    """Total rupee cost of a set of decisions.

    labels: 1 for card testing, 0 for legitimate
    actions: the action taken on each event
    """
    total = 0.0
    for y, a, amt in zip(labels, actions, amounts_paise):
        total += (cost_action_on_attack(a, amt, p_authorize) if y
                  else cost_action_on_legitimate(a, amt))
    return total


def describe():
    """The parameter table, for the report. Every row tagged."""
    rows = []
    for name, prm in PARAMS.items():
        rows.append((name, prm.value, prm.tag, prm.source))
    return rows
