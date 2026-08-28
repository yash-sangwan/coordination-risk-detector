"""Row assembly and the two-store split.

The event record follows notes/event-schema.md with the section 6 field cuts
applied. Cut and therefore never generated: acquirer_data, error_description,
user_agent_hash, ip_prefix, card.sub_type, entity.

Two files are written and they are not the same file:

  events.jsonl   the stream a detector may read
  sealed.jsonl   labels and generative truth, joined by id, never a feature input

Everything in this module is legitimate traffic, so every sealed row carries
label 0. The store exists now so that the separation is structural from the
start rather than bolted on when attacks arrive.
"""

import json

from . import config as C
from .ids import IdMinter


CUT_FIELDS = ["acquirer_data", "error_description", "user_agent_hash",
              "ip_prefix", "card.sub_type", "entity"]


def build_row(minter, actor, attempt):
    ts = attempt["ts"]
    pay_id = minter.mint("pay", ts)

    method = attempt["method"]
    card = attempt["card"]
    failed = attempt["failed"]

    # A guest session has no account, and a session for goods that do not ship
    # has no shipping pincode. Attack identities arrive with these already None.
    account_id = None if attempt.get("is_guest") else actor.account_id
    if account_id == "":
        account_id = None
    pincode = actor.pincode if attempt.get("ships", True) else None
    if pincode == "":
        pincode = None

    row = {
        "id": pay_id,
        "created_at": ts,
        "order_id": attempt["order_id"],
        "amount": attempt["amount"],
        "currency": "INR",
        "international": attempt["international"],
        "method": method,
        "card": None,
        "vpa": None,
        "bank": None,
        "wallet": None,
        "email": actor.email,
        "contact": actor.contact,
        "status": "failed" if failed else "authorized",
        "error_code": None,
        "error_source": None,
        "error_step": None,
        "error_reason": None,
        # notes is polymorphic in real responses: an object when set, an empty
        # ARRAY when unset (docs/api-probe.md section 7). Never generator metadata.
        "notes": [],
        "merchant_context": {
            "account_id": account_id,
            "device_id": actor.device_id,
            "session_id": attempt["session_id"],
            "attempt_seq": attempt["attempt_seq"],
            "checkout_ms": attempt["checkout_ms"],
            "shipping_pincode": pincode,
            # No account means no account age. Legitimate guest checkout and
            # attack traffic both land here, which is the point.
            "account_age_days": (None if account_id is None or actor.signup_ts is None
                                 else max(0, (ts - actor.signup_ts) // 86400)),
        },
    }

    if method in ("card", "emi") and card is not None:
        row["card"] = {
            "iin": card.iin,
            "last4": card.last4,
            "network": card.network,
            "type": card.type,
            "issuer": card.issuer,
        }
    elif method == "upi":
        row["vpa"] = actor.vpa
    elif method == "netbanking":
        row["bank"] = card.issuer if card else "HDFC"
    elif method == "wallet":
        row["wallet"] = attempt["wallet"]

    if failed:
        reason, code, source, step = attempt["reason"]
        row["error_reason"] = reason
        row["error_code"] = code
        row["error_source"] = source
        row["error_step"] = step

    return row


def sealed_record(row, actor, attempt, in_flash_sale, label=0):
    """Generative truth. Never joined before scoring.

    The event stream carries no marker of any kind. Everything that distinguishes
    an attack row from a legitimate one lives here and nowhere else.
    """
    return {
        "id": row["id"],
        "label": label,
        "attack_type": "card_testing" if label else None,
        "burst_id": attempt.get("burst_id"),
        "actor_id": actor.actor_id,
        "actor_class": actor.actor_class,
        "tier": actor.tier,
        "in_flash_sale": in_flash_sale,
        "in_downtime": attempt.get("downtime_active", False),
    }


def write_stream(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def write_manifest(path, manifest):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
