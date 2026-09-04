# Narrative Pacing Loop — Module Specification

**Status:** Approved for implementation, pending the §0 validation gates
**Version:** 4 (open questions resolved; `example` module authored)
**Project:** cyoa-app / Palimpsest
**Related docs:** `SCHEMA_V2_SPEC.md`, `SCHEMA_COVERAGE_CRD.md`, `CLAUDE.md`,
`docs/Narrative_Engine_Spec.md`, `UI_SPEC.md`

---

## Changes in v4

All eight open questions resolved. No design reversals; the decisions mostly cut scope
out of v1 and add one worked example.

| Q | Decision | Effect |
|---|---|---|
| Q1 | Schema supports a rules list; **v1 implements a single rule** | §9 arbitration deferred; §15 arbitration test deferred |
| Q2 | Ledger lives at `state.protagonist.leverage` | §7, §8 fixed |
| Q3 | **Retain** spent leverage | §7 gains a retention and bounding policy |
| Q4 | Ship a copyable default vocabulary | §5 now carries two worked vocabularies |
| Q5 | **Drop** `player_action_escalating` from v1 | §6.2, §10 simplified; option format untouched |
| Q6 | Author the module for `example` too | New Appendix A; §14 gains a second acceptance sample |
| Q7 | `max_deferrals` default 3 | Confirmed, still playtested |
| Q8 | Rewrite the §14 sample to second person | Done; marked as the post-CR-14 target |

Q4 and Q6 interacted. Rather than shipping New Babel's four beats as a generic default,
`example` now carries a **structurally parallel but genre-appropriate** vocabulary — two
accumulating beats, two releasing, opposite correction direction. Two worked vocabularies
demonstrate the abstraction better than one default would, and Appendix A doubles as the
copy-from reference for a new story.

Q5's removal is worth noting as a scope win: tagging options with an `escalating` boolean
would have changed the `OPTIONS:` format and `parse_narration_and_options`, colliding
with schema v2's `narration.option_count` work. The deferral ceiling (§10) already guards
the deadlock that predicate was partly for.

---

## Changes in v3

v2 carried a standing warning that its field names were inferred from conversation rather
than read from `story_engine.py`. That reconciliation has now been done, and it
invalidated two sections outright.

**Corrections from the code read:**

- **`entity_tracker` does not exist.** v2's §7 declared `"entity_tracker": {"...":
  "existing fields, unchanged"}`. The repo has `plot.entity_interaction_count` — a bare
  integer incremented when the state pass returns `entity_interaction: true`, read only
  by `check_and_advance_act`. No budget, no conditions, no reveal scheduling. The
  budget-and-conditions mechanism was designed in an August session and never built.
- **No threat state exists anywhere.** v2 §9's first suppression condition had nothing to
  read. Now sourced from `scene.threat_present`, which rides along on the `scene_update`
  field that CR-01 already adds to the state pass.
- **Act/phase tracking does exist** (`plot.main_thread.current_act`). Act-scaled
  thresholds are a small change, not a prerequisite feature. Promoted from "consider" to
  specified (§13).
- **`world_state.json` does not exist.** State lives at
  `data/saves/<user_id>/<story_slug>.json` behind `state_store.py`, multi-user and
  multi-story.
- **State mutations do not live in `state_store.py`.** That file is pure storage; every
  mutation is in `story_engine.py`. v2 §14.4 pointed implementation at the wrong file.
- **Scene length is 470–500, not 450–700**, and under schema v2 it's
  `narration.scene_length` rather than a module constant.
- **Reveal content never reaches the narration prompt.** CR-03 found `memory_fragments`
  content is written, marked revealed, and never prompted. v2 §11's reveal queue would
  have scheduled reveals into a pipe that isn't connected. CR-03 is now a hard
  prerequisite, stated as such.

**Structural change:** the whole feature is now an **optional module** under schema v2's
`mechanics` block, with the beat vocabulary, thresholds, and correction *direction*
authored per story. v2 hardcoded a thriller/LitRPG assumption — accumulate tension, force
release — into engine behaviour. A cozy mystery's failure mode is the opposite, and a
horror story may want forced escalation after too much quiet. See §4.

**Carried forward unchanged from v2:** §0 validation gates, intensity weighting, the
progression ledger, the deferral ceiling, the concrete lull sample as acceptance
criterion, and the stub-based test strategy. All of that survived the code read intact.

---

## 0. Pre-implementation validation (still gates everything)

Unaffected by the v3 restructure. Do these before writing feature code.

**0.1 — Full-corpus baseline classification.** The problem statement rests on a purposive
sample of roughly 1,000 of `the_attention_economy.txt`'s 2,517 lines, with ranges chosen
partly by scanning for interesting content. Enough to establish the problem exists; not
enough to size it. Classify *every* scene in the file by beat type. This gives the true
release rate — if it's 8% and clustered somewhere unsampled, threshold tuning changes —
and a measurable baseline for after.

**0.2 — Classifier agreement check.** The design assumes `call_llm_json` can reliably
self-report beat type. Hand-label ~30 scenes, run the classifier prompt, measure
agreement.

- Poor `lull` vs. `resolution` agreement → collapse to one releasing beat before building
  counter logic on it.
- Poor `crisis` vs. `escalation` agreement → same collapse on the accumulating side; both
  feed the counter identically anyway, and §6's intensity score may already capture the
  distinction.
- Below ~70% overall → stop and fix the classifier prompt. A pacing system on noisy
  classification fires at random and is worse than nothing.

**0.3 — Cross-genre vocabulary check (new in v3).** Run 0.2's methodology once more
against a non-thriller beat vocabulary — the `regency.json` conformance fixture from
`SCHEMA_V2_SPEC.md` §7 is the natural target. If the classifier can only separate beats
when they're violence-shaped, the authored-vocabulary design in §5 doesn't hold and the
module should ship thriller-only with that limitation documented.

---

## 1. Problem statement

Pacing analysis of `the_attention_economy.txt`, sampled across the opening, the
Lowmarket/ward-intake stretch, the rooftop negotiation, and the archive/chase sequence:

- **No downtime beats in any sampled scene.** Every scene is already mid-crisis; every
  scene end launches a *new* complication rather than resolving the last (sedation debate
  → early transfer team → board review → broker breach → rooftop negotiation →
  Containment claxon → archive handshake → blown door → chase). Tension stacks, never
  resets. Sample finding, not yet a corpus finding — see §0.1.
- **Reveals cluster at the reader's lowest-bandwidth moments.** The four-signature scene
  (Cordon Dynamics, Mesmer Holdings, Praetor, Marlowe's trust) lands as one dense block
  of new proper nouns *while* the protagonist is mid-handshake with an entity and about
  to be chased.
- **No legible progression signal.** Threat escalates continuously; capability,
  resources, and leverage do not. The protagonist accumulates burns, leashes, and
  pursuers, and banks nothing. Tension has no rising baseline to measure against.
- **Root cause:** turn-by-turn LLM narration defaults to escalation as the cheapest
  available signal that a turn mattered. Without an explicit rule pushing back, `call_llm`
  chooses "add a new threat" over "let this one breathe" almost every time.

Western LitRPG's chapter loop is **encounter → resolution → visible gain → brief downtime
→ next hook**. It works structurally rather than through authorial restraint: the loop
forces both a release beat and a legible gain on a schedule. The gain is what makes the
release feel earned rather than merely paused.

This spec proposes both halves. A release with nothing gained is a stall.

Explicitly **not** OP-MC-style cozy/iyashikei downtime (comfort, banter,
competence-display played warm) — wrong register for a protagonist who is threatened and
reactive rather than powerful. The target is LitRPG's tighter version: pressure lifts,
dread doesn't.

---

## 2. Goals

- Guarantee a corrective beat after sustained accumulation in whichever direction the
  story defines as pathological, without an authored per-scene trigger.
- Track and surface concrete player-side gains, so the loop has a progression half.
- Give reveals a scheduled window to land outside active-crisis scenes.
- Preserve tone: a release beat is not a safe scene. Dread, cost, and consequence persist;
  only the immediate pursuing threat recedes.
- Reuse the existing two-pass architecture — no new LLM call, no new pass.
- Be genre-neutral at the engine level (schema v2 P-2, P-3).

## 3. Non-goals

- No player-facing UI. No tension meter, no XP readout. Hidden authoring constraint,
  consistent with the rest of the state-tracking system. Revisit only if playtesting shows
  players can't perceive progression without it.
- Not a difficulty or combat system. Doesn't touch resolution mechanics, only which *kind*
  of scene gets generated next.
- Not extended slice-of-life mode. Corrective beats are single scenes at
  `narration.scene_length`, not multi-scene arcs.
- Not a numeric progression system. The ledger tracks diegetic assets — no levels, no
  points, no derived values.

---

## 4. Module shape and opt-in

Per schema v2 P-2, absent means the feature does not exist: no state, no prompt section,
no fields in the state-update schema.

```json
"mechanics": {
  "pacing_loop": {
    "beats": { },
    "counters": { },
    "rules": [ ]
  },
  "progression": { }
}
```

`pacing_loop` and `progression` are independent. A story can take the gain ledger without
the beat correction, or vice versa — though the release directive is noticeably weaker
without leverage to name (§7).

**The correction direction is authored, not assumed.** v2 hardcoded "accumulate tension →
force release." Expressed as rules (§6.2), the same machinery covers:

| Story | Pathological state | Rule |
|---|---|---|
| New Babel (thriller) | Unbroken escalation | Watch `tension`, force a release beat |
| `example` (cozy mystery) | Nothing has advanced for a long stretch | Watch `stasis`, force a complication |
| Survival horror | Either — quiet too long *or* pressure too long | Two rules, both directions |

The first two are authored and shipped (§5.1, Appendix A). The third is expressible in the
schema but needs multi-rule support, which is deferred out of v1 per Q1.

---

## 5. Beat vocabulary (authored)

Beat names and definitions come from the template. The definition strings are
interpolated into the classifier prompt, so the vocabulary is genuinely story-specific
rather than cosmetically renamed.

### 5.1 Worked vocabulary — New Babel (thriller)

```json
"beats": {
  "crisis": {
    "definition": "Active pursuit or direct threat requiring an immediate decision this scene.",
    "feeds": "tension",
    "resets": ["stasis"]
  },
  "escalation": {
    "definition": "A new complication or threat is introduced; stakes rise, but no immediate life-or-death branch yet.",
    "feeds": "tension",
    "resets": ["stasis"]
  },
  "lull": {
    "definition": "The immediate pursuing threat has receded or resolved. Dread and cost may remain. No new pursuer introduced this scene.",
    "feeds": "stasis",
    "resets": ["tension"]
  },
  "resolution": {
    "definition": "A concrete sub-goal is achieved or a threat conclusively closed off. Rare.",
    "feeds": "stasis",
    "resets": ["tension"]
  }
}
```

> Both correction directions are symmetric by construction: each pair of beats resets the
> counter the other pair feeds. A vocabulary where one counter is never reset is monotonic
> and will fire its rule exactly once before `just_fired` suppresses it permanently.

Per-beat fields:

| Field | Meaning |
|---|---|
| `definition` | Verbatim into the classifier prompt. The whole quality of the system rests on these being crisp. |
| `feeds` | Which counter this beat's intensity accumulates into. |
| `resets` | Counters zeroed when this beat fires. |

### 5.2 Worked vocabulary — `example` (cozy mystery)

Structurally parallel — two accumulating beats, two releasing — but the pathological
state is inverted. Millbrook's failure mode is that everything stays pleasant and nothing
about yesterday ever advances. Full module config in Appendix A.

```json
"beats": {
  "hospitality": {
    "definition": "Warm, welcoming, socially pleasant. Nothing about the town's inconsistencies advances.",
    "feeds": "stasis"
  },
  "reassurance": {
    "definition": "A strangeness is explained away, and the explanation holds for now. The player is soothed rather than informed.",
    "feeds": "stasis",
    "resets": ["tension"]
  },
  "unsettling": {
    "definition": "A concrete detail refuses to add up, and the player notices. No confrontation yet.",
    "feeds": "tension",
    "resets": ["stasis"]
  },
  "confrontation": {
    "definition": "The player presses someone directly, or is warned off the lighthouse in terms that don't hold up.",
    "feeds": "tension",
    "resets": ["stasis"]
  }
}
```

The engine ships **no built-in default vocabulary** — the two above are template content,
not fallbacks. `example` is the copy-from reference when authoring a new story, per
schema v2's "adding a story is a content change, not a code change" principle. A story
with `pacing_loop` present must author `beats`; a story without the module classifies
nothing.

---

## 6. Counters and rules

### 6.1 Intensity

The classifier emits an **intensity score of 1–3** alongside `beat_type`, in the same
call. One extra field, no extra request.

A flat scene counter treats a tense negotiation and a live firefight as equivalent, which
under-models the text — the rooftop scene with Venn and the corridor chase are both
`crisis` and land very differently.

| Score | Meaning | Example from the existing text |
|---|---|---|
| 1 | Pressure present, no immediate physical danger | Broker negotiation in Lowmarket; the ward-intake sedation argument |
| 2 | Direct confrontation or forced decision in the room | The rooftop read with Venn; the board review with Containment present |
| 3 | Physical danger, active pursuit, body-horror escalation | The archive handshake; the north-stair flight from Praetor's team |

Counters accumulate intensity, not scene count. A `tension` of 8 might be four moderate
scenes or three heavy ones — closer to how the rhythm actually reads.

### 6.2 Rules

```json
"counters": { "tension": 0, "stasis": 0 },

"rules": [
  {
    "id": "force_release",
    "watch": "tension",
    "threshold": 8,
    "threshold_by_act": { "finale": null },
    "max_deferrals": 3,
    "suppress_when": ["threat_present", "just_fired"],
    "directive": "<see §11.1>",
    "reduced_directive": "<see §11.2>"
  }
]
```

| Field | Notes |
|---|---|
| `watch` | Counter name from `counters`. |
| `threshold` | Accumulated intensity before the rule arms. Default 8 ≈ three heavy or four moderate scenes. Tune by playtest (§16). |
| `threshold_by_act` | Optional overrides — see §13. `null` disables the rule for that act. |
| `max_deferrals` | Deferrals before the reduced directive fires. Default 3. |
| `suppress_when` | Named eligibility predicates, §10. Optional — `example`'s rule uses only `just_fired`. |
| `directive` / `reduced_directive` | Authored prompt text. Genre-specific by nature; belongs in the template, not the engine. |

**v1 scope:** the schema accepts a list so both correction directions are expressible
without code, but **v1 implements and tests exactly one rule per story.** Multi-rule
arbitration (§9) is deferred — nothing in New Babel or `example` exercises it, and it's
cheap to add once something does. A template declaring two rules should log a warning and
use the first.

---

## 7. Progression ledger

**The half the v1 draft dropped, and arguably more important than the counter.**

The LitRPG loop works because the exhale is *earned* — the reader can point at what
changed, and the next escalation has a higher floor. New Babel's protagonist accumulates
only liabilities. Without a gain ledger, forcing a release produces a pause, not a beat.

Since there's no status screen (§3), gains are **diegetic ratchets** — things the
protagonist has that they didn't, expressible in prose. The categories are authored, so a
romance can bank *confidences* and *social standing* rather than *capability* and
*material*:

```json
"progression": {
  "label": "leverage",
  "kinds": ["knowledge", "relationship", "capability", "material"],
  "prompt_hint": "A durable gain is something the protagonist can use later: a name he can now use, someone who owes him, a technique he controls rather than endures, physical access he didn't have."
}
```

Runtime entries live at `state.protagonist.leverage` — an asset of the protagonist, and
it renders in the same prompt region as inventory and relationships:

```json
{ "id": "lev_004", "kind": "knowledge",
  "label": "The four signatories burned into his palm",
  "acquired_turn": 47, "spent": false }
```

The state pass appends an entry whenever a scene produces a durable gain, and marks
`spent: true` when one is used up or invalidated. The release directive names recent
unspent entries, so the beat has something to be *about*.

**Retention.** Spent entries are **retained, not pruned.** They're cheap, they enable
callbacks, and `history.compressed_summary` is already lossy — a spent-but-retained entry
may end up the only surviving record that something was ever gained. Bound the list at
`LEVERAGE_LIMIT` (suggest 40) and, when over, evict **spent entries oldest-first, never
unspent ones**, mirroring how `flags_archive` retires aged non-pinned flags. If unspent
entries alone exceed the limit, allow the overflow rather than dropping a live asset.

Only unspent entries are interpolated into directives; retention costs prompt tokens only
via the roster cap, not per entry.

**Diagnostic:** if two or three consecutive release beats fire with no unspent leverage
to point at, the story is in a pure-attrition stretch. Log it. That's information about
the narrative, not a bug in this system — but it's exactly the condition where a reader
starts to feel the story is spinning.

---

## 8. Data model

Thin by design — schema v2 already defines the containers. Only the deltas are listed.

**Template** (`stories/<slug>/template.json`, authored, immutable):
`mechanics.pacing_loop` (§5, §6.2), `mechanics.progression` (§7).

**Save** (`data/saves/<user_id>/<story_slug>.json`, runtime):

```json
"pacing": {
  "turn_count": 22,
  "turns_since_nudge": 3,
  "subplots_completed_this_act": 1,
  "last_direction": "...",

  "counters": { "tension": 8, "stasis": 0 },
  "last_beat": { "type": "crisis", "intensity": 3 },
  "armed": { "force_release": { "deferrals": 1 } },
  "reveal_queue": ["rev_003"]
},

"protagonist": {
  "leverage": [ ]
}
```

Notes:

- `pacing` is **top-level runtime state** under schema v2, not nested under `plot`.
  `turn_count` is session state, not a plot property.
- `armed` replaces v2's `forced_lull_pending` boolean — keyed by rule id, so multiple
  rules can arm independently. Presence of the key means armed; `deferrals` is its
  counter. Absent means not armed.
- `reveal_queue` holds ids from `mechanics.revelations` (schema v2 §3.6 — the renamed
  `memory_fragments`). Reveal *state* lives in `plot.revelations_revealed`; this queue is
  a placement buffer only.
- `protagonist.leverage` sits beside `inventory` and the relationship scores, which is
  also where it renders in the prompt. Retention and bounding per §7.

**Threat state.** `scene.threat_present` (boolean) is added to the `scene_update` field
that CR-01 introduces to the state pass. Not a new field on this module — it's scene
state, and CR-01 is building the scene writer regardless.

---

## 9. Pipeline integration

1. Player submits a choice → existing `/api/turn` flow.
2. `call_llm` generates narration. On an armed turn the directive is present as a prompt
   section (step 5 of the *previous* turn decided this).
3. `call_llm_json` / `update_progress_from_turn` runs — the extended schema emits
   `beat_type`, `intensity`, new leverage entries, and `scene_update.threat_present`
   alongside what it already extracts. Fields present only when the corresponding module
   is configured, same conditional pattern as `stat_changes`.
4. Counter update:
   - Beat's `feeds` counter += `intensity`.
   - Each counter in the beat's `resets` → 0; clear that rule's `armed` entry and its
     deferral count.
   - For each rule: if `counters[rule.watch] >= effective_threshold` (§13), add
     `armed[rule.id]`.
5. Before the next turn's narration, evaluate eligibility (§10) for each armed rule and
   select at most one directive.

Under schema v2 §5's `SECTIONS` refactor, injection is a section builder:

```python
(rule_armed_and_eligible, _section_pacing_directive),
```

placed with the volatile sections near the pacing nudge, not in the cacheable prefix. It
is a **single-turn addition, dropped afterward** — not a permanent system-prompt change.
That matters given the input-token-dominant cost profile.

If two rules are armed and eligible on the same turn, fire the one with the higher
`counters[watch] / threshold` ratio and leave the other armed. Never inject two
directives; they will contradict each other. **Out of v1 scope** — with one rule per
story the situation can't arise; specified here so the behaviour is settled when a second
rule is added.

---

## 10. Eligibility, deferral, and the ceiling

A forced release must not fire mid-pursuit — that reads as a tonal snap, not a release.
The named predicates in `suppress_when`:

| Predicate | Source |
|---|---|
| `threat_present` | `scene.threat_present` from the state pass (§8) |
| `just_fired` | This rule fired on the previous turn |

Predicates are opt-in per rule. New Babel uses both; `example`'s stasis rule uses only
`just_fired`, since a cozy mystery has no pursuit state to guard against.

**Dropped from v1: `player_action_escalating`.** v3 proposed suppressing when the
submitted action is itself escalating, sourced by tagging each generated option with an
`escalating` boolean. That would change the `OPTIONS:` block format and
`parse_narration_and_options`, colliding with schema v2's `narration.option_count` work,
and a keyword heuristic on free text would be wrong often enough to matter. The deferral
ceiling below already prevents the deadlock this predicate was partly guarding against.
Revisit only if playtesting shows directives firing against clear player intent.

**Each suppression increments `armed[rule_id].deferrals`.**

**The ceiling.** Suppression conditions with no escape hatch deadlock: during a sustained
chase, `threat_present` stays true turn after turn, the rule stays armed forever, and the
feature silently never fires — precisely the failure it exists to prevent, now with extra
machinery.

Once `deferrals >= max_deferrals`, inject `reduced_directive` instead of deferring again.
Guaranteed floor: pressure is released *somehow* within `max_deferrals + 1` turns of the
threshold, even if a full release never becomes available.

---

## 11. Directives

Both are authored per story. The text below is New Babel's, and doubles as the reference
for what a directive should do.

### 11.1 Full release

```
PACING DIRECTIVE — LULL BEAT REQUIRED

Accumulated tension has reached {counter_value} without release.
This scene must function as a LULL:

- No new pursuing threat may be introduced this scene.
- If a threat was actively present at the end of the prior scene, it
  must recede or be lost before this scene's midpoint (escape achieved,
  pursuers lose the trail, a door closes) — narrate this concretely,
  don't skip past it.
- Dread, cost, and consequence remain on the page. This is NOT a safe
  or cozy scene. Do not write comfort, banter-as-relief, or any sense
  that the danger is over. The threat is paused, not gone.
- Name at least one concrete thing the protagonist now has that he did
  not have three scenes ago — a name he can use, someone who owes him,
  a technique he now controls rather than endures, physical access he
  didn't have. Unspent leverage currently available:
  {unspent_leverage}
- If the reveal queue is non-empty, surface exactly ONE reveal and let
  it land without competing against an active chase.
- If a character whose role supports it is present, the space the
  receding threat opened is where the two of them actually get
  somewhere with each other. How far they go is set by their current
  standing on the roster — this escalates what they *do*, not merely
  what goes unsaid:
    below +25    nothing. Professional distance, unclear motives.
    +25 to +60   open, mutual flirtation. Deliberate touch. Innuendo
                 neither of them pretends not to understand.
    +60 to +85   they act on it — kissing, hands, clothing in the way.
                 The scene gets physical on the page.
    above +85    they sleep together, played on the page at whatever
                 explicitness meta.content_rules allows.
  This is not comfort or reward. In this city people get physical
  because things are bad, not because they're safe — the cost from
  prior scenes stays on the page and the threat is still out there.
  Only for characters whose authored role supports it. A rising score
  with an informant or a rival is loyalty or respect, not attraction.
- Options should reflect the lower-stakes register: who to trust, what
  to ask, how to spend a moment without a pursuer, what to do with
  information just learned — not fight-or-flee branching.
```

The options line must not name a count — schema v2 makes that `narration.option_count`.

### 11.2 Reduced (deferral ceiling)

```
PACING DIRECTIVE — MID-ACTION BREATH REQUIRED

A full release has been deferred {deferrals} times and the scene rhythm
has been unbroken for too long. A full release isn't available, so this
scene must contain a genuine breath *within* the action:

- No NEW threat may be introduced this scene. Existing threats continue.
- Include at least one sustained moment where the protagonist is not
  being acted upon: a held position, a conversation that isn't shouted,
  a physical pause of more than a sentence.
- Surface ONE queued reveal, or name ONE piece of unspent leverage and
  what it's now good for.
- Do not resolve the pursuit. This is a trough in the wave, not the end
  of it.
```

Interpolated values available to directive text: `{counter_value}`, `{deferrals}`,
`{unspent_leverage}`, `{queued_reveal}`. Document the list; authors writing a new story's
directives need it.

---

## 12. Reveal placement

> **Hard prerequisite: CR-03.** Revelation *content* currently never reaches the narration
> prompt at all — it's authored, marked revealed by the state pass, and then nothing reads
> it. Queueing reveals into a pipe that isn't connected accomplishes nothing. CR-03 must
> land before this section is implemented, and its acceptance criteria are the gate.

`mechanics.revelations` already gates *whether* a reveal may happen. This module doesn't
change that gating — it only asks whether an unlocked reveal should wait for a corrective
beat before being written into a scene.

Recommendation: **yes, when possible.** When the state pass marks a revelation eligible,
append its id to `pacing.reveal_queue` rather than assuming it fires next scene. The
directive consumes one entry per firing, FIFO, with a time-critical revelation jumping the
queue. If a revelation's own trigger hard-requires it on a specific turn, that overrides —
this is a placement *preference*, not a gate.

---

## 13. Act-scaled thresholds

Confirmed available: `plot.main_thread.current_act` and the acts list.

A flat threshold forces release beats into the climax, where unbroken pressure is the
entire point. `threshold_by_act` keys against act identity:

```json
"threshold_by_act": { "1": 6, "finale": null }
```

Resolution order: exact act number → `"finale"` if the current act has `is_finale` →
base `threshold`. `null` disables the rule for that act entirely.

`"finale": null` should be the default in any authored template. The endgame prompt
already instructs the model to resolve and introduce nothing new; a competing release
directive would fight it.

---

## 14. Acceptance criteria

### 14.1 Acceptance sample — New Babel (release correction)

**Current behaviour** (from the text, turns 47–49): archive handshake breaks → glass
shatters → four signatures burn into the protagonist's palm → door blows inward →
north-stair flight → Praetor's floor team, boots on metal, ninety-second seal warning.
Four proper nouns, a body-horror escalation, and a new pursuit inside two scenes.

**Target behaviour** — the scene after the signatures, directive active. The pursuit is
genuinely off-page, one reveal (Mesmer Holdings) gets unpacked, one leverage item is
named, and the register stays cold. **Rewritten to second person per Q8**: this is the
post-CR-14 target, not a description of what the engine produces today.

> The freight lift stops between floors and nobody tells it to move again.
>
> Venn kills the panel light with the side of her fist. In the dark, the four glyphs under
> your palm are the only thing burning — slow, blue, patient, like they're waiting for you
> to finish reading them.
>
> "Mesmer," you say. "Second name. Who are they?"
>
> "Nobody, on paper." The advocate's pen has finally stopped. "A holding company that
> files three documents a year and owns nothing. I've seen the name twice in eleven years
> of ward review, both times on a memory hold."
>
> "Both times on mine?"
>
> "Once on yours." She doesn't look up. "I don't know whose the other was. That's the part
> that should worry you."
>
> The floor man has his back to the door, silver tool dark across his knees. Nobody is
> coming. That's the thing you keep turning over — nobody is coming, right now, for the
> first time since the chapel, and the quiet doesn't feel like safety. It feels like being
> set down somewhere while something decides what to do with you.
>
> You flex your hand. The glyphs brighten and the lift's dead ceiling strip answers, a
> flicker you can feel in your teeth.
>
> "Stop that," Venn says, without heat.
>
> "I'm not doing it on purpose."
>
> "I know. That's worse." She slides down the wall until she's sitting. "But you folded
> the fragment twice now, and both times you chose it. That's not nothing. Three days ago
> it was writing on walls with your hands."
>
> Three days ago you didn't have a name either. You keep that to yourself.

**Five-point test for a generated release beat:** no new threat introduced; pursuit
concretely off-page; exactly one reveal deepened; one gain explicitly named; nothing that
reads as comfort. The sample hits all five. Use it when tuning directive wording.

**Craft note surfaced by the rewrite.** In second person, narration "you" and
dialogue-addressed "you" collide — Venn's *"you folded the fragment twice now"* reads
identically to the narrator's *"you flex your hand"* until the quotation marks
disambiguate. First person keeps those channels separate for free; second person doesn't.
It works in the sample above, but it needs deliberate handling, and it's a plausible
contributor to why the model drifted toward first person in the first place. Worth a
bullet in New Babel's `narration.style` once CR-14 lands: keep dialogue that addresses the
protagonist short, or attribute it early.

### 14.2 Acceptance sample — `example` (inverse correction)

The `stasis` rule fires when Millbrook has been pleasant for too long. The target isn't a
tension spike — it's a crack in the surface while the warmth continues, which is a
genuinely different shape from New Babel's release beat and the reason the module is
authored per story rather than built into the engine.

> The innkeeper sets the plate down and it's the same breakfast as yesterday. The same
> three rashers laid the same way, the same wedge of tomato at four o'clock. You'd think
> nothing of it, except that yesterday she told you the eggs came in on Tuesday's boat,
> and there was no Tuesday boat.
>
> "You're not eating."
>
> "I'm looking at the calendar." It hangs by the stairs. It says the eleventh. It said the
> eleventh when you arrived, and you have been here four days.
>
> "Someone must turn it." She says it warmly, and she doesn't look at it, and she doesn't
> stop wiping the counter. "More tea?"

**Four-point test:** a concrete detail refuses to add up; the player notices; the social
surface stays warm and unbroken; nothing is confronted or explained. Note what's absent —
no threat, no chase, no dread in the New Babel register. The correction is a *complication*,
not a *release*.

### 14.3 POV note — now diagnosed

v2 flagged that the file opens in second person and runs
first-person-present from turn 2 onward, and asked which was intended. The code read
answers it: `meta.pov` is declared in every template and **never stated in the prompt**
(CR-14). The only POV signal the model receives is the instruction to write option prose
in first person, which bleeds upward into narration. The mixed voice is half deliberate —
first-person options against second-person narration is the documented Choice Format
design — but the narration drift is not.

CR-14 is the fix. Both samples above are written in the story's *intended* voice, so
until CR-14 ships they will not match what the engine actually produces. That's
deliberate: the acceptance criteria describe the target, not the current defect.

---

## 15. Testing

Consistent with the existing `test/_llm_stubs.py` monkeypatch pattern. No live calls.

- **Counter arithmetic:** stub `call_llm_json` returns for each beat × intensity
  combination; assert `feeds` accumulates and `resets` zeroes correctly.
- **Arming:** stubbed accumulating beats summing past threshold set `armed[rule_id]` and
  produce the directive in the next turn's assembled prompt.
- **Eligibility:** stub `scene.threat_present = true`; confirm the directive is *not*
  injected despite being armed, and that `armed` persists rather than being dropped.
- **Deadlock regression:** stub `max_deferrals + 1` consecutive suppressions; assert the
  reduced directive fires. This is the regression test for the v1 design flaw.
- **Leverage:** stub an extraction response with a new entry; assert it appends and that
  `{unspent_leverage}` interpolates into directive text.
- **Leverage retention:** mark an entry spent; assert it persists, is excluded from
  `{unspent_leverage}`, and that eviction past `LEVERAGE_LIMIT` removes spent entries
  oldest-first and never touches an unspent one.
- **Module absent:** a template with no `mechanics.pacing_loop` produces no `beat_type` or
  `intensity` fields in the state-update schema, no `pacing.counters` in state, and no
  directive section — schema v2 P-2. Run against the `regency.json` fixture.
- **Inverse correction:** run `example`'s config (Appendix A); assert the `stasis` counter
  accumulates on `hospitality`/`reassurance`, resets on `unsettling`/`confrontation`, and
  that the complication directive fires — the same code path, opposite direction.
- **Act scaling:** in an act with `threshold_by_act` of `null`, the rule never arms.
- **Two-rule arbitration:** *deferred with the feature (Q1). Add alongside multi-rule
  support.*

Classification *accuracy* stays out of the stub suite — that's §0.2's job.

---

## 16. Rollout and dependency order

This feature is now **downstream of the schema work**, not parallel to it.

```
§0.1 §0.2 §0.3  (validation gates — independent, run anytime)
        ↓
CR-03  revelations reach the narration prompt        → unblocks §12
CR-01  scene writer + scene.threat_present           → unblocks §10
CR-14  pov stated in prompt                          → unblocks §14's sample rewrite
        ↓
Schema v2 phases 1–4  (split, migrator, SECTIONS, mechanics modules)
        ↓
Pacing loop implementation
```

Implementation order once unblocked:

1. Add `mechanics.pacing_loop` / `mechanics.progression` to New Babel's template with the
   §5.1 vocabulary and one `force_release` rule.
2. Extend the state-update schema and prompt: `beat_type`, `intensity`, leverage entries.
   Conditional on the module being present.
3. Counter and ledger update logic — **in `story_engine.py`**, alongside the existing
   `update_progress_from_turn` application block. Not `state_store.py`, which is pure
   storage.
4. Eligibility, deferral ceiling, and the `SECTIONS` directive builder.
5. Add `example`'s config (Appendix A). This is the genericity proof and should land
   *before* playtest tuning, so any assumption baked into the New Babel path fails loudly
   while the code is still fresh rather than months later against a third story.
6. Playtest New Babel's `threshold` at 6, 8, and 10 against §14. Tone judgment, not
   analytically derivable. Tune `example`'s separately — its scale is different.
7. Set `threshold_by_act` with `"finale": null` before the first endgame playtest.

---

## 17. Decisions log

All v3 open questions are resolved. Recorded here rather than deleted, so the reasoning
survives for anyone who later wants to revisit one.

| Q | Question | Decision | Where it landed |
|---|---|---|---|
| Q1 | One rule or a rules list in v1? | Schema accepts a list; **v1 implements one rule per story**. Arbitration specified but deferred. | §6.2, §9, §15 |
| Q2 | Where does the ledger live? | `state.protagonist.leverage` — beside inventory and relationships, which is also where it renders. | §7, §8 |
| Q3 | Prune or retain spent leverage? | **Retain.** Bounded at `LEVERAGE_LIMIT` (40), evicting spent entries oldest-first and never unspent ones. | §7 |
| Q4 | Ship a default beat vocabulary? | Yes, as **template content in `example`**, not an engine fallback. | §5.2, Appendix A |
| Q5 | How to source `player_action_escalating`? | **Dropped from v1.** Option-tagging would change the `OPTIONS:` format and collide with `narration.option_count`; the deferral ceiling already guards the deadlock. | §10 |
| Q6 | Does `example` get the module? | **Yes**, with an inverted `stasis` rule. Doubles as the genericity proof and the copy-from reference. | Appendix A, §14.2 |
| Q7 | `max_deferrals` default? | **3.** Guarantees release within four turns of threshold. Still playtested. | §6.2, §10 |
| Q8 | Rewrite the sample to second person? | **Yes**, marked explicitly as the post-CR-14 target. | §14 |

### Still open (deliberately)

These are measurement outcomes, not design decisions, and can only be settled by running
§0:

- **Beat vocabulary granularity.** Four beats or a collapsed two-way split — decided by
  §0.2's agreement numbers, not by discussion.
- **Whether authored vocabularies survive outside thriller shapes** — §0.3. If they
  don't, `example`'s config is the thing that fails, and the module ships thriller-only
  with the limitation documented.
- **Threshold values** for both stories — §16 step 6.

---

## Appendix A — `example` module configuration

The cozy-mystery counterpart to New Babel's config. Same machinery, opposite correction
direction. This is the reference to copy when authoring a new story's pacing module.

Millbrook's failure mode is not escalation — it's that the town stays pleasant, the
innkeeper stays warm, and nothing about yesterday ever advances. Left alone, the LLM will
happily generate hospitality indefinitely, because in a cozy register "nothing bad
happened" reads as a successful scene.

```json
"mechanics": {
  "pacing_loop": {
    "beats": {
      "hospitality": {
        "definition": "Warm, welcoming, socially pleasant. Nothing about the town's inconsistencies advances.",
        "feeds": "stasis"
      },
      "reassurance": {
        "definition": "A strangeness is explained away, and the explanation holds for now. The player is soothed rather than informed.",
        "feeds": "stasis",
        "resets": ["tension"]
      },
      "unsettling": {
        "definition": "A concrete detail refuses to add up, and the player notices. No confrontation yet.",
        "feeds": "tension",
        "resets": ["stasis"]
      },
      "confrontation": {
        "definition": "The player presses someone directly, or is warned off the lighthouse in terms that don't hold up.",
        "feeds": "tension",
        "resets": ["stasis"]
      }
    },

    "counters": { "tension": 0, "stasis": 0 },

    "rules": [
      {
        "id": "force_complication",
        "watch": "stasis",
        "threshold": 6,
        "threshold_by_act": { "finale": null },
        "max_deferrals": 3,
        "suppress_when": ["just_fired"],
        "directive": "<see below>",
        "reduced_directive": "<see below>"
      }
    ]
  },

  "progression": {
    "label": "footing",
    "kinds": ["observation", "trust", "access", "corroboration"],
    "prompt_hint": "A durable gain is something the player can use later: a contradiction they can now cite, someone who will speak to them candidly, a door or record they can now reach, a second source for something they'd only heard once."
  }
}
```

**Notes on the differences, since they're the point:**

- `suppress_when` carries only `just_fired`. There is no pursuit state in Millbrook, so
  `threat_present` would never be true and including it would be noise. Predicates being
  opt-in per rule is what makes this clean.
- `threshold` is 6, not 8. Cozy scenes are lower-intensity across the board, so the same
  number of scenes accumulates less. Expect to tune this separately.
- `progression.kinds` are investigative rather than survival-shaped. The gain ledger
  generalizes better than the beat vocabulary does — "what does the protagonist now have
  that they didn't" is close to genre-neutral.
- `"finale": null` for the same reason as New Babel: the endgame prompt already drives
  toward resolution, and a competing complication directive would fight it.

### Directive

```
PACING DIRECTIVE — THE SURFACE MUST CRACK

Millbrook has been pleasant for {counter_value} accumulated scenes and
nothing about yesterday has advanced. This scene must contain a genuine
complication:

- Surface at least one concrete detail that refuses to add up, and let
  the player notice it. Specific and physical — a date, an object, a
  repetition, a name — never a vague sense of unease.
- The social surface stays warm. No one is hostile, no one is caught,
  nothing is confronted. Whoever is present remains hospitable
  throughout, and does not acknowledge the thing that doesn't fit.
- Do not explain it. Do not have a character offer a plausible reason.
  The discrepancy is left standing.
- Name at least one concrete thing the player now has that they did not
  have three scenes ago — something they can cite, someone who will
  talk, somewhere they can now go. Unspent footing currently available:
  {unspent_leverage}
- If the reveal queue is non-empty, surface exactly ONE reveal.
- Options should offer ways to pursue the discrepancy, let it lie, or
  test it against someone else — not confrontation-or-flee branching.
```

### Reduced directive

```
PACING DIRECTIVE — ONE DETAIL OUT OF PLACE

A full complication has been deferred {deferrals} times. This scene must
still leave one thing unresolved:

- End the scene with a single concrete detail the player has noticed and
  cannot account for. One sentence is enough.
- Nothing else about the scene needs to change. Warmth, routine, and
  hospitality continue.
- Do not explain it, and do not have the player raise it.
```

