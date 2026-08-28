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
