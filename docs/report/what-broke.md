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
