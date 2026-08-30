# What Broke

Append only. One entry per real problem, four lines each:
**Broke / Thought / Actually / Fixed.** The wrong guess is the valuable part. Never remove it.

Newest entries go at the bottom.

---

### 2026-08-28 — Key secret echoed into the session transcript

- **What broke:** The Razorpay test key secret was printed into the session transcript on the first attempt to load credentials.
- **What we thought was wrong:** That `.env` held shell-safe `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` assignments and could simply be sourced.
- **What was actually wrong:** `.env` used `Test API Key=` and `Test Key Secret=` as names. The spaces made `source .env` fail, and the shell echoed the offending lines back, secret included. Sourcing a file whose format had not been checked was the actual mistake.
- **How we fixed it:** Replaced sourcing with a parser (`scripts/probe/env.sh`) that extracts values with `sed` and never echoes them, matching both the spaced names and the standard ones. Confirmed no secret reached any file: `.env` was already in `.gitignore` before the first API call, and a scan of all tracked files for the key id and secret returned clean. Recommended regenerating the test key pair; **not yet confirmed done.**

Recorded in api-probe.md (private working doc), security note at the top.

---

### 2026-08-28 — Plans and subscriptions returned 401

- **What broke:** `GET` and `POST` on `/v1/plans` and `/v1/subscriptions` both returned HTTP 401 with body `{"error":"Unauthorized"}`.
- **What we thought was wrong:** Bad or expired credentials.
- **What was actually wrong:** Not credentials. The same key pair returned 200 on `/v1/orders`, `/v1/payment_links` and `/v1/customers` seconds either side of the failure. Two signals pointed at a feature gate instead: the credentials demonstrably worked elsewhere in the same session, and the error envelope was a bare `{"error":"Unauthorized"}` rather than Razorpay's standard `{"error":{"code":…,"description":…,"source":…,"step":…}}` shape seen on every other failure in the run, indicating rejection at a different layer than the normal API error handler.
- **How we fixed it:** Not fixed. The documented prerequisite for Subscriptions is enabling **Flash Checkout** under Account & Settings → Checkout Features. `api-probe.md` records this as *likely a dashboard toggle rather than a hard block, but unresolved and needing a manual action to confirm.* **Status: open pending dashboard verification.**

Recorded in api-probe.md (private working doc) §4. Consequence tracked in the private strategy notes, where the Track 03 feasibility row was downgraded.

---

### 2026-08-28 — Card declines came out at 26% against a cited 12.5%

- **What broke:** The first working generator run produced a card decline rate of 26.29% and a UPI rate of 1.53%, roughly double the cited per-method figures of 12.5% and 0.8%. Overall decline was 11.8%.
- **What we thought was wrong:** That the base per-method decline constants in config had been mistyped, or that the evening multiplier of 2.5x was simply too aggressive and needed lowering.
- **What was actually wrong:** Neither. The constants were right and the multiplier was defensible. The bug was double-counting: the cited per-method rates from Razorpay are already blended across geography and time of day, so multiplying them again by a tier factor (up to 2.4x) and an evening factor (2.5x) applied the same effects twice. The expected product of those two multipliers is 2.11, which is very close to the 2.1x overshoot observed.
- **How we fixed it:** Added `config.DECLINE_NORMALISER`, computed analytically as the expected tier multiplier times the expected evening multiplier weighted by the hourly traffic profile, and divide by it after the multipliers are applied. Downtime is applied after normalisation, since a downtime window is a genuine excursion above the blended baseline rather than part of it. Card decline is now 12.79% against a cited 12.5%, UPI 0.81% against 0.8%, and the metro < tier2 < tier3 ordering is preserved.

---

### 2026-08-28 — Null rates made account_id and shipping_pincode a perfect label

- **What broke:** Card testing is guest checkout with nothing shipped, so attack rows carry `account_id` and `shipping_pincode` as null. The legitimate stream emitted a value for both on every single row. Nullness alone separated the two populations perfectly.
- **What we thought was wrong:** Nothing, at first. Both halves looked correct in isolation. The legitimate generator had passed every collision check it was given, and the attack spec explicitly says these fields are usually null, so each side was doing what it was told.
- **What was actually wrong:** The failure only existed in the join. Neither stream is wrong on its own; the leak appears the moment they sit in the same file, because the legitimate side had no mechanism that produces a null. This is the class of bug that per-stream checks cannot catch by construction, and it is an argument for running the collision checks on the combined stream rather than on legitimate traffic alone.
- **How we fixed it:** Added the two real mechanisms that produce nulls in legitimate traffic. Guest checkout at 12% of sessions and non-shipping digital goods, recharges and subscriptions at 18%. Benign null rates are now 11.99% and 18.10% against attack's 90.92% and 92.81%.

---

### 2026-08-28 — Campaign envelope never produced a decline phase

- **What broke:** Every burst in a campaign landed on the rising limb of the envelope. The plateau and decline the spec cites were never generated, and the campaign visibly stopped halfway through the window.
- **What we thought was wrong:** That the envelope function itself was miscoded, or that too few bursts were being scheduled to show the shape.
- **What was actually wrong:** The envelope was correct and the burst count was adequate. The position fed into it was wrong: envelope was computed as the burst's fraction of the whole **window**, while the campaign only occupied the first half of that window. A campaign that ends 45% of the way through the window can never reach a phase that begins at 75%. Compounding it, a cursor advanced by whole-ish day gaps put nearly every burst at the same time of day, around 02:20.
- **How we fixed it:** Gave the campaign an explicit span, computed the envelope as a fraction of that span rather than the window, and placed one burst per interval jittered inside it instead of advancing a cursor.

---

### 2026-08-28 — Attack email domains had higher entropy than real traffic

- **What broke:** Domain pair-collision was 12.51% within attack traffic against 29.48% in legitimate traffic, so attack email domains were measurably *more* uniformly spread than real ones.
- **What we thought was wrong:** Initially read as harmless, since the attack side looked less concentrated rather than more.
- **What was actually wrong:** It is a tell in the opposite direction, and just as usable. Attack domains were drawn uniformly from a hand-written list while legitimate domains follow a Gmail-heavy weighted distribution. A detector could separate on domain entropy alone. Real throwaway addresses are Gmail-heavy too, so uniform drawing was also simply unrealistic.
- **How we fixed it:** Attack emails now draw from the same weighted domain distribution the legitimate population uses. The ratio is 1.0x.

---

### 2026-08-28 — Empty string sentinel collapsed to None downstream

- **What broke:** Attack `account_id` and `shipping_pincode` were null on 100% of rows despite being configured at 90% and 93%.
- **What we thought was wrong:** That the probabilities in config were inverted or misread.
- **What was actually wrong:** The config was right. The attack builder used `""` as a "populate this later" sentinel for the minority that should carry a value, and a downstream normaliser in `emit.build_row` converted `""` to `None` because an empty string is not a valid identifier. Both branches therefore ended as null, and the 10% and 7% cases silently never existed.
- **How we fixed it:** Replaced the sentinel with an explicit `wants_account` flag, minted real account ids for those identities from the same monotonic sequence, and drew real pincodes for the shipping minority. Observed null rates are now 90.92% and 92.81%, matching config.

---

### 2026-08-28 — Background run output looked empty until the process exited

- **What broke:** A five-seed measurement run was launched in the background. Reading its output file twice returned nothing, and a blocking wait loop timed out, so the run appeared to have died.
- **What we thought was wrong:** That the background process had crashed or been killed.
- **What was actually wrong:** The process was running normally the whole time. Python fully buffers stdout when it is redirected to a file rather than a terminal, so nothing reached the file until the process exited and flushed. The output arrived intact afterwards.
- **How we fixed it:** Re-ran with `python -u`. Any long background run whose progress needs watching must be unbuffered, or its output file will look identical to a dead process.

---

### 2026-08-28 — Flash sales redistributed traffic instead of adding it

- **What broke:** Flash sales reached only 3.8 to 8.0 events per minute against bursts at 8.8 to 40.1, despite stated multipliers of 6.3x to 13.0x on a 1.48 per minute baseline that should have produced roughly 9 to 19. The confounder could not masquerade as a burst, so volume alone separated attack from legitimate traffic almost perfectly.
- **What we thought was wrong:** Nothing, for a long time. Flash sales were present in the manifest, events fell inside their windows, and the multipliers were being applied. Every surface check passed.
- **What was actually wrong:** The multiplier was folded into the hourly intensity profile, which the session sampler uses to decide *when* sessions happen. The number of sessions is fixed in advance by the actor population, so multiplying a weight only moved existing demand into the sale window rather than creating any. A sale concentrated traffic and could never exceed what the population was already going to generate.
- **How we found it:** Only by measuring what volume alone could score. A time-only model on rolling volume and inter-arrival gaps reached AUC 0.9789, which was too high for a feature set containing no linking attributes and no instrument fields. Working back from that number to ask why density was so discriminative led to comparing flash-sale density against burst density, where the gap was obvious. No amount of inspecting the flash-sale code in isolation would have shown it, because the code did exactly what it said.
- **How we fixed it:** Removed the multiplier from the intensity profile and made sales additive. Extra sessions are drawn on top of the baseline that actually landed in the window, from the same actor population weighted by normal purchase frequency, so a sale brings real customers with real devices and instruments. Also added bank strain under sale load, mirroring the evening coupling, so a high-volume window with elevated declines reads as normal rather than suspicious. Flash sales now reach 10.8 to 22.1 per minute and the time-only volume AUC fell from 0.9789 to 0.8678.

---

### 2026-08-28 — Ring drop pincodes were drawn from the traffic-weighted distribution

- **What broke:** The oracle scored ring recall 0.0000 at precision 0.70. Rings looked undetectable, and the obvious reading was that the pattern was simply too thin: 174 events, 25 accounts, 0.257% prevalence.
- **What we thought was wrong:** That rings needed to be more numerous, larger, or longer-running, and that the ring floor in T8 might just be unreachable at this data size.
- **What was actually wrong:** Two separate things, neither of which was ring size. First, the scoring unit: a ring is a group of accounts, not an event, and moving to the account level took PR AUC from 0.0507 to 0.3878 on identical data. Second, and the generator's fault: ring drop pincodes were drawn from the same traffic-weighted distribution as a customer's home pincode, so rings landed where the customers are. Two of three drops sat on pincodes shared with 125 and 130 innocent accounts, capping precision from pincode alone at 0.053 and 0.078.
- **What pointed at it:** account-level recall saturated at exactly **0.4000** across every precision from 0.05 to 0.80, and exactly **10 of 25 members (40%)** shared a device. A recall ceiling landing precisely on a generator constant is not a coincidence; it said the device conjunction was the *only* working feature and the drop address was contributing nothing. That is what sent us to look at how drop pincodes were chosen.
- **How we fixed it:** Drop pincodes are now drawn unweighted. Clusters went from 9, 126, 134 accounts to 7, 11, 8. Account-level PR AUC improved 0.3878 to 0.4283 and recall at precision 0.70 from 0.4000 to 0.4400. Still below the T8 floor of 0.60, because the ceiling is now set by the device-sharing rate rather than by the drop address.

---

### 2026-08-28 — Adding the contact format inconsistency broke the VPA derivation

- **What broke:** Adding the `+91` versus bare phone inconsistency the schema asks for immediately failed T3 on `vpa local part`, which dropped to 0.567% against a 0.777 to 1.166% band. It had passed for several commits.
- **What we thought was wrong:** That the derived VPA target had shifted because the contact collision rate moved, since the target is computed from it.
- **What was actually wrong:** A silent type assumption. A phone-derived VPA local part is copied straight from the contact string, which is now sometimes `+919876543210`. The code that propagates a shared phone into a shared VPA guards on `.isdigit()`, and `"+919876543210".isdigit()` is False, so the propagation silently skipped every `+91` formatted pair. Roughly a third of shared-phone pairs stopped sharing a VPA.
- **How we fixed it:** Keep the bare digits separate from the formatted contact and derive the VPA local part from the digits. This is also what reality looks like: a UPI handle is `9876543210@okhdfcbank`, never `+919876543210@okhdfcbank`. Back to 0.909%, inside band.

---

### 2026-08-28 — The T1a mechanism predictor was wrong in two ways at once

- **What broke:** `contact` scored 0.8997 against a mechanism prediction of 0.8307 and failed T1a by +0.069. `email` had an identical declared mechanism and passed at 0.8192. Same mechanism, different outcome.
- **What we thought was wrong:** The generator. Something about phone numbers was assumed to be more separable than the throwaway-identity story allowed, so the search was for a structural tell: entropy of the phone generator, a `+91` format correlation, prefix or digit-distribution differences, an encoding artefact.
- **What was actually wrong:** None of those. Measured, the phone and email generators are structurally identical (digit entropy 3.2735 against 3.2716 bits, same first-digit distribution), the `+91` format contributes nothing (format flag alone scores 0.4961, and P(freq==1) is identical across formats *within* each population), and the phone values carry nothing (contact-as-integer scores 0.5181). The fault was in the predictor, and it had **two independent errors**. It computed the account-weighted probability that an identifier repeats when AUC is computed over rows, giving 0.34 against a true row-weighted 0.15; and it assumed attack identifiers are always unique, which holds for a 10^9 phone namespace but not for email, whose local part has only 384 distinct values on one of its five shapes.
- **The part worth remembering:** the two errors pointed in opposite directions and **cancelled for `email` but not for `contact`**. `email` was passing by luck. A test that is wrong twice can look right, and the field it happens to mis-score then looks like a generator defect. Both errors had to be found; fixing either alone would have moved the other field into failure.
- **How we fixed it:** Score the **simulated frequency** directly, exactly as the encoder does, instead of a `freq == 1` indicator, and compute attack uniqueness per field by simulating each declared generator rather than assuming it. Row weighting comes out of building the benign sample as **one entry per row** rather than per actor, so it is structural rather than a correction term.
  - *Corrected 2026-08-30.* This line previously read "row-weight the benign probability as `exp(-lam)`". That was an intermediate version. `_p_benign_row_unique`, the closed form it named, was superseded when the predictor moved to scoring frequency, had no caller afterwards, and has now been deleted. The entry named a function that did not run. The finding and every number in it are unchanged; only the description of the mechanism was wrong. Four fields shared the broken predictor and all four were fixed. Corrected deltas: `contact` -0.023, `card.last4` -0.011, `vpa` -0.008, `mc.device_id` -0.000. `email` still exceeds by +0.058 against a predictor spread of 0.006, so that one is a real flag and stays open.

---

### 2026-08-29 — The declared 5.0% floor was not the floor

- **What broke:** spec 2.1e was written claiming 5.0% as the floor of the evasive sweep, derived from the mechanism: a freshly validated card at a micro amount can only reach `incorrect_pin` and `gateway_timeout`, which is 0.40 of the legitimate reason mix, so `0.125 x 0.40 = 0.050`. The generator bottomed out at **10.58%**, more than double it.
- **What we thought was wrong:** nothing, initially. The derivation is arithmetically correct and the number was written into the spec before the sweep ran, which is exactly how it slipped through.
- **What was actually wrong:** the derivation described **one** contribution to the decline rate and was then stated as though it described the total. 35% of bursts end with the issuer blocking the IIN, which ramps the decline rate to 99% over the burst's last 25% **regardless of list grade**, because a block is issuer-side. That is a roughly fixed additive term, `0.35 x 0.25 x 0.5 x (0.99 - base)`. Against an 88% base it is invisible; against a 5% base it is most of what is left.
- **What pointed at it:** the gap between declared and observed widened monotonically with grade, +0.01, +1.34, +2.60, +4.51, +5.75, +5.58 pp. A constant additive term is exactly what a gap that grows as the base shrinks looks like.
- **How we fixed it:** the spec is corrected, and the sweep prints declared and observed side by side with the cause named, so the two are never read as the same quantity. The generator is **unchanged**: the floor is a property of the attack, not a limit of the fixture. An operator cannot buy their way below the rate at which their own IINs get blocked, and pretending otherwise to hit a rounder number would be exactly the tuning we do not do.
- **The part worth remembering:** a number derived correctly from a mechanism is still only a claim about that mechanism. This one was written into the spec as a claim about the *data* before any data existed to check it against.

---

### 2026-08-29 — The combined baseline is more fragile than either of its parts

- **What broke:** baseline 3, "combined volume and decline", was built to be the strongest baseline, the one a competent payments team would actually deploy. Under evasion it became the **worst** of the four. At `v=1.00` its PR AUC is **0.1464**, below the decline baseline's 0.2887 and far below volume's 0.9281, even though volume alone was completely unaffected.
- **What we thought was wrong:** a scoring bug, since a detector combining two signals should not score below the worse of the two.
- **What was actually wrong:** nothing was wrong with the code. `score_combined` is `min(volume / vol_ref, decline / dec_ref)`, chosen so a burst has to be **both** busy and failing. That conjunction is exactly the fragility. When the attacker defeats one component, the `min` follows it down and discards the component that still works. A conjunctive detector is only as strong as the signal its attacker chooses to defeat, and the healthy volume signal it still had access to was thrown away.
- **What pointed at it:** the collapse is later than the decline baseline's but sharper. Combined holds 0.9161 at `v=0.75` where decline is already at 0.6763, then falls to 0.7775 and 0.1464. It inherits its component's failure with a lag, not an immunity.
- **The part worth remembering:** "require both signals" reads as conservative and is the opposite under adversarial pressure. Requiring both means an attacker only has to break one. Nothing was changed in response: the baseline is a faithful implementation of a rule teams really deploy, and its fragility is a finding about that rule rather than a defect in our copy of it.

---

### 2026-08-30 — Households shared a device but not an address, and that was a planted answer

> **RESOLVED 2026-08-30.** Kept in full below because it is the record of what happened. The fix is now applied: the pipeline regenerates from seed, so its first run picked up the corrected `population.py` and every dataset on disk is post-fix. The scoring-time patch has become a no-op and is retained only as a regression guard. **Measured cost of the whole detour: 0.0009.** The patch had put the ring detector at 0.5811; the real fix puts it at 0.5820. The 72 card testing metrics were unchanged and T3 passed at all six grades, both as predicted.

**The most consequential defect in the project so far.** It did not add a signal to the attack. It removed one from the benign population, which is harder to see and does the same damage.

**What broke.** The ring detector, scored at the account level on the test split, returned **precision 0.9444, recall 0.9444, PR AUC 0.9291**. The structure oracle for the same pattern reaches **0.4400**, and the oracle is allowed to read the sealed store at configuration time. A detector built from the event stream alone had apparently beaten a label-informed oracle by more than double.

**What prompted the check was that number, not the report.** Nothing failed. No test flagged it, the isolation checks were clean, the train/test protocol was correct, and every acceptance test behaved exactly as before. Had the result been 0.52 it would have been written up and shipped. It was investigated only because beating an informed oracle by that margin is not a thing that happens, and a result too good to be true is a claim to disbelieve first and verify second.

**What we thought was wrong.** A label leak in the harness, or the train sweep seeing test structure through the shared cluster construction.

**What was actually wrong.** Neither. `src/generator/population.py` built households by copying **only the device id** between members:

```python
shared = actors[group[0]].device_id
for k in group[1:]:
    actors[k].device_id = shared        # and nothing else
```

Each actor kept the pincode it had drawn independently from the weighted table. So two people the generator modelled as living in one home lived at two different postcodes. Measured on `data/sample`: of **652 benign device-sharing groups observed in the window, exactly 1 also shared a pincode. 0.15%.**

The ring pattern's defining structure is that its members share a drop address and, partially, a device. With benign households failing to share an address, **"shares a pincode AND shares a device" had almost no benign population to compete with.** It was not a hard signal the detector had learned to find. It was close to a pure label, and stage 1 of the detector confirmed it by scoring **precision 1.0000 with zero false positives** across 12,482 accounts. A hand-built two-attribute rule achieving perfect precision at a 0.144% base rate is not a detector working.

**The tell we walked past first.** Detection latency came out **negative**: -6.2 days for r01 and -10.1 days for r02, meaning the detector fired before either ring transacted at all. That was rationalised on the spot as a real and even interesting property, since ring members share the drop address and device from account setup and the structure genuinely does exist through the dormancy period. That reasoning is not wrong, and it was still the wrong conclusion to stop at. A detector that identifies a ring before the ring does anything is reading the ring's *construction*, and the right response was to ask what made the construction visible rather than to admire that it was. On the repaired population the same replay gives **+3.8 and +5.9 days**, with one ring of three never detected at all.

**This is the same failure we refused in August, arriving from the other side.** On 2026-08-28 an address hash finer than pincode was rejected, on the grounds that it "would work far too well" because a per-flat identifier has near-zero benign collision, so "two accounts share an address" would be close to a pure label. That judgement was correct and the defect landed anyway, because it was guarded on only one side. We were watching for a signal **planted in the attack** and the gap was an **omission in the benign population**. Both produce a pure label. A missing benign collision is a planted answer, and it leaves no artefact to grep for: the ring code was correct, the household code looked correct, and only the joint distribution was wrong.

**The numbers, patched against unpatched.**

| detector | PR AUC as generated | PR AUC households fixed |
|---|---|---|
| pincode baseline | 0.0037 | 0.0036 |
| stage 1 only, conjunction | 0.3343 | 0.2291 |
| **ring detector, conj + drop address** | **0.9291** | **0.5753** |

**0.9291 against 0.5753.** The gap is the defect. The baseline barely moves, which is itself confirming: the pincode baseline never used the device edge, so it had nothing to lose.

**How we fixed it.** `population.py` now copies the pincode along with the device id. The committed datasets were **not** regenerated, for the reason recorded in decisions.md, and the reported ring numbers come instead from a label-free equivalent patch applied at scoring time. The generator fix is real and is there for whoever regenerates next.

**The part worth remembering.** Every acceptance test passed throughout. T1 through T8 are built to catch a signal planted in the attack, and this was the absence of a signal in the benign population, which none of them measures. The thing that caught it was a number that was better than it had any right to be.

---

### 2026-08-30 — Density as a multiplier ranked households above rings

- **What broke:** on the repaired population the detector's score put two-person households **above** an eleven-member ring.
- **What was actually wrong:** the scoring function, and this one was ours. The score was `component_size x density`, where density is the component's share of its pincode's population. A family of two sharing a device and an address is a component of 2 on a pincode of 2, so density is 1.0 and the score is **2.00**. A ring of eleven with four device-linked members is density 0.36 and scores **1.45**. Rewarding purity ranks the smallest possible cluster highest.
- **How we fixed it:** the separating idea is not purity but **reach**. A drop address collects for more people than a household contains, so `min_pin_population` gates a pincode out unless it serves enough accounts to be implausible as one family. Swept on train like every other parameter; the train sweep selected 4.
- **The part worth remembering:** the flaw was invisible in the generated population and only appeared under the counterfactual, because with households at two different postcodes there were no small pure clusters to rank wrongly. The generator defect was **hiding a scoring defect**. Fixing the measurement found the second bug for free.

---

### 2026-08-30 — The determinism check failed, and it was right to

- **What broke:** two full pipeline runs on the same seed produced different `results.json`. Run 1 `0a1499c5...`, run 2 `c070d551...`. Under the project's own rule that a number which does not reproduce from seed matters more than the tooling around it, everything stopped here.
- **What we thought was wrong:** the obvious suspect was sklearn. T1, T2, T5 and T6 all fit gradient-boosted models, and multi-threaded float reduction reorders summation, which can move the last bits of a score. Threads were already pinned to 1 for exactly this reason, so the working theory was that the pinning was not reaching the subprocesses.
- **What was actually wrong:** nothing in any model, and nothing in any detector. Diffing the two artifacts key by key: **630 of 632 leaf values were byte identical.** The two that moved were `streaming.throughput_events_per_sec`, 453.0 against 553.0, and its reciprocal `throughput_ms_per_event`. A stopwatch reading. It varies with machine load and it was sitting in the file that is required to be reproducible.
- **What pointed at it:** the diff, immediately. The failure looked alarming as a hash mismatch and was trivial as a diff, which is the argument for comparing structures rather than digests when something fails. The same run showed the streaming stage taking 51.2 minutes against 21.5 minutes earlier with no code change, which is the same machine-load variance showing up in a place we were already treating as untrustworthy.
- **How we fixed it:** throughput moved to `run_meta.json`, where wall time and stage timings already live. The split was always meant to be numbers on one side and measurements-of-the-machine on the other; throughput was misfiled. Verified without paying for a third two-hour run: both existing artifacts, re-canonicalised with the two keys removed, hash to `a99decec1e636f9a...` and are byte identical.
- **The part worth remembering:** the check earned its keep on its first real use, and what it caught was **our own artifact design**, not a model defect. A reproducibility check that only ever passes has not been tested. This one failed, the failure was cheap to diagnose because the artifact is structured, and the thing it found was a category error we would otherwise have shipped: a performance measurement presented as a result.

---

### 2026-08-30 — Shipped a syntax error and verified it with an import that could not see it

- **What broke:** `make evaluate` crashed at stage 6 of 6 with `SyntaxError: unterminated string literal` in `tests/runtime/evaluate_stream.py`, after 39 minutes of compute had already run.
- **What was actually wrong:** a `print("
  ...")` written through an unquoted shell heredoc, where the escape collapsed into a real newline and split the string across two lines. **The same mistake had been made and fixed in `pipeline/evaluate.py` less than an hour earlier.**
- **Why the check missed it:** it was verified with `python -c "import pipeline.evaluate"`. That imports the pipeline, which references the streaming harness only as a *subprocess argument string*, so the broken module was never loaded. The check passed and proved nothing about the file that had just been edited.
- **How we fixed it:** the string is repaired, and verification is now `python -m compileall src tests pipeline` plus an import of every module found by `pkgutil.walk_packages`. A file that has been edited is compiled, not assumed.
- **Recovery, and the second lesson:** the run was not wasted. Stages 1 to 5 had already archived their logs and all of them still parse, so only stage 6 needed repeating. That is now a supported path, `--stage 06_streaming` followed by `--from-logs`, rather than something to reconstruct by hand each time.
- **The part worth remembering:** a verification that cannot fail is worse than none, because it converts an unchecked edit into a checked-looking one. The import proved the pipeline imports; the thing that had changed was a subprocess entry point, which no import reaches. **Check the artifact you edited, not the one that calls it.**

