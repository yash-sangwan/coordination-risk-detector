"""Bounded action. Three tiers, every one reversible, none blocks a customer.

    MONITOR      log it, watch the entity, no customer-visible effect
    STEP_UP      ask for additional authentication; the customer can complete it
    HOLD_REVIEW  authorise, hold fulfilment, queue for a person; released on review

Track 02 is defence only, and two rules follow from that and are enforced here
rather than described:

  1. **No irreversible action exists.** DECLINE is deliberately not in ACTIONS.
     A customer can always finish a step-up, and a held order is released by a
     reviewer. The cited asymmetry is why: Rs 400-600 is lost to false declines
     per Rs 100 saved by blocking fraud (Razorpay, see cost.py), so an outright
     block is a bad trade at any plausible precision, and removing it from the
     action set is cheaper than trying to threshold it safely.
  2. **No action without a logged reason.** `decide()` returns an AlertRecord
     carrying the evidence, the score, the boundary crossed and the expected
     cost of being wrong. There is no code path that acts without producing one.

**The tier boundaries are computed, not chosen.** For an event with estimated
fraud probability p, the expected cost of action a is

    E[cost | a] = p * cost_on_attack(a) + (1 - p) * cost_on_legitimate(a)

which is a straight line in p for each action. The optimal action is whichever
line is lowest, so the boundaries sit exactly where two lines cross. Those
crossings are solved for in `tier_boundaries()`. Nothing here is a round number
picked for convenience, and if the cost parameters change the boundaries move
with them.
"""

from dataclasses import dataclass, field
from typing import Optional

from .cost import cost_action_on_attack, cost_action_on_legitimate

# Ordered by severity. DECLINE is absent by design, see rule 1 above.
ACTIONS = ("MONITOR", "STEP_UP", "HOLD_REVIEW")

REVERSIBLE = {"MONITOR": True, "STEP_UP": True, "HOLD_REVIEW": True}

RATIONALE = {
    "MONITOR": "below the step-up boundary: expected fraud loss is smaller than "
               "the friction of asking this customer for more authentication",
    "STEP_UP": "expected fraud loss exceeds step-up friction, but not the cost "
               "of occupying a reviewer",
    "HOLD_REVIEW": "expected fraud loss exceeds the cost of a review plus the "
                   "delay imposed on a legitimate customer",
}


def expected_cost(action, p_fraud, amount_paise, p_authorize):
    """Expected rupee cost of one action at fraud probability `p_fraud`."""
    return (p_fraud * cost_action_on_attack(action, amount_paise, p_authorize)
            + (1.0 - p_fraud) * cost_action_on_legitimate(action, amount_paise))


def tier_boundaries(amount_paise, p_authorize, grid=20001):
    """The fraud probabilities at which the cheapest action changes.

    Solved by scanning p on a fine grid rather than algebraically, because
    cost_action_on_attack is not guaranteed linear if the cost model grows a
    non-linear term later. The scan is exact to 1/grid and cannot silently
    return a stale closed form.

    Returns {"STEP_UP": p1, "HOLD_REVIEW": p2}: the smallest p at which each
    action becomes the cheapest available one. A boundary of None means that
    action is never optimal at this amount, which is a real answer: on a small
    enough order, occupying a reviewer never pays.
    """
    out = {}
    prev = None
    for i in range(grid):
        pf = i / (grid - 1.0)
        best = min(ACTIONS,
                   key=lambda a: expected_cost(a, pf, amount_paise, p_authorize))
        if best != prev:
            if best not in out:
                out[best] = pf
            prev = best
    out.pop("MONITOR", None)
    return {a: out.get(a) for a in ("STEP_UP", "HOLD_REVIEW")}


def choose(p_fraud, amount_paise, p_authorize):
    """The cheapest available action, and what it is expected to cost."""
    costs = {a: expected_cost(a, p_fraud, amount_paise, p_authorize)
             for a in ACTIONS}
    best = min(costs, key=costs.get)
    return best, costs


# --------------------------------------------------------------------------
# The audit trail
# --------------------------------------------------------------------------

@dataclass
class AlertRecord:
    """One decision, readable by a person.

    This is the audit trail. It carries what was decided, what triggered it,
    the score and the boundary it crossed, and what being wrong is expected to
    cost. Rule 2 above means nothing acts without producing one of these.
    """
    event_id: str
    created_at: int
    detector: str
    action: str
    reversible: bool
    score: float
    threshold: float
    p_fraud: float
    amount_paise: int
    evidence: dict
    expected_costs: dict
    boundaries: dict
    rationale: str
    note: Optional[str] = None

    def cost_if_wrong(self):
        """Rupees at risk if this decision is the wrong one, both directions."""
        return {
            "if_actually_legitimate": cost_action_on_legitimate(
                self.action, self.amount_paise),
            "if_actually_fraud_and_we_only_monitored": (
                self.expected_costs.get("MONITOR", 0.0)),
        }

    def render(self):
        """Human-readable. A reviewer should be able to act on this alone."""
        w = self.cost_if_wrong()
        ev = "\n".join(f"      {k:<22} {v}" for k, v in self.evidence.items())
        bounds = ", ".join(
            f"{a} at p>={b:.4f}" if b is not None else f"{a} never optimal"
            for a, b in self.boundaries.items())
        costs = ", ".join(f"{a} Rs {c:,.2f}" for a, c in
                          sorted(self.expected_costs.items(), key=lambda kv: kv[1]))
        return f"""ALERT  {self.event_id}   t={self.created_at}
  action        {self.action}   (reversible: {self.reversible})
  why           {self.rationale}
  detector      {self.detector}
  score         {self.score:.4f}  against threshold {self.threshold:.4f}
  p(fraud)      {self.p_fraud:.4f}   calibrated on the train split
  order value   Rs {self.amount_paise/100:,.2f}
  evidence
{ev}
  tier boundaries, solved from the cost model, not chosen
                {bounds}
  expected cost of each available action
                {costs}
  cost of being wrong
      if this customer was legitimate      Rs {w['if_actually_legitimate']:,.2f}
      if this was fraud and we only watched Rs {w['if_actually_fraud_and_we_only_monitored']:,.2f}
  no irreversible action is available to this system; DECLINE is not implemented
"""


def decide(event, detector, score, threshold, p_fraud, p_authorize, evidence,
           note=None):
    """Score to bounded action, with the record that justifies it.

    There is no path through this module that takes an action without returning
    a record, which is what makes rule 2 structural rather than a convention.
    """
    amount = event["amount"]
    action, costs = choose(p_fraud, amount, p_authorize)
    return AlertRecord(
        event_id=event["id"],
        created_at=event["created_at"],
        detector=detector,
        action=action,
        reversible=REVERSIBLE[action],
        score=float(score),
        threshold=float(threshold),
        p_fraud=float(p_fraud),
        amount_paise=amount,
        evidence=evidence,
        expected_costs=costs,
        boundaries=tier_boundaries(amount, p_authorize),
        rationale=RATIONALE[action],
        note=note,
    )
