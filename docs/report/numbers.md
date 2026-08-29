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
