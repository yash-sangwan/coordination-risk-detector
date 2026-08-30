# Generator Spec — Coordination Detector Stream

**Status:** spec only. No code, no data generated.
**Produces:** the record defined in [event-schema.md](event-schema.md), plus a separate sealed outcome store.
**Goal:** someone else could build this and get comparable data.

---

## 0. Disagreements with the schema, raised now

The instruction was to say these rather than work around them quietly. Five things.

1. **`acquirer_data.auth_code` should be cut.** The schema keeps it "for fidelity, flagged as redundant". It is worse than redundant: `auth_code` is non-null exactly when the attempt is approved, so it is a perfect proxy for `status`. Any model gets `status` twice, which inflates apparent feature importance and makes the single-feature acceptance test harder to read. Recommend cutting the field.

2. **`error_description` should be cut or frozen.** Free text is where an accidental tell creeps in most easily: if the generator templates descriptions and fraud draws from even a slightly different template pool, the string is the label. It carries no information that `error_code` + `error_reason` do not.

3. **`ip_prefix` is rated Medium and should be Weak, possibly cut.** Jio, Airtel, BSNL and ACT all place subscribers behind carrier-grade NAT, and mobile broadband in India is almost always CGNAT'd ([A10 Networks](https://www.a10networks.com/glossary/what-is-carrier-grade-nat-cgn-cgnat/), [PureVPN ISP list](https://www.purevpn.com/blog/top-isps-using-cgnat/)). Two unrelated Indian mobile users sharing a /24 is unremarkable. Worse, I could find **no citable subscribers-per-address figure**, so its benign collision rate would be a number we invented for the attribute that needs it most. See §6.

4. **`user_agent_hash` duplicates `device_id`.** They are near-collinear by construction. Carrying both means calibrating two correlated benign rates instead of one.

5. **The `contact` normalisation inconsistency needs a constraint the schema does not state.** Carrying the `+91…` vs bare-`9…` inconsistency is right, but normalisation must be drawn **independently of the label**. If fraud events are more often bare-format, format is the label. This belongs in the acceptance tests.

Everything else in the schema I agree with, in particular the as-of-attempt rule and the two-store split, which are what make the rest of this checkable.

---

## 1. Legitimate traffic

### 1.1 The actor model

Traffic is generated from **actors**, not from rows. An actor has persistent attributes (a device, a card or two, a home pincode, an email, a phone, an account age) and a behavioural rate. Rows are the observable consequence of actors acting.

This ordering matters for anti-planting: **the label is never an input to field generation.** An actor's parameters are drawn, the actor behaves, and the label is a *description* of what the actor did. Nothing in the row is filled in "because this is fraud".

Population mix (assumption, see §3):

| Actor class | Share of accounts | Behaviour |
|---|---|---|
| Returning customer | 55% | 1–4 purchases/month, saved instrument, fast checkout |
| Occasional customer | 35% | 0–1 purchases/month, slower checkout |
| New customer | 10% | First purchase, slowest checkout, `account_age_days` near 0 |

### 1.2 Arrival pattern

Three multiplicative components over a base rate λ.

**Time of day** — a bimodal shape with a lunch bump and a dominant evening peak. Peak 19:00–22:00 IST. The evening peak is not a decoration: Razorpay reports success rates drop **8–12 percentage points during 7–10 PM** from bank-side load ([Razorpay](https://razorpay.com/blog/payment-success-rate-optimization-india/)), so the busiest hour is also the hour with the most legitimate declines. This coupling is the single most important realism detail in the whole spec, because it is what stops "high volume + high decline rate" from being a free fraud signal.

Suggested hourly weights (assumption, shape anchored on the cited peak window):

```
00-05  0.15   06-08  0.45   09-11  0.80   12-14  1.05
15-17  0.85   18-19  1.35   20-21  1.80   22-23  0.90
```

**Day of week** — Sat/Sun at 1.25× weekdays, Monday lowest at 0.9×.

**Payday** — a bump on the 1st–3rd and 25th–31st of each month at 1.3×, reflecting Indian salary-credit timing. Assumption.

**Flash sales** — 2 to 4 per simulated month, each 60–180 minutes, 6–15× base rate, announced in advance in the sense that they are scheduled by the generator and recorded in the sealed store. Flash sales exist for one reason: **they are the legitimate event that most resembles a card-testing burst.** A generator without them makes burst detection trivial and the resulting recall meaningless.

### 1.3 Method mix

Anchored on Indian e-commerce reality, with UPI dominant. Assumption for exact shares.

| Method | Share |
|---|---|
| `upi` | 55% |
| `card` | 28% (of which ~60% debit, 40% credit) |
| `netbanking` | 9% |
| `wallet` | 6% |
| `emi` | 2% |

`international: true` on ~2% of card attempts.

### 1.4 Amount distribution

Log-normal, median ≈ ₹850 (85000 paise), with a long right tail to ₹50,000 and a deliberate **low-value shoulder**: **10%** of legitimate attempts below ₹100, of which a third are below ₹50. Digital goods, top-ups, recharges and small UPI transfers genuinely live there.

**This figure was raised from 4% to 10% by the §7 reconciliation.** At 4%, with attack amounts concentrated in the same band, `amount` alone breaches the T1 ceiling. See §7.2.

That shoulder is load-bearing. Card testing concentrates at the bottom of the amount range, so if the only sub-₹50 traffic is the attack, an amount threshold alone solves the task. Legitimate micro-transactions must exist.

Round-number bias: ~30% of legitimate amounts land on ₹99/₹199/₹499-style price points.

### 1.5 Decline rate

Legitimate payments fail constantly, and per method:

| Method | Success | Source |
|---|---|---|
| UPI | ~99.2% (technical decline ~0.8%) | [Razorpay](https://razorpay.com/blog/payment-success-rate-optimization-india/); NPCI publishes per-bank technical decline monthly |
| Cards (domestic) | 85–90% | Razorpay, same page |
| Netbanking | 90–95% | Razorpay, same page |
| International cards | 70–80% | Razorpay, same page |

Overall D2C success sits at **68–74%** against an achievable 85%+, so a realistic blended failure rate is roughly **1 in 4 legitimate attempts** once method mix and geography are folded in.

Geographic modulation, cited from the same page: metro 78–82%, tier-2 62–68%, tier-3 55–62%. Assign each actor a tier from their pincode.

Failure reason mix for legitimate declines (assumption for the split; the categories are Razorpay's):

| `error_reason` class | Share of legit failures |
|---|---|
| insufficient funds | 30% |
| incorrect PIN / auth failure | 22% |
| `payment_cancelled` (user abandonment) | 20% |
| bank/gateway timeout | 18% |
| card expired / invalid | 10% |

Note `payment_cancelled` and `input_validation_failed` were both observed live in the probe.

**Retry behaviour.** Automated retries recover 15–20% of failed transactions ([Razorpay](https://razorpay.com/blog/payment-success-rate-optimization-india/)). So a legitimate failed attempt is followed by a retry ~35% of the time, within 30–180 seconds, same `order_id`, incrementing `attempt_seq`. This is why `attempt_seq > 1` cannot be a fraud signal on its own.

**Downtime windows.** The probe found 11 real downtime records, all `severity: high`, spanning `card`, `netbanking`, `upi` and `fpx`. Model 1–3 downtime windows per simulated month affecting one method or issuer, during which that method's decline rate rises 5–10× and `error_source` shifts toward `gateway`. This produces correlated decline spikes with no fraud in them, which is exactly the confounder a burst detector must survive.

---

## 2. Attack patterns

### 2.1 Card testing burst — the priority

**How it starts.** A bot operator acquires a list of stolen card numbers, typically sharing an issuer range. The attack appears with no ramp: from ~0 to full rate within one or two minutes. There is no preceding browsing behaviour, because there is no browsing.

**How long it runs.** Two nested timescales, and both matter:

- *Burst:* 10–90 minutes of sustained attempts at 20–200 attempts/minute.
- *Campaign:* bursts recur over days or weeks. The citable anchor for campaign shape is an airline whose carding attempts climbed from **under 1% of transactions early in 2025 to over 8% at peak, then fell back below 1% by year end** ([Chargebacks911](https://chargebacks911.com/ecommerce-fraud/card-testing/card-testing-statistics-financial-impact/)). Slow rise, sustained plateau, decline.

Volume sanity check: Visa's VAMP rules flag a merchant as "Excessive" above **300,000 enumeration attempts per month** at a **20%+ enumeration ratio**, effective 1 October 2025 ([Chargebacks911](https://chargebacks911.com/ecommerce-fraud/card-testing/card-testing-statistics-financial-impact/)). Our merchant is far smaller, so bursts should sit well below that, but the ratio is the useful part: enumeration as a fraction of total attempts is the industry's own metric.

**What it shares.**

| Attribute | Sharing behaviour |
|---|---|
| `card.iin` | 1–3 IINs per burst. The BIN walk is the defining feature. |
| `device_id` | 1–5 device fingerprints across the whole burst. |
| `checkout_ms` | Low but **not degenerate**: 150–2500ms, heavy mode near 300ms, with a jittered tail because real bot frameworks add randomised delays. Widened by the §7 reconciliation. |
| `amount` | Mixture, **not** a single low band: 55% micro (₹1–50), 30% low (₹50–500), 15% drawn from the *legitimate* amount distribution as deliberate blending. Widened by the §7 reconciliation. |
| `error_reason` | Concentrated in a CVV/expiry-class reason, unlike the broad legitimate mix. |
| `attempt_seq` | Frequently 1, because each attempt is a fresh session. |

**What it does not share.** This is the half that generators usually get wrong.

- `card.last4` — every attempt is a *different* card. Same IIN, different PAN.
- `email`, `contact` — freshly generated per attempt, no reuse.
- `account_id` — usually `null`. Card testing is guest checkout.
- `shipping_pincode` — usually `null`. Nothing is being shipped.
- `session_id` — new per attempt.

So a card-testing burst is a **high-fanout, low-overlap** structure: many distinct identities, converging on very few instruments and devices. A ring is the opposite shape, which is why one detector cannot naively serve both.

**How it ends.** Three endings, and the generator should produce all three in proportion, because a detector that only ever sees one will overfit to it:
1. Card list exhausted — attempts stop abruptly (~50%).
2. Merchant or acquirer blocks the IIN or device — attempts continue briefly at a rising decline rate, then stop (~35%).
3. Operator moves on — rate decays over 10–20 minutes (~15%).

### 2.1e Evasive card testing — a detector robustness test (added 2026-08-29)

**Purpose, stated first because it governs everything below.** This variant is a
**detector robustness test**. It exists to measure *where a decline-rate baseline
fails*. It is a **test fixture, not an attack tool**. The evasion it models is
**standard published knowledge** about card testing rather than anything novel,
and Track 02 is defence only, so this stays firmly on the defensive side of that
line: the output is a labelled synthetic stream in our own schema, scored against
our own detectors. Nothing here issues a request at any real system, and nothing
here tells an operator something they do not already know.

The motivation is empirical. On `data/sample` the rolling decline-rate baseline
reached PR AUC 0.9583 and fired at attempt 3, beating the graph detector on every
metric. The reason is that our card testing declines at **100% from attempt 1**,
so the decline rate is saturated before any structure has accumulated. That makes
the whole comparison a measurement of one attack model rather than of the two
detectors. This variant is how we find out which.

**What changes: the decline rate. What does not: everything else.**

Coordination is held fixed by construction, not by assertion. Still 1–3 IINs,
still 1–5 devices, still throwaway identities fresh per attempt, still high
fanout and low overlap. The sweep table in `src/generator/sweep.py` measures the
coordination structure at every step and reports the largest spread on any of
those measures, so "unchanged" is a checked claim.

#### The mechanism, and why this one

Three mechanisms were on the table. Only the first is used.

**1. Mixing in cards known to be valid. CHOSEN.**

Stolen card lists are sold **graded** by how many of their numbers are still
live. A "checked" list costs more than a fresh unchecked one precisely because
its authorisation rate is higher. An operator working a high-grade list observes
a low decline rate for no other reason than that the list is good.

The published incentive to prefer that is already cited in §2.1: Visa's VAMP
rules flag a merchant above **300,000 enumeration attempts per month at a 20%+
enumeration ratio** ([Chargebacks911](https://chargebacks911.com/ecommerce-fraud/card-testing/card-testing-statistics-financial-impact/)).
The scheme's own metric is a **ratio**, not a count. A ratio threshold is a
standing, publicly documented reason to keep an observed decline rate down, and
buying a better list is the obvious way to do it.

Critically, this mechanism is the only one of the three that **actually moves the
decline rate**, and it moves it **without touching coordination at all**. Each
attempt still uses a fresh PAN behind the same IIN, a fresh identity, and one of
the burst's few devices. Only `status` and `error_*` differ.

An alternative reading of the same mechanism — the operator keeps a small pool of
known-good cards and re-uses them to dilute the ratio — was **rejected**, because
a small pool repeats, which creates overlap on `card.last4` and changes the
coordination structure. List grade does not have that problem.

**2. Pacing attempts so the rolling window rate stays under threshold.
IMPLEMENTED, DELIBERATELY NOT SWEPT.**

Real operators do throttle, so this is not unrealistic. It is excluded from the
sweep for a measurement reason: **throttling changes events per minute, not the
decline rate.** Folding it into the same axis would confound the curve the sweep
is meant to produce, and the volume baseline already exists to be measured
against it separately. `EVASIVE_RATE_SCALE` and `--evasive-rate-scale` are wired
up and default to no throttling, so a pacing sweep is one flag away whenever we
want it as its own experiment.

There is a second reason to keep it separate, and it is the more important one.
If pacing were tuned until the volume baseline also failed, the fixture would be
engineered so that only a graph detector could win. That is the exact move we
have agreed not to make. This fixture isolates **one** failure mode, and the
honest expectation is that **the volume baseline still catches the evasive
variant**. That is the correct outcome, not a shortfall.

**3. Spreading attempts across a longer span at lower rate. NOT USED.**

At campaign scale this is mechanism 2 again, so it inherits the same confound.
It also fights the economics: stolen card data has a short shelf life, since
numbers get reported and cancelled, so an operator has a real incentive to turn a
list around quickly rather than stretch it over weeks (ASSUMPTION — the
directional argument is standard, but we have no primary source for a decay
rate, so no number is claimed).

#### The floor, which is a real limit and not a tuning choice

`EVASIVE_VALID_DECLINE` is **derived, not picked**. A freshly validated card
charged a micro amount cannot reach every reason in the legitimate
`DECLINE_REASONS` mix. Three of the five are closed to it:

| Reason | Weight | Reachable for a validated card at ₹20? |
|---|---|---|
| `insufficient_funds` | 0.30 | No. A live card is not short of ₹20. |
| `card_expired` | 0.10 | No. A validated card is not expired. |
| `payment_cancelled` | 0.20 | No. A bot does not abandon at the bank page. |
| `incorrect_pin` | 0.22 | Yes. Ordinary authentication failure. |
| `gateway_timeout` | 0.18 | Yes. Infrastructure, hits everyone. |

So the validated slice declines at `METHOD_DECLINE["card"] × (0.22 + 0.18)`
= `0.125 × 0.40` = **5.0%**, and its failures draw from the two reachable
reasons renormalised, because a live card that fails, fails for live-card
reasons.

Two consequences worth stating plainly:

- The sweep **cannot** be asked to reach an arbitrary target. 5.0% is the floor
  this mechanism permits and it is where the curve stops, so a request for
  "near the legitimate 5.6%" is answerable only by accident, not by tuning.
- 5.0% is **below** the 12.97% ambient card decline rate. That is not a bug:
  micro amounts on live cards authorise more reliably than the general
  population of card attempts, which includes large purchases hitting limits. It
  does mean an *unusually low* decline rate becomes a signal in the other
  direction, which is a detector question and not a generator one.

**Correction, 2026-08-29, after measuring.** 5.0% is the floor of the *list
grade mechanism*, and it is **not** the floor of the observed decline rate. The
generated stream bottoms out at **10.58%**, not 5.0%. The claim above was
written from the mechanism alone and did not survive contact with the generator.

The cause is the **blocked ending**. 35% of bursts end with the issuer or
merchant blocking the IIN, and that ramps the decline rate to
`ATTACK_DECLINE_BLOCKED` (99%) over the burst's last 25% *regardless of list
grade*, because a block is issuer-side and does not care how good the list is.
That contributes a roughly fixed additive `0.35 x 0.25 x 0.5 x (0.99 - base)`,
which is invisible against an 88% base and dominates the residual against a 5%
one. Measured gap between declared and observed:

| grade | declared | observed | gap |
|---|---|---|---|
| 0.00 | 88.00% | 87.99% | -0.01 pp |
| 0.25 | 67.25% | 68.59% | +1.34 pp |
| 0.50 | 46.50% | 49.10% | +2.60 pp |
| 0.75 | 25.75% | 30.26% | +4.51 pp |
| 0.90 | 13.30% | 19.05% | +5.75 pp |
| 1.00 | 5.00% | 10.58% | +5.58 pp |

This is kept rather than removed, and it is a **property of the attack, not a
limit of the fixture**: an operator cannot buy their way below the rate at which
their own IINs get blocked. `evasive_decline()` therefore describes the
mechanism's contribution and is **not** a prediction of the observed rate. The
sweep reports both columns side by side so the two are never confused.

#### The sweep

`valid_list_share` ∈ `{0.00, 0.25, 0.50, 0.75, 0.90, 1.00}`, giving observed
decline rates of **88.0%, 67.3%, 46.5%, 25.8%, 13.3%, 5.0%**. Step `v = 0.00`
reproduces the ordinary burst **byte for byte**, RNG sequence included, and is
the control.

In evasive mode the decline reason is drawn on **every** attempt rather than only
on failures, so the RNG sequence is identical at every non-zero step. Each step
therefore carries byte-identical identities, devices, amounts and timestamps and
differs **only** in `status` and `error_*`. The sweep is a single-variable axis in
the strictest available sense. (At `v = 0.00` the draw is skipped, which is what
preserves byte-identity with the existing `data/sample`.)

An issuer block still ramps to `ATTACK_DECLINE_BLOCKED` regardless of list grade,
because a block is issuer-side: evasion buys nothing once the IIN is blocked.

#### Effect on the acceptance tests

T1a's `status` and `error_*` mechanisms are computed from
`ATTACK_DECLINE_BASE`. Left alone they would remain pinned at the 88% prediction
while the observed AUC collapsed, and the tests would pass *trivially* rather
than meaningfully. The predictors now read the run's **declared** list grade from
the manifest and predict accordingly, including the split of attack failures
between the two reason tables. That stays non-circular: the grade is a run
parameter, never an observed rate.

T7 replays the generator from manifest parameters, so the list grade is now
replayed with the rest of them. A run parameter that is not replayed is a
different run.

#### What the suite actually returned, 2026-08-29

T1 and T8 fail identically at **every** step of the sweep **including `v=0.00`**,
which is byte-identical to `data/sample`. Both are the two already-recorded open
items and neither is caused by the variant: `email` over its mechanism by +0.053
to +0.058, and ring account-level recall at 0.4400 against a 0.60 floor. Card
testing's own T8 floor **passes at every grade** (recall@P0.80 of 1.0000 down to
0.9955), so the oracle's card-testing ceiling does not depend on the decline
rate. T3 through T7 pass everywhere.

The one step-specific failure was **T2 at `v=0.50`**, median 0.4615 against a
0.03 tolerance. It is the **test**, not the data. Holding the dataset fixed and
varying only the permutation seed moves the median from 0.4615 to 0.5397, a
spread of **0.0782** against a threshold of **0.0300** on the same statistic, and
**3 of 5 seeds fail on identical data**, on both sides of 0.50. There is also no
trend across the sweep: `v=0.50` sits between two passing neighbours.

T2 at 50 permutations cannot resolve the difference it is asked to test.
**Nothing was changed in response.** The threshold stands, the failure is
reported as a failure, and the fix, roughly 350 permutations, is a cost decision
to take deliberately rather than one to slip into a test run.

---

### 2.2 Ring — briefer, as agreed

**How it starts.** A group of 3–15 accounts, created over days or weeks rather than minutes, behaving normally at first. Rings have a dormancy period; card testing does not.

**How long it runs.** Weeks. Activity is low-rate and interleaved with legitimate traffic, never bursty.

**What it shares.** `shipping_pincode` (drop address), sometimes `device_id` across a subset, occasionally `contact` reuse from carelessness, and email local-part *shape* rather than domain.

**What it does not share.** `card.iin` — ring members deliberately use varied instruments. `account_id` is distinct by design; that is the entire point of a ring. `session_id` never shared.

**How it ends.** Rings do not end on their own. They end when detected, or they go dormant and resume. The generator should include at least one ring that is never caught within the simulated window, so recall is not accidentally 100% achievable.

**The constants, recorded.** §2.2 is deliberately brief, but these shape every ring number we report, so they belong in the document rather than only in `config.py`. Values as implemented; no reasoning is added here beyond what the tags say.

| Constant | Value | Status |
|---|---|---|
| `RING_SIZE` | 3–15 accounts | **[spec]** stated above |
| `RING_COUNT` | 3–5 rings per window | **[assumption]** §2.2 gives no count |
| `RING_SIGNUP_SPREAD_DAYS` | 5–25 days | **[assumption]** implements "created over days or weeks" |
| `RING_DORMANCY_DAYS` | 5–20 days | **[assumption]** implements the stated dormancy period |
| `RING_DEVICE_SUBSET` | 0.30–0.60 of members share the device | **[assumption]** implements "sometimes `device_id` across a subset". Drawn per ring, so the realised rate varies; it was **observed at 40%** in the run that set the T8 ring ceiling |
| `RING_CONTACT_REUSE_PROB` | 0.25 per ring, one pair | **[assumption]** implements "occasional `contact` reuse from carelessness" |
| `RING_SESSIONS_PER_DAY` | 0.15–0.45 per member | **[assumption]** implements "low-rate, never bursty" |
| `RING_CAUGHT_PROB` | 0.70 | **[assumption]** §2.2 requires only that *at least one* ring is never caught |
| `RING_CAUGHT_AFTER_DAYS` | 4–16 days after activation | **[assumption]** |

The drop pincode is drawn **unweighted** over the pincode list, unlike a customer's home pincode which is traffic-weighted. That is a decision with its own record: see `docs/report/decisions.md`, 2026-08-28.

---

## 3. Base rates, with sources

> **Citations verified 2026-08-28.** Both sources in this section were checked against their primary text, not against a summary.
>
> - **arXiv 2604.13125 resolves to a real paper** and says what was claimed, in fact more strongly. Details in the methodological note at the end of this section.
> - **All eight Razorpay figures were confirmed verbatim** in the raw page text. One caveat worth recording: the string "8–12 percentage points" appears in that article **twice with two different meanings** — once for what optimization can *recover*, and once for the evening-peak drop. The figure cited here is the second: *"Payment success rates drop 8–12 percentage points during evening peaks (7–10 PM) when multiple banks experience load-related slowdowns."* Anyone re-checking this citation will hit the other occurrence first.
> - **The UPI technical decline figure now cites NPCI directly** rather than the Razorpay blog, since NPCI is the primary publisher. See the table row.
>
> A bonus figure found during verification, directly relevant to the Track 02 false-positive-cost requirement and not yet used anywhere: *"For every ₹100 saved by preventing fraud, brands lose ₹400–600 to falsely declined legitimate orders"* ([Razorpay](https://razorpay.com/blog/payment-success-rate-optimization-india/)). That is a citable 4–6x asymmetry against over-blocking.

Marked **[cited]** or **[assumption]**. No assumption is dressed as a finding.

| Quantity | Value | Status |
|---|---|---|
| UPI technical decline | ~0.8%, NPCI ceiling <1% | **[cited, primary]** [NPCI Declined (BD/TD) & Uptime](https://www.npci.org.in/statistics/bd-td-and-uptime) publishes bank-wise TD monthly. NPCI circular OC-149 (June 2022) sets TD <1%, BD <5%. |
| Card success (domestic) | 85–90% | **[cited]** Razorpay, same page |
| Netbanking success | 90–95% | **[cited]** Razorpay, same page |
| International card success | 70–80% | **[cited]** Razorpay, same page |
| Overall D2C success | 68–74% | **[cited]** Razorpay, same page |
| Evening peak success drop (19:00–22:00) | 8–12 pp | **[cited]** Razorpay, same page |
| Metro / T2 / T3 success | 78–82 / 62–68 / 55–62% | **[cited]** Razorpay, same page |
| Retry recovery of failed txns | 15–20% | **[cited]** Razorpay, same page |
| Merchants experiencing card testing | 33% globally | **[cited]** [Chargebacks911](https://chargebacks911.com/ecommerce-fraud/card-testing/card-testing-statistics-financial-impact/) |
| Carding as share of one merchant's traffic | <1% → >8% → <1% over a year | **[cited]** Chargebacks911, airline case |
| Visa VAMP enumeration threshold | 300,000/month, 20% ratio, from 1 Oct 2025 | **[cited]** Chargebacks911 |
| Reference fraud prevalence | 3.5% | **[cited]** IEEE-CIS, 590,540 txns ([Kaggle](https://www.kaggle.com/competitions/ieee-fraud-detection/data)) |
| CGNAT prevalence, Indian ISPs | near-universal on mobile (Jio, Airtel, BSNL, ACT) | **[cited]** [A10](https://www.a10networks.com/glossary/what-is-carrier-grade-nat-cgn-cgnat/), [PureVPN](https://www.purevpn.com/blog/top-isps-using-cgnat/) |
| **Attack-event prevalence in our stream** | **2.5% of attempts** | **[assumption]** Chosen to sit near IEEE-CIS's 3.5% while reflecting that our stream is attempt-level, so declines inflate the denominator. |
| **Card testing / ring split** | **80% / 20% of attack events** | **[assumption]** Reflects that card testing is far higher volume per incident. |
| **Actor population mix** | 55 / 35 / 10 | **[assumption]** |
| **Method mix** | 55/28/9/6/2 | **[assumption]**, directionally anchored on UPI dominance |
| **Median legitimate amount** | ₹850 | **[assumption]** |
| **Sub-₹50 legitimate share** | 4% | **[assumption]**, deliberately non-zero, see §1.4 |
| **Legit failure reason split** | 30/22/20/18/10 | **[assumption]**; categories are Razorpay's, proportions are ours |
| **Payday bump** | 1.3× | **[assumption]** |
| **Weekend uplift** | 1.25× | **[assumption]** |
| **Subscribers per shared /24** | — | **[no source found]** — this is the gap that motivates cutting `ip_prefix`, §6 |

One methodological note, **citation verified 2026-08-28**. [arXiv 2604.13125](https://arxiv.org/abs/2604.13125), *Synthetic Tabular Generators Fail to Preserve Behavioral Fraud Patterns: A Benchmark on Temporal, Velocity, and Multi-Account Signals* (Bhavana Sajja, 13 April 2026, cs.LG), supports this design more directly than first cited. It defines a taxonomy of four behavioural fraud patterns covering inter-event timing, burst structure, multi-account graph motifs and velocity-rule trigger rates, and benchmarks CTGAN, TVAE, GaussianCopula and TabularARGN on IEEE-CIS and the Amazon Fraud Dataset. All four fail badly: composite degradation ratios of 24.4x (TVAE) to 39.0x (GaussianCopula) on IEEE-CIS.

Two results matter for us specifically. **Proposition 1** proves row-independent generators are *structurally* incapable of reproducing multi-account graph motifs, and **Proposition 2** shows they produce non-positive within-entity inter-event-time autocorrelation, making the positive burst fingerprint unachievable regardless of architecture or training data volume. Those are exactly our two attack types: rings are graph motifs, card testing is a burst. This is a proof that the off-the-shelf path cannot work here, not merely evidence that it performs poorly, and it is the strongest single justification for the actor-level causal generator specified in §1.1.

---

## 4. Benign collisions

This is the section that stops the generator planting the answer. **Every rate below is measured over label-0 events only, and every one must be non-zero.**

The principle: if attribute *A* collides only among attackers, then "shares *A*" **is** the label and the detector is reading the generator. Each attribute therefore needs an innocent mechanism producing collisions at a stated rate.

| Attribute | Benign collision mechanism | Target rate | Status |
|---|---|---|---|
| `card.iin` | An IIN is an issuer range covering millions of cards. Any two customers of the same bank on the same product share one. With ~10 dominant Indian issuers over our card traffic, IIN collision between two random legitimate card attempts is **common by construction, not by accident**. | **8–15%** of legitimate card-attempt pairs share an IIN | **[assumption]** on the exact figure; the mechanism is arithmetic, not guesswork |
| `device_id` | Households share a phone or laptop. Shared family accounts, one device used by a couple or by parent and child. | **6%** of legitimate accounts share a device with at least one other account | **[assumption]** — no citable household device-sharing rate found for Indian e-commerce |
| `shipping_pincode` | India has roughly 19,000 pincodes and a highly skewed population distribution. Urban pincodes carry very high traffic; a hostel, apartment block or office shares one. | Top-50 pincodes carry **~25%** of legitimate orders. Pair collision is then **0.147%**, the analytic value that falls out of that shape. | **[assumption]** on the concentration curve; the collision rate is **derived from it, not chosen** |
| `email` domain | Gmail dominance means domain equality is nearly uninformative. | **~70%** of legitimate emails on the top-3 domains | **[assumption]** |
| `email` local-part shape | Real people also use `firstname.lastname1994@`. Shape classes must overlap between populations. | No shape class may be **>85% pure** for either label | **[assumption]**, expressed as a purity ceiling rather than a rate |
| `contact` | Genuine phone reuse: a shared family number used on two accounts, or a customer with two accounts. Rare but non-zero. | **1.5%** of legitimate accounts share a phone | **[assumption]** |

> **Assigned rate against observed rate, added 2026-08-30.** The two account-level rows above, `device_id` at 6% and `contact` at 1.5%, are **targets for the OBSERVED rate in the generated stream**. The constants that produce them are deliberately higher, and anyone comparing the spec to `config.py` will otherwise read a mismatch:
>
> | attribute | §4 target, observed | constant in config | ratio |
> |---|---|---|---|
> | `device_id` | 6% of accounts | `DEVICE_SHARE_RATE = 0.072` | 1.20x |
> | `contact` | 1.5% of accounts | `CONTACT_SHARE_RATE = 0.025` | 1.67x |
>
> **Why they differ.** Both collisions are assigned over the whole actor population at generation time, but a collision is only *observable* when **both** members of the pair actually transact inside the window. Only about 62% of actors do. A pair therefore survives into the stream at roughly `0.62^2`, and the assigned rate has to be raised to compensate. The two attributes need different corrections because they are not the same shape: a household is a group of 2 to 3 drawn from `DEVICE_HOUSEHOLD_SIZE`, so it survives if any two of its members appear, while a shared phone is assigned strictly pairwise and needs both of its two.
>
> **These are calibration constants, not modelling claims.** The claim is the observed 6% and 1.5%; the constants are whatever produces them at this actor count and window length. T3 checks the observed value, which is the number that matters, and it is the observed value that must stay inside ±20%.
>
> **This moves with scale.** The 62% observability figure is a property of `ACTOR_CLASSES` rates against a 30-day window. Change the window length, the actor count or the purchase rates and the correction changes with them, so both constants must be **re-derived rather than carried forward**. Treat 0.072 and 0.025 the way §4 already tells you to treat the `vpa` target: the current evaluation of a correction, never a constant in their own right.
| `vpa` handle | Millions legitimately share `@okhdfcbank`, `@kotak811` (the latter observed live in the probe downtime feed). | Handle collision **>40%** among legitimate UPI attempts, i.e. deliberately near-worthless | **[assumption]** |
| `vpa` local part | Local parts are usually the phone number, so this is **not an independent attribute**: it is a function of `contact`. | **Derived, not chosen.** `contact_rate x 0.92^2 x 0.72`, which evaluates to **~1.03%** at the current configuration. See the derivation below. | **[derived]** |
| `card.last4` | 10,000 possible values. Collisions are pure birthday-problem noise at any real volume. | Whatever falls out of uniform draw; **must not be suppressed** | derived |
| `checkout_ms` | Returning customers with saved instruments and one-tap UPI are genuinely fast. The fast tail must not be exclusively fraud. | **≥30%** of legitimate attempts under 1000ms | **[assumption]**, raised from 12% by the §7 reconciliation |
| `account_age_days` | Real new customers exist, and attackers use aged stolen accounts. | **≥10%** of legitimate attempts from accounts under 7 days old, and **≥20%** of attack attempts from accounts over 90 days old | **[assumption]** |
| `ip_prefix` | CGNAT means unrelated mobile users routinely share a /24. | Would need to be very high, and **we have no source for it** | **[no source]** — see §6 |

> **Corrected 2026-08-28.** This row previously also asked for a 2-4% pair-collision rate. **That is arithmetically incompatible with top-50 carrying 25%.** Pair collision is `sum(p_i^2)`, so a 2-4% rate implies an effective population of only 25-50 pincodes, i.e. essentially every order arriving from about 33 postcodes. India has ~19,000 and the shape above gives 681 effective pincodes at 0.147%. Only one of the two numbers can be true, and the concentration curve is the one grounded in a real mechanism, so the collision rate is now derived from it rather than asserted alongside it.
>
> A low benign collision rate is the correct outcome here rather than a shortfall. Pincode is the ring edge (section 6), and rings converge on drop addresses. The rarer an innocent pincode collision is, the more evidence an observed one carries. This is the opposite of `vpa` handle, which is deliberately near-worthless precisely because it collides constantly.

> **Corrected 2026-08-28. `vpa` local part is a derived quantity, not an independent target.**
>
> This row previously stated **~1.5%, tracking `contact`**. That number was **copied from the `contact` row rather than derived**, and the generator cannot reach it without distorting the world to fit it.
>
> **The derivation.** A VPA local part is the actor's phone number for `VPA_FROM_PHONE_SHARE = 92%` of actors. So two accounts share a VPA local part only when three things hold at once:
>
> 1. they share a phone, at the `contact` rate;
> 2. **both** drew phone-derived VPAs, at `0.92^2 = 0.8464`;
> 3. **both** actually made at least one UPI payment inside the window, since an account that never paid by UPI has no VPA to collide on.
>
> Factor 3 is measured, not assumed: 74.4% of transacting accounts make at least one UPI payment in a 30-day window, and the resulting retention on this statistic is **0.718**. (It sits above the naive `0.744^2 = 0.553` because the denominator loses the same non-UPI accounts the numerator does.)
>
> ```
> vpa_local_rate = contact_rate  x  0.92^2  x  0.72
>                = 1.70%         x  0.8464  x  0.72     =  ~1.03%
> ```
>
> Measured across seeds 42-46: **1.028% mean** (min 0.894%, max 1.200%). The formula predicts the observation; the old 1.5% did not.
>
> **Why we did not tune to reach 1.5%.** Only two levers exist, and both are worse than the problem. Raising `VPA_FROM_PHONE_SHARE` toward 1.0 asserts that essentially every Indian UPI user's handle is their phone number, which is false. Raising `CONTACT_SHARE_RATE` pushes `contact` itself out of its own band, breaking a passing attribute to fix a derived one. Either would mean **changing the modelled world to fit a number we invented**, which is precisely the failure mode section 4 exists to prevent.
>
> **Dependency, recorded so a later change does not silently break this.** `vpa` local part is **computed from `contact`**. If `CONTACT_SHARE_RATE` changes, or the observed `contact` rate moves for any other reason, **the `vpa` target moves with it** and must be recomputed from the formula above rather than left at 1.03%. The same applies if `VPA_FROM_PHONE_SHARE` or the UPI share of the method mix changes, since both enter the derivation. Treat 1.03% as the current evaluation of a formula, never as a constant.

**The general test this implies:** for every attribute, the ratio of *within-attack* collision rate to *benign* collision rate should be a finite, modest number. If any attribute has a benign rate of zero, the generator has planted the answer and the run is discarded.

---

## 5. Acceptance tests

These are tests, not intentions. A dataset version that fails any of them **is not used**.

**The rule on failure is fixed and not negotiable: fix the generator, never adjust the test.** A threshold may only change if a written argument is added to `decisions.md` explaining why the original was wrong on its own terms, before the failing data is seen. Every failure is recorded in `what-broke.md`, including the wrong first guess about the cause.

### T1 — Single-feature ceiling

For each field in turn, train a one-feature model (gradient-boosted stump ensemble, fixed hyperparameters) on the train split and score the validation split.

| Result | Action |
|---|---|
| AUC ≤ 0.70 | Pass |
| 0.70 < AUC ≤ 0.75 | Investigate. Document the mechanism. A field may legitimately land here if the mechanism is real and cited. |
| AUC > 0.75 | **Fail.** Treat as planted. Fix the generative process for that field. |

Thresholds are **chosen, not derived**, and that is stated on the tin. The reasoning: with attack prevalence at 2.5%, no single raw attribute should separate the classes well, because real fraud is a joint pattern. `amount`, `checkout_ms` and `error_reason` are the fields expected to sit highest, and each has a cited real mechanism.

**Additionally:** no field may have an AUC of exactly 0.5 across all runs either. A field that is pure noise for both populations is a field that should be cut, not shipped.

#### T1a — Mechanism-bounded ceiling (added 2026-08-28)

**The flat ceiling above is not a valid test on its own, and this is why.**

The first run put 17 of 30 fields above 0.75. The worst offenders were `status` (0.8903) and the four `error_*` fields (0.8961 to 0.9197). Those are not planted. Card testing declines at 82 to 88% because it is testing stolen and often expired cards, against 5.6% for legitimate traffic. That gap is the cited phenomenon from section 3, not an artefact: Chargebacks911 records that "the ratio of failed transactions to successful transactions is high because fraudsters are testing large lists of stolen and often expired data."

**A flat ceiling cannot tell "planted" from "genuinely discriminative".** A field that separates the classes because reality separates them is exactly what we want. Lowering the attack decline rate to satisfy 0.75 would mean falsifying a cited base rate to pass our own test, which is the forbidden direction.

**The fix is a different question, not a different number.** For any field with a declared mechanism, ask:

> Does this field discriminate **more than its own stated mechanism predicts**?

A genuinely discriminative field lands at roughly the AUC its declared generative parameters imply. A planted field exceeds it, because the plant is signal the mechanism does not account for.

**This is non-circular only if the prediction comes from the declared parameters, never from the observed data.** Predicting from observed class-conditional rates would make observed and predicted equal by construction and test nothing. So the predicted AUC is computed by Monte Carlo from the constants in `src/generator/config.py`, each of which traces to section 3 as either cited or a named assumption. The data is not consulted.

- **Pass:** observed AUC <= predicted AUC + 0.05 tolerance.
- **Fail:** observed exceeds the mechanism's prediction by more than the tolerance. That is signal the declared mechanism cannot account for, which is the definition of a plant.
- **Fields with no declared mechanism keep the flat 0.75 ceiling**, unchanged.

> **Predictor corrected 2026-08-28.** The first version of the T1a predictor had two independent errors, and they are worth recording because together they made a *test* problem look like a *generator* problem.
>
> 1. **Account-weighted where it should have been row-weighted.** AUC is computed over rows, not accounts. Rows from an actor with N events all carry frequency N, so a many-event actor contributes many high-frequency rows while a one-event actor contributes a single freq-1 row. The correct quantity is `P(N=1)/E[N]`, which for Poisson reduces to `exp(-lam)`. The old code returned the account-weighted `1 - P(N=1|N>=1)`, giving 0.34 against a true row-weighted 0.15.
> 2. **Attack uniqueness assumed to be 1.0 for every identifier.** It is computed now, by simulating each declared generator. It is near 1 for `contact`, whose namespace is 10^9, but only about 0.70 for `email`, whose local part draws from a 24 x 16 name pool with just 384 distinct values on shape 1, so attack emails collide with each other and with benign ones.
>
> **`email` was passing only because the two errors cancelled for it, and they did not cancel for `contact`.** Error 1 pushed the prediction up and error 2 pushed it down; for email those roughly offset, for contact they did not. That is what made `contact` look like a leaking field when the leak was in the predictor.
>
> **This makes the test correct, not more permissive.** The tolerance is untouched at 0.05, no field was exempted, and the corrected predictions are *tighter* against observation than the old ones: `contact` -0.023, `card.last4` -0.011, `vpa` -0.008, `mc.device_id` -0.000. `email` still fails at +0.058, which is roughly ten times the predictor's own Monte Carlo spread of 0.006, so it is a genuine flag rather than simulation noise.
>
> **Scope.** Four fields shared the broken predictor: `contact`, `email`, `card.last4` and `vpa`. All four were fixed, not the two that happened to be under discussion. Two of them, `card.last4` and `vpa`, also needed a mechanism the old version lacked entirely: they are populated only on card and UPI rows respectively, so most rows fall into a shared NULL bucket whose frequency dwarfs any real value. For those two, nullness rather than uniqueness is the operative mechanism. The predictor now scores on the simulated frequency directly, exactly as the encoder does, so it captures whichever mechanism is stronger without having to choose in advance.

**Nothing is silently exempt.** Every mechanism-bounded field is listed in `MECHANISMS` in `tests/acceptance/runner.py` with a named mechanism, the config constants it is computed from, and a citation or an explicit assumption tag. A field cannot be added to that table without one. The table is auditable in one place, which a scattering of exemptions would not be.

### T2 — Label shuffle

Shuffle labels within the training set, preserving prevalence, and retrain the full model. Repeat 20 times.

- **Pass:** 0.50 lies inside the central 95% interval of the empirical null distribution of permuted AUCs.
- **Fail:** 0.50 falls outside that interval. That indicates label information reachable through row structure rather than field values, which T4 then localises.

> **Median assertion removed 2026-08-30.** The pass condition previously carried a second leg: the median of the permuted AUCs had to sit within 0.03 of 0.50. That leg is gone. The argument, measured rather than asserted:
>
> **1. It cannot be resolved at any practical permutation count.** Holding the dataset fixed and varying only the permutation seed, the median moved **0.0782** across five seeds at 50 permutations, and **0.0489** across three seeds at 350. Both are larger than the 0.03 tolerance being tested. A criterion whose own measurement noise exceeds its threshold does not test the data; it tests the seed. At 50 permutations, 3 of 5 seeds failed on a dataset with no leak, and the failures landed on **both sides** of 0.50, which is the signature of noise rather than bias.
>
> **2. The raise to 350 was itself based on a wrong model, and this is the more useful lesson.** 350 came from assuming sqrt(n) convergence: `(0.0782/0.03)^2` is about 6.8x. Measured, seven times the permutations bought a factor of **1.6**, not the 2.65 that sqrt(n) predicts. The permutation AUCs are **heavy-tailed**, with a 95% band spanning roughly 0.19 to 0.85, and the median of a heavy-tailed sample converges far slower than the normal approximation assumes. Extrapolating a second time would repeat the same error, so no larger count is proposed. The count stays at **350**, which is strictly better than 50 and is what the sweep was run at.
>
> **3. The remaining leg is the standard criterion and it already passes everywhere.** "Does 0.50 lie inside the empirical null's 95% interval" is what a permutation test actually asks: is the observed statistic extreme under the null. It passed at **all six** grades of the 2.1e sweep, at both permutation counts. The median leg was an additional assertion that the null is *centred* at 0.50, which is a stronger claim than a permutation test makes and one this null does not satisfy: the distribution is wide and skewed, so its median has no reason to sit at the reference value even when the data is clean.
>
> **What this costs us.** A real leak that shifted the null's centre by less than its own spread would now go unflagged. That is accepted, because the previous leg could not have detected such a shift either: it would have been buried in the same 0.05 of seed noise that produced four spurious failures. Removing it loses no detection power that was actually present, and it stops the suite reporting failures that are properties of the random seed. T4 remains the test that localises row-structure leakage, and it is unchanged.

> **Rewritten 2026-08-28.** The original pass condition was "mean AUC in [0.48, 0.52]". Measured, the permuted AUCs were **0.329 to 0.733 with a mean of 0.571**, against a null standard error for this sample of **0.007**. A spread seventy times the null SE is not a leak, it is a degenerate model.
>
> **Diagnosis.** With labels shuffled, every leaf value is drawn from the same 4.2% base rate, so predictions come out near constant: measured standard deviation 0.006 to 0.019 around a mean of 0.042. The tree still partitions on features, and some of those features are strongly class-separating on their own, so an essentially arbitrary assignment of near-identical leaf values across that partition lands anywhere between strongly positive and strongly negative correlation with the true labels. AUC on near-constant scores is decided by which side of a hair's-width difference each tie falls, so it swings wildly. The statistic was measuring numerical instability, not leakage.
>
> **Why we chose the empirical null over a tie-aware statistic.** Both were available. A tie-aware alternative (for example scoring by a rank statistic with explicit tie correction) would stabilise the number but would still be compared against an assumed centre of 0.50. The empirical null makes no such assumption: it characterises what this model, on this data, actually produces under the null hypothesis, then asks whether chance sits inside it. That is what a permutation test is for, and it is robust to the degeneracy rather than merely tolerant of it. It also costs nothing extra, since the permutations were already being run. Permutation count is raised from 20 to 50 so the interval is stable. *(Raised again to 350 on 2026-08-29, and the median leg dropped on 2026-08-30. See the note above.)*

### T3 — Benign collision check

For every linking attribute in §4, measure the collision rate **on label-0 events only**.

- **Pass:** observed rate within ±20% relative of the §4 target, and strictly greater than zero.
- **Fail on zero:** any attribute with no benign collisions is a planted attribute. Hard fail, no exceptions.
- Also assert the attack-to-benign collision ratio is finite and below 50×, **measured in that attribute's own §4 unit**.
  - **One documented exception: `email`.** Its §4 unit is *top-3 domain share* (~70%), which is a concentration measure rather than a collision rate, so a ratio of it is uninformative by construction: both populations sit near 0.70 and the ratio is ~1.0 whatever the attack does. The ratio leg therefore uses **domain pair collision** for `email` alone. Recorded here rather than left as a silent divergence between spec and code. The **band** leg still uses top-3 share, so the §4 target itself is unchanged, and the other five attributes use their own unit in both legs.

> **Corrected 2026-08-28.** The ratio leg previously used pair-collision for every attribute while the band check used each attribute's §4 measure. Those are not the same quantity, and for `device_id` they disagree by two orders of magnitude:
>
> | measure | benign | attack | ratio |
> |---|---|---|---|
> | §4 unit (accounts sharing a device) | 5.472% | 95.787% | **17.5x** |
> | pair collision over events | 0.006% | 10.281% | **1786.6x** |
>
> **Why they diverge.** §4 defines `device_id` as *"6% of legitimate accounts share a device with at least one other account"*, an account-level measure. Card testing is guest checkout, so **only 9.1% of its rows carry an account at all**. The account measure is therefore computed over a tenth of the attack rows while the pair measure is computed over all of them, and the two are evaluated over almost disjoint sets. Pair collision also counts a burst hammering one device thousands of times, which is volume rather than sharing.
>
> `vpa local part` showed the same artefact in the other direction: 726x in pair units, **0.0x in §4 units**, because only 79 attack rows have a VPA at all.
>
> This is a test measuring the wrong quantity, not a threshold we failed. In §4 units both attributes pass comfortably.

### T4 — Ordering and identity (Category E leaks)

Four assertions, matching the schema's Category E:

1. **Row order.** Rows sorted by file position must be sorted by `created_at`. Kendall tau ≥ 0.999.
2. **Order carries nothing.** A model on `row_index` alone must score AUC ≤ 0.52.
3. **ID monotonicity and allocation.** All `id` values must be monotonic with `created_at` under the base62 ordering. A model on ID-derived features alone (suffix rank, character-level features) must score AUC ≤ 0.52.
4. **No positional separability.** Assert all three:
   a. No run of consecutive attack rows longer than the largest single burst's event count.
   b. A model trained on **row position alone** (`row_index`, and rank within the file) scores AUC <= 0.52.
   c. Attack rows are interleaved rather than appended, **measured per attack type, not pooled**: for any attack type that is low-rate by construction, the median gap in row position between consecutive attack rows must be > 1. Dense burst patterns are exempt from 4c and are covered by 4a instead.

> **Corrected 2026-08-28.** Measured per type, the median row gap is **1.0 for card testing and 159.0 for rings**. Pooling them reported 1.0 and failed.
>
> **4c is unsatisfiable for card testing by construction.** A burst runs at 20 to 40 events per minute against a legitimate baseline of about 1.5, so roughly 96% of the rows inside a burst window are attack rows and adjacent positions are inevitable. Demanding a gap above 1 would demand a burst that is not a burst, the same contradiction with section 2.1 that the old 30-hour assertion had.
>
> The Category E leak 4c exists to catch is *appending a block of attack rows to the file*. For a dense pattern that is already caught by 4a, which bounds the longest run by the largest burst's own event count: a burst of 1,629 events producing runs of at most 142 is interleaved with legitimate traffic exactly as much as its rate permits. Rings, being low-rate by construction, are still held to the interleaving requirement and pass it at 159.

> **Rewritten 2026-08-28.** This assertion previously also required attack rows in **at least 30 distinct hours** across the window. That requirement was wrong for card testing and has been removed.
>
> It contradicts the generator's own attack model. Section 2.1 specifies 4-7 bursts of 10-45 minutes; such a campaign touches roughly 5 hours. Reaching 30 distinct hours would force about 30 separate bursts, which is a different attack from the one section 2.1 describes.
>
> It also tests the wrong thing. **Measured: hour of day and day of week alone score AUC 0.5146, which is chance.** The hours a campaign occupies (00, 08, 09, 12, 19 in the reference run) are ordinary hours carrying heavy legitimate traffic, with attack share inside them ranging 9% to 47%. Temporal concentration at the hour level is simply not what gives the attack away.
>
> What T4 is for is Category E: leaks in *position and identity* rather than in field values. The replacement above tests exactly that. Arrival **density** does separate the classes, but density is the phenomenon rather than an artefact, and it is capped by T6 rather than forbidden by T4.

### T5 — String and metadata hygiene

1. `notes` must contain **no** generator metadata: no seed, archetype, persona, actor class or row index. Assert against a keyword denylist and assert `notes` content distribution is independent of label (chi-squared, p > 0.01).
2. No identifier string may encode its class. Assert no substring from a denylist (`attack`, `bot`, `fraud`, `ring`, `test`, `legit`) appears in fields whose **content we author**: `email`, and the literal prefixes of every identifier format. Values minted from a label-blind counter or RNG (`id`, `order_id`, `account_id`, `device_id`, `session_id` suffixes) are excluded. A materiality floor also applies: a hit counts only if it appears in at least 1% of attack rows.

> **Corrected 2026-08-28.** The check failed on `bot`, enriched 33.4x in attack rows. All three hits were in the base62 `id` field, for example `pay_0000000DJboT3o`, and none were in `device_id`, `email` or `account_id`.
>
> **Base62 identifiers contain chance letter runs.** Ours are minted from a monotonic counter that never sees a label, over an alphabet of `0-9A-Za-z`, so three-letter sequences appear at a predictable background rate. Three hits in 3,921 attack rows against one in 64,040 benign rows is a 33x ratio on a count of three, which is noise.
>
> **What the check is actually for** is catching us naming something `bot_device_7`: a semantic tell we authored. That failure mode would appear in essentially every attack row, not three of them, which is why the materiality floor of 1% is the right second guard. Excluding machine-minted suffixes removes the false positive without weakening the check against the thing it exists to find.
3. Identity strings for both populations must be drawn from **one** generator. Assert character-distribution parity: a classifier on character n-grams of `email` local part alone must score AUC ≤ 0.55.
4. `contact` format (`+91…` vs bare) must be independent of label. Chi-squared, p > 0.01. This is the constraint flagged in §0.5.

### T6 — Confounder survival

The generator must produce the confounders that make the task honest, and their presence is asserted:

1. At least 2 flash sales per simulated month, each with volume within the range a card-testing burst produces.
2. At least 1 downtime window per month producing a decline spike **with zero attack events in it**.
3. A model trained only on windowed volume and decline rate must **not** exceed AUC 0.80 at the window level. If it does, bursts are too easy to separate from flash sales, and the flash sale model needs strengthening.

> **Note on comparing numbers across 2026-08-28.** Adding the `+91` contact-format draw consumes one extra random number per identity, which shifts every downstream draw in the stream. Every generated value moved as a result, not only the ones the change was aimed at. `card.iin` went from 0.7525 to 0.5665 across that boundary for this reason alone. **Single-field comparisons spanning that commit are not meaningful**; only distributions and aggregate pass/fail counts carry over.

### T7 — Determinism

`generate --seed N` twice must produce byte-identical output. Non-determinism is a bug, not noise.

### T8 — Signal floor

Every test above caps how *easy* the task is. Nothing so far checks the task is still **possible**. A dataset could pass T1 through T7 and still be worthless, because the pattern was scrubbed away along with the leaks. T8 is the floor under that.

#### The oracle

A **structure oracle**: a hand-written detector that is configured from the sealed outcome store and the generator's own parameters, then run against the ordinary event stream.

The distinction that makes it meaningful:

| | |
|---|---|
| **The oracle may read the sealed store** | at **configuration** time only, to set its thresholds from the true generative parameters. It is told, for example, the real IIN-concentration threshold and the real `checkout_ms` mode. |
| **The oracle may not read the sealed store** | at **inference** time. It never looks up a label for a row it is scoring. |

An oracle that looked up labels would trivially score 1.0 and measure nothing. This one answers a different and useful question: *given perfect knowledge of what pattern was planted, how well can that pattern be recovered from the observable stream?* That is the achievability ceiling.

#### Floors for acceptance

| Metric | Floor | Rationale |
|---|---|---|
| Event-level ROC-AUC, all attack types | **>= 0.85** | Below this the joint pattern is too weak to be worth detecting. |
| Card-testing burst recall @ precision 0.80 | **>= 0.90** | Card testing is a strong, high-volume pattern. An oracle that cannot find 9 in 10 burst events has been denied the structure. |
| Ring member recall @ precision 0.70, **scored at the account level** | **>= 0.60** | Rings are genuinely harder, low-rate and interleaved. A lower floor is honest, not a concession. |

> **Unit corrected 2026-08-28. Ring is scored per account; card testing stays per event.**
>
> A ring is a group of accounts by definition, and its evidence exists only in aggregate: one ring row in isolation is an ordinary purchase. Scoring rows spreads 25 accounts' worth of evidence across 182 rows against a 0.268% base rate. Measured on identical data, moving to the account level took ring PR AUC from **0.0507 to 0.3878**, and a control that broadcast the same account features back down to rows scored 0.2882, so roughly a quarter of the gap is the unit alone rather than the features.
>
> Card testing stays at event level, and not merely by convention: 90% of its rows are guest checkout with a null `account_id`, so there is no account to aggregate to. The event is the unit of action.
>
> **The oracle's ring rule was also stale.** It fired only when a pincode cluster exceeded the 99.5th percentile, which is 112 accounts. After drop pincodes were drawn unweighted the clusters are 7, 11 and 8, so the rule never fired and event-level ring PR AUC fell to 0.0034. The rule now scores a cluster against how populous that pincode normally is, rather than against a global percentile, and adds the conjunction of shared pincode with shared device, which the account-level diagnostic identified as the only feature separating a ring member from its innocent neighbours.
| Oracle vs impoverished window model (T6) | **gap >= 0.10 AUC** | See section 7.3. If the full-feature oracle cannot beat the volume-and-decline-only model by a clear margin, the linking attributes carry no signal and the coordination premise is unsupported. |

#### What we do when T8 fails

A T8 failure means the generator over-suppressed. The order of investigation is fixed:

1. Check whether a section 4 benign collision rate was set so high it drowned the attack signal. This is the most likely cause, because those rates were raised specifically to satisfy T1 and T3.
2. Check whether section 2's "what it does not share" lists were applied too aggressively, leaving attacks with no shared structure at all.
3. Check the attack-event prevalence assumption in section 3. At 2.5%, absolute event counts may simply be too small for a stable estimate.

**The forbidden fix is lowering the T8 floor, and the equally forbidden fix is loosening T1, T3 or T6 to let signal back in.** T8 failing while T1 to T7 pass means the *generator* is wrong, not the tests. Every T8 failure is recorded in `what-broke.md` with the wrong first guess kept in.

#### Keeping the oracle out of the submission

The oracle is a test fixture. It is also, by construction, a detector that has seen the answer key, so it must never leak into the thing we submit.

1. **Physical separation.** Oracle lives in `tests/oracle/`. The submitted detector lives in `src/detector/`. The oracle may import from `src/`; `src/` may never import from `tests/`.
2. **Enforced by test, not convention.** A CI check asserts that no module reachable from the detector's entry point imports `tests.oracle`, and that no detector module references the sealed store path.
3. **Path isolation.** The sealed outcome store is readable only through a fixture helper that lives in `tests/`. The detector's data loader is given the event stream path and has no code path to the outcome file.
4. **Reported separately and always.** Oracle scores are published in `numbers.md` labelled **oracle ceiling**, never as detector performance. This is a feature: reporting "detector 0.82 against an oracle ceiling of 0.91" is far more informative, and far more honest, than reporting 0.82 alone. It tells a reviewer how much of the achievable signal we actually captured.

---

---

## 6. Field cut

The schema carries roughly 35 fields and 11 linking attributes. Every linking attribute needs a defensible benign collision rate, and §4 shows that four of them cannot get one without inventing a number. Carrying fewer attributes we can defend is the better trade.

### Cut entirely (6 fields)

| Field | Reason |
|---|---|
| `acquirer_data` | Perfect proxy for `status`. §0.1. |
| `error_description` | Free text, no information beyond `error_code` + `error_reason`, high accidental-tell risk. §0.2. |
| `user_agent_hash` | Collinear with `device_id`. Two correlated benign rates to calibrate instead of one. §0.4. |
| `ip_prefix` | **The clearest cut.** CGNAT makes a /24 collision near-meaningless in India, and no subscribers-per-address figure could be sourced, so its benign rate would be fabricated for the one attribute most in need of a real number. |
| `card.sub_type` | `consumer`/`business` adds a near-constant field. |
| `entity` | Constant `"payment"`. Keep only if byte-fidelity to Razorpay's shape is wanted for the published sample. |

### Demote from linking attribute to plain field (5 attributes)

Keep the field, stop treating it as a graph edge, and drop the obligation to defend a benign collision rate for it.

| Attribute | Reason |
|---|---|
| `vpa` handle | Benign collision >40% by design. An edge this weak adds noise to the graph and calibration burden for nothing. Use `vpa` local part instead. |
| `card.last4` + `network` | Weak alone, and combined with `iin` it adds nothing `iin` does not already carry. |
| `account_id` | Rings use distinct accounts by definition. Useful as a grouping key for velocity, never as an edge. |
| `order_id` | Links attempts within one checkout, not accounts to each other. Grouping key only. |
| `session_id` | Same. Grouping key for `attempt_seq`, not an edge. |

### Load bearing, keep and defend (6 edges)

| Attribute | Why it survives |
|---|---|
| `card.iin` | The defining structure of card testing. Cut this and the priority attack is undetectable. |
| `device_id` | The only strong cross-identity link once `ip_prefix` is gone. Benign rate defensible via household sharing. |
| `contact` | Rare benign collision means a collision is real evidence. High value per edge. |
| `email` (domain rarity + local-part shape) | Domain *equality* is worthless; domain *rarity* and local-part shape are not. Use as a weighted edge, not a binary one. |
| `vpa` local part | Inherits phone-level uniqueness, so it behaves like `contact` for UPI traffic. |
| `shipping_pincode` | The ring signal. Irrelevant to card testing (nothing ships), which is fine: not every edge needs to serve both attacks. |

**Net effect:** 11 linking attributes down to 6 real edges, 6 fields cut, 5 demoted. That is 6 benign collision rates to defend instead of 11, and the four undefendable ones are gone rather than papered over.

Fields kept but explicitly non-feature (fidelity only, excluded from any model): `currency`, `notes`, `international` if it proves near-constant.


---

## 7. Reconciling T8 against T1 and T6

The instruction was not to assert compatibility. **They are not automatically compatible.** Two parameters as originally written would have breached T1 outright, and a third sits near the line. The fixes are already applied above. The structural argument then shows the rest is satisfiable.

### 7.1 Why there is no contradiction in principle

T1 bounds **single raw row fields**. T8 measures a **joint, relational** detector. These operate on different spaces, and a joint model is not bounded above by its best marginal, the textbook case being XOR, where each feature alone is AUC 0.5 and the pair is 1.0.

Concretely, under an equal-covariance Gaussian approximation with *k* roughly independent features of standardised effect *d*, combined separation is `D = d * sqrt(k)` and `AUC = Phi(D / sqrt(2))`. Six features each at marginal AUC 0.70 (`d ~ 0.742`) give `D ~ 1.82`, so joint AUC is about **0.90**, comfortably clearing the 0.85 floor with every marginal well under the 0.75 cap.

The stronger argument is that **the coordination signal is not a row field at all.** "Shares an IIN with 40 other attempts inside 10 minutes" is a computed relation over a graph, not a column. T1 cannot see it, so T1 cannot cap it. Nearly all of the signal T8 requires is structurally invisible to T1.

### 7.2 Where they genuinely collided, and what changed

This is the part that needed checking rather than asserting.

| Field | Original spec | Approx. marginal AUC | Verdict | Fix applied |
|---|---|---|---|---|
| `amount` | Attack "concentrated in a narrow low band"; only 4% of legit below 50 rupees | **~0.95+** | **Breaches T1 badly.** If nearly all attack traffic sits in a band holding 4% of legitimate traffic, a single threshold on `amount` almost solves the task. | Legit low shoulder raised 4% to **10% below 100 rupees**; attack amounts changed from a narrow band to a **mixture including 15% drawn from the legitimate distribution** (sections 1.4 and 2.1). |
| `checkout_ms` | Attack tight in 200-900ms; only >=12% of legit under 1000ms | **~0.88** | **Breaches T1.** | Legit fast tail raised 12% to **>=30% under 1000ms**; attack widened to **150-2500ms with a jittered tail** (sections 2.1 and 4). |

A third, `error_reason`, sits near the line. Card testing concentrates on a CVV/expiry-class reason while legitimate declines spread across five classes. Whether it breaches 0.75 depends on the legitimate share of that same reason class, which section 1.5 does not currently pin down. **This is left as a measured quantity, not an assumed one:** it is the field most likely to fail T1 on first run, and the fix if it does is to raise the legitimate incorrect-PIN/auth-failure share rather than to weaken the attack.

### 7.3 T6 versus T8

T6 caps a window-level model at AUC 0.80, and burst detection *is* substantially a window-level problem, so this is the sharper of the two tensions.

It resolves because **T6's model is deliberately impoverished: volume and decline rate only.** A flash sale has high volume and elevated declines and is indistinguishable from a burst on those two axes alone, which is exactly why T6 caps them. The oracle sees what T6's model cannot: IIN concentration, device concentration, `checkout_ms` distribution, amount concentration. Flash sales have none of those.

So the constraint is an **ordering**, not a conflict:

```
T6 model (2 features, window level)    <= 0.80
                                          |  gap >= 0.10
Oracle (full features, window level)   >= 0.90
```

The gap assertion is new and is now part of T8. It earns its place: if the oracle *cannot* beat the volume-and-decline model by a clear margin, then the linking attributes are not carrying signal, and the entire coordination premise of the project is unsupported by its own data. That is worth failing loudly on.

### 7.4 The residual risk, stated plainly

The satisfiable region is real but not wide. The binding constraint is that every attribute must overlap legitimate traffic enough to pass T1 and T3, while the *joint* pattern stays sharp enough to clear T8. If a future run cannot satisfy both, the honest resolutions in order of preference are:

1. Add linking attributes back. The section 6 cut list is reversible, and `ip_prefix` is the obvious candidate to restore if we can source a benign rate.
2. Raise attack prevalence above the 2.5% assumption, which is ours and not cited.
3. Lower the T8 ring floor, which is the softest of the four floors.

Raising the T1 ceiling is **not** on that list.
