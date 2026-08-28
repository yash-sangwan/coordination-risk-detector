"""Actor population.

Section 1.1 of the spec: actors are drawn first with persistent attributes, then
they behave, and rows are the consequence. Nothing here is filled in because of
what an actor is going to do later.

Section 4 benign collisions are built here, structurally, because they cannot be
retrofitted later without planting the answer. Households share a device, some
people share a phone, a VPA local part is usually the phone number, and an IIN is
an issuer range that many unrelated customers sit inside.
"""

from dataclasses import dataclass, field
from typing import Optional

from . import config as C


@dataclass
class Card:
    iin: str
    last4: str
    network: str
    type: str
    issuer: str


@dataclass
class Actor:
    actor_id: str
    account_id: str
    actor_class: str
    signup_ts: int
    tier: str
    device_id: str
    pincode: str
    email: str
    contact: str
    vpa: Optional[str]
    cards: list = field(default_factory=list)
    monthly_rate: float = 0.0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _weighted(rng, pairs):
    """pairs: sequence of (value, weight). Weights need not sum to 1."""
    total = sum(w for _, w in pairs)
    x = rng.random() * total
    upto = 0.0
    for value, w in pairs:
        upto += w
        if x <= upto:
            return value
    return pairs[-1][0]


def build_iin_table():
    """10 issuers x 2 IINs, 80/20 intra-issuer.

    Returns (pairs, collision) where pairs is [((iin, issuer, network), weight)]
    and collision is the analytic sum(p^2), i.e. the probability two random card
    attempts share an IIN. Asserted against the spec 4 target range.
    """
    pairs = []
    for idx, (issuer, share) in enumerate(C.ISSUERS):
        # Visa/Mastercard/RuPay ranges. The digits are synthetic but well-formed.
        base = 400000 + idx * 1111
        alt = 520000 + idx * 1111
        net_a = "Visa" if idx % 3 != 2 else "RuPay"
        net_b = "MasterCard" if idx % 3 != 2 else "RuPay"
        pairs.append(((str(base), issuer, net_a), share * C.IIN_PRIMARY_SHARE))
        pairs.append(((str(alt), issuer, net_b), share * (1 - C.IIN_PRIMARY_SHARE)))
    total = sum(w for _, w in pairs)
    collision = sum((w / total) ** 2 for _, w in pairs)
    lo, hi = C.IIN_TARGET_RANGE
    if not lo <= collision <= hi:
        raise ValueError(
            f"IIN pair-collision {collision:.4f} outside spec 4 target {lo}-{hi}. "
            "Adjust ISSUERS or IIN_PRIMARY_SHARE in config, do not adjust the target."
        )
    return pairs, collision


def build_pincode_table():
    """Skewed pincode population, realistic 19,000-pincode shape.

    Top-50 carry 25%, which yields a 0.147% pair-collision rate. The spec once
    asked for 2-4% as well; that needs an effective population of 25-50 pincodes
    and is arithmetically incompatible with the top-50 figure, so it was corrected
    in section 4 rather than forced here.
    """
    tiers = C.PINCODE_TIERS
    pairs = []
    code = 110001
    for count, share in tiers:
        per = share / count
        for _ in range(count):
            pairs.append((f"{code:06d}", per))
            code += 7
            if code > 999999:
                code = 110001
    total = sum(w for _, w in pairs)
    collision = sum((w / total) ** 2 for _, w in pairs)
    top50 = sum(sorted((w / total for _, w in pairs), reverse=True)[:50])
    return pairs, collision, top50


_FIRST = ["aarav", "vivaan", "aditya", "diya", "ananya", "ishaan", "kabir", "meera",
          "rohan", "sneha", "arjun", "priya", "karan", "nisha", "rahul", "tara",
          "vikram", "pooja", "sanjay", "kavya", "manish", "riya", "amit", "shreya"]
_LAST = ["sharma", "verma", "patel", "reddy", "nair", "iyer", "singh", "gupta",
         "mehta", "shah", "das", "bose", "rao", "menon", "kapoor", "joshi"]


def format_contact(rng, digits: str) -> str:
    """Apply the +91 / bare normalisation inconsistency the schema asks for.

    Drawn independently of the label: the same probability is used for
    legitimate actors and for attack identities.
    """
    return ("+91" + digits) if rng.random() < C.CONTACT_PLUS91_SHARE else digits


def _email_local(rng, shape: int) -> str:
    """Several local-part shapes, all used by both real people and, later, by
    attackers. Shape classes must overlap between populations (spec 4)."""
    f, l = rng.choice(_FIRST), rng.choice(_LAST)
    if shape == 0:
        return f"{f}.{l}{rng.randint(70, 99)}"
    if shape == 1:
        return f"{f}{l}"
    if shape == 2:
        return f"{f}_{l}{rng.randint(1, 999)}"
    if shape == 3:
        return f"{f[0]}{l}{rng.randint(1000, 9999)}"
    return f"{f}{rng.randint(10, 99)}"


def build_population(rng, n_actors: int, window_start: int, window_end: int):
    """Draw the actor population. Returns (actors, diagnostics)."""
    iin_pairs, iin_collision = build_iin_table()
    pin_pairs, pin_collision, pin_top50 = build_pincode_table()

    class_pairs = [(k, v["share"]) for k, v in C.ACTOR_CLASSES.items()]
    tier_pairs = list(C.TIER_SHARE.items())
    handle_pairs = list(C.VPA_HANDLES)
    domain_pairs = list(C.EMAIL_TOP_DOMAINS) + [("__other__", C.EMAIL_OTHER_DOMAIN_SHARE)]

    actors = []
    for i in range(n_actors):
        cls = _weighted(rng, class_pairs)
        tier = _weighted(rng, tier_pairs)

        # Signup time. Actors who sign up inside the window will naturally produce
        # attempts from young accounts; account_age_days is derived at attempt
        # time, never assigned. This is what makes the spec 4 young-account target
        # a consequence rather than a plant.
        if rng.random() < C.SIGNUP_DURING_WINDOW:
            signup = rng.randint(window_start, window_end)
        else:
            signup = window_start - rng.randint(1, 900) * 86400

        contact_digits = "9" + "".join(str(rng.randint(0, 9)) for _ in range(9))
        contact = format_contact(rng, contact_digits)

        if rng.random() < C.VPA_FROM_PHONE_SHARE:
            # Bare digits, never the formatted contact. A real UPI handle is
            # 9876543210@okhdfcbank, never +919876543210@okhdfcbank, and taking
            # the formatted string also broke the isdigit() check that propagates
            # a shared phone into a shared VPA.
            vpa_local = contact_digits
        else:
            # Wider suffix than the email pool: a name-shaped VPA local part drawn
            # from a small pool collides far above the phone-derived rate, which
            # would overstate this edge. Real handles carry more entropy.
            vpa_local = f"{_email_local(rng, rng.randint(0, 4))}{rng.randint(100, 99999)}"
        vpa = f"{vpa_local}@{_weighted(rng, handle_pairs)}"

        dom = _weighted(rng, domain_pairs)
        if dom == "__other__":
            dom = rng.choice(["airtelmail.in", "bsnl.in", "acme-corp.in", "vsnl.net",
                              "zoho.in", "icloud.com", "gmx.com", "mail.in"])
        email = f"{_email_local(rng, rng.randint(0, 4))}@{dom}"

        n_cards = 1 if rng.random() < 0.72 else 2
        cards = []
        for _ in range(n_cards):
            iin, issuer, network = _weighted(rng, iin_pairs)
            cards.append(Card(
                iin=iin,
                last4=f"{rng.randint(0, 9999):04d}",
                network=network,
                type="debit" if rng.random() < C.CARD_DEBIT_SHARE else "credit",
                issuer=issuer,
            ))

        lo, hi = C.ACTOR_CLASSES[cls]["monthly_purchases"]
        rate = rng.uniform(lo, hi)

        actors.append(Actor(
            actor_id=f"A{i:07d}",
            account_id="",           # minted with a real id later, in emit order
            actor_class=cls,
            signup_ts=signup,
            tier=tier,
            device_id=f"dev_{rng.getrandbits(48):012x}",
            pincode=_weighted(rng, pin_pairs),
            email=email,
            contact=contact,
            vpa=vpa,
            cards=cards,
            monthly_rate=rate,
        ))

    # ---- benign collisions, applied structurally over the drawn population ----

    # Households: 6% of accounts share a device with at least one other account.
    n_share = int(round(C.DEVICE_SHARE_RATE * n_actors))
    idxs = rng.sample(range(n_actors), n_share) if n_share else []
    j = 0
    households = 0
    while j < len(idxs):
        size = rng.randint(*C.DEVICE_HOUSEHOLD_SIZE)
        group = idxs[j:j + size]
        if len(group) >= 2:
            shared = actors[group[0]].device_id
            for k in group[1:]:
                actors[k].device_id = shared
            households += 1
        j += size

    # Shared phone numbers: 1.5% of accounts. A shared family number, or one
    # person with two accounts. The VPA local part follows the phone where the
    # actor's VPA was phone-derived, which is why vpa-local tracks contact.
    n_phone = int(round(C.CONTACT_SHARE_RATE * n_actors))
    pidx = rng.sample(range(n_actors), n_phone) if n_phone else []
    for k in range(0, len(pidx) - 1, 2):
        a, b = actors[pidx[k]], actors[pidx[k + 1]]
        b.contact = a.contact
        if b.vpa and b.vpa.split("@")[0].isdigit():
            # Match on the bare digits of a's phone, since that is what a
            # phone-derived VPA local part contains.
            b.vpa = f"{a.contact.lstrip('+').removeprefix('91')}@{b.vpa.split('@')[1]}"

    diagnostics = {
        "iin_pair_collision_analytic": iin_collision,
        "pincode_pair_collision_analytic": pin_collision,
        "pincode_top50_share": pin_top50,
        "pincode_shape": "realistic-19k",
        "households_formed": households,
        "n_actors": n_actors,
    }
    return actors, diagnostics
