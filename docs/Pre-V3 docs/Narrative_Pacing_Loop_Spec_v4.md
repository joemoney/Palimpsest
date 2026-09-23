# Narrative Pacing Loop — Module Specification

**Status:** Approved for implementation, pending the §0 validation gates
**Version:** 4 (open questions resolved; `example` module authored)
**Project:** cyoa-app / Palimpsest
**Related docs:** `SCHEMA_V2_SPEC.md`, `SCHEMA_COVERAGE_CRD.md`, `CLAUDE.md`,
`docs/Narrative_Engine_Spec.md`, `UI_SPEC.md`

---

## Changes in v4

All eight open questions are now resolved. There are no design reversals. The decisions
mostly cut scope out of v1 and add one worked example.

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

Q4 and Q6 interacted. Instead of shipping New Babel's four beats as a generic default,
`example` now carries a vocabulary that is **structurally parallel but
genre-appropriate**. It has two accumulating beats and two releasing beats, with the
opposite correction direction. Two worked vocabularies demonstrate the abstraction better
than one default would. Appendix A also doubles as the copy-from reference for a new
story.

Q5's removal is a scope win worth noting: tagging options with an `escalating` boolean
would have changed the `OPTIONS:` format and `parse_narration_and_options`. This would
collide with schema v2's `narration.option_count` work. The deferral ceiling (§10) already
guards against the deadlock that this predicate was partly designed to prevent.

---

## Changes in v3

v2 included a standing warning: it inferred field names from conversation rather than
reading them from `story_engine.py`. The reconciliation is now complete. It invalidated
two sections outright.

**Corrections from the code read:**

- **`entity_tracker` does not exist.** v2's §7 declared `"entity_tracker": {"...":
  "existing fields, unchanged"}`. The repo has `plot.entity_interaction_count`. It is a
  bare integer. The state pass increments it when it returns `entity_interaction: true`.
  Only `check_and_advance_act` reads it. There is no budget, no conditions, and no reveal
  scheduling. The budget-and-conditions mechanism was designed in an August session and
  never built.
- **No threat state exists anywhere.** v2 §9's first suppression condition had nothing to
  read. The engine now sources this condition from `scene.threat_present`. This field
  rides along on the `scene_update` field that CR-01 already adds to the state pass.
- **Act/phase tracking does exist** (`plot.main_thread.current_act`). Act-scaled
  thresholds are a small change, not a prerequisite feature. §13 promotes this feature
  from "consider" to fully specified.
- **`world_state.json` does not exist.** State lives at
  `data/saves/<user_id>/<story_slug>.json`, managed by `state_store.py`. The system
  supports multiple users and multiple stories.
- **State mutations do not live in `state_store.py`.** That file is pure storage. Every
  mutation lives in `story_engine.py`. v2 §14.4 pointed implementation at the wrong file.
- **Scene length is 470–500, not 450–700.** Under schema v2, it is
  `narration.scene_length` rather than a module constant.
- **Reveal content never reaches the narration prompt.** CR-03 found that the state pass
  writes `memory_fragments` content, marks it revealed, and never sends it to the prompt.
  v2 §11's reveal queue would have scheduled reveals into a pipe that is not connected.
  CR-03 is now a hard prerequisite.

**Structural change:** the whole feature is now an **optional module** under schema v2's
`mechanics` block. Each story authors its own beat vocabulary, thresholds, and correction
*direction*. v2 hardcoded a thriller/LitRPG assumption into engine behaviour: accumulate
tension, then force release. A cozy mystery's failure mode is the opposite. A horror story
may want forced escalation after too much quiet. See §4.

**Carried forward unchanged from v2:** §0 validation gates, intensity weighting, the
progression ledger, the deferral ceiling, the concrete lull sample as acceptance
criterion, and the stub-based test strategy. These elements all survived the code read
intact.

---

## 0. Pre-implementation validation (still gates everything)

The v3 restructure does not affect this section. Complete these steps before you write
feature code.

**0.1 — Full-corpus baseline classification.** The problem statement rests on a purposive
sample of about 1,000 of `the_attention_economy.txt`'s 2,517 lines. The sample ranges
came partly from scanning for interesting content. This sample is enough to establish
that the problem exists. It is not enough to size the problem. Classify *every* scene in
the file by beat type. This step gives the true release rate. If the true rate is 8% and
clustered somewhere unsampled, the threshold tuning changes. This step also gives a
measurable baseline for later comparison.

**0.2 — Classifier agreement check.** The design assumes `call_llm_json` can reliably
self-report beat type. Hand-label about 30 scenes. Run the classifier prompt. Measure the
agreement between them.

- If agreement between `lull` and `resolution` is poor, collapse them into one releasing
  beat before you build counter logic on it.
- If agreement between `crisis` and `escalation` is poor, apply the same collapse on the
  accumulating side. Both beats feed the counter identically, and §6's intensity score may
  already capture the distinction.
- If overall agreement is below about 70%, stop and fix the classifier prompt. A pacing
  system based on noisy classification fires at random, and it is worse than nothing.

**0.3 — Cross-genre vocabulary check (new in v3).** Run 0.2's methodology once more
against a non-thriller beat vocabulary. The `regency.json` conformance fixture from
`SCHEMA_V2_SPEC.md` §7 is the natural target. If the classifier can only separate beats
when they are violence-shaped, the authored-vocabulary design in §5 does not hold. In that
case, the module must ship thriller-only, and the spec must document that limitation.

---

## 1. Problem statement

This section analyzes pacing in `the_attention_economy.txt`. The analysis draws on
samples from the opening, the Lowmarket/ward-intake stretch, the rooftop negotiation, and
the archive/chase sequence:

- **No downtime beats in any sampled scene.** Every scene is already mid-crisis. Each
  scene's end launches a *new* complication (sedation debate → early transfer team →
  board review → broker breach → rooftop negotiation → Containment claxon → archive
  handshake → blown door → chase). It never resolves the last complication. Tension stacks
  and never resets. This is a finding from the sample, not yet a finding for the whole
  corpus. See §0.1.
- **Reveals cluster at the reader's lowest-bandwidth moments.** The four-signature scene
  (Cordon Dynamics, Mesmer Holdings, Praetor, Marlowe's trust) lands as one dense block of
  new proper nouns. This happens *while* the protagonist is mid-handshake with an entity
  and about to be chased.
- **No legible progression signal.** Threat escalates continuously. Capability,
  resources, and leverage do not escalate. The protagonist accumulates burns, leashes, and
  pursuers. The protagonist banks nothing. Tension has no rising baseline to measure
  against.
- **Root cause:** turn-by-turn LLM narration defaults to escalation as the cheapest
  available signal that a turn mattered. Without an explicit rule that pushes back,
  `call_llm` chooses "add a new threat" over "let this one breathe" almost every time.

Western LitRPG's chapter loop is **encounter → resolution → visible gain → brief downtime
→ next hook**. The loop works through structure, not through authorial restraint. It
forces both a release beat and a legible gain on a schedule. The gain is what makes the
release feel earned, not merely paused.

This spec proposes both halves. A release with nothing gained is a stall.

This module is explicitly **not** OP-MC-style cozy/iyashikei downtime (comfort, banter,
competence-display played warm). That register is wrong for a protagonist who is
threatened and reactive rather than powerful. The target is LitRPG's tighter version:
pressure lifts, but dread does not.

---

## 2. Goals

- Guarantee a corrective beat after sustained accumulation in whichever direction the
  story defines as pathological, without an authored per-scene trigger.
- Track and surface concrete player-side gains, so the loop has a progression half.
- Give reveals a scheduled window to land outside active-crisis scenes.
- Preserve tone: a release beat is not a safe scene. Dread, cost, and consequence persist.
  Only the immediate pursuing threat recedes.
- Reuse the existing two-pass architecture. Add no new LLM call and no new pass.
- Be genre-neutral at the engine level (schema v2 P-2, P-3).

## 3. Non-goals

- No player-facing UI. No tension meter, no XP readout. This is a hidden authoring
  constraint, consistent with the rest of the state-tracking system. Revisit only if
  playtesting shows that players cannot perceive progression without it.
- Not a difficulty or combat system. This module does not touch resolution mechanics. It
  only affects which *kind* of scene the system generates next.
- This is not an extended slice-of-life mode. Corrective beats are single scenes at
  `narration.scene_length`, not multi-scene arcs.
- This is not a numeric progression system. The ledger tracks diegetic assets. It has no
  levels, no points, and no derived values.

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
the beat correction, or take the beat correction without the gain ledger. However, the
release directive is noticeably weaker without leverage to name (§7).

**The correction direction is authored, not assumed.** v2 hardcoded "accumulate tension →
force release." §6.2 expresses this as rules. The same machinery covers:

| Story | Pathological state | Rule |
|---|---|---|
| New Babel (thriller) | Unbroken escalation | Watch `tension`, force a release beat |
| `example` (cozy mystery) | Nothing has advanced for a long stretch | Watch `stasis`, force a complication |
| Survival horror | Either — quiet too long *or* pressure too long | Two rules, both directions |

The first two are authored and shipped (§5.1, Appendix A). The third is expressible in the
schema, but it needs multi-rule support. Q1 defers multi-rule support out of v1.

---

## 5. Beat vocabulary (authored)

Beat names and definitions come from the template. The classifier prompt interpolates the
definition strings directly, so the vocabulary is genuinely story-specific rather than
cosmetically renamed.

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

> Both correction directions are symmetric by construction. Each pair of beats resets the
> counter that the other pair feeds. If a vocabulary never resets one counter, that
> vocabulary is monotonic. It will fire its rule exactly once before `just_fired`
> suppresses it permanently.

Per-beat fields:

| Field | Meaning |
|---|---|
| `definition` | Verbatim into the classifier prompt. The whole quality of the system depends on how crisp these are. |
| `feeds` | Which counter this beat's intensity accumulates into. |
| `resets` | The counters that this beat sets to zero when it fires. |

### 5.2 Worked vocabulary — `example` (cozy mystery)

This vocabulary is structurally parallel to New Babel's: it has two accumulating beats
and two releasing beats. However, the pathological state is inverted. Millbrook's failure
mode is that everything stays pleasant, and nothing about yesterday ever advances.
Appendix A has the full module configuration.

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

The engine ships **no built-in default vocabulary**. The two vocabularies above are
template content, not fallbacks. `example` is the reference to copy when you author a new
story, per schema v2's "adding a story is a content change, not a code change" principle.
A story with `pacing_loop` present must author `beats`. A story without the module
classifies nothing.

---

## 6. Counters and rules

### 6.1 Intensity

The classifier emits an **intensity score of 1–3** alongside `beat_type`, in the same
call. This adds one extra field and needs no extra request.

A flat scene counter treats a tense negotiation and a live firefight as equivalent. This
under-models the text: the rooftop scene with Venn and the corridor chase are both
`crisis`, but they land very differently.

| Score | Meaning | Example from the existing text |
|---|---|---|
| 1 | Pressure present, no immediate physical danger | Broker negotiation in Lowmarket; the ward-intake sedation argument |
| 2 | Direct confrontation or forced decision in the room | The rooftop read with Venn; the board review with Containment present |
| 3 | Physical danger, active pursuit, body-horror escalation | The archive handshake; the north-stair flight from Praetor's team |

Counters accumulate intensity, not scene count. A `tension` count of 8 might represent
four moderate scenes or three heavy ones. This measure is closer to how the rhythm
actually reads.

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
| `threshold` | Accumulated intensity before the rule arms. Default 8 ≈ three heavy or four moderate scenes. Tune this by playtest (§16). |
| `threshold_by_act` | Optional overrides — see §13. `null` disables the rule for that act. |
| `max_deferrals` | Deferrals before the reduced directive fires. Default 3. |
| `suppress_when` | Named eligibility predicates (§10). This field is optional. `example`'s rule uses only `just_fired`. |
| `directive` / `reduced_directive` | Authored prompt text. This is genre-specific by nature, so it belongs in the template, not the engine. |

**v1 scope:** the schema accepts a list, so both correction directions are expressible
without code. However, **v1 implements and tests exactly one rule per story.** Multi-rule
arbitration (§9) is deferred. Nothing in New Babel or `example` exercises it, and it is
cheap to add once something does. If a template declares two rules, the system must log a
warning and use the first rule.

---

## 7. Progression ledger

**This is the half that the v1 draft dropped, and it is arguably more important than the
counter.**

The LitRPG loop works because the exhale is *earned*. The reader can point at what
changed, and the next escalation has a higher floor. New Babel's protagonist accumulates
only liabilities. Without a gain ledger, a forced release produces a pause, not a beat.

Since there is no status screen (§3), gains are **diegetic ratchets**: things the
protagonist has now that they did not have before, expressible in prose. The categories
are authored, so a romance can bank *confidences* and *social standing* rather than
*capability* and *material*:

```json
"progression": {
  "label": "leverage",
  "kinds": ["knowledge", "relationship", "capability", "material"],
  "prompt_hint": "A durable gain is something the protagonist can use later: a name he can now use, someone who owes him, a technique he controls rather than endures, physical access he didn't have."
}
```

Runtime entries live at `state.protagonist.leverage`, an asset of the protagonist. This
asset renders in the same prompt region as inventory and relationships:

```json
{ "id": "lev_004", "kind": "knowledge",
  "label": "The four signatories burned into his palm",
  "acquired_turn": 47, "spent": false }
```

The state pass appends an entry whenever a scene produces a durable gain. The state pass
marks an entry `spent: true` when the entry is used up or invalidated. The release
directive names recent unspent entries, so the beat has something to be *about*.

**Retention.** The system **retains spent entries; it does not prune them.** They are
cheap, they enable callbacks, and `history.compressed_summary` is already lossy. A
spent-but-retained entry may end up as the only surviving record that something was ever
gained. Bound the list at `LEVERAGE_LIMIT` (suggested value 40). When the list goes over
this limit, evict **spent entries oldest-first, and never evict unspent ones.** This
mirrors how `flags_archive` retires aged non-pinned flags. If unspent entries alone exceed
the limit, the system must allow the overflow. It must not drop a live asset.

Directives interpolate only unspent entries. Retention costs prompt tokens only through
the roster cap, not per entry.

**Diagnostic:** if two or three consecutive release beats fire with no unspent leverage
to point at, the story is in a pure-attrition stretch. Log this condition. This is
information about the narrative, not a bug in this system. However, it is exactly the
condition where a reader starts to feel that the story is spinning.

---

## 8. Data model

This data model is thin by design, because schema v2 already defines the containers. This
section lists only the deltas.

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
- `armed` replaces v2's `forced_lull_pending` boolean. The engine keys `armed` by rule id,
  so multiple rules can arm independently. The presence of a key means that rule is
  armed, and `deferrals` is its counter. The absence of a key means the rule is not armed.
- `reveal_queue` holds ids from `mechanics.revelations` (schema v2 §3.6, the renamed
  `memory_fragments`). Reveal *state* lives in `plot.revelations_revealed`. This queue is
  only a placement buffer.
- `protagonist.leverage` sits beside `inventory` and the relationship scores. The prompt
  also renders `protagonist.leverage` in that same location. See §7 for retention and the
  bounding rule.

**Threat state.** CR-01 adds `scene.threat_present` (boolean) to the `scene_update` field
that it introduces to the state pass. This is not a new field for this module; it is
scene state, and CR-01 builds the scene writer regardless.

---

## 9. Pipeline integration

1. The player submits a choice through the existing `/api/turn` flow.
2. `call_llm` generates the narration. On an armed turn, the directive appears as a
   prompt section, because step 5 of the *previous* turn decided this.
3. `call_llm_json` / `update_progress_from_turn` runs. The extended schema emits
   `beat_type`, `intensity`, new leverage entries, and `scene_update.threat_present`,
   alongside what it already extracts. These fields appear only when the story
   configures the corresponding module, the same conditional pattern that `stat_changes`
   uses.
4. Counter update:
   - Beat's `feeds` counter += `intensity`.
   - The engine sets each counter in the beat's `resets` to 0. It also clears that rule's
     `armed` entry and its deferral count.
   - For each rule: if `counters[rule.watch] >= effective_threshold` (§13), add
     `armed[rule.id]`.
5. Before the next turn's narration, evaluate eligibility (§10) for each armed rule.
   Select at most one directive.

Under schema v2 §5's `SECTIONS` refactor, injection is a section builder:

```python
(rule_armed_and_eligible, _section_pacing_directive),
```

The engine places this section builder with the volatile sections near the pacing nudge,
not in the cacheable prefix. It is a **single-turn addition, dropped afterward**, not a
permanent system-prompt change. This distinction matters, given the input-token-dominant
cost profile.

If two rules are armed and eligible on the same turn, fire the rule with the higher
`counters[watch] / threshold` ratio. Leave the other rule armed. Never inject two
directives. Two directives will contradict each other. **This is out of v1 scope.** With
one rule per story, this situation cannot arise. This spec settles the behavior here, so
it is ready for when a story adds a second rule.

---

## 10. Eligibility, deferral, and the ceiling

A forced release must not fire mid-pursuit. A release that fires mid-pursuit reads as a
tonal snap, not a release. The named predicates in `suppress_when`:

| Predicate | Source |
|---|---|
| `threat_present` | `scene.threat_present` from the state pass (§8) |
| `just_fired` | This rule fired on the previous turn |

Predicates are opt-in per rule. New Babel uses both. `example`'s `stasis` rule uses only
`just_fired`, because a cozy mystery has no pursuit state to guard against.

**Dropped from v1: `player_action_escalating`.** v3 proposed a suppression that would
trigger when the submitted action is itself escalating. The design sourced this by
tagging each generated option with an `escalating` boolean. This tagging would change the
`OPTIONS:` block format and `parse_narration_and_options`. It would also collide with
schema v2's `narration.option_count` work. In addition, a keyword heuristic on free text
would be wrong often enough to matter. The deferral ceiling below already prevents the
deadlock that this predicate was partly meant to guard against. Revisit this decision
only if playtesting shows that directives fire against clear player intent.

**Each suppression increments `armed[rule_id].deferrals`.**

**The ceiling.** Suppression conditions with no escape hatch cause a deadlock. During a
sustained chase, `threat_present` stays true turn after turn. The rule stays armed
forever, and the feature never fires. This is precisely the failure that the feature
exists to prevent, now with extra machinery.

Once `deferrals >= max_deferrals`, inject `reduced_directive`. Do not defer again. This
guarantees a floor: the system releases pressure *somehow* within `max_deferrals + 1`
turns of the threshold, even if a full release never becomes available.

---

## 11. Directives

Both are authored per story. The text below is New Babel's, and it doubles as the
reference for what a directive should do.

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

The options line must not name a count. Schema v2 already handles option count through
`narration.option_count`.

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
> prompt at all. The story authors it, the state pass marks it revealed, and then nothing
> reads it. A reveal queued into a pipe that is not connected accomplishes nothing. CR-03
> must land before anyone implements this section, and its acceptance criteria are the
> gate.

`mechanics.revelations` already gates *whether* a reveal may happen. This module does not
change that gating. It only asks whether an unlocked reveal should wait for a corrective
beat before the narration writes it into a scene.

Recommendation: **yes, when possible.** When the state pass marks a revelation eligible,
append its id to `pacing.reveal_queue`. Do not assume that the revelation fires in the
next scene. The directive consumes one entry per firing, in FIFO order. A time-critical
revelation jumps the queue. If a revelation's own trigger requires it on a specific turn,
that requirement overrides the queue. This queue order is a placement *preference*, not a
gate.

---

## 13. Act-scaled thresholds

`plot.main_thread.current_act` and the acts list are already available.

A flat threshold forces release beats into the climax, where unbroken pressure is the
entire point. `threshold_by_act` keys against act identity:

```json
"threshold_by_act": { "1": 6, "finale": null }
```

Resolution order: first the exact act number, then `"finale"` if the current act has
`is_finale`, then the base `threshold`. `null` disables the rule for that act entirely.

`"finale": null` should be the default in any authored template. The endgame prompt
already instructs the model to resolve the story and introduce nothing new. A competing
release directive would fight that instruction.

---

## 14. Acceptance criteria

### 14.1 Acceptance sample — New Babel (release correction)

**Current behaviour** (from the text, turns 47–49): archive handshake breaks → glass
shatters → four signatures burn into the protagonist's palm → door blows inward →
north-stair flight → Praetor's floor team, boots on metal, ninety-second seal warning.
This sequence packs four proper nouns, a body-horror escalation, and a new pursuit into
two scenes.

**Target behaviour:** this is the scene after the signatures, with the directive active.
The pursuit is genuinely off-page. The narration unpacks one reveal (Mesmer Holdings) and
names one leverage item. The register stays cold. **This sample is rewritten to second
person per Q8.** It is the post-CR-14 target, not a description of what the engine
produces today.

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
reads as comfort. The sample hits all five criteria. Use it when you tune directive
wording.

**A craft note that the rewrite surfaced.** In second person, narration "you" and
dialogue-addressed "you" collide. Venn's *"you folded the fragment twice now"* reads
identically to the narrator's *"you flex your hand"* until the quotation marks
disambiguate them. First person keeps those channels separate for free. Second person
does not. This works in the sample above, but it needs deliberate handling. It is also a
plausible contributor to why the model drifted toward first person in the first place.
This deserves a bullet in New Babel's `narration.style` once CR-14 lands: keep dialogue
that addresses the protagonist short, or attribute it early.

### 14.2 Acceptance sample — `example` (inverse correction)

The `stasis` rule fires when Millbrook has been pleasant for too long. The target is not
a tension spike. Instead, it is a crack in the surface while the warmth continues. This is
a genuinely different shape from New Babel's release beat. It is also the reason each
story authors its own module, rather than the engine building one module for all
stories.

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
surface stays warm and unbroken; nothing is confronted or explained. Note what is absent:
no threat, no chase, and no dread in the New Babel register. The correction is a
*complication*, not a *release*.

### 14.3 POV note — now diagnosed

v2 flagged that the file opens in second person, then runs first-person-present from turn
2 onward. v2 asked which voice was intended. The code read answers this question: every
template declares `meta.pov`, but **the prompt never states it** (CR-14). The only POV
signal that the model receives is the instruction to write option prose in first person.
This instruction bleeds upward into the narration. The mixed voice is half deliberate.
First-person options against second-person narration is the documented Choice Format
design. However, the drift in the narration voice is not deliberate.

CR-14 is the fix. Both samples above use the story's *intended* voice, so until CR-14
ships, they will not match what the engine actually produces. This is deliberate: the
acceptance criteria describe the target, not the current defect.

---

## 15. Testing

This testing plan is consistent with the existing `test/_llm_stubs.py` monkeypatch
pattern. It makes no live calls.

- **Counter arithmetic:** stub the `call_llm_json` returns for each beat × intensity
  combination. Assert that `feeds` accumulates correctly and that `resets` zeroes
  correctly.
- **Arming:** stub accumulating beats that sum past the threshold. Assert that this sets
  `armed[rule_id]` and produces the directive in the next turn's assembled prompt.
- **Eligibility:** stub `scene.threat_present = true`. Confirm that the directive is
  *not* injected, even though the rule is armed. Confirm that `armed` persists and the
  system does not drop it.
- **Deadlock regression:** stub `max_deferrals + 1` consecutive suppressions. Assert that
  the reduced directive fires. This is the regression test for the v1 design flaw.
- **Leverage:** stub an extraction response with a new entry. Assert that the system
  appends the entry, and that `{unspent_leverage}` interpolates it into the directive
  text.
- **Leverage retention:** mark an entry as spent. Assert that the entry persists and that
  `{unspent_leverage}` excludes it. Assert that eviction past `LEVERAGE_LIMIT` removes
  spent entries oldest-first, and that it never removes an unspent entry.
- **Module absent:** a template with no `mechanics.pacing_loop` produces no `beat_type` or
  `intensity` fields in the state-update schema. It also produces no `pacing.counters` in
  state and no directive section, per schema v2 P-2. Run this test against the
  `regency.json` fixture.
- **Inverse correction:** run `example`'s configuration (Appendix A). Assert that the
  `stasis` counter accumulates on `hospitality` and `reassurance`, and resets on
  `unsettling` and `confrontation`. Assert that the complication directive fires, using
  the same code path in the opposite direction.
- **Act scaling:** in an act with `threshold_by_act` of `null`, the rule never arms.
- **Two-rule arbitration:** *deferred with the feature (Q1). Add alongside multi-rule
  support.*

Classification *accuracy* stays out of the stub suite. That work belongs to §0.2.

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

1. Add `mechanics.pacing_loop` and `mechanics.progression` to New Babel's template, with
   the §5.1 vocabulary and one `force_release` rule.
2. Extend the state-update schema and the prompt to add `beat_type`, `intensity`, and
   leverage entries. Add these only when the module is present.
3. Add the counter and ledger update logic **in `story_engine.py`**, alongside the
   existing `update_progress_from_turn` application block. Do not add it to
   `state_store.py`, which is pure storage.
4. Add the eligibility check, the deferral ceiling, and the `SECTIONS` directive builder.
5. Add `example`'s configuration (Appendix A). This is the genericity proof, and it must
   land *before* playtest tuning. This way, any assumption built into the New Babel path
   fails loudly while the code is still fresh, rather than months later against a third
   story.
6. Playtest New Babel's `threshold` at 6, 8, and 10 against §14. This is a judgment about
   tone, not something you can derive analytically. Tune `example`'s threshold
   separately, because its scale is different.
7. Set `threshold_by_act` with `"finale": null` before the first endgame playtest.

---

## 17. Decisions log

All v3 open questions are now resolved. This document records them here rather than
deleting them, so the reasoning survives for anyone who wants to revisit one later.

| Q | Question | Decision | Where it landed |
|---|---|---|---|
| Q1 | One rule or a rules list in v1? | The schema accepts a list, but **v1 implements one rule per story**. The spec specifies arbitration but defers it. | §6.2, §9, §15 |
| Q2 | Where does the ledger live? | `state.protagonist.leverage`, beside inventory and relationships. The prompt also renders it in that location. | §7, §8 |
| Q3 | Prune or retain spent leverage? | **Retain.** The system bounds the list at `LEVERAGE_LIMIT` (40). It evicts spent entries oldest-first and never evicts unspent ones. | §7 |
| Q4 | Ship a default beat vocabulary? | Yes, as **template content in `example`**, not an engine fallback. | §5.2, Appendix A |
| Q5 | How to source `player_action_escalating`? | **Dropped from v1.** Option-tagging would change the `OPTIONS:` format and collide with `narration.option_count`. The deferral ceiling already guards against the deadlock. | §10 |
| Q6 | Does `example` get the module? | **Yes**, with an inverted `stasis` rule. This choice doubles as the genericity proof and the copy-from reference. | Appendix A, §14.2 |
| Q7 | `max_deferrals` default? | **3.** This guarantees release within four turns of the threshold. Playtesting continues on this value. | §6.2, §10 |
| Q8 | Rewrite the sample to second person? | **Yes.** The spec marks this explicitly as the post-CR-14 target. | §14 |

### Still open (deliberately)

These are measurement outcomes, not design decisions. Only §0 can settle them.

- **Beat vocabulary granularity.** The choice is four beats or a collapsed two-way split.
  §0.2's agreement numbers decide this, not discussion.
- **Whether authored vocabularies survive outside thriller shapes.** See §0.3. If they do
  not, `example`'s configuration is the thing that fails, and the module ships
  thriller-only, with the limitation documented.
- **Threshold values** for both stories. See §16 step 6.

---

## Appendix A — `example` module configuration

This appendix is the cozy-mystery counterpart to New Babel's configuration. It uses the
same machinery, with the opposite correction direction. This is the reference to copy
when you author a new story's pacing module.

Millbrook's failure mode is not escalation. Instead, the town stays pleasant, the
innkeeper stays warm, and nothing about yesterday ever advances. If no one corrects it,
the LLM will happily generate hospitality indefinitely, because in a cozy register,
"nothing bad happened" reads as a successful scene.

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
  `threat_present` would never be true. This predicate would only add noise here. Each
  predicate is opt-in per rule, and that is what keeps this design clean.
- `threshold` is 6, not 8. Cozy scenes are lower-intensity across the board, so the same
  number of scenes accumulates less. Expect to tune this separately.
- `progression.kinds` are investigative rather than survival-shaped. The gain ledger
  generalizes better than the beat vocabulary does. The question "what does the
  protagonist now have that they did not have before" is close to genre-neutral.
- This module also sets `"finale": null`, for the same reason as New Babel. The endgame
  prompt already drives toward resolution, and a competing complication directive would
  fight it.

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

