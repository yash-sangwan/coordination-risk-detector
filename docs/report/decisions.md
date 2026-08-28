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

---

### 2026-08-28 — Purpose-built synthetic stream rather than IEEE-CIS

- **Chose:** Generate our own event stream from an actor-level causal model, specified before any data exists.
- **Rejected:** Training and scoring on IEEE-CIS, the standard public card-fraud benchmark (590,540 transactions, ~3.5% fraud rate).
- **Why:** Not for the reason we first assumed. The working claim going in was that IEEE-CIS is anonymised into V1 to V339 with no device, IP, shared entities or stream structure. **That claim did not survive checking and is wrong on four counts.** The dataset ships a second identity table carrying `DeviceType` and `DeviceInfo`; the transaction table carries `P_emaildomain` and `R_emaildomain`, `addr1`/`addr2` as billing-region proxies, and `card1`, which functions as a card fingerprint; and `TransactionDT` gives real temporal ordering, which is why a chronological split is the standard protocol on it. Shared entities are not merely present but recoverable: the 1st-place solution won by classifying **clients rather than transactions**, reconstructing a per-cardholder UID from `card1` + `addr1` + `D1`. Coordination structure is therefore detectable in IEEE-CIS, contrary to what we believed.
- **The actual reason to reject it:** IEEE-CIS has **no coordination labels**. `isFraud` is a per-transaction flag. Nothing in it says "these twelve accounts are one ring" or "this window is one card-testing burst", so it cannot score a coordination detector's precision and recall at all, only a per-transaction classifier's. Secondary reasons: no IP field of any kind, entity columns anonymised so an edge cannot be explained to a reviewer, and it is US card-not-present e-commerce with no UPI, no VPA and no Indian IIN ranges.
- **The cost we accept:** synthetic data invites the charge that we planted the signal we then found. That is answered by the acceptance tests in the generator spec, not by argument.

Sources: [IEEE-CIS data description](https://www.kaggle.com/competitions/ieee-fraud-detection/data), [1st place solution writeup](https://www.kaggle.com/competitions/ieee-fraud-detection/writeups/fraudsquad-1st-place-solution-part-2), [Fraud Dataset Benchmark](https://arxiv.org/pdf/2208.14417). On why not to synthesise with an off-the-shelf tabular generator: [arXiv 2604.13125](https://arxiv.org/pdf/2604.13125) finds such generators fail to preserve temporal, velocity and multi-account signals specifically.

---

### 2026-08-28 — Cut `ip_prefix` and all IP-based linking

- **Chose:** Drop `ip_prefix` from the record and remove IP entirely as a linking attribute.
- **Rejected:** Keeping a /24 prefix as a medium-strength edge for detecting bot pools.
- **Why:** Jio, Airtel, BSNL and ACT all place subscribers behind carrier-grade NAT, and Indian mobile broadband is almost always CGNAT'd, so two unrelated users sharing a /24 is unremarkable and the edge carries almost no information here. Compounding it, no subscribers-per-shared-address figure could be sourced, so its benign collision rate would have been invented for the one attribute most needing a real number. Reversible: if a citable benign rate turns up, this is the first attribute to restore.

Sources: [A10 Networks on CGNAT](https://www.a10networks.com/glossary/what-is-carrier-grade-nat-cgn-cgnat/), [PureVPN list of ISPs using CGNAT](https://www.purevpn.com/blog/top-isps-using-cgnat/).

---

### 2026-08-28 — Pincode distribution kept realistic, spec target corrected

- **Chose:** The realistic 19,000-pincode shape, top-50 carrying 25% of orders, giving a 0.147% pair-collision rate. Section 4 of the spec was corrected to state that derived value.
- **Rejected:** The spec's original 2-4% pair-collision target, and any switch that would let us generate a concentrated shape instead.
- **Why:** The two numbers are arithmetically incompatible. Pair collision is `sum(p_i^2)`, so 2-4% implies an effective population of 25-50 pincodes, meaning essentially every order arrives from about 33 postcodes. That is not India. Only the concentration curve is grounded in a real mechanism, so the collision rate is now derived from it rather than asserted beside it.
- **Why the low rate is correct, not a shortfall:** pincode is our ring edge, and rings converge on drop addresses. The rarer an innocent pincode collision, the stronger the evidence when one is observed. This is the exact opposite of the `vpa` handle, which is deliberately near-worthless because it collides constantly.

---

### 2026-08-28 — Decline rate measured on attempts, not sessions

- **Chose:** Model decline at the gateway-attempt level, driven by the per-method rates (UPI 99.2%, cards 85-90%, netbanking 90-95%). Measured overall decline is about 5.5%.
- **Rejected:** Calibrating the stream to the cited 68-74% D2C success figure, which would imply roughly 26-32% decline.
- **Why:** Different denominators. The 68-74% figure is end-to-end and includes pre-gateway checkout abandonment, which never becomes a payment attempt and therefore never becomes a row in this stream. Our record is one attempt at the gateway, so the per-method rates govern. Forcing the stream to 28% decline would have meant inventing failures the gateway never sees.

---

### 2026-08-28 — vpa local part target was inherited from contact, not derived

- **Chose:** Correct the spec. The `vpa` local part target is now the formula `contact_rate x 0.92^2 x 0.72`, evaluating to about 1.03%, with the dependency on `contact` recorded so a later change to one does not silently break the other.
- **Rejected:** Tuning the generator to reach the original 1.5%.
- **Why:** The 1.5% was copied from the `contact` row rather than derived. A VPA local part is the phone number for 92% of actors, so a collision needs a shared phone, both actors drawing phone-derived VPAs, and both actually paying by UPI inside the window. Only two levers could have forced 1.5%: raising `VPA_FROM_PHONE_SHARE` toward 1.0, which asserts that essentially every Indian UPI handle is a phone number and is false, or raising `CONTACT_SHARE_RATE`, which breaks a passing attribute to fix a derived one. Both mean changing the modelled world to fit a number we invented, which is the failure mode section 4 exists to prevent.
