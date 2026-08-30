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

### 2026-08-30 — Patched the household defect at scoring time rather than regenerating

> **RESOLVED 2026-08-30, and the decision held.** The reasoning below is unchanged and the outcome vindicated it: we declined to regenerate in order to protect the evasive sweep, and the pipeline regenerated from seed anyway on its first run, so the real fix landed without anyone paying for it deliberately. The scoring-time patch had approximated it **to within 0.0009**. Everything below describes the interim state and should be read in past tense.

- **Chose:** fix `population.py` so household members share a pincode as well as a device, but **do not regenerate any dataset**. The ring numbers on record come from a label-free equivalent patch applied at scoring time in `tests/detector/evaluate_ring.py`.
- **Rejected:** regenerating `data/sample` and the six `data/evasive` steps so the fix is present in the data.
- **Why:** cost, and specifically what the cost falls on. Regenerating changes `events.jsonl`, which invalidates **every card testing number and the entire evasive sweep**. That sweep is the central result of the project: it is what showed the decline baseline collapsing from 0.9583 to 0.2887 PR AUC while the graph detector held 0.9451 with bit-identical scores, and it is the only evidence we have that the graph detector is worth anything. Regenerating would cost about 12 minutes of generation plus roughly 40 minutes of acceptance re-runs plus a re-score of four detectors across six grades, and every test split scored once would have to be scored again. Trading the central result to improve a supporting one, days from the deadline, is the wrong way round.
- **Why the patch is legitimate:** it is **label free**. It gives every account group observed on a shared device a common pincode, and "accounts seen on one device" is visible in the event stream. It reads no sealed record. It is applied uniformly to ring and benign groups alike, and it is a no-op for ring members, who already share their drop address. It cannot flatter the detector: it can only add benign competition that was missing.
- **What is true of the code:** the generator fix is **real and committed**, not a comment. `population.py` now copies the pincode alongside the device id, with the defect, its measurement and the regeneration cost recorded at the site. Anyone who regenerates gets a correct population without having to rediscover any of this. T3 is expected to survive it: the change adds 2.700e-06 to a 0.001468 pair-collision target, +0.18%, far inside the +/-20% band, though it should be rechecked rather than assumed.
- **What we report:** the **patched** numbers. Ring detector PR AUC **0.5753**, recall **0.5556 at precision 1.0000**. The unpatched 0.9291 appears beside them only as the artefact it is, never as a result. Every ring figure in numbers.md is the patched one and is labelled as such.
  - *Superseded, 2026-08-30.* **The current ring PR AUC is 0.5820.** Three values appear across this log and each was correct when written, so reading top down without this note gives a stale number:
    - **0.5753** — this entry. Scoring-time patch, `min_pin_population = 4` selected by an implicit tie-break.
    - **0.5811** — after the tie-break was made explicit and selected `min_pin_population = 6`. Still the scoring-time patch, still pre-fix data.
    - **0.5820** — current. Same detector and same parameters as 0.5811; the only change is that the data is now regenerated with the household fix applied, so the patch is a no-op. The 0.0009 gap between the last two is the measured accuracy of the patch.
  - The 0.9291 remains an artefact and is never a result, which is unchanged.

---

### 2026-08-30 — Ring detector reports a curve, not a frozen operating point

- **Chose:** report the ring detector's full precision-recall curve and its PR AUC, and state plainly that the frozen operating point does not transfer from train to test.
- **Rejected:** quoting a single precision/recall pair as if the threshold held, and re-picking the threshold on test to make one.
- **The limitation, exactly:** the train sweep selects a threshold of **2.2857**. The test split's operating point is **1.6**. At the frozen cut the detector flags **nothing** on test, so precision and recall both read 0.0000 while PR AUC is 0.5753. **The ranking transfers and the cut does not.**
  - *Corrected 2026-08-30:* the selected configuration is now `min_pin_population = 6` rather than 4, chosen by an explicit tie-break instead of dict ordering, so the train threshold is **1.9231** and PR AUC is **0.5811**. The test operating point of 1.6 and the conclusion are unchanged. Superseded by the entry below, which records why reshaping the score does not fix this.
- **Why:** the score is `component_size x density`, which scales with observed cluster size, and clusters are rebuilt from each split's own events so that the train sweep cannot see test structure. The test split holds 30% of the window, so the same ring presents a smaller cluster and a lower score there. The detector is measuring the same structure at a different scale, and an absolute threshold cannot survive that.
- **Why we did not just rescale:** any fix is a design change and this was the last detector. Choosing a normalisation now, checking it only against the split whose numbers we are about to quote, is how a tuned-on-test result gets built by accident.
- **What a deployable version needs:** a **scale-invariant score**, for example the component's size relative to the pincode's population alone, or a rank within the split rather than a raw magnitude. Then a threshold chosen on train means the same thing on test.
- **What this does not undermine:** PR AUC and the precision-recall curve are threshold-free, so the comparison against the pincode baseline stands as measured: 0.5753 against 0.0036, and recall 0.5556 at precision 1.0000 against a baseline that reaches no precision above 0.0044 at any recall.

---

### 2026-08-30 — The ring operating point does not transfer, and cannot be made to by reshaping the score

- **Chose:** keep `raw`, component size times density, `k^2 / n_p`. Test PR AUC **0.5811**, recall **0.5556 at precision 1.0000**. Record the non-transferring operating point as a limitation rather than continuing to engineer against it.
- **Rejected:** all four scale-invariant reformulations, after measuring each.

**What each cost.**

| score | what it is | outcome |
|---|---|---|
| `density` | `k / n_p` | Selected on train, then **cost PR AUC 0.5811 to 0.2047**. Dropping the `k^2` term lets a three-person household on a six-account pincode tie a real ring. Removed about a quarter of the drift, 2.03x to 1.52x. Cut still did not fire. |
| `lift` | `k / (n(n-1) p_pair)`, ratio to an analytic chance rate | Drifted **worse than raw and in the opposite direction**, 2.0e4 down to 3.4e3, because it over-rewards small pincodes. |
| `bg` | raw over the window's median cluster | Tied on train F1, transferred badly on train sub-windows, 0.15 against density's 0.68. |
| `q95` | raw over the window's 95th-percentile cluster | As `bg`, 0.37. |

**The diagnosis behind the whole attempt was wrong, and that is the finding.** The operating point does not fail because of scale. Measured per ring across window lengths, the pincode population is stable (r00 **7/8/8**, r02 **10/10/11**) and so is `k`. What breaks the threshold is that **`r01`, the ring that sets the train cut, is absent from the test window entirely**, having been caught before the split. The two rings that remain sit at 0.25 and 0.40 density. With three rings in the window, **between-ring variation dominates window length**. That is a sample-size problem, and no normalisation of `(k, n_p)` addresses it.

**The limitation, stated plainly.** Train selects a threshold of 1.9231. The test split's operating point is 1.6. The frozen cut flags nothing on test, so precision and recall read 0.0000 while PR AUC is 0.5811 and the curve reaches recall 0.5556 at precision 1.0000. **The ranking transfers; the cut does not.** We report the curve, never a frozen operating point. Fixing this needs more rings, not a better formula: with three, a threshold is being calibrated on a sample of three.

**Observation, not a result: `rank`.** The quantile-position form fires on test at **precision 1.0000 and recall 0.5556**, the only variant whose frozen cut fires at all. It is **not selectable**: its train F1 is 0.5714 against the 0.8095 tie, so it never reaches the tie-break on the split we are allowed to choose on. We know it looks good on test only because the stability table was in front of us while iterating candidates, so choosing it now would be selection on test and the number would not mean what it appears to mean. Recorded here so the temptation is visible rather than acted on, and so that anyone with more rings knows where to look first.

**One process change kept from the attempt.** Train F1 cannot distinguish `raw`, `bg`, `q95` and `rank`, because they are monotone transforms of each other inside a window and therefore produce identical rankings. Ties are now broken explicitly, on train-side transfer and then on the larger `min_pin_population`, the latter being a stated prior that a drop address serves more people than a household contains. Previously an arbitrary tie-break decided, which is how the worst-transferring mode came to be selected on the first pass.

---

### 2026-08-30 — Decision layer: three reversible tiers, boundaries solved from cost

- **Chose:** `MONITOR`, `STEP_UP`, `HOLD_REVIEW`. **`DECLINE` is not implemented**, so no irreversible action exists in the codebase rather than merely being discouraged. Every alert produces an `AlertRecord` and there is no code path that acts without one.
- **Why no decline:** the cited asymmetry. **"For every Rs 100 saved by preventing fraud, brands lose Rs 400-600 to falsely declined legitimate orders"** ([Razorpay](https://razorpay.com/blog/payment-success-rate-optimization-india/), verified 2026-08-28, spec section 3). At a 4-6x penalty an outright block is a bad trade at any precision we can reach, and deleting it from the action set is more reliable than trying to threshold it safely.
- **How the tier boundaries are set:** solved, not chosen. Expected cost of an action at fraud probability `p` is `p*cost_on_attack + (1-p)*cost_on_legitimate`, a straight line in `p` per action, so the boundaries are where the lines cross. `tier_boundaries()` scans for the crossings. They move with the cost parameters and with the order value: on a Rs 16.89 order `HOLD_REVIEW` is **never** optimal, because a Rs 120 review costs more than the exposure, and the record says so rather than hiding it.
- **The cited check FAILED, and it is recorded as a failure.** Built bottom up, the model implies **1.54x**, not 4-6x. The cause is structural: it prices the immediate order only, while the citation is the full economic cost of a false decline, dominated by the customer not returning. Solving the other way, the citation implies a false decline costs **2.6x to 3.9x** one order's margin, so 1.6 to 2.9 further orders of lost repeat business. That figure is **derived from the citation, not fitted to it**, and is not an input anywhere.
- **Why we did not add a churn parameter to close the gap:** choosing a churn multiple and then checking it against the citation would be fitting the model to its own test. The single-order model is a **lower bound** on false-positive cost, so every operating point it selects is if anything more aggressive than the citation justifies. Understating over-blocking is the safe direction to be wrong in, and it barely bites here because nothing declines.

---

### 2026-08-30 — Money-optimal operating points, and what freezing one costs

- **Chose:** report the money-optimal operating point beside the F1-optimal one for every detector, and keep both. No detector was retuned; only the operating point is chosen twice.
- **They do not coincide.** F1 is consistently too conservative, most severely for the volume baseline where the money-optimal threshold is less than half the F1 one, worth **Rs 27,644, or 27.18%**, on a single test split. For the other three the gap is 1.4% to 1.7%, so F1 is a decent proxy there and a poor one for volume.
- **The money-optimal threshold moves as the attacker evades, and it has to.** The mechanism is measured, not assumed: an evasive attacker works a better card list, so the share of attack attempts that authorise rises from **12.01% to 89.42%** across the sweep. Each missed attempt therefore costs far more, and the optimal threshold falls.
- **The price of not adapting**, a threshold frozen at the v=0.00 money optimum against one recomputed per grade, worst grade:

| detector | worst excess | as % |
|---|---|---|
| GRAPH fanout vs overlap | Rs 2,548 | **0.79%** |
| baseline 1 rolling volume | Rs 40,696 | 10.51% |
| baseline 3 combined | Rs 2,123,214 | 331.94% |
| baseline 2 rolling decline | Rs 2,032,544 | **433.24%** |

- **The graph detector is the only one whose threshold is worth freezing.** Its scores are bit-identical across the evasive grades, so its money-optimal cut barely moves and a frozen one costs under 1% at every grade. The decline baseline's cut is worth 433% of the achievable cost by the time the attacker is working a clean list. That is the earlier bit-identical-scores result restated as money, and it is the strongest form of the argument for the graph detector we have: not that it scores higher, but that **it is the only one you can set and leave**.
- **Total cost still rises for everyone** as the attack evades, because each miss costs more. At v=1.00 the graph runs at Rs 324,970 against the decline baseline's Rs 2,763,090, an 8.5x difference in money on the same data.

---

### 2026-08-30 — Streaming runtime rescans the window instead of updating counters

- **Chose:** hold the current window in a deque and call the **existing** batch scoring functions on it, taking the last element. Cost per event is O(window).
- **Rejected:** incremental counters updated per event, which would be O(1) amortised and considerably faster.
- **Why:** exactness is structural this way rather than something to chase. The detectors in `src/detector/` are already sliding-window, and they locate their window with `bisect_left(ts, t - window_s)`. If the deque holds precisely the events satisfying `ts >= t - window_s`, the window the function sees is identical in both paths, so the streaming score is not an approximation of the batch score, it **is** the same arithmetic on the same inputs. Measured: **0 mismatches on 67,961 events across all four detectors, max difference exactly 0**, and 3,890 alerts identical in the same order.
- **The rejected option is where the bugs live.** An incremental version means reimplementing every detector's update rule, which the task explicitly ruled out, and every counter is a place the two paths can drift apart silently. We would then be maintaining two implementations and testing one against the other, four days out.
- **What it costs:** 1,264 events/sec, 0.791 ms per event with all four detectors scored. Stated plainly as a limitation rather than presented as a design target. For this merchant, roughly 1.4 events/minute average and about 40/minute at burst peak, it is three orders of magnitude of headroom, so the constant factor buys nothing worth the risk.
- **One boundary detail that would have broken equality:** eviction is `ts < cutoff`, strictly less than. `bisect_left` includes an event sitting exactly on the window boundary, so evicting on `<=` would have dropped one event on exact timestamp ties and produced a small, rare, hard-to-find divergence. It is called out in the code because it is the kind of thing that looks like a rounding difference and is not.

---

### 2026-08-30 — One pipeline, and results.json is the only place numbers come from

- **Chose:** a single entry point, `make evaluate` / `python -m pipeline.evaluate`, that regenerates every dataset from seed, runs all eight acceptance tests at all six grades, all four detectors, the ring detector, the cost model and the streaming check, and writes `results/results.json`.
- **How "no number typed by hand" is enforced rather than promised:** `pipeline/cite.py` renders a document containing `{{key}}` placeholders against `results.json` and **raises on an unknown key**. A figure that does not exist in the artifact cannot be written, and a figure whose value moved is picked up on the next render. The rule is enforced by the renderer failing, not by anyone remembering it.
- **Split into two files, deliberately.** `results.json` holds only numbers and must be byte identical between runs. `run_meta.json` holds the git commit, seeds, config, package versions and per-stage timings, which change every run. Mixing them would have made byte-identity impossible to check, which is the same mistake as putting a timestamp in a build artifact.
- **Threads pinned to 1** (`OMP_NUM_THREADS` and friends, set before numpy or sklearn is imported, recorded in `run_meta.json`). Multi-threaded float reduction reorders summation and can move the last bits of a gradient-boosting score. This is a pipeline setting, not a model change, and it is what makes byte-identity achievable at all.
- **Parsers fail loudly.** Every stage's output is parsed by a strict parser that raises if a table moves or a heading is renamed, because a silent mis-parse would put a wrong number into `results.json` and from there into a document. Raw stage output is archived under `results/logs/` regardless.

---

### 2026-08-30 — The committed datasets were stale, and the pipeline is what surfaced it

- **What the pipeline found on its first run:** regenerating from seed produced `events.jsonl` with hash `cfcc11dd...`, against the committed `data/sample` at `b5e9e998...`. The datasets on disk were **not** what the current generator produces.
- **Why:** the household fix from earlier the same day. `population.py` was corrected so household members share a pincode as well as a device, and that fix was explicitly recorded as **not applied to any committed dataset** because regenerating would have invalidated the evasive sweep. The pipeline regenerates from source by construction, so it applies the fix.
- **What actually moved, measured rather than assumed:**
  - The **72 card-testing and evasive-sweep metrics are unchanged**, verified element by element against the pre-pipeline reference. Card testing never touches `shipping_pincode`, so a change to benign pincodes cannot reach it.
  - **T3 passes at every grade**, confirming the +0.18% pair-collision prediction made when the fix was written.
  - The **ring detector moves from 0.5811 to 0.5820** PR AUC. That is the useful result: the scoring-time counterfactual patch, applied because we would not regenerate, approximates the real generator fix to within 0.0009.
- **What this means going forward:** `results/` is now the source of truth and it is generated from the current source, so this class of drift cannot recur silently. The committed `data/` remains gitignored and is a cache, not a record.

---

### 2026-08-30 — Memory profile split out of the main pipeline run

- **Chose:** `make memory-profile` as its own target, writing `results/memory_profile.json`. The streaming **equivalence** check stays in `make evaluate`.
- **Why:** the profile re-streams the file five times, at 2,000 / 8,000 / 20,000 / 40,000 / 67,961 events. That is **137,961 of the 225,922 events** the streaming stage touched, 61% of the stage and roughly 40% of the whole pipeline, spent re-demonstrating that peak memory tracks the event rate and not the stream length. That is a property of the design; it does not change between runs.
- **What stays and why:** equivalence is a **correctness** check. It asserts the streaming path reproduces batch scores exactly, which can break with any change to a detector or the window logic, so it runs every time.
- **Expected effect, estimated not measured:** the stage keeps 87,961 of 225,922 events, **38.9%** of its current work. On the clean 53.8 min run the stage was 21.5 min, so about 8 to 10 min, giving roughly **40 to 43 min total, about 20 to 25% faster**. Under load it was 51.2 min of 112.3, so roughly **80 to 85 min**. The saving is estimated slightly conservatively because the profile re-streams from the file start each time, where windows are smaller and the per-event cost is at or below average. Not confirmed by a run.
- **`results.json` is identical apart from the moved key**, confirmed against the archived stage log rather than by a fresh run: parsing it with and without the memory section gives `['memory']` as the only key that differs and every other key byte identical. The committed artifact was migrated with `make from-logs`, which relocates the measurement without recomputing anything.

---

### 2026-08-30 — The household fix is now measured, not estimated

- **What changed:** the fix is still not applied to any committed dataset, but the pipeline regenerates from seed, so its cost is now a measurement instead of a prediction.
- **The drift the pipeline surfaced:** regenerating gives `events.jsonl` at `cfcc11dd...` against the committed `data/sample` at `b5e9e998...`. The committed data predates the fix, exactly as recorded when we chose not to regenerate.
- **Measured cost, against what was predicted:**
  - **72 metrics unchanged.** Predicted, because card testing never reads `shipping_pincode`.
  - **T3 passes at every one of the six grades.** Predicted at +0.18% on the pair-collision target, well inside the +/-20% band.
  - **Ring moves 0.5811 to 0.5820** PR AUC.
- **The useful number is the last one.** The scoring-time counterfactual patch, adopted because regenerating would have cost the evasive sweep, **approximated the real generator fix to within 0.0009**. The decision to patch rather than regenerate is now supported by a measurement rather than an argument.

---

### 2026-08-30 — Household story closed, and the counterfactual kept as a guard

- **Status:** resolved. The two earlier entries stay as the record and are marked resolved in place.
- **What the code says now:** `population.py` no longer claims the fix is unapplied, because it is applied. `evaluate_ring.py` no longer explains a defect the scored data does not have; it states plainly that **both columns are now the same population** and that the delta should read `+0.0000` throughout.
- **Why the two-column comparison stays:** as a **regression guard**, not an active correction. If the generator ever loses the shared household address again, the delta stops being zero. That is the cheapest alarm available for a defect whose only symptom last time was a detector quietly beating its own oracle by more than double, which no test in the suite was looking for.
- **Also corrected:** the printed oracle-ceiling line hardcoded "the 40% device sharing rate" as though 40% were a constant. It is an **observed outcome** of `RING_DEVICE_SUBSET = (0.30, 0.60)`, drawn per ring. The line now cites the range from config and labels 40% as observed in the run that first measured the ceiling.
- **Also moved into the spec:** the assigned-versus-observed correction for `DEVICE_SHARE_RATE` (0.072) and `CONTACT_SHARE_RATE` (0.025). Both are calibration constants chosen so the **observed** rate lands on section 4's 6% and 1.5%, because a collision is only visible when both members transact and only ~62% of actors do. That reasoning lived only in code comments, so a reader comparing spec to config saw an unexplained mismatch. It is now in section 4 beside the targets, with the rule that both must be re-derived if the window, actor count or purchase rates change.
- **Resolved 2026-08-30:** the schema and the generator spec are now published at `docs/event-schema.md` and `docs/generator-spec.md` and are tracked. The competitor scan and the buildathon context note stay private in `notes/` permanently.

---

### 2026-08-30 — Latent traps closed, and one deviation recorded rather than removed

- **`vpa` target now references the constant.** `runner.py` computed `contact_rate * 0.92 ** 2 * 0.72` with `0.92` as a literal, while spec 4 states it recorded that dependency specifically so a change to `VPA_FROM_PHONE_SHARE` could not move the generator without moving the test target. It now reads `C.VPA_FROM_PHONE_SHARE`. **Bit-identical today**, 0.60940800000000006076 either way, and T3 re-run in isolation reproduces every line including the `0.777-1.166%` band.
- **The third factor stays a literal, and that is correct.** 0.72 is a **measured emergent statistic**, not a declared parameter: 74.4% of transacting accounts make at least one UPI payment in a 30-day window, retention 0.718. Nothing in config equals it, so there is nothing to reference. It is now named `UPI_RETENTION` with its dependency on `METHOD_MIX["upi"]` and `ACTOR_CLASSES` written at the site, so a change to either prompts a re-measurement instead of being carried over silently.
- **T3's `email` ratio leg: recorded, not changed.** Its §4 unit is top-3 domain share, a concentration measure, so a ratio of it would sit near 1.0 whatever the attack did and would test nothing. Pair collision on the domain is the informative measure and is what the code uses. **Chose to document the exception in spec T3** rather than make the code match a rule that produces a useless statistic. The band leg still uses top-3 share, so the §4 target is untouched, and the other five attributes use their own unit in both legs.
- **Dead code removed.** `_p_benign_row_unique` had no caller. It was superseded when `_simulate_identity` began scoring simulated frequency directly; row weighting now comes from building the benign sample one entry per row rather than from a closed form. `what-broke.md` named that function as the fix, so it described something that did not run. The entry is corrected in place: the finding and its numbers stand, the mechanism description was wrong. All five affected predictors verified unchanged against the last run: `contact` 0.9223, `card.last4` 0.8728, `vpa` 0.7741, `mc.device_id` 0.9990.
- **Ring constants written down.** All nine now appear in spec 2.2 with their values and tags. §2.2 stays brief and no reasoning was invented; the table records what the code does. `RING_DEVICE_SUBSET` is flagged as a **range drawn per ring**, with 40% noted as the observed value in the run that set the T8 ceiling rather than as the parameter.

