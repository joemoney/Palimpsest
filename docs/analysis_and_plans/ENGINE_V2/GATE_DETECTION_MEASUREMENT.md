# Gate detection — measurement

**The detector is safe to ship, and the one thing to change is authoring guidance, not code.**
Across 119 real player actions it raised **zero** false refusals; on actions that genuinely
reach for a shut door it fires about **90%** of the time and attributes the right gate **every
time it fires**. The defect it did surface is that a `refusal_hint` written as finished prose
comes back verbatim in roughly half of all refusals — which is §7.4's forbidden `refusal_text`
arriving through the back door, and is fixed by wording the hint as a fragment.

Reproduce with `scripts/gate_detection_eval.py`. Needs a real API key; this is the one
measurement in the project that cannot run offline.

---

## What was measured

`story_engine.detect_gate_refusal` — the Tier C call that decides whether a free-text action
reaches for a gate the engine has already found unmet, and writes the sentence the player sees.

Two gates were authored for `new_babel` from the story's own worldbuilding (the template
describes the Spire's "private security checkpoints, and skywalks that let the right ID badge
avoid the street entirely"): `spire_checkpoint` behind an `item_tag`, `tidewall_seal` behind a
flag. Both were forced genuinely unmet, since a satisfied predicate is never shown to the model
and would have measured nothing. No shipped story declares a `gate` block, so these exist only
in the harness.

**Negatives are real actions, not invented ones.** All 119 player turns from both flagship
saves. This matters: invented negatives are written by someone who already knows where the
gates are, so they avoid them without meaning to. Every real action was written against a world
with no gates in it, which makes them the only honest negatives available. Each was evaluated
against the narration that actually preceded it, not the save's final scene.

**Positives are 15 hand-written actions** in the voice of the real ones — first person, ~130
chars, concrete — 9 reaching for the Spire and 6 for the Tidewall, so gate *attribution* is
measurable and not just detection.

## Results

| | prose hints | terse hints |
|---|---|---|
| False positives / 119 real actions | **0 (0.0%)** | not re-run |
| Detected / 15 genuine attempts | 15, 14, 13, 13 (**~92%**) | 12, 14, 14 (**~89%**) |
| Right gate, of those detected | **100%, every run** | **100%, every run** |
| Sentence identical to `refusal_hint` | 8/15, 7/14, 7/13, 8/13 (**~55%**) | **0/12, 0/14, 0/14** |

**Zero false positives is the number that matters**, because a false positive is a modal
blocking a legitimate action. Set against the string-matching approach §4 originally specified,
which fires on ordinary prose — `hold`, `hand`, `behind`, and a bare `s` from "The Ninth-Hand's"
all match real actions — this is the difference between a usable feature and one that refuses
players at random.

**Recall is ~90%, not 100%.** A first single run read 15/15 and that was luck; repeated runs
land 13–15. Treat any single run of n=15 against a non-deterministic model as indicative only.

## Why ~90% recall is acceptable here, and would not be on its own

A missed detection does not open the gate. It lets the turn proceed to narration, where the
**location veto** is the hard backstop — the modal is the good experience, the veto is the rail.
That two-layer shape is what makes a 90% detector safe: the layer that must be right every time
is deterministic engine code, and the layer that is allowed to be merely good is the one writing
prose. It is the same split as `mechanics.stats.readout` and for the same reason (P-7).

It does bear on the **retry cap**. At ~90% per attempt, a player trying the same door three
times slips past detection on at least one attempt about a quarter of the time. That does not
get them through — the veto still holds — but it does mean the cap prices retries less reliably
than a 100% detector would, and the cap should not be described as the thing that stops probing.

## The `refusal_hint` finding

**A hint that reads as finished prose is returned verbatim.** With hints written as complete
second-person sentences, ~55% of refusals came back byte-identical to the hint. §7.4 is explicit
that the template supplies tone and the model writes the sentence — at 55% echo, that design is
half not holding.

The cause is authoring, not code. When the hint is already a polished sentence, handing it back
*is* the cheapest correct answer. Rewriting the same two gates' hints as tone fragments —

> `"bureaucratic indifference; the system does not even register them"`

rather than

> `"The checkpoint does not challenge you. It simply does not read you, and the guard's attention has already moved on."`

— dropped the echo rate to **0 across three runs**, with no measurable cost to recall (89% vs
92%, inside run-to-run variance), and produced genuinely per-scene prose: a clerk waving you off
at a window, a screen where your name does not flicker, guards' eyes sliding past.

**So the guidance is: write `refusal_hint` as an unfinished fragment.** A sentence gets returned;
a fragment gets written from. This belongs beside the field in the schema docs, because it is the
difference between §7.4 holding and quietly not holding, and nothing in the code can enforce it.

## One consistent miss, and what it says about `target`

`"I follow the skywalk in and try to get past Cordon's security without stopping"` was missed in
several runs. It names the organisation (*Cordon*) and the mechanism (*security*) but never the
gate's `target` string (*Spire District*). The detector is shown `target` verbatim, so a gate is
recognised most reliably when its `target` reads the way the fiction refers to the place. That is
an authoring consideration too — and a cheap one to get wrong, since `target` doubles as a
location id in the obvious authoring style.

---

## The real authored gate (`the_missing_core`)

Everything above measures gates *this harness invented*. `the_missing_core` now authors one in
its own template — REACH under 20 shuts Tally Station, straight from `world.rules` — so it can
be measured as an author would actually write it. Reproduce with
`--story the_missing_core`; the save is forced to REACH 5 and nothing is injected.

| | synthetic (`new_babel`) | **authored (`the_missing_core`)** |
|---|---|---|
| False positives / 71 real actions | 0 / 119 | **0, 1, 1, 2, 2 across five runs (~1.2%)** |
| Recall / 7 genuine attempts | ~90% | **4–7 of 7, highly variable** |
| Hint echoed | 0% with fragments | **0 in every run** |

**The fragment rule held completely.** Not one refusal came back as the authored hint, and the
prose is per-scene: *"the drive answers with a long, tired groan… the stars outside the hold
window barely shift"*. This is the finding above, confirmed on content someone wrote for a
story rather than for a test.

**Both rates move enough that one run is not a number.** Five runs of the identical
configuration produced 0, 1, 1, 2 and 2 false positives, and recall swung 4–7 of 7 — with n=7,
a single miss is 14 points. Quote a range or run it again.

## Why recall is the cheap axis and precision is not

**A miss has a backstop. A false positive does not.** `blocking()` vetoes a gated
`scene_update.location` whatever the detector said, so an undetected attempt still fails to get
the player in — they get a worse scene, not a broken rail. Nothing catches a false positive: it
is a modal refusing an action that was never gated, and the player simply cannot proceed.

**So trade recall for precision, never the reverse** — which is the opposite of the instinct a
recall number invites.

That rule was tested directly. The one consistent miss, *"I aim for the weigh-floors"*, fails
because the weigh-floors live in Tally Station's **description** while the detector is shown
only its name. Adding the description to the detector prompt fixed it — recall went to 7/7 —
and **took false positives from ~1.2% to 7.0%**, firing on actions that merely mention a run to
Tally or that reach for something else entirely. It buys a backed benefit with an unbacked
cost, so it was measured, rejected, and reverted rather than kept for the better recall number.

If that miss is ever worth closing, the shape to try is a narrower authored field — the terms
this gate should catch — rather than handing the model more prose to pattern-match against.
