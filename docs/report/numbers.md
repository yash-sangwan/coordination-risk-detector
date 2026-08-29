# Numbers

Append only. Every metric gets an entry, written when it is produced, never reconstructed.

Each entry must carry all four:

1. **Date** the number was produced.
2. **The number**, with its uncertainty if it has one.
3. **The data** it ran on: which dataset, which split, how many rows, and whether that split had been scored before.
4. **The exact command** that produced it, copy-pasteable.

A number without a reproducing command does not go in this file.

Newest entries go at the bottom.

---

## Ground rules

- Report the operating point that was chosen and why, not the best one found afterwards.
- A held-out split is scored **once**. If it gets scored again, that is a new entry saying so, not an edit of the old one.
- If a number turns out to be unreproducible, it stays here with a correction appended below it, and the incident goes in [what-broke.md](what-broke.md). Numbers are never silently deleted.

---

## Entries

### 2026-08-29 — Evasive card testing sweep (spec 2.1e)

**Data.** Six generated datasets, `data/evasive/v000` through `v100`, seed 42, 30 days, 40,000 actors. 67,961 events at `v=0.00` and 68,039 at every other step. `v=0.00` is byte-identical to `data/sample` (SHA-256 match on `events.jsonl`, `sealed.jsonl` and `manifest.json`). No split: these are generator-side descriptive measurements, not model scores, and no detector has been run on this data.

**Command.**

```
python -m src.generator.sweep --seed 42 --days 30 --actors 40000 --out data/evasive
python -m src.generator.sweep --report-only --out data/evasive     # reprint, no regeneration
```

**Observed decline rate against declared, and volume.**

| grade v | declared | observed | benign all | benign card | attack events | attack ev/min per burst |
|---|---|---|---|---|---|---|
| 0.00 | 88.00% | 87.99% | 5.57% | 12.97% | 3739 | 9.2 - 40.5 |
| 0.25 | 67.25% | 68.59% | 5.57% | 12.97% | 3817 | 9.2 - 42.8 |
| 0.50 | 46.50% | 49.10% | 5.57% | 12.97% | 3817 | 9.2 - 42.8 |
| 0.75 | 25.75% | 30.26% | 5.57% | 12.97% | 3817 | 9.2 - 42.8 |
| 0.90 | 13.30% | 19.05% | 5.57% | 12.97% | 3817 | 9.2 - 42.8 |
| 1.00 | 5.00% | 10.58% | 5.57% | 12.97% | 3817 | 9.2 - 42.8 |

Declared is the list-grade mechanism's contribution only. The gap is the issuer-block ending, which ramps to 99% whatever the grade. See [what-broke.md](what-broke.md), 2026-08-29.

**Coordination, the thing required not to move.** Largest spread on any coordination measure across the five evasive steps: **0.0000 pp**. Across all six including the control: 1.1804 pp, entirely the control's separate RNG sequence. Decline-rate spread over the same steps: 77.4072 pp.

---

### 2026-08-29 — Acceptance tests across the 2.1e sweep

**Data.** The six datasets above, each scored once. **Command.**

```
python -m tests.acceptance.sweep_runner data/evasive
```

| test | v000 | v025 | v050 | v075 | v090 | v100 |
|---|---|---|---|---|---|---|
| T1 single-feature ceiling | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| T2 label shuffle | PASS | PASS | **FAIL** | PASS | PASS | PASS |
| T3 benign collisions | PASS | PASS | PASS | PASS | PASS | PASS |
| T4 ordering and identity | PASS | PASS | PASS | PASS | PASS | PASS |
| T5 string and metadata hygiene | PASS | PASS | PASS | PASS | PASS | PASS |
| T6 confounder survival | PASS | PASS | PASS | PASS | PASS | PASS |
| T7 determinism | PASS | PASS | PASS | PASS | PASS | PASS |
| T8 signal floor | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| T8 oracle isolation | PASS | PASS | PASS | PASS | PASS | PASS |

T1 and T8 fail identically at every step **including the control**, which is byte-identical to `data/sample`. Both are the two already-recorded open items and neither is caused by the variant:

- T1: `email` exceeds its mechanism by +0.058 at v000 and +0.053 at every other step. One field, the same field, throughout.
- T8: ring recall@P0.70 at the account level is 0.4400 against a 0.60 floor at every step. Card-testing recall@P0.80 **passes everywhere**: 1.0000, 1.0000, 1.0000, 0.9979, 0.9955, 0.9955. The oracle's card-testing ceiling does not depend on the decline rate.

**T1a mechanism tracking.** The `status` and `error_*` predictors now read the run's declared list grade, so they fall with the variant instead of passing trivially. Observed stays just under predicted throughout:

| field | v000 observed / mech | v050 observed / mech |
|---|---|---|
| `status` | 0.8903 / 0.9128 | 0.6897 / 0.7078 |
| `error_reason` | 0.9197 / 0.9412 | 0.7154 / 0.7293 |
| `error_code` | 0.8961 / 0.9186 | 0.6915 / 0.7099 |

---

### 2026-08-29 — T2 resolution check: the v050 failure is the test, not the data

**Data.** `data/evasive/v050`, unchanged, 50 permutations per seed, only the permutation seed varying. **Command.**

```
python -m tests.acceptance.t2_resolution data/evasive/v050
```

| permutation seed | median | \|median - 0.50\| | T2 verdict |
|---|---|---|---|
| 0 | 0.4615 | 0.0385 | FAIL |
| 1 | 0.5395 | 0.0395 | FAIL |
| 2 | 0.5397 | 0.0397 | FAIL |
| 3 | 0.4834 | 0.0166 | PASS |
| 4 | 0.4999 | 0.0001 | PASS |

Median ranges 0.4615 to 0.5397, spread **0.0782**, against a T2 threshold of **0.0300** on the same statistic. **3 of 5 seeds fail on identical data**, and the failures land on both sides of 0.50. T2 at 50 permutations cannot resolve the difference it is asked to test.

There is also no trend across the sweep: v050's 0.4615 sits between v025's 0.5070 and v075's 0.4918, both passing. A leak driven by the decline mechanism would move in one direction, not dip in the middle.

**Nothing was changed in response to this.** The threshold stands as written and the failure is reported as a failure. Stabilising the median to 0.03 needs roughly `(0.0782/0.03)^2` = about 7x the permutations, so around 350, which is a cost decision and not one to make silently inside a test run.

---

### 2026-08-30 — All four detectors across the 2.1e sweep, thresholds frozen

**Data.** The six datasets `data/evasive/v000` to `v100`. Every window, floor and threshold chosen **once** on the `v=0.00` train split and applied unchanged to all six. Nothing refitted per step. Each grade's test split scored once, except `v=0.00`, which was scored before for the graph detector commit and reproduces those numbers exactly (0.9402 / 0.9583 / 0.9446 / 0.9447 PR AUC), which is why it is here.

**Frozen parameters.** volume `{window_s: 180, thr: 24.333}`; decline `{window_s: 60, min_events: 5, thr: 0.625}`; combined `{window_s: 180, min_events: 5, vol_ref: 24.333, dec_ref: 0.625, thr: 0.3288}`; graph `{window_s: 180, min_events: 8, thr: 0.4741}`.

**Command.**

```
python -m tests.detector.evaluate_sweep data/evasive
python -m tests.detector.diagnose_sweep data/evasive
```

**PR AUC against observed decline rate.**

| detector | 88.0% | 68.6% | 49.1% | 30.3% | 19.1% | 10.6% |
|---|---|---|---|---|---|---|
| baseline 1 rolling volume | 0.9402 | 0.9281 | 0.9281 | 0.9281 | 0.9281 | 0.9281 |
| baseline 2 rolling decline | **0.9583** | 0.9421 | 0.8916 | 0.6763 | 0.4309 | **0.2887** |
| baseline 3 combined | 0.9446 | 0.9423 | 0.9332 | 0.9161 | 0.7775 | **0.1464** |
| GRAPH fanout vs overlap | 0.9447 | **0.9451** | **0.9451** | **0.9451** | **0.9451** | **0.9451** |

**Recall.** decline 0.9944, 0.4847, 0.0119, 0.0000, 0.0000, 0.0000. combined 0.9803, 0.9801, 0.9801, 0.7707, 0.0000, 0.0000. volume 0.8723 then 0.8791 flat. graph 0.9933 then 0.9921 flat.

**Precision.** volume 0.9434 / 0.9439 flat. graph 0.9364 / 0.9363 flat. decline 0.9399, 0.9521, 0.9130, then 0.0000. combined 0.9396, 0.9366, 0.9366, 0.9385, then 0.0000.

**Bursts missed entirely** (2 in the test split): volume 0 everywhere. graph 0 everywhere. decline 0, 0, 0, **2, 2, 2**. combined 0, 0, 0, 0, **2, 2**.

**False positives / of which in a flash sale.** volume 93/0 then 92/0 flat. decline 113/0, 43/0, 2/0, 0/0, 0/0, 0/0. combined 112/0, 117/0, 117/0, 89/0, 0/0, 0/0. graph 120/0 then 119/0 flat. **No detector produced a single false positive inside a flash sale at any grade.**

**Latency per burst, minutes / attempts before first alert.**

| detector | v=0.00 b02 | v=0.00 b03 | v=1.00 b02 | v=1.00 b03 |
|---|---|---|---|---|
| volume | 2.40m / 66 | 1.62m / 65 | 2.00m / 63 | 1.42m / 65 |
| decline | 0.12m / 3 | 0.05m / 3 | NOT DETECTED | NOT DETECTED |
| combined | 0.58m / 17 | 0.30m / 16 | NOT DETECTED | NOT DETECTED |
| graph | 0.22m / 6 | 0.15m / 6 | 0.22m / 8 | 0.07m / 6 |

The decline baseline's latency degrades before it dies: 0.12m/3 att, then 1.02m/38, then 7.57m/222, then nothing.

---

### 2026-08-30 — Three diagnostics on the sweep

**1. The decline baseline does not invert. It goes silent.** ROC AUC stays at **0.8521** at `v=1.00`, and PR AUC of the negated score is 0.0493, i.e. the base rate. Flipping the sign recovers nothing. The premise that the attack is cleaner than legitimate traffic holds only against benign **card** traffic at 12.97%; the rolling window is method-agnostic and 55% of legitimate traffic is UPI at a 0.8% decline rate, so the benign windowed rate is **1.57%** and the attack's is **9.89%**, still **6.30x** higher.

| v | attack window decline | benign window decline | ratio |
|---|---|---|---|
| 0.00 | 84.29% | 1.77% | 47.61x |
| 0.50 | 46.67% | 1.67% | 27.95x |
| 1.00 | 9.89% | 1.57% | 6.30x |

What fails is the frozen cut at 0.625, which nothing in the test split reaches. **Catching it would require retuning per grade, and that recovers the threshold but not the separation:** refitting on each grade's own train split gives precision 0.5142 and recall 0.8326 at `v=1.00`, F1 0.6358, PR AUC still 0.2887. Roughly one false alarm per true one. Inversion would need the attack below 1.57%, which the issuer-block floor at 10.58% prevents.

**2. The graph is independent of the decline rate, provably.** Its **score vectors are bit-identical** across all five evasive grades: `max |score diff| = 0.00e+00`, `np.array_equal` True for every pair. Not approximately flat, identical. It reads no outcome field and the coordination is byte-identical, so this is what correctness looks like rather than a coincidence.

**3. The volume baseline catches every grade,** as it must, since pacing was deliberately not swept. PR AUC 0.9281 flat, recall 0.8791 flat, zero bursts missed at any grade. This is the honest caveat on every claim above.

---

### 2026-08-30 — T2 at 350 permutations: does not pass at every step, and 350 is not enough

**Command.**

```
python -m tests.acceptance.t2_sweep data/evasive
python -m tests.acceptance.t2_resolution data/evasive/v100 350 0,1,2
```

| grade | median | \|med-0.50\| | 95% band | verdict | wall |
|---|---|---|---|---|---|
| v000 | 0.5002 | 0.0002 | [0.1956, 0.8505] | PASS | 5.0 min |
| v025 | 0.5449 | 0.0449 | [0.2236, 0.8672] | FAIL | 4.8 min |
| v050 | 0.5111 | 0.0111 | [0.1938, 0.8543] | PASS | 4.8 min |
| v075 | 0.5309 | 0.0309 | [0.2088, 0.8535] | FAIL | 4.7 min |
| v090 | 0.5320 | 0.0320 | [0.2143, 0.8471] | FAIL | 4.7 min |
| v100 | 0.5394 | 0.0394 | [0.1965, 0.8485] | FAIL | 4.4 min |

**2 of 6 pass.** Cost: **28.4 min total, 4.7 min per dataset**, against roughly 0.7 min per dataset at 50, so **6.7x**.

**The raise did not fix it, and the reason matters.** 350 came from assuming sqrt(n) convergence on the 50-permutation seed spread of 0.0782. Measured at 350 on `v100` across seeds 0, 1, 2: medians **0.5394, 0.5263, 0.4905**, spread **0.0489**. Seven times the permutations bought a factor of 1.6, not the 2.65 sqrt(n) predicts, because the permutation AUCs are heavy-tailed and the median converges far slower than the normal approximation assumes. The spread is still larger than the 0.0300 threshold.

**Correction to the 2026-08-29 entry.** That entry called the `v050` failure noise and proposed 350 as the fix. The first half stands and the second does not. Mid-run the four evasive grades all sat above 0.50 and looked like a systematic offset; seed 2 then landed at 0.4905, below it. Failures fall on both sides at both counts, which is the signature of noise, so there is no offset to explain.

**Nothing further was changed.** 350 is kept, since it is strictly better than 50 and it is what was asked for. The 0.03 threshold is untouched: loosening a test we failed is not a call to make inside a test run. Worth noting that T2's **other** leg, that the 95% empirical band contains 0.50, **passes at every grade** and is the standard permutation criterion; the failing leg is the added assertion that the median sits at 0.50.

---

### 2026-08-30 — Ring detector, account level, test scored once

**Data.** `data/sample`, 67,961 events, chronological 70/30. Test split: **12,482 accounts, of which 18 are ring members (0.144%)**. All detectors judged over that same common universe, scoring 0 where they have no opinion; without this the pincode baseline is judged over 10,555 accounts and the ring detector over 12,482, which is not a comparison. Parameters swept on train, frozen before test. **Command.**

```
python -m tests.detector.evaluate_ring data/sample
python -m tests.detector.diagnose_ring data/sample
```

**Two populations, because the first one is not a detection result.**

| detector | PR AUC as generated | PR AUC households fixed | delta |
|---|---|---|---|
| pincode baseline | 0.0037 | 0.0036 | -0.0000 |
| stage 1 only, conjunction | 0.3343 | 0.2291 | -0.1052 |
| **RING DETECTOR, conj + drop addr** | 0.9291 | **0.5753** | -0.3538 |

The left column is an artefact: benign households share a device but not a pincode, so of **652 benign device-sharing groups only 1 also shares a pincode (0.15%)**, and the conjunction is close to a pure label. See [what-broke.md](what-broke.md), 2026-08-30. **The right column is the result.**

**Test split, households-fixed population, scored once.**

| detector | precision | recall | PR AUC | flagged | of |
|---|---|---|---|---|---|
| pincode baseline | 0.0037 | 0.9444 | 0.0036 | 4,596 | 12,482 |
| stage 1 only, conjunction | - | - | 0.2291 | 0 | 12,482 |
| RING DETECTOR | - | - | **0.5753** | 0 | 12,482 |

Precision and recall at the frozen threshold are 0.0000 because the train-tuned cut of 2.2857 flags nothing on test, where the operating point is 1.6. That is a threshold-transfer failure, not a ranking failure, and it is a real limitation: clusters are rebuilt per split, so a score scaling with observed cluster size scales with split length. The curve below is what to read.

**Full precision-recall curve, best precision at each achievable recall.**

| recall | pincode baseline | stage 1 only | RING DETECTOR |
|---|---|---|---|
| 0.2222 | - | **1.0000** | - |
| 0.3333 | - | 0.0536 | - |
| 0.5556 | 0.0033 | - | **1.0000** |
| 0.9444 | 0.0044 | - | 0.0506 |
| 1.0000 | 0.0014 | 0.0014 | 0.0014 |

**Recall at fixed precision:** pincode baseline 0.0000 at every level from P>=0.30 to P>=0.90. Stage 1 only, 0.2222. **Ring detector, 0.5556 at precision up to and including 1.0000** — 10 of 18 ring accounts caught with **zero false positives**.

**Where that lands.** Above the structure oracle's ceiling of 0.4400 at P0.70, below the T8 floor of 0.60. Against the pincode baseline it is 0.5556 recall at perfect precision versus 0.0000 recall at any precision above 0.0044, and 10 accounts flagged instead of 4,596.

**Detection latency per ring**, days from the ring's first fraudulent event to its first alert, replayed daily on what was visible at the time, frozen threshold:

| ring | members | events | first fraud | latency | never caught |
|---|---|---|---|---|---|
| r00 | 7 | 56 | day 8.9 | NOT DETECTED | True |
| r01 | 7 | 33 | day 7.2 | +3.8 days | False |
| r02 | 11 | 93 | day 11.1 | +5.9 days | True |

One of three rings is never detected. On the population as generated the same replay gives **negative** latencies of -6.2 and -10.1 days, i.e. detection before the ring transacts at all, because members share the drop address and device from account setup and the structure exists throughout dormancy.

**False positives:** 0 at the operating point on the fixed population. On the population as generated, 1, an account on an 8-account pincode flagged by drop-address propagation without being in a conjunction component itself.

---

### 2026-08-30 — T2 with the median assertion removed

**Command.** `python -m tests.acceptance.t2_sweep data/evasive`

The pass condition is now the single standard permutation criterion: 0.50 inside the empirical null's central 95% interval. 350 permutations retained. Spec T2 carries the argument.

| grade | 95% band | contains 0.50 | median (reported, not asserted) | old verdict |
|---|---|---|---|---|
| v000 | [0.1956, 0.8505] | PASS | 0.5002 | PASS |
| v025 | [0.2236, 0.8672] | PASS | 0.5449 | FAIL |
| v050 | [0.1938, 0.8543] | PASS | 0.5111 | PASS |
| v075 | [0.2088, 0.8535] | PASS | 0.5309 | FAIL |
| v090 | [0.2143, 0.8471] | PASS | 0.5320 | FAIL |
| v100 | [0.1965, 0.8485] | PASS | 0.5394 | FAIL |

**6 of 6 pass**, against 2 of 6 before. The four recovered cells were seed noise: at 350 permutations the median moves 0.0489 across seeds on identical data, which is larger than the 0.03 it was being compared against, and failures fell on both sides of 0.50 at both permutation counts.
