# Coordination risk detector

Card testing and reshipping rings on a synthetic Razorpay-shaped payment stream.
Razorpay AI Buildathon, Track 02.

**This file is generated.** Every number below is substituted from
`results/results.json` by `python -m pipeline.cite --render README.template.md`.
A figure that is not in the artifact cannot appear here: the renderer fails on an
unknown key rather than passing it through.

## What we found

For ordinary card testing, a rolling decline rate is already enough. It scores
0.9583 PR AUC against our graph detector's
0.9447, and it fires earlier. A coordination detector adds
nothing there. We report that first because it is the result that decides whether
the rest is worth reading.

It earns its place against an attacker who controls their decline rate. Working a
card list that has already been validated, the same attack drops the decline
baseline to 0.2887 while the graph detector holds
0.9451. The graph reads who is transacting rather than how
the bank replied, so evading it means changing the attack instead of changing its
outcome.

The practical consequence is what a fixed threshold costs. Set each detector's
threshold once on the easy attack, then leave it alone as the attacker adapts:

| detector | at attack decline | excess cost | as % of achievable |
|---|---|---|---|
| graph, fanout against overlap | 10.6% | Rs 2,548 | **0.79%** |
| rolling volume | 10.6% | Rs 40,696 | 10.51% |
| combined volume and decline | 10.6% | Rs 2,123,214 | 331.94% |
| rolling decline | 30.3% | Rs 1,751,607 | **433.24%** |

Each row is that detector's worst point. The decline baseline's largest absolute
loss is later still, Rs 2,032,544 at a
19.1% decline rate.

The graph detector is the only one whose threshold is worth setting and
forgetting. That, rather than accuracy, is the case for it.

## Reproduce everything

```
python -m pipeline.evaluate
```

Regenerates all six datasets from seed, runs the eight acceptance tests at six
attack grades, all four detectors, the ring detector, the cost model and the
streaming equivalence check, then writes `results/results.json`, a readable
summary, and the chart below. Roughly 45 to 60 minutes. Two runs produce a
byte-identical `results.json`.

---

# Evidence

## The negative result, in full

At the easy end the decline baseline wins on every metric and fires sooner. The
reason is structural rather than incidental: card testing fails from its very
first attempt, so the decline rate is saturated before any coordination structure
has accumulated. There is no window in which the graph knows something the
decline rate does not. We expected the opposite and measured otherwise. The
per-burst detail is in [docs/report/numbers.md](docs/report/numbers.md).

## The evasion curve

![PR AUC against observed decline rate](results/pr_auc_vs_decline.png)

Thresholds are frozen at the easy end and applied unchanged as the attacker
improves their card list. The observed attack decline rate falls left to right.

| detector | 88.0% | 68.6% | 49.1% | 30.3% | 19.1% | 10.6% |
|---|---|---|---|---|---|---|
| rolling volume | 0.9402 | 0.9281 | 0.9281 | 0.9281 | 0.9281 | 0.9281 |
| rolling decline | 0.9583 | 0.9421 | 0.8916 | 0.6763 | 0.4309 | 0.2887 |
| combined | 0.9446 | 0.9423 | 0.9332 | 0.9161 | 0.7775 | 0.1464 |
| **graph** | 0.9447 | 0.9451 | 0.9451 | 0.9451 | 0.9451 | 0.9451 |

Bursts missed entirely, out of two in the test split: at the hard end the decline
baseline misses 2, the combined rule
2, volume and the graph
0.

The evasion is one parameter, the grade of the card list, and it changes the
decline rate and nothing else. Every identity, device, amount and timestamp is
drawn from the same random sequence at every grade, so the curve moves in a single
variable. The generator side is specified in
[docs/generator-spec.md](docs/generator-spec.md), section 2.1e.

## Requiring two signals makes you worse than either one alone

The combined rule fires when traffic is both busy and failing. It is a rule real
teams deploy, and we implemented it to be strong rather than to lose. Under
evasion it becomes the **worst** of the four, at
0.1464 against the decline baseline's
0.2887 and volume's
0.9281, even though the volume signal it had access to was
never affected by the evasion.

The score is the weaker of its two normalised inputs, so when an attacker defeats
one component the rule follows it down and discards the component that still
works. Requiring both signals means an attacker only has to break one.

## Cost, in rupees

Alerts are converted to money by a cost model whose every parameter is tagged
cited, measured, or an assumption we chose, in `src/decision/cost.py`. The
money-optimal operating point is **not** the F1-optimal one for any detector:

| detector | F1 threshold | money threshold | gap |
|---|---|---|---|
| rolling volume | 24.3333 | 11.6667 | Rs 27,644 (27.18%) |
| rolling decline | 0.6250 | 0.5000 | Rs 889 (1.43%) |
| combined | 0.3288 | 0.2667 | Rs 1,134 (1.69%) |
| graph | 0.4741 | 0.3874 | Rs 919 (1.46%) |

F1 is consistently too conservative, and badly so for volume.

The money-optimal threshold moves as the attack changes because the share of
attack attempts that authorise rises from
12.01% to
89.42% across the sweep, so each missed attempt
costs more. That share is measured from the stream, not assumed.

One check inside the model failed, and is reported as a failure. Razorpay publish
that brands lose Rs 400 to 600 to falsely declined orders per Rs 100 saved by
blocking fraud. Our per-event model implies 1.54x, not 4 to 6x, because it
prices the immediate lost order while the citation includes the customer not
returning. The direction is the safe one: it understates the cost of
over-blocking, so any operating point it picks is more aggressive than the
citation would justify.

Acting on an alert is bounded. Three tiers: monitor, step up authentication, hold
for review. **There is no decline action in the code at all**, because at a 4 to
6x penalty an outright block is a bad trade at any precision we can reach.
Removing it from the action set is more reliable than trying to threshold it
safely. Tier boundaries are solved from the cost model rather than chosen, and
every alert emits a record carrying the evidence, the score, the boundary crossed
and the cost of being wrong in each direction.

## Rings, and what we cannot claim about them

Rings are the opposite shape: a few real accounts sharing a drop address over
weeks, never bursty. Scored per account on a held-out split, once.

| detector | PR AUC | recall | precision |
|---|---|---|---|
| pincode baseline | 0.0036 | 0.9444 | 0.0037 |
| **ring detector** | **0.5820** | see below | see below |

The baseline reaches that recall by flagging 4,591 innocent accounts. The
ring detector reaches recall 0.5556 at precision 0.90 or better.

Two limits, both real:

**The operating point does not transfer.** The threshold chosen on train does not
fire on test. The ranking transfers and the cut does not, because with three rings
in the window the variation between rings dominates the variation we were trying
to normalise away. We report the curve, never a frozen operating point. A
deployable version needs a scale-invariant score; we tried four and measured all
of them losing.

**There is no achievability ceiling for rings.** The structure oracle that bounds
our card-testing result cannot find rings on a correct population, and it never
had ring signal independent of a generator defect we later fixed. We declined to
rebuild it around the detector's own design decisions, because an oracle built in
the detector's image measures our approach rather than the achievable signal. So
we do not know how much ring signal is recoverable here, and we say that instead
of quoting a number.

## What we got wrong

The full log is [docs/report/what-broke.md](docs/report/what-broke.md), kept in
the order things happened with the wrong first guesses left in. The entry worth
reading is the household defect, which has two halves.

**First half.** The generator built households that shared a device but not an
address, so two people modelled as living in one home lived at two different
postcodes. That left "shares an address and shares a device" with almost no benign
population, and the ring detector was reading a planted label rather than a
signal. We caught it because the number was far better than it had any right to
be, not because a test failed. Every acceptance test passed throughout: they are
built to catch a signal planted in the attack, and this was a signal missing from
the benign side.

**Second half, which we did not catch.** The same defect was inflating the
structure oracle we had built so that the detector could not grade its own
homework. We fixed the detector, never re-established the reference on corrected
data, and went on quoting a ceiling that had stopped existing. A detector reading
suspiciously high gets investigated within minutes. A fixture reading suspiciously
low looks like the world being hard.

Also in that log: a scale-invariance fix that cost more than the drift it removed
and was reverted; a determinism check that failed on its first real use and turned
out to be catching our own artifact design rather than a model defect; and a
syntax error shipped because it was verified with an import that could not reach
the file that had changed.

## How the numbers are kept honest

Labels live in a sealed store joined only after scoring. No module under `src/`
may import from `tests/`, and no inference module may mention the outcome store.
Both are asserted by a test. Thresholds are chosen on a train split and the test
split is scored once.

The generator is checked by eight acceptance tests before any detector runs, at
all six attack grades. Their job is to fail if the data has been made too easy.
Two fail, and ship failing:

- **T1**, because one field, `email`, exceeds the AUC that its declared mechanism
  predicts. A real flag, left open.
- **T8**, because the ring leg has no working ceiling, as above.

The streaming runtime reproduces the batch scores exactly rather than
approximately: 0 mismatches across
67,961 events on all four detectors, maximum absolute
difference 0, and
3,890 alerts emitted in identical order.

## Documents

- [docs/event-schema.md](docs/event-schema.md), the record definition, what was
  cut from it and why
- [docs/generator-spec.md](docs/generator-spec.md), the generator, base rates with
  sources, and the acceptance tests
- [docs/report/decisions.md](docs/report/decisions.md), what we chose and what we
  rejected
- [docs/report/what-broke.md](docs/report/what-broke.md), what broke, including
  the wrong first guesses
- [docs/report/numbers.md](docs/report/numbers.md), every measurement with the
  command that produced it
- [results/summary.md](results/summary.md), the current artifact in readable form

Both spec documents were written before the code and corrected in place as
measurement contradicted them. The superseded claims are still visible next to the
evidence that replaced them.
