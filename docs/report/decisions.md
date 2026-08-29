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

---

### 2026-08-28 — Merchant size deferred, recorded as a known limitation

- **Chose:** Keep the merchant at 40,000 actors and accept that attack share runs about 5.4% overall with peak days near 40%, well above the cited 8%-at-peak anchor.
- **Rejected:** Scaling the merchant to reach 8%.
- **Why:** Measured cost. Attack volume is absolute (3,644 events at every size tested), so only the legitimate side scales, and reaching 8% needs about 333,000 actors. That is roughly 10 minutes per generation, over 1.3 GB peak memory and ~400 MB on disk, which would push the five-seed T3 sweep past an hour. Too expensive for the time available. More to the point, it only dilutes the ratio; it does nothing about *why* volume separated the classes, which was the weak flash-sale confounder fixed in the same commit. Recorded as a known limitation to state plainly rather than a problem we believe is solved.

---

### 2026-08-28 — Ring drop pincodes drawn unweighted, and no finer address field

- **Chose:** Draw a ring's drop pincode uniformly over the pincode list rather than from the traffic-weighted distribution used for a customer's home pincode.
- **Rejected:** Two things. The traffic-weighted draw, and adding an address hash finer than pincode.
- **Why unweighted:** A controlled drop address is chosen for operational control, not for being in a busy commercial area. Under the weighted draw, 2 of 3 rings landed on hot urban pincodes shared with 125 and 130 innocent accounts, so the drop address carried almost no information: maximum precision from pincode alone was 0.053 and 0.078. Drops now sit on clusters of 7 to 11 accounts.
- **Why no address hash:** it would work far too well. A per-flat identifier would have near-zero benign collision, so "two accounts share an address" would be close to a pure label. That is exactly the failure `device_id` is showing right now at a 1746x attack-to-benign ratio in pair units. Adding a second attribute with the same defect would buy detectability by planting the answer.
- **Leak checked, not assumed:** rarity does not become the label, because rare pincodes are ordinary for legitimate traffic. 69.0% of benign orders already land on a pincode with 15 or fewer accounts, and the benign cluster distribution is unchanged (median 1, p90 10, max 141). T1 AUC for `shipping_pincode` moved 0.8776 to 0.8846, and the rarity-only variant 0.8765 to 0.8809. No jump.

---

### 2026-08-28 — Ring ships without clearing its T8 floor, as a measured limitation

- **Chose:** Ship the ring pattern with its ceiling stated. Account-level recall is **0.44 against a 0.60 floor**, and the cause is that only **40% of ring members share a device**, which is exactly where recall saturates.
- **Rejected:** Raising `RING_DEVICE_SUBSET` to clear the floor.
- **Why:** A ring in which every member transacts from one device is not a realistic ring. Members deliberately use separate accounts and separate instruments; partial device sloppiness is the realistic case, and it is what makes the pattern hard. Raising the sharing rate would buy the number by making the world less true, which is the failure mode section 4 exists to prevent. The floor is a target we did not hit, not a threshold to move.
- **How it ships:** as a measured limitation with the ceiling and its cause stated, not as a solved problem. Anyone reading the numbers sees 0.44, why it is 0.44, and what would have to change.

**Second limitation, recorded alongside it.** Attack contacts are **100% unique** in the generated stream, which is optimistic. Real carding bots recycle number pools, so `contact` is more separable here than it would be against a real attacker. The same is true to a lesser degree of any field where we assume a fresh identity per attempt.

---

### 2026-08-29 — Evasive card testing models list grade, not pacing

- **Chose:** One swept lever, the **grade of the card list** being run: the fraction of its numbers already known live. It sets the decline rate and touches nothing else.
- **Rejected:** Pacing and long-span spreading as sweep axes, and the "small pool of known-good cards, re-used" reading of the same mechanism.
- **Why list grade:** it is the only one of the three candidate mechanisms that actually moves the *decline rate*. Pacing and spreading move events per minute. Card lists are sold graded by liveness, and the schemes score merchants on the enumeration **ratio** rather than the count (Visa VAMP, 20%+ ratio, cited in spec 2.1), so a ratio threshold is a standing published incentive to hold an observed decline rate down. Working a better list is the obvious response to it.
- **Why not a re-used good-card pool:** a small pool repeats, which creates overlap on `card.last4` and changes the coordination structure the fixture is required to hold fixed. List grade has no such side effect: same IIN, fresh PAN, fresh identity, same few devices, every attempt.
- **Why pacing is implemented but not swept:** two reasons. Measurement, because folding volume into a decline-rate axis confounds the curve. And restraint: pacing tuned until the volume baseline also failed would engineer a fixture only a graph detector could win. The honest expectation is that **the volume baseline still catches this variant**, and that is the correct outcome rather than a shortfall. `--evasive-rate-scale` is wired up at no-throttle so a pacing sweep is one flag away as its own experiment.
- **Why not longer spans:** at campaign scale it is pacing again, and it fights the economics, since stolen card data has a short shelf life. Marked ASSUMPTION: the direction is standard but we have no primary source for a decay rate, so no number is claimed.
- **What is held fixed, and checked rather than asserted:** across the five evasive steps the largest spread on **any** coordination measure is **0.0000 pp**, against 77.41 pp of movement on the decline rate. In evasive mode the decline reason is drawn on every attempt rather than only on failures, so all five steps share one RNG sequence and carry byte-identical identities, devices, amounts and timestamps.
- **The control is the existing data:** `v=0.00` is **byte-identical** to `data/sample` (events, sealed and manifest all hash the same), so every number measured before this commit applies to it unchanged.

---

### 2026-08-29 — The graph detector earns its place, on the curve rather than at a point

- **Chose:** Keep the graph detector, and state its case as a **robustness** result rather than a headline-accuracy one.
- **Rejected:** The conclusion drawn one commit earlier, that the graph adds nothing over the decline rate. That was true at `v=0.00` and it does not generalise, which is exactly what the evasive sweep was built to find out.
- **Why:** With every threshold frozen on `v=0.00` train and applied unchanged across all six grades, the decline baseline goes from 0.9583 PR AUC to **0.2887**, misses both test bursts entirely from `v=0.75` onward, and its recall reaches exactly **0.0000**. The graph sits at **0.9451 at every single evasive grade**, misses nothing, and holds recall at 0.9921 and precision at 0.9363 throughout. The crossover is between `v=0.00` and `v=0.25`.
- **The evidence is stronger than the metric:** the graph's **score vectors are bit-identical** across all five evasive grades, `max |diff| = 0.00e+00`. That is proof rather than inference that it reads nothing decline-linked. It was the one thing that could have invalidated the result and it did not.
- **The honest caveat, which stands:** the **volume baseline also holds flat** at 0.9281 PR AUC and misses no burst at any grade. Pacing was deliberately not swept, so this curve does not show the graph beating a well-tuned volume rule by much; it shows the graph beating the *decline* rule by a lot. The graph is ahead of volume everywhere (0.9451 against 0.9281) and detects far earlier, 6 to 8 attempts against 63 to 66, but a claim that only the graph survives evasion would be false and we are not making it.

---

### 2026-08-30 — Ring detector shipped on the counterfactual number, not the flattering one

- **Chose:** report **PR AUC 0.5753** and **recall 0.5556 at precision 1.0000** as the ring detector's result, measured against a population patched so households share an address.
- **Rejected:** the 0.9291 PR AUC measured on the population as generated, and regenerating the datasets to fix the underlying defect.
- **Why not the higher number:** it is not a detection result. Benign households in the generated population share a device but not a pincode, so the conjunction has a benign occurrence rate of 0.15% and is close to a pure label. Quoting 0.9291 would be reporting the generator's gap as our detector's skill.
- **Why not regenerate:** the fix belongs in `population.py` and is two lines, but it changes `events.jsonl`, which invalidates every card-testing and evasive-sweep number already committed. Two days from the deadline that trade is not worth it. The counterfactual patch gets the same measurement without the collateral, and the generator defect is recorded so the fix can be made deliberately.
- **Where it lands:** above the oracle's 0.4400 ceiling and below the T8 floor of 0.60, at 0.5556. Against the pincode baseline's 0.0036 PR AUC it is a **160x** improvement, and it is the difference between flagging 4,596 accounts to catch 17 and flagging 10 to catch 10.
- **Known limitation, stated rather than smoothed over:** the train-tuned threshold does **not** transfer. Clusters are rebuilt per split, so a score that scales with observed cluster size scales with how many events the split contains, and the threshold of 2.2857 chosen on train flags nothing on test where the operating point is 1.6. The ranking transfers, the cut does not. A deployable version needs a scale-invariant score, and we are reporting the PR curve rather than pretending the operating point survived.
