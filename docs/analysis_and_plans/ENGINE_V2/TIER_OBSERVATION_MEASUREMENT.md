# §12.5 — is Tier C still right for the observation pass?

**Yes, and the cheap model is measurably the *more* consistent of the two.** Over 20 held-out
turns from both flagship saves, run twice per tier, Tier C matches or beats Tier A and Tier B on
self-consistency for **8 of 11** classification fields. The difference between tiers is
indistinguishable from the difference between two runs of the same tier, so a tier switch buys
nothing. §5.5's "Tier C stands until the numbers say otherwise" now has numbers.

**The larger finding is not about tiers at all.** Two fields — `revelations.eligible` and
`subplot_beats` — reproduce at 44–50% *on every tier*. That is a question the model cannot
answer stably, not a model that is too cheap, and no tier upgrade touches it.

Reproduce with `scripts/tier_observation_probe.py`. Needs a real API key.

---

## How it was measured without ground truth

Hand-labelling what each turn *should* have reported is subjective and expensive, and the
decision does not need it. Two measurable things settle it:

- **Agreement between tiers.** If the expensive model returns the same classifications as the
  cheap one, Tier C stands and no labels are needed.
- **Self-consistency within a tier** — each config run twice over the identical prompt. This is
  the control the experiment is useless without. If Tier C disagrees with *itself* as often as it
  disagrees with Tier A, the between-tier difference is noise.

That control is what decided it. Whole-answer agreement, across all 11 fields at once:

| | agreement |
|---|---|
| Tier C with itself | 10% |
| Tier A with itself | 5% |
| Tier B with itself | 6% |
| Tier C vs Tier A | 15% |
| Tier C vs Tier B | 5% |

**Cross-tier agreement sits inside the self-agreement range.** The expensive model does not agree
with itself any more than it agrees with the cheap one.

Only categorical fields are compared — a beat type, a subplot's band, a revelation id, a location
from a closed set. A model-invented flag name or a prose item label will never match across runs
and its disagreement means nothing; including them would report noise as signal.

## Per field, which is where the answer actually is

Whole-answer agreement collapses multiplicatively across 11 fields and says more about the number
of fields than the model. Per field, self-consistency:

| field | C | A | B | C vs A | C vs B |
|---|---|---|---|---|---|
| `failure_triggered` | **100%** | 100% | 100% | 100% | 100% |
| `beat.type` | **95%** | 85% | 83% | 80% | 90% |
| `n_items_gained` | 95% | **100%** | 94% | 100% | 95% |
| `location` | **90%** | 90% | 89% | 95% | 85% |
| `threat_present` | **90%** | 85% | 89% | 75% | 85% |
| `n_social` | **90%** | 85% | 78% | 85% | 80% |
| `entity_interaction` | 85% | **100%** | 94% | 90% | 90% |
| `beat.intensity` | **85%** | 85% | 83% | 65% | 80% |
| `revealed` | 70% | **75%** | 72% | 70% | 75% |
| `eligible` | **50%** | 45% | 44% | 60% | 35% |
| `subplot_beats` | **50%** | 45% | 50% | 65% | 55% |

Tier C ties or wins on eight. It is *more* self-consistent than the expensive model on the beat
classification the whole pacing loop is built on — 95% against 83–85%. Paying more would buy a
slightly better `entity_interaction` and `revealed`, and a worse everything else.

## The finding worth acting on

**`eligible` and `subplot_beats` are close to coin flips, on every tier.** Both ask the model to
make a judgement with no closed set behind it: which revelations are *satisfied but not yet
written* (§12's placement), and how far each thread moved. `failure_triggered` and `location`,
which do have closed sets, sit at 90–100%.

That is a design signal, not a procurement one. The two least reproducible fields in the system
are the two asking the model to grade something continuous, and the fix is to narrow the question
rather than to buy a better model — the same move that took the stat readout away from the model
entirely (P-7) and that gave `subplot_beats` its vocabulary in the first place. Worth knowing
before anyone spends a phase on either.

## Caveats, stated rather than buried

- **20 turns, 2 repeats, 120 calls.** Enough to separate "tiers differ" from "runs differ"; not
  enough to rank two tiers three points apart. Read the per-field table as bands, not places.
- **Latency did not separate** — all three configs landed at ~2.1s p50 — and that is not a
  finding. The probe runs four-way concurrent, and `measure_baseline` puts `state_update` at
  1.73s against `act_advancement_check`'s 12.29s on the same Tier B path, so a reasoning pass is
  normally far slower than this run suggests. Whether `reasoning=True` engaged at all for this
  model on this provider was not verified. **Nothing here should be read as evidence that Tier B
  is cheap.**
- **Every turn is scored against the save's final state**, not the state as it stood at that turn,
  so absolute classifications are not what production produced. Both tiers see identical bytes,
  which is all a comparison needs, but these are not accuracy numbers and are not offered as any.
