# Phase 0 — Pacing validation gate report

**Date:** 2026-09-04
**Sample:** 24-turn live playthrough, `new_babel`, user `9a20892e`, schema v2 working tree
(commit `396af0f`). Exported via `backend/export_story.py --include-actions`; 15,330 words.
**Verdict:** **PROCEED TO 6.1/6.2 ON A TWO-BEAT VOCABULARY.** Gate 0.2 fails at four beats
(41.7%) and passes collapsed to two (75.0%) — §7. The §4 fix plan has landed and its
revelation defect is closed (§4.3). Thresholds must be re-derived before 6.3 (§7.2), and gate
0.3 is still unrun.

---

## 0. Status of each gate

| Gate | Specified work | Status | Result |
|---|---|---|---|
| 0.1 | Classify every scene in `the_attention_economy.txt` by beat type | **RUN** (24 turns; regenerated corpus) | Release-beat rate 58%, one eight-turn release run (§2) |
| 0.2 | Hand-label ~30 scenes, run classifier prompt, measure agreement ≥70% | **RUN** (24 scenes) | 41.7% on four beats (FAIL); 75.0% collapsed to two (PASS) — §7 |
| 0.3 | Repeat 0.2 against a second genre's vocabulary | **NOT RUN** | Needs an `example` playthrough — no fixture required |

### On the two missing files

Neither is a blocker, and neither should be authored.

**`the_attention_economy.txt` was regenerated, not substituted.** "The Attention Economy"
is `new_babel`'s own `meta.title`, and `backend/export_story.py` emits precisely the
scene-by-scene plain text gate 0.1 asks to be classified. The named file is an export of
this story; §2 classifies a fresh one. The gate is properly run. Its real limitation is
sample size and rater count — 24 turns, one player, one session — not the artifact.

**`regency.json` is not needed for 0.3.** The gate's purpose is to test whether the
classifier can separate beats outside a violence-shaped story. The repo already ships that
case: `stories/example`, *The Last Ferry to Millbrook*, a cozy small-town mystery, with its
four-beat vocabulary already authored in pacing spec Appendix A and already designated the
inverse-correction reference (spec §14). What 0.3 actually needs is **a ~30-turn
playthrough of `example` to classify** — the same instrument §2 used, pointed at the other
story. A regency fixture would be a *third* genre: useful breadth later, not a prerequisite
now. `data/saves/local-cli/example.json` currently sits at `turn_count: 0`.

**Method caveat:** every beat label in §2 is from a *single rater* (a model). It is the
corpus baseline for 0.1 and is **not** used as either side of the 0.2 comparison — §7 measures
an independently-produced human label set against the classifier, which is what the gate asks
for. Where §2 and the human disagree, §7.3 records it.

---

## 1. Structural state after 24 turns

Read directly from the save, not inferred:

| Field | Value | Expected-healthy |
|---|---|---|
| `pacing.turn_count` | 24 | — |
| `plot.current_act` | 1 | 2+ |
| `plot.act_completion["1"].completed` | `false` | — |
| `plot.generated_acts` | `[]` | ≥1 |
| Subplots completed | 0 of 3 | ≥1 |
| `subplot_001` progress | 62/100 | — |
| `subplot_002` progress | **0/100, `not_started`** | started |
| `subplot_003` progress | 56/100 | — |
| `plot.revelations_revealed` | **`{}` (0 of 2)** | ≥1 |
| `plot.entity_contact_count` | 0 | 0 is fine (authored as rare) |
| `history.compressed_summary` | **2,912 words** | ≤2,000 (`SUMMARY_MAX_WORDS`) |

The act-advancement machinery itself behaved correctly: `act_check_frequency` is 12, and
checks ran at turns 12 and 24 (`turns_since_act_check` is 0 at turn 24). Both declined to
advance. That is the **right** answer — Act 1's completion signals require
`at least one memory fragment revealed`, and zero have fired. The act loop is not broken;
it is correctly blocked by an upstream defect (§4).

---

## 2. Gate 0.1 — beat baseline

Classified against New Babel's §5.1 vocabulary (`crisis` / `escalation` / `lull` /
`resolution`), intensity 1–3 per §6.1.

| # | Turn summary | Beat | Int |
|---|---|---|---|
| 1 | Wake in intake ward, check hands/pockets, drone exchange | lull | 2 |
| 2 | Corridor, reception, Renner interview, flagged for observation | escalation | 1 |
| 3 | Slip into service corridor, evade camera, exit to street | escalation | 2 |
| 4 | Scavenge hoodie and shoes from noodle-stall closet | lull | 1 |
| 5 | Flag cargo skiff, ride down to the Drowned Quarter | lull | 1 |
| 6 | The Drowned Saint: stew, Sal, Voss enters at close | escalation † | 1 |
| 7 | Turn and watch Voss press Sal for names | escalation | 2 |
| 8 | Slide for the back; Voss blocks, Wolves take the door, vision doubles | escalation | 3 |
| 9 | Step around Voss and walk out; "I'll find you either way" | escalation † | 2 |
| 10 | Trace the palm scar under the rainbreak; Cipher arrives at close | lull † | 2 |
| 11 | "Quit lurking" — Cipher drops to the dock, introduces herself | lull | 1 |
| 12 | "I woke up on a gurney six hours ago" — Cipher pitches the Choir | lull | 1 |
| 13 | Ask what the Wolves want from a fresh recomputation's skull | lull | 1 |
| 14 | Follow Cipher through the drowned walkways | lull | 1 |
| 15 | Ask why skiffs won't run past the pump station | lull | 1 |
| 16 | Ask what the Choir wants in exchange for the route | lull | 1 |
| 17 | Enter the pump station, give Nadia the name "Joe" | lull | 1 |
| 18 | **Palm to the listening dish** — building flexes, double-voiced | crisis | 3 |
| 19 | Accept the route and the rest on offer | resolution | 2 |
| 20 | Sleep on the cot, the underwater dream, three bells at close | lull † | 2 |
| 21 | Ask what the third bell changes; Wolves cut their fans | escalation | 2 |
| 22 | Ask Nadia what the dish heard when the bells rang | lull | 1 |
| 23 | **Palm to the dish again** — beacon, one bell inside the skull | crisis | 3 |
| 24 | Slip out the north approach, skiffs turn away | escalation | 2 |

† Boundary case — scene turns at its close. See §5.

**Distribution:** lull 13 (54%), escalation 8 (33%), crisis 2 (8%), resolution 1 (4%).
Releasing beats (`lull` + `resolution`) are **58%** of the sample.

**Clustering — partially superseded, see §7.3.** On these labels turns **10–17 are eight
consecutive releasing beats**, all `lull`, seven of them intensity 1. Under the independent
human labels the longest release run is **4**, not 8; the 58% release rate is identical under
both. Treat the rate as established and the run length as unresolved. The story stops moving and becomes
ask-answer dialogue from Cipher's arrival through to Nadia. A shorter three-turn release
run sits at 4–6. Prose quality is high and consistent throughout the sample; the failure
is structural, not stylistic. This is precisely the "infinite wandering" the pacing loop
exists to correct, and it is the strongest evidence in this report *for* building Phase 6.

---

## 3. Counter simulation — the rule as specified would not have fired

Applying implementation plan §6.1's New Babel config (`force_release`, watch `tension`,
threshold 8, `suppress_when: [threat_present, just_fired]`) to the §2 trace, with pacing
spec §5.1 semantics (`lull`/`resolution` feed `stasis` and reset `tension`;
`crisis`/`escalation` feed `tension` and reset nothing):

| Turn | 1 | 4 | 8 | 9 | 10 | 13 | 17 | 18 | 19 | 22 | 24 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `tension` | 0 | 0 | 6 | **8** | 0 | 0 | 0 | 3 | 0 | 0 | 5 |
| `stasis` | 2 | 3 | 4 | 4 | 6 | 9 | **13** | 13 | 15 | **18** | 18 |

- `tension` reaches the threshold exactly **once**, at turn 9 — and Voss is physically
  blocking the player, so `threat_present` suppresses it. Turn 10 is a `lull`, which
  resets `tension` to 0 and clears the armed entry per spec §9 step 4.
- Depending on how `threat_present` is scored at the turn 9/10 boundary (the player has
  walked out; "No one follows you out. Yet."), the rule fires **zero or one** times in 24
  turns. If it fired, it would inject a release directive into turn 10 — which is already
  a `lull`. A no-op correction either way.
- `stasis` climbs monotonically to **18** with **no rule watching it**.

**The rule watches the direction this sample does not fail in.**

One caveat against over-reading this: n=1 session.

A second caveat was assumed and then **disproved** — see §3.1. It is not player style.

### 3.1 The release run is caused by the option sets, not by player style

The obvious objection to §3 is that the player chose the lulls — turns 12, 13, 15, 16 and
22 are all "ask them X" actions. Checking the options actually *offered* during turns 10–17
does not support that reading:

- **Turn 17** — "Step inside and announce yourself" / "Stay in the doorway and ask to hear
  their first question" / "Step inside but put the door at your back and scan the room."
  Options 1 and 3 are the same act at different postures; option 2 stalls for exposition.
  There is no non-lull choice in the set.
- **Turn 15** — "Ask why the Wolves don't run skiffs past the pump station" / "demand to
  know where she's actually leading you" / "keep moving and start memorizing the route."
  All three investigate rather than commit.
- **Turn 12** — all three options conversational; no physical or spatial option offered.

The option generator mirrors the scene's current mode: inside a conversation it offers
three flavours of conversation. Once the story enters a dialogue, the option list contains
no exit, and the player cannot choose one that isn't there.

**This is already prohibited.** `_options_block_instruction`
(`backend/story_engine.py:1787-1792`) has instructed the model since commit `58bcf95` —
live for all 24 turns — that each option must be *"a meaningfully different course of
action with real consequences for the story - never an option that just asks for more
detail, investigates further before committing to anything, or otherwise stalls for more
exposition."* Turn 17's set violates every clause of that sentence.

Two consequences. First, the direction question in §6 is *not* confounded by player style,
and the stasis reading stands on its own. Second, and more useful for Phase 6: a permanent,
negatively-phrased footer rule has now demonstrably failed to hold across 24 turns. That is
a direct argument for the pacing directive's design — injected fresh, for a single turn, at
high salience — over any further static prompt text. Fix plan Task 4 tests the positive
restatement first; if that also fails, the finding is that this class of constraint belongs
to Phase 6 rather than to the footer.

### 3.2 Spec inconsistency: `stasis` is monotonic in New Babel

Independent of the direction question, New Babel's §5.1 vocabulary gives `crisis` and
`escalation` **no `resets` key at all**, so nothing ever decrements `stasis`. Compare
`example`'s Appendix A vocabulary, where `unsettling` and `confrontation` both carry
`"resets": ["stasis"]`.

Consequence: even if New Babel were given a `stasis`-watching rule, the counter only ever
grows. It would cross any threshold once, fire, and then sit permanently above it,
suppressed forever by `just_fired`. The two worked vocabularies are not structurally
parallel, though §5.2 describes them as such. This is a defect in the spec, not a
judgement call, and it is fixable without any playtest data.

---

## 4. Blocking defect: revelation triggers never fire

Zero of two revelations fired across 24 turns, and Act 1 cannot complete without one.

> **CORRECTION (2026-09-04, post-fix).** This section originally asserted that the
> narration plainly satisfied `frag_0001` and that the state-update prompt's missing
> instruction was the cause. Replay testing after the fix (§4.2) **disproved both claims.**
> The prompt gap was real and worth closing, but it was not why nothing fired. The analysis
> below is retained down to §4.1; §4.2 carries the corrected root cause and supersedes it.

`frag_0001`'s authored trigger is *"The first time the protagonist attempts a non-trivial
computational proof."* On turns 18 and 23 the player presses a scarred palm to a listening
dish and the narration renders an explicit "lattice of dark computation", a structural flex
of the building, and a double-voiced utterance. `frag_0002`'s trigger — *"Encountering a
sigil, glyph, or data pattern associated with the mysterious entity"* — looks plausibly met
by the chalk circle vision on turn 8 and the spiderwebbed black lines on the tarp on turn 23.

Both of those readings turned out to be wrong. See §4.2.

The plumbing is correct. `story_engine.py:746-750` filters to unrevealed triggers and
`story_engine.py:918-921` records whatever ids come back. Existing coverage in
`test/test_revealed_memories.py` confirms both. The failure is at the prompt: the triggers
are interpolated at `story_engine.py:870` as a bare JSON dict —

```
UNREVEALED MEMORY FRAGMENT TRIGGERS: {json.dumps(unrevealed_fragments)}
```

— and its matching schema line is equally bare:

```
  "memory_fragments_revealed": ["<fragment_id>", "..."]
```

Nothing in the prompt tells the model **what to do with them**. The only guidance is the
generic "Only include subplot ids, flags, fragment ids, items, character names, and stats
that actually changed this turn."

Every comparable field in the same prompt is instructed and revelations are the outlier.
`subplot_progress` spells out accumulate-and-complete semantics inline. `new_characters`
gets a full instructional paragraph on when to emit an entry. `failure_triggered` — which
carries the *same* authored-id-plus-trigger shape as revelations — reads
`"<the exact id of a FAILURE CONDITION below that has now been met this turn, or null if
none have>"`, i.e. it states the evaluation explicitly. Revelations alone state nothing.

This call runs on **Tier C**, the fastest and weakest model (`story_engine.py:893`,
`call_llm_json` defaulting to `TIER_C_MODEL`), every single turn.

### 4.1 Why this is a Phase 6 finding, not just a bug

Phase 6.2 proposes extending *this same Tier C call* with `beat_type`, `intensity`, and
`leverage_gained`. What that call does with an authored judgement is therefore directly
predictive of gate 0.2.

> **CORRECTION.** This section originally read the zero-fire result as evidence the Tier C
> call ignores authored triggers, and concluded it was "a strong negative prior" for gate
> 0.2's ≥70% threshold. §4.2 disproved that. The model was reading the trigger closely and
> declining it correctly. The revised prior is **mildly positive**: on the one judgement
> observed, Tier C parsed an authored criterion, weighed a near-miss ("maybe yes... but
> maybe not because black lines not associated with entity"), and declined rather than
> pattern-matching on surface vocabulary — which is the discrimination beat classification
> needs.
>
> This is one observation, not a measurement; gate 0.2 still has to be run. But it should
> be run **on Tier C alone**. The Tier B comparison the original text called for is closed
> off — see §4.2's token-budget finding.

---

### 4.2 Corrected root cause: the trigger is unreachable, and nothing steers toward it

Fix plan Task 1 landed (commit `86a559c`) and `scripts/replay_turn.py` re-ran turns 18 and
23 against the fixed prompt, with `revelations_revealed` cleared and flags rewound to their
pre-turn state. **Both still returned `[]`.**

The Tier B run then exceeded its token budget mid-reasoning and surfaced the model's own
trace, which settles the question:

> *"frag_0001: first time protagonist attempts a non-trivial computational proof. **No
> proof.**"*
> *"The protagonist did 'let the deep fix on that signal', **not proof. No.**"*
> *"frag_0002 maybe yes. But maybe not because black lines not associated with entity?
> **Need not force**, but seems likely."*

Three things follow, and they reverse the original diagnosis.

**The instruction works.** "Need not force" is Task 1's own added text ("never force a
match") being read and applied. The model is evaluating the triggers deliberately, not
ignoring them. Task 1 was worth landing and should stay.

**The model is right.** The protagonist never attempts a computational proof. They press a
palm to a dish and something answers *them* — the narration's own framing is "the hand
remembers", i.e. the act happens to the protagonist rather than being undertaken by them.
An amnesiac who does not know they can compute cannot deliberately attempt a proof. On a
strict reading, `frag_0001` was not satisfied on any of the 24 turns, and the correct output
was `[]` every time.

**Nothing ever steers the story toward staging one.** `unrevealed_fragments` appears only
inside `update_progress_from_turn` (`story_engine.py:748-887`). The narration prompt never
sees unrevealed triggers — deliberately, per CR-03's comment at `story_engine.py:1729`:
*"neither pass sees the other half"*, so the narrator cannot telegraph a reveal it has not
earned. The unintended consequence is that revelations are **purely passive**: they wait for
the narration to coincidentally stage an authored event that nothing has asked it to stage.
Across 24 turns the coincidence did not occur, and there is no mechanism by which it
reliably would.

So this is a **content and steering defect, not a classifier defect**. Two fixes, neither
of which is a prompt reword:

1. **Author triggers as observable events, not as protagonist intentions.** `frag_0001`
   describes a deliberate act the protagonist is characterologically unable to perform in
   Act 1. **Done and validated — see §4.3.**
2. **Give the reveal an active path.** This is already designed: pacing spec §6.5 /
   implementation plan §6.5, "reveal placement integration", schedules queued reveals into
   release windows. It is the intended mechanism for exactly this gap. That makes Phase 6.5
   load-bearing for act progression, not the optional polish the plan's risk table implies.

**Tier B is not an available escape hatch.** The Tier B attempt failed with
`finish_reason=length` and `content: None` — the reasoning phase consumed the entire
`OPENROUTER_MAX_TOKENS` (4096) budget before emitting any JSON. This is precisely the
failure CLAUDE.md predicts for reasoning-on tiers, and it means "escalate the state-update
pass to Tier B" is not viable for this prompt without a token-budget change. Recorded so the
option is not re-proposed. (The empty-content guard raised `LLMUnavailableError` correctly,
so the narration-`None` fix is confirmed working under a real provider failure.)

### 4.3 Trigger rewrite — done, and validated by replay

Both `new_babel` triggers were rewritten to describe observable narration events rather than
protagonist intentions, and to drop qualifiers the model cannot verify from the scene
(`frag_0002`'s "associated with the mysterious entity" required an identification the
narration never makes). `frag_0002` also had to state explicitly that a pattern **counts as
a vision or afterimage** — its first rewrite still missed turn 8's chalk circle, because the
narration ends that beat with "the chalk lines were never there" and the model reasonably
ruled it out as not real.

Replayed against the same 24-turn save on **Tier C**, no tier change:

| Turn | Content | Fires |
|---|---|---|
| 1 | Checking hands and pockets in the intake ward | — |
| 4 | Scavenging a hoodie and shoes | — |
| 8 | Chalk circle in a half-second doubled vision | `frag_0002` |
| 10 | Tracing the palm scar, probing what the hand remembers | `frag_0001` |
| 18 | Palm to the listening dish | both |
| 23 | Palm to the dish again | both |

Silent on mundane turns, firing on the four that carry the imagery — the discrimination that
was missing. In live play the first reveal now lands around turn 8 rather than never, which
unblocks Act 1's `at least one memory fragment revealed` completion signal. Turn 10 is a
better home for `frag_0001` than turn 18: that fragment's payoff is a memory of *hands*
typing, and turn 10 is the protagonist deliberately interrogating their hand's muscle memory.

This closes the §4 defect. Fix 2 (an active steering path, implementation plan §6.5) is
still worth building — these triggers now fire when the story happens to produce the
imagery, but nothing yet *causes* it to — though it is no longer load-bearing for Act 1
progression on this story.

---

## 5. Classifier boundary cases observed (input to 0.2 prompt design)

Four of 24 scenes (marked † in §2) turn at their close: the body sits in one beat and the
final paragraph introduces the next. Turn 6 is ~400 words of warmth, food and rest,
ending with Voss walking in. Turn 20 is a sleep-and-dream lull ending on three bells.

A classifier asked for one label per scene will split on these, and they are 17% of the
sample. Before running 0.2, settle the rule and state it in the prompt — recommended:
**classify by the scene's terminal state**, since that is what carries into the next turn
and what the directive would need to correct. Whatever is chosen, hand-labels and the
classifier prompt must use the same rule or the agreement number measures nothing.

Note that the `crisis`/`escalation` pair the plan flags as an 0.2 collapse risk was *not*
a source of ambiguity here — turns 18 and 23 are unmistakably crisis against the other
eight escalations. The `lull`/`resolution` pair is untested: the sample contains exactly
one `resolution`.

---

## 6. Decisions

**Gate 0.1 — PASS.** A baseline exists. New Babel's observed pathology in this sample is
stasis: 58% releasing beats and an eight-turn release run. Recorded as a characterisation
of one 24-turn session by one player, to be widened by the 50-turn run.

**Gate 0.2 — RUN. Fails at four beats, passes at two.** 41.7% / 75.0%; adopt the two-beat
vocabulary and rewrite §5.1 and Appendix A accordingly. Full results, the intensity
calibration problem and the threshold consequence are in §7.

**Gate 0.3 — RUNNABLE, needs a playthrough not a fixture.** Play `stories/example` ~30
turns, export it, and classify against the Appendix A vocabulary exactly as §2 did here.
Per the plan's own failure-mode table, if 0.3 ultimately fails the pacing loop ships
New-Babel-specific and `example`'s Appendix A module is dropped; nothing else in the plan
is affected.

**Phase 6.1 / 6.2 — CLEARED TO START,** on two beats per story, not four.

**Phase 6.3 — BLOCKED on threshold derivation (§7.2).** The default threshold of 8 is
unreachable under the observed trace; peak counters were 4 and 5. Derive thresholds from a
classified trace of the 50-turn run, and settle the reset-vs-decay question first.

**Open question deferred to the 50-turn playthrough:** whether New Babel's single v1 rule
should watch `stasis` (`force_complication`) rather than `tension` (`force_release`). The
24-turn evidence points that way and is *not* confounded by player style (§3.1), but it
remains n=1, and pacing spec §6.2 constrains v1 to **exactly one rule per story** — so this
is a swap, not an addition, and it is not reversible for free.

The dependency that matters: fix plan Task 4 changes option generation, and if divergent
options alone break up the release clustering, New Babel may need no direction change at
all. Land Task 4 first, then re-run the §3 simulation against the 50-turn trace, then
decide. Deciding before Task 4 has been measured would tune the rule against a defect
that is about to be fixed.

---

## 7. Gate 0.2 results — four beats fail, two beats pass

Run 2026-09-05. Human labels: `data/labels_new_babel.md`, all 24 scenes, filled in before
reading §2. Classifier: `scripts/gate_02.py` on Tier C, given the same four definitions
verbatim, the same boundary rule and the same intensity scale the worksheet gave the human.

| Vocabulary | Agreement | Gate ≥70% |
|---|---|---|
| Four beats (`crisis`/`escalation`/`lull`/`resolution`) | 10/24 = **41.7%** | **FAIL** |
| Two beats (`TENSION` = crisis+escalation, `RELEASE` = lull+resolution) | 18/24 = **75.0%** | **PASS** |

Confusion pairs on the four-beat run: `escalation`/`lull` 6, `lull`/`resolution` 4,
`crisis`/`escalation` 4.

**Both pairs the plan named as collapse candidates disagreed**, at 4 each — gate 0.2's
instruction is explicit that when `lull`/`resolution` or `crisis`/`escalation` disagree, that
side collapses to one beat before proceeding. Collapsing both is what produces the 75%.

**Decision: adopt the two-beat vocabulary.** This is the plan's own prescribed remedy firing
as designed, not a workaround. It costs nothing the loop needs — a counter only has to know
which side a scene fed, and magnitude is carried by intensity — and it removes the §3.2
asymmetry problem outright, since two beats are symmetric by construction: `TENSION` feeds
tension and resets stasis, `RELEASE` feeds stasis and resets tension. Phase 6.1 should author
two beats per story, not four, and §5.1/Appendix A should be rewritten accordingly.

The six residual disagreements are all `escalation`/`lull` and run in **both** directions
(turns 2 and 7 human-tension/classifier-release; turns 10, 12, 14 and 20 the reverse), so
they are genuine boundary noise rather than a correctable classifier bias.

### 7.1 Intensity is not calibrated between rater and classifier

| | Mean | Distribution |
|---|---|---|
| Human | 1.12 | 1×21, 2×3 |
| Classifier | 1.71 | 1×10, 2×11, 3×3 |

Exact intensity agreement is 11/24 = 45.8%. The classifier scored **higher on 12 of 24
scenes and lower on 1** — a systematic upward bias against this rater, who used `1` for 21 of
24 scenes.

This is **not** a gate failure, and it should not be treated as one. The human's intensity
scale never runs in production; only the classifier's does. What matters is that the
classifier is self-consistent and that thresholds are derived from *its* output distribution.
The actionable consequence is that **thresholds cannot be authored a priori** — see §7.2.

### 7.2 Threshold 8 is unreachable under the observed trace

Simulating the two-beat vocabulary with symmetric resets over the human labels:

- peak `tension` = **4**, peak `stasis` = **5**, over 24 turns.

Neither counter comes close to §6.2's default threshold of 8, so no rule would fire in this
entire playthrough. The cause is structural rather than a wrong constant: with beats
alternating sides every few turns and each side resetting the other's counter, neither
accumulates long enough to build. A threshold of 8 implicitly assumes a long unbroken run of
same-side beats, which this trace does not contain.

Two things follow for Phase 6.3/6.6. Thresholds must be **derived from a real classified
trace**, not authored from intuition — likely in the 4–5 range for this story, not 8. And the
reset rule deserves a second look: full reset-to-zero on every opposite beat may be too
aggressive, with decay-by-N a better fit for a story that alternates this much. That is a
design question for the spec, and it should be settled before 6.3 implements the arithmetic.

### 7.3 Correction to §2's clustering finding

Under the human labels the longest unbroken release run is **4 turns** (12–15), not the eight
reported in §2. My labels called turns 11, 16 and 17 `lull` where the human called them
`escalation`, which is what produced the longer run.

The **release rate is unchanged at 58%** under both label sets, so §2's headline distribution
finding stands. The *clustering* finding does not, at least not at the severity originally
stated. This weakens — though does not eliminate — the case for New Babel needing a
stasis-watching rule, and it reinforces §6's instruction to defer that decision until Task 4
has been measured over the 50-turn run.

---

## 8. Non-findings

Recorded so they are not re-litigated:

- **`entity_contact_count: 0`** is correct behaviour. The Architect is authored as
  *"appears extremely rarely... Most of the story unfolds without direct entity
  intervention."* Zero contacts in 24 turns is the design working.
- **Prose quality** is not a concern in this sample. Dialogue-forward, concrete, on-tone,
  consistent from turn 1 to 24; the `mechanics.narration` style rules from Phase 5.1 are
  visibly holding.
- **`new_babel.json.bak-turn18-salvage`** is an artifact of an unrelated LLM output bug
  being debugged at the time. Not a pacing signal.
- **`CLAUDE.md` is stale** with respect to schema v2, `SECTIONS`, and mechanics modules.
  Known and deliberately scheduled as Phase 7.4. Not a finding of this report.
