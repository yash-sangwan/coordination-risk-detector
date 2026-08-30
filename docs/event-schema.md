# Event Record Schema — Coordination Detector (Track 02)

**Status:** design only. No generator, no detector, no code.
**Grounded in:** [`api-shapes/`](api-shapes/) (real test-mode captures, 2026-08-28) and the API probe findings, which are kept in a private working note and deliberately not published.

## Scope and stated assumptions

Two things I decided rather than asked, both stated here so they are easy to overrule:

1. **"Accounts" means customer accounts on a single merchant's stream**, not merchant accounts across Razorpay. Track 02 is merchant-side defense, so the detector sees one merchant's traffic.
2. **The record is one payment *attempt*, not one payment.** Card testing is defined by declines, so failed attempts are first-class rows, not omissions.

## The governing rule: as-of-attempt

Every field in this record must be **knowable at the instant the attempt hits the gateway**. That single rule does most of the anti-leak work, because the strongest label signals in payments (chargeback, refund, settlement) are all *later* facts. A record that carries them is not a detector input, it is the answer.

So the design splits in two:

| Store | Contents | Used for |
|---|---|---|
| **Event record** (this document) | Only what is true at attempt time | Features. The only thing the detector may read. |
| **Outcome store** (separate, out of scope here) | Chargeback, refund, dispute, settlement, and the ring/campaign truth | Labels only. Joined by `id` **after** scoring, never before. |

Keeping the outcome store physically separate is what makes "we did not plant the signal" checkable rather than merely asserted.

---

## The record, field by field

Modelled on the **single-fetch** representation of Razorpay's payment entity, not the list representation. The probe found these disagree (see Deviations §4).

### Envelope

| Field | Type | Why it is here |
|---|---|---|
| `id` | `string` | `pay_` + 14 base62 chars, matching the observed `order_TUyazxwv5PmVvf` / `cust_TUycL7Df2L6nrf` format. **Must be monotonic with `created_at`** — every ID captured in the probe shared a time-ordered prefix (`TUya…`, `TUyb…`, `TUyc…` within one minute). Random IDs would be both unrealistic and, if fraud rows were minted in a separate block, an ordering leak. |
| `entity` | `string` | Constant `"payment"`. Real Razorpay records carry it; cheap fidelity. |
| `created_at` | `integer` | Unix **seconds**, never ISO strings, never milliseconds. Every captured timestamp was integer seconds. This is the axis both detectors run on: bursts are density in `created_at`, and any train/test split must be chronological on it. |
| `order_id` | `string \| null` | `order_` + 14. Links attempts to one checkout. Multiple attempts against one `order_id` is the honest signal for retry-hammering, and it is genuinely null for some flows. |

### Money

| Field | Type | Why it is here |
|---|---|---|
| `amount` | `integer` | **Paise, never float.** Card testing lives at the bottom of this range; the amount distribution is a real signal. |
| `currency` | `string` | `"INR"` dominant. `international:true` rows may differ. |
| `international` | `boolean` | Known at attempt. Cross-border mix shifts during some attacks. |

### Method and instrument

| Field | Type | Why it is here |
|---|---|---|
| `method` | `string` | Observed live in the downtime feed: `card`, `netbanking`, `upi`, `fpx`. Also `wallet`, `emi`. Method mix is a burst signature. |
| `card` | `object \| null` | Populated when `method:"card"`. Sub-fields below. |
| `card.iin` | `string` | **6-digit issuer identification number.** The single most important linking key for card testing, because a BIN walk reuses the IIN while every other card field varies. See Deviations §2 — this is an addition. |
| `card.last4` | `string` | Real field. Weak alone, useful for exact-card reuse. |
| `card.network` | `string` | `Visa`, `MasterCard`, `RuPay`, `Diners`, `Amex`. |
| `card.type` | `string` | `debit` / `credit`. |
| `card.issuer` | `string \| null` | Bank code. The downtime feed uses the same vocabulary (`BKID`, `PUNB`, `CNRB`), so codes should come from that set. |
| `card.sub_type` | `string` | `consumer` / `business`. |
| `vpa` | `string \| null` | Populated when `method:"upi"`, format `local@handle`. **The handle is a weak link and the local part is a strong one** — millions legitimately share `@okhdfcbank`, but `@kotak811` appeared as a real handle in the downtime feed. Store whole; let the detector decide how to split. |
| `bank` | `string \| null` | Netbanking bank code. |
| `wallet` | `string \| null` | Wallet provider. |

### Contact

| Field | Type | Why it is here |
|---|---|---|
| `email` | `string` | Linking key. Domain and local-part shape both matter. |
| `contact` | `string` | Phone. The probe showed **inconsistent normalisation in real data** — `"+919000000000"` on the payment link, `"9000000001"` on the customer. Carry that inconsistency; a detector that only matches on exact string equality should be penalised for it. |

### Attempt outcome (as-of-attempt only)

| Field | Type | Why it is here |
|---|---|---|
| `status` | `string` | Restricted to `created` / `authorized` / `failed`. **`captured` and `refunded` are excluded** — those are later merchant actions. |
| `error_code` | `string \| null` | Documented values e.g. `BAD_REQUEST_ERROR`, `GATEWAY_ERROR`. |
| `error_description` | `string \| null` | Human text. |
| `error_source` | `string \| null` | Documented: `customer`, `business`, `gateway`, `internal`. The probe observed `business`, `internal` and `gateway` in live errors. |
| `error_step` | `string \| null` | e.g. `payment_authentication`, `payment_initiation` (both observed). |
| `error_reason` | `string \| null` | e.g. `payment_cancelled`, `input_validation_failed`. **The decline-reason mix is the core card-testing signal** — a CVV walk shows as a concentrated `INCORRECT_CVV`-class reason against one IIN. |
| `acquirer_data` | `object` | `{auth_code: string\|null}`. Non-null iff approved, so it partly mirrors `status`; kept for fidelity, flagged as redundant. |

### `notes`

| Field | Type | Why it is here |
|---|---|---|
| `notes` | `object \| array` | **Polymorphic, and this is a real quirk worth reproducing:** the probe returned `{"probe":"…"}` when set and `[]` (empty *array*, not object) when unset. Carry it, because code that assumes `dict` will break on real data. **See the leak list — this field is the most likely place to accidentally leak the answer.** |

### `merchant_context` — not from Razorpay

Razorpay's payment entity carries **no device, IP, session or address data**. A real merchant has it from their own checkout and joins it on `payment_id`. Modelling that join honestly is better than pretending the gateway returns it, so it lives in a clearly separated object. Full rationale in Deviations §3.

| Field | Type | Why it is here |
|---|---|---|
| `account_id` | `string \| null` | Merchant-side customer account. Null for guest checkout. |
| `device_id` | `string \| null` | Checkout SDK fingerprint. |
| `ip_prefix` | `string` | **/24 only, not the full address.** The /24 is the actual linking unit for a bot pool, and truncating avoids storing a personal identifier for no analytic gain. |
| `user_agent_hash` | `string` | Hashed, not raw. Exact-collision linking without a fingerprinting surface. |
| `session_id` | `string` | Groups attempts within one checkout visit. |
| `attempt_seq` | `integer` | Nth attempt in this session. Retry-hammering signal, knowable at attempt. |
| `checkout_ms` | `integer` | Milliseconds on the checkout page before submit. Bots are fast, but so are returning customers with saved cards — see the leak warning below. |
| `shipping_pincode` | `string \| null` | 6-digit. Drop-address reuse is a genuine ring signal. |
| `account_age_days` | `integer \| null` | Age at attempt time. Standard, legitimately available, and **conditionally dangerous** — see the leak warning below. |

---

## Shared attributes usable for linking

Coordination detection is a graph problem: events are nodes, a shared attribute is an edge. These are the edge types, ordered by how much a single collision should be worth.

| Attribute | Strength | What two coordinated events would actually share |
|---|---|---|
| `card.iin` | **Strong** | A BIN walk tests many cards from one issuer range. Everything else about the card varies; the IIN does not. |
| `merchant_context.device_id` | **Strong** | One machine driving many "customers". |
| `merchant_context.ip_prefix` | **Medium** | A bot pool sits in a few /24s. Weakened by carrier-grade NAT, which is very common in India. |
| `merchant_context.shipping_pincode` | **Medium** | Reshipping rings converge on drop addresses. |
| `email` (domain, and local-part shape) | **Medium** | Disposable-domain reuse, or generated local parts sharing a pattern. |
| `contact` | **Medium** | Number-block reuse. Weakened by the normalisation inconsistency noted above. |
| `vpa` local part | **Medium** | Reused UPI identity. |
| `vpa` handle | **Weak** | Millions share one PSP handle legitimately. Should carry almost no weight alone. |
| `card.last4` + `card.network` | **Weak alone** | Only meaningful combined with `iin`. |
| `account_id` | **Weak for rings** | Ring members deliberately use distinct accounts. Useful for velocity, not for linking. |
| `order_id` | **Not a ring edge** | Links attempts within one checkout, not accounts to each other. |

**The load-bearing point:** every one of these collides legitimately too. Families share devices, offices share a /24, a hostel shares a pincode, one issuer's IIN covers millions of honest cards. A detector that treats any single collision as proof is wrong, and a generator that never produces benign collisions has planted the answer. Edge weight has to be earned from the observed benign collision rate, not assigned by fiat.

---

## Considered and rejected as leaky

### Category A — definitional. The field *is* the answer.

| Rejected | Reason |
|---|---|
| `is_fraud`, `label`, `fraud_type` | Definitional. |
| `ring_id`, `campaign_id`, `attack_id`, `burst_id` | These are the exact thing the detector is supposed to output. Present in the record, the task is a `GROUP BY`. |
| `risk_score`, `fraud_probability` | A precomputed answer. If the generator wrote it, the model is reading the generator. |
| `is_bot`, `device_is_emulator` | Only honest if it were a real third-party signal with a real error rate. Derived from ground truth it is the label wearing a hat. |

### Category B — temporal leaks. True later, not at attempt time.

These are the dangerous ones, because they are all **real Razorpay fields** and so feel legitimate.

| Rejected | Reason |
|---|---|
| `dispute_id`, dispute status, `chargeback_at` | For chargeback fraud this **is** the label, and it arrives weeks later. The probe confirmed disputes cannot even be created in test mode, so any dispute field in a synthetic record is fabricated *and* future information. |
| `amount_refunded`, `refund_status` | Post-attempt. A refunded payment is often a resolved fraud. |
| `fee`, `tax` | Only populated after capture. Their mere presence correlates with success. The documented failure example has `fee: null` while the success example has `fee: 198` — so nullness alone would leak outcome. |
| `captured: true`, `settlement_id`, `invoice_id` | Later merchant or Razorpay actions. |
| `status: "captured"` / `"refunded"` | Same reason; hence the restricted status enum above. |

### Category C — derived features. Not raw event data.

| Rejected | Reason |
|---|---|
| `velocity_1m`, `velocity_5m`, `attempts_last_hour` | Windowed aggregates are the **detector's** job. Precomputing them into the record hands over the design and gives the generator a place to plant the exact discriminator. |
| `shared_device_count`, `ring_size`, `entity_fanout` | Computed from the graph the detector is supposed to build, and `ring_size` is a direct function of the truth. |
| `distance_from_usual_location` | Requires a per-account history model that is itself a detector. |

### Category D — fields kept, with a warning about the generator

Not rejected, because they are legitimately available at attempt time and a real system would use them. But each is a place where a careless generator turns a fair field into a planted one.

| Field | The failure mode to avoid |
|---|---|
| `merchant_context.device_id` | If legitimate users get unique devices and fraudsters draw from a shared pool, device-sharing *is* the label. The generator must produce benign sharing: households, shared machines, office NAT. |
| `merchant_context.checkout_ms` | If only fraud is fast, speed is the label. Returning customers with saved cards are also fast; slow, confused fraudsters exist. |
| `account_age_days` | If every fraud account is new and every legit account is old, this one integer solves the task. Stolen and aged accounts must appear on the fraud side, brand-new accounts on the legit side. |
| `amount` | If ₹1–20 appears only in attacks, the amount threshold is the label. Legitimate micro-transactions must exist. |
| `error_reason` | If declines only ever happen to fraud, the decline flag is the label. Legitimate payments fail constantly, which is the entire premise of Track 03. |
| `created_at` | If fraud is bursty and legit traffic is smooth Poisson, arrival density is the label. Legitimate traffic has flash sales, paydays and 8pm peaks. |
| `notes` | **The most likely accidental leak in the whole record.** Generator metadata (seed, archetype, persona, row index) written into `notes` is a total giveaway, and it is easy to do by accident — the probe's own captured order carries `notes:{"probe":"buildathon-capability-check"}`. Must be scrubbed or held to merchant-realistic content. |

### Category E — ordering and identity leaks, which are not fields at all

Worth writing down because they defeat a clean field list:

- **Row order.** If attack rows are appended contiguously, sort order is the label. The stream must be interleaved and sorted by `created_at`.
- **ID block allocation.** If fraud IDs are minted from a separate counter, the monotonic ID prefix leaks membership. All IDs must come from one time-ordered sequence.
- **String tells.** `attacker_01@`, `bot-device-7`, sequential `account_id`s for ring members. Fraud identities must be drawn from the same generator as legitimate ones.

---

## Deviations from Razorpay's real shapes

**§1. No real `payment` entity was ever captured — the most important caveat here.**
The probe's `GET /v1/payments` returned `{"count":0,"items":[]}`, and creating a paid payment needs the hosted Checkout UI, which was out of scope. So every payment field above is taken from Razorpay's **documented** entity ([api/payments/entity](https://razorpay.com/docs/api/payments/entity/)), not from an observed response. The envelope, ID format, timestamp type, `notes` polymorphism and contact-normalisation quirks *are* observed, from orders, customers, payment links and downtimes. This distinction should be stated wherever the schema is presented, not buried.

**§2. `card.iin` added.**
Razorpay's card object exposes `token_iin` but not a plain `iin`. Without an IIN there is no BIN-walk detection at all, which is the canonical card-testing pattern. `iin` is Razorpay's own vocabulary rather than an invented name, so this extends their model rather than departing from it.

**§3. `merchant_context` added wholesale.**
No device, IP, session, user-agent or address data exists anywhere in Razorpay's payment entity. Coordination detection is impossible without some of it. Two honest options existed: pretend the gateway returns it, or model the join a real merchant would actually perform. The second is modelled, in a clearly named separate object, so nobody later mistakes it for gateway data. `ip_prefix` is truncated to /24 rather than storing full addresses.

**§4. Single-fetch representation chosen, and real fields omitted.**
The probe found list and single-fetch responses disagree for the same entity (`amount_paid` came back `0` from `GET /v1/orders/:id` but `null` from `GET /v1/orders?count=1`). This schema models single-fetch. Separately, several genuine Razorpay fields are deliberately **omitted** — `fee`, `tax`, `amount_refunded`, `refund_status`, `invoice_id`, `card_id`, `card.token_iin`, `card.emi` — for the Category B reasons above. Omitting real fields is itself a deviation and is recorded as one.

**§5. Restricted `status` enum.**
Real Razorpay payments reach `captured` and `refunded`. This record stops at `created` / `authorized` / `failed`, because the later states are outcome data.

**§6. Collection wrappers not modelled.**
If these records are ever served through a Razorpay-shaped list endpoint, note the probe's finding that empty-collection envelopes are **not uniform**: `/v1/disputes` returns neither `items` nor `count`, `/v1/settlements` includes `has_more`, and `/v1/settlements/recon/combined` does not. Out of scope for the record itself, relevant to anything wrapping it.
