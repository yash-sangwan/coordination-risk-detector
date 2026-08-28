# Decisions

Append only. One entry per real fork in the road: what we chose, what we rejected, why.
Written when the decision is made, never reconstructed afterwards.

Newest entries go at the bottom.

---

### 2026-08-28 — Event record carries only what is knowable at attempt time

- **Chose:** An "as-of-attempt" rule. Every field in the event record must be true at the instant the payment attempt hits the gateway.
- **Rejected:** Carrying Razorpay's full payment entity, which includes `amount_refunded`, `refund_status`, `fee`, `tax`, `captured` and the `captured`/`refunded` status values.
- **Why:** The strongest label signals in payments (chargeback, refund, settlement) are all later facts, so a record carrying them is the answer rather than an input. The trap is that these are all *real* Razorpay fields and therefore feel legitimate. Concretely, the documented success example has `fee: 198` while the failure example has `fee: null`, so nullness alone would leak the outcome.

Source: event-schema.md (private working doc), "The governing rule: as-of-attempt" and rejected-fields Category B.

---

### 2026-08-28 — Event record and outcome store kept physically separate

- **Chose:** Two stores. The event record holds attempt-time fields and is the only thing the detector may read. A separate outcome store holds chargeback, refund, dispute, settlement and the ring/campaign truth, joined by `id` only after scoring.
- **Rejected:** One combined record with a label column, relying on discipline to exclude it at feature time.
- **Why:** Physical separation makes "we did not plant the signal" checkable rather than merely asserted. A label sitting in the same row is a leak waiting for one careless join.

Source: event-schema.md (private working doc), the two-store table.

---

### 2026-08-28 — Added `card.iin` to the card object

- **Chose:** Carry a 6-digit `iin` on the card object.
- **Rejected:** Using only the real Razorpay card fields (`last4`, `network`, `type`, `issuer`, `sub_type`, `token_iin`).
- **Why:** A BIN walk reuses the issuer range while every other card attribute varies, so without an IIN there is no card-testing detection at all. `iin` is Razorpay's own vocabulary — the entity already exposes `token_iin` — so this extends their model rather than inventing a foreign name.

Source: event-schema.md (private working doc), Deviations §2.

---

### 2026-08-28 — Added a separate `merchant_context` object

- **Chose:** Put device, IP prefix, session, user-agent hash, address and account-age fields in a clearly named `merchant_context` object, documented as data the merchant joins from its own checkout telemetry on `payment_id`.
- **Rejected:** Two alternatives. Folding these fields into the top level as if Razorpay returned them, and dropping them entirely to stay pure to the gateway shape.
- **Why:** Razorpay's payment entity carries no device, IP, session or address data, and coordination detection is impossible without some of it. Pretending the gateway returns it would be a silent falsehood; dropping it would make the task unsolvable. Modelling the join a real merchant would actually perform is the honest third option. `ip_prefix` is truncated to /24 because that is the real linking unit and full addresses add no analytic value.

Source: event-schema.md (private working doc), Deviations §3.

---

### 2026-08-28 — All working docs private during the build; publication decided on Sept 4

- **Chose:** Every working document stays private for the duration of the build. On **Sept 4** we decide what becomes public. Intended to be published at submission: `event-schema.md` and `api-probe.md`. Permanently private: `competitor-scan.md` and `buildathon-context.md`.
- **Rejected:** Keeping the schema and probe findings public in `docs/` throughout the build.
- **Why:** Strategy and competitor research should not be visible to a field we are competing in. The schema and probe write-up are worth publishing at submission as evidence of method, but not before, and holding everything private by default means publication is a deliberate act on a known date rather than a leak by omission.
