# Engine v2 / Schema v3 — Specification

**Status:** Design. Nothing in this document is implemented. It supersedes nothing yet;
`docs/SCHEMA_V2_SPEC.md` remains the description of what is running.

**Naming.** `schema_version: 2` already shipped, so "v2" is ambiguous in this codebase.
This document uses **engine v2** for the runtime architecture and **schema v3** for the
template/save format it requires. They land together; neither is useful alone.

**Relationship to schema v2.** v2 drew the boundary between *authored content* and
*runtime state* (P-1). It did not draw a boundary between *who decides a thing is true*
and *who describes it*. Engine v2 draws that second boundary. Every v2 principle P-1
through P-7 survives unchanged; this spec adds E-1 through E-7 and generalises P-7 from
one feature into the architecture.

---

## 1. Motivation

### 1.1 The mantra acquires a mechanism

`CLAUDE.md` opens with "Tight Rails, Loose Paint." Today that phrase describes **scope**:
world rules are strict, scene execution is free. It does not describe **authority** — who
is permitted to decide that the player's fuel is now 37, that a door opened, that a clue
landed.

Engine v2 promotes the mantra to a statement about authority:

> **The LLM observes. The engine adjudicates. The LLM renders.**

Rails are code. Paint is the model. A mechanic is a rail. Therefore mechanics are code,
and the template's job is to *select and configure* which mechanic engines a story runs.

### 1.2 Where the current boundary actually sits

The leak is visible in one function. `story_engine.update_progress_from_turn` asks the
model for a **state diff**: `stat_changes`, `relationship_changes`, `subplot_progress`,
`items_gained`, `items_lost`, `failure_triggered`. The engine then bounds-checks the
result and writes it down.

```python
# story_engine.py, relationship application
entry["relationship"] = max(-100, min(100, entry.get("relationship", 0) + int(delta)))
```

The model chose `delta`. The engine chose the clamp. That is not the engine owning a
mechanic; that is the engine acting as a range check on the model's arithmetic.

Two details of that line are worth recording, because they are the shape of the whole
problem:

- `mechanics.relationships.axis` lets an author rename the poles (*unaware → fixated*,
  *indebted → owed*). The scale itself is still hardcoded `±100`. The template configures
  the **words in the prompt**, not the mechanic. Most of `mechanics.*` in v2 is prompt-string
  configuration wearing a mechanic's name.
- `mechanics.stats.floor`/`.ceiling` *did* get real per-story bounds — and that is exactly
  as far as engine ownership currently extends: the bounds, never the delta.

### 1.3 The pattern already exists in the tree

`mechanics.pacing_loop` is engine v2, written early and by accident. The model emits
`beat_type` and `intensity` — a **classification against an authored vocabulary**, not a
number it invented. The engine then does all of the work: accumulation into the beat's
`feeds` counter, zeroing every counter in `resets`, disarming any rule watching a reset
counter, computing the effective threshold against the current act, and arming the rule
when the threshold is crossed.

Nothing in that arithmetic is delegated. The model is a sensor; the engine is the referee.
That block sits roughly forty lines below the relationship clamp above, in the same
function, in the opposite shape.

**Engine v2's thesis is one sentence: generalise the pacing loop's shape to every mechanic.**

### 1.4 What this buys that a firmer prompt cannot

P-7 already records the argument in miniature. On a real 70-turn save the displayed `SYNC`
read 26 for four consecutive turns while the save held 34, and the displayed sequence was
not even monotonic. The fix was not a better instruction; it was `mechanics.stats.readout`,
where the model emits a token and the engine substitutes the figure.

Generalised, three things follow:

1. **Drift becomes bounded and auditable.** The model can still misclassify a scene. But a
   misclassification is one wrong categorical choice, recorded in the event log, with a
   consequence the engine priced — not unbounded arithmetic that compounds silently across
   seventy turns.
2. **Promises become enforceable.** Today no statement made to the player is guaranteed.
   "You cannot enter the lighthouse without the brass key" is a hope. Under E-7 it is a
   precondition the engine evaluates, and the model is never asked.
3. **Mechanics become testable without an API key.** `test/` is offline-first by design.
   A pure `resolve()` needs no stub at all — it is the first part of this system that can
   be tested the way ordinary code is tested.

---

## 2. Design principles

These extend, and do not replace, `docs/SCHEMA_V2_SPEC.md` §1.

**E-1 — The LLM observes, the engine adjudicates, the LLM renders.**
Three roles, three moments in the turn, never blended. The observation pass returns
evidence about what happened. The engine converts evidence into state. The narration pass
describes state it is given and never computes it. A single call that both narrates and
decides is forbidden for the same reason `CLAUDE.md` already forbids merging narration and
state-update: the output gets messier and the authority gets muddy.

**E-2 — A mechanic is a code module; the template selects and configures it.**
`mechanics.<slot>.engine` names a registered implementation. The template supplies its
parameters. An author writes JSON; the rules live in Python. This is P-3 read in the other
direction: P-3 pushed *creative* decisions out of the engine into the template, and E-2
pulls *mechanical* decisions out of the template's prompt strings into the engine. Act
advancement is the single declared exception to E-1 and E-2 alike; §2.1 reasons it from
E-7 rather than carving it out, and bounds it to the verdict only.

**E-3 — Observations are typed, categorical, and mutually independent.**
An observation field asks the model to *classify*, never to compute. Legal:
`{"type": "travel", "distance": "long"}`. Illegal: `{"fuel": -3}`. No engine's observation
may depend on another engine's observation, which is what makes §5's sharding safe.

**E-4 — Resolution is pure, ordered, and offline.**
`resolve()` takes state plus observations and returns effects. It performs no I/O, makes
no LLM call (§6.4 is the one declared exception), and is deterministic. Given the same
inputs it returns the same effects, on any machine, forever.

**E-5 — Absence is structural, not disciplinary.**
P-2 says an absent module leaks nothing into prompt, schema, state, or UI. In v2 that is
enforced by `.get()` discipline spread across 2,885 lines of `story_engine.py` and watched
by `test_genre_conformance.py`. In v3 an unregistered engine has no code path that could
contribute a prompt line or a schema field. The fixtures stop being the guard and become
the smoke test.

**E-6 — Fuzzy judgement is an explicit, gated, logged escape hatch.**
Some triggers do not decompose into an event vocabulary — "the player catches a second
contradiction about yesterday" is a judgement, not a category. An engine may call Tier C
for a boolean against an authored predicate, under §6.4's constraints: a code predicate
must gate it first, the question and answer are both written to the event log, and it is
monkeypatchable. What is forbidden is smuggling judgement into a general state diff, which
is what v2 does today.

**E-7 — Anything the player is invited to reason over is enforced by code.**
P-7 generalised. P-7's test question is unchanged and still the right one: *if the model
gets this wrong once, is the result a slightly worse scene, or a broken promise to the
player?* Prose, tone and pacing are slightly-worse and stay in the template. Numbers,
inventory contents, whether a door is locked, whether a deadline has passed, and the odds
of an attempt are broken promises and belong to an engine.

### 2.1 The declared exception: act advancement

**Act advancement is exempt from E-1 and E-2. The model keeps the verdict.**

An exception that isn't reasoned from the principles is just a leak, so this one is
derived from E-7's own test rather than carved out around it. *If the model gets this
wrong once, is the result a slightly worse scene or a broken promise to the player?* An
act advancing a turn early or a turn late is a pacing wobble. Nobody was shown a number,
no threshold was published, and the player was never invited to reason over it. By E-7's
test that is paint, and moving it into code would be moving it to the wrong side of the
line — the engine has no better basis for the judgement than the model does, and would
have to fake one by inventing a threshold that no novelist would defend (which is P-3
run backwards).

This is also the one place where the model's opinion is not a fallible proxy for a fact.
"Has this act narratively resolved?" has no ground truth for `resolve()` to compute. It
is a reading of the story, and the director prompt in `check_and_advance_act` already
tells the model to judge "what's actually happened - not a checklist."

**The exception is narrow, and it is about the verdict only.** The engine already owns
everything around it and keeps all of it:

| Engine (rails) | Model (paint) |
|---|---|
| The no-op once `endgame.requested` is set | `ready` — has this act resolved? |
| The OR-trigger: a subplot completed this act, *or* `act_check_frequency` turns elapsed | `reason` |
| Resetting `turns_since_act_check` | The next act's title, description and `completion_signals` |
| Act numbering, `_mark_act_completed`, `act_history`, `current_act` | |
| Resetting `subplots_completed_this_act` | |
| Validating the verdict before it is allowed to take effect | |
| NPC insertion, via `insert_character` | |

Read as a rule: **the engine owns when the question is asked, whether the answer is
allowed to land, and everything that happens afterwards. The model owns only the reading.**

The last engine-side row is work, not description. `SCHEMA_V2_SPEC` §3.8 requires that a
generated act saved with an empty `completion_signals` list be treated as a validation
failure, equivalent to a missing title — because without signals the pacing-nudge block
silently disappears for the rest of the story. What shipped is
`verdict.get("completion_signals") or []`, which accepts the empty list. Engine v2 should
close that gap while it is formalising the boundary: an exception granted to the model's
*judgement* is not an exception granted to its *output shape*.

**Why this is not a §6.4 judgement.** It looks like one and it is not. A §6.4 judgement
returns a boolean, is capped at one per turn across all engines, and belongs to an engine
that declared `judgement: true`. Act advancement returns structured generated content,
runs on its own pacing cadence, and belongs to no engine at all. Folding it into §6.4
would either break that section's boolean-only rule or force a fake engine into the
registry to host it. It stays where it is, outside the registry, as a named exception.

**Subplot progress is not covered by this exception.** It was raised alongside act
advancement and it resolves the other way. `subplot_progress` asks the model for an
integer 0–100 that the engine adds to a running total and compares against
`completion_threshold` — arithmetic performed by the model, which is exactly the shape
E-3 forbids, and the same shape as the `stat_changes` and `relationship_changes` this
spec exists to remove. The model should classify how materially a beat advanced a thread;
the engine should price the classification. That makes subplot progress ordinary registry
work (§10, phase 4), not an exception.

**Consequences for §3.2.** The registry resolves `mechanics` only. `check_and_advance_act`
is not a bound engine, contributes no observation field, and is not reached through
`resolve()`. It keeps its own `_timed("act_advancement_check", ...)` call site and its
existing `STATUS_LABELS`/`DEFAULT_STEP_ESTIMATE_SECONDS` entries, unchanged.

### 2.2 Act completion preconditions

§2.1 gives the model the verdict. It does not have to give it the *whole* verdict. The
condition splits cleanly:

- **Necessary conditions → engine.** An authored `requires` predicate over engine state,
  evaluated before the director is called at all. Unmet means no LLM call, no verdict to
  validate, and no advancement.
- **Sufficiency → model.** With the predicates met, the director is still asked whether the
  act *feels* resolved, and can still say no. §2.1 is intact: the model can always refuse,
  it just cannot approve prematurely.

```jsonc
{ "act_number": 1, "title": "Arrival",
  "requires": { "all": [ { "revelation": "rev_001" },
                         { "flag": "warned_off_lighthouse" } ] },
  "completion_signals": [ "..." ] }
```

An absent `requires` is today's pure-judgement behaviour, which keeps P-4: the minimal
template still runs, and an author who wants none writes none. Authored, it gives an act a
floor the model cannot talk its way under.

The evaluator is the `gate` engine's (§7.4) pointed at act advancement rather than at a
door — the same predicate code, a different target. §7.9's "act advancement is not an
engine" is unaffected: an engine supplies the evaluator, and still does not own the verdict.

**What a predicate may point at.** Two independent conditions. The referent must be
**enumerable when the predicate is written**, and the predicate must **latch** — once true,
stay true. A completion condition that can un-satisfy itself is worse than no condition.

| Referent | Enumerable when written | Latching | Usable |
|---|---|---|---|
| Revelation id | Yes — authored, finite, stable ids | Yes — `revelations_revealed` is written at one site and deleted at none | **Yes** |
| Stat threshold | Yes — axes are fixed at save creation and the model can never add one | No — stats move both ways | Needs a high-water mark |
| Authored character's tier | Yes — authored characters are never evicted | No — scores move | Needs a high-water mark |
| Authored location visited | Yes | No state exists — `scene.location` is current-only | Needs new state |
| Flag | Only if authored ahead of time; runtime flag names are model-invented | Yes, *if evaluated correctly* — see below | **Yes, with care** |
| Discovered character | No | No — evicted closest-to-neutral at `RELATIONSHIPS_LIMIT` | No |
| Subplot id | No — the subplot does not exist yet | Ids are stable once created | No |

**Flag predicates must read `flags.active ∪ flags.archive`.** This is not a preference.
`archive_stale_flags` retires any unpinned flag out of `active` once its setting turn falls
outside `RECENT_TURN_LIMIT` (10), and `act_check_frequency` defaults to 12 — so a predicate
reading `active` alone would be consulted on a cadence *longer than the flag's own lifetime
there*, and would be reliably false at exactly the moment it is checked. `archive` is
written at two sites and popped at none, which makes `active ∪ archive` monotonic and gives
the "did this ever happen" semantics completion actually wants.

**Generated acts carry no `requires`.** This is a deliberate asymmetry, and it is the whole
reason the feature is authored-only:

> An authored predicate may be hard because a human verified it is satisfiable.
> A generated predicate may not be, because nothing did.

The engine cannot check satisfiability. `{"revelation": "rev_005"}` is unsatisfiable if
rev_005's own trigger requires a location the act never visits, and no static check sees
that. The failure that produces is a save whose main thread **can never advance** — with
`generate_pacing_nudge` still steering toward an act that cannot end. Weigh that against
what act advancement gets wrong today, which is landing a turn early or late. By E-7's own
test the first is a broken promise and the second is a wobble, so **E-7 argues against
generated predicates**, the same test that put the verdict on the model's side in §2.1.

There is a second reason: a model that may *declare* a referent rather than select one
creates a name-coordination problem across calls, where a narration-time pass turns later
must emit a byte-identical string. That is the exact fragility
`relationships[name]["npc_id"]` exists to fix.

**If it is ever revisited** (§12.5), the shape that would work is recorded here so the
analysis is not redone: the engine builds a menu of live, monotonic referents at generation
time and the model **selects** from it rather than writing a predicate — the same
constrained-selection pattern as `VALID LOCATION IDS` and as `stat_changes` only moving
axes that already exist; unknown referents are dropped silently on write rather than
failing the act (the `CR-04 dangling connected_to id skipped silently` precedent); and a
generated predicate **expires**, blocking at most a fixed number of act checks before
degrading to advisory. Expiry is a correctness requirement, not a nicety — it is what turns
a possible hard-lock into a bounded delay. Note where that lands: a floor that decays to
nothing is judgement-only with a delay, which is most of the way back to carrying no
`requires` at all. That is the argument for not building it until a real playtest shows
acts advancing too early.

---

## 3. The mechanic engine contract

### 3.1 Interface

```python
class MechanicEngine:
    slot: str            # "resource", "gate", ... — the mechanics.<slot> key it serves
    name: str            # "bounded_counter" — the value of mechanics.<slot>.engine
    resolve_order: int   # §6.2

    def init_state(self, cfg, ctx) -> dict:
        """Runtime state this engine owns, at save creation. {} for a stateless engine."""

    def observations(self, cfg, ctx) -> list[ObservationField] | None:
        """Typed questions for the observation pass, or None to ask nothing this turn.
        Returning None is the normal case for a gated engine (§5.2)."""

    def prompt_sections(self, cfg, ctx) -> dict[str, str]:
        """Named fragments for the narration prompt. An omitted key is an omitted
        section — never an empty header (P-2)."""

    def resolve(self, cfg, ctx, observations, events) -> list[Effect]:
        """Pure. The mechanic's actual rules. No I/O, no LLM (except §6.4)."""

    def render(self, cfg, ctx, text) -> str:
        """Deterministic post-narration substitution. The apply_stat_readouts slot."""
```

Every method except `resolve` has a do-nothing default. A stateless, promptless engine that
only prices events implements one method.

### 3.2 Registration

Engines register into a process-level registry keyed by `(slot, name)`. `load_state`
resolves a story's `mechanics` block into a list of *bound engines* — an
`(engine, cfg, state_path)` triple — once, at load, and hangs it off `ctx`. Every later
call site iterates bound engines rather than reaching into `ctx["story"]["mechanics"]`
directly.

That single change is what makes E-5 structural: the 25 `.get("mechanics", ...)`
lookups scattered through `story_engine.py`, `subplot_manager.py`, `app.py` and
`state_store.py` today collapse into one resolution point, and a slot with no entry
produces no bound engine, which produces nothing at all.

An unknown `engine` name is a **load-time failure**, not a silent skip. A template that
names a mechanic the runtime does not have is broken content, and the player should be told
at story-list time rather than discovering it as a missing rule forty turns in.

### 3.3 What the template no longer does

An engine owning a mechanic means the template stops carrying prompt sentences for it.
`axis.description` ("trust and warmth") exists today so the engine can interpolate it into
a request for a number. Under E-3 no such request exists, so the field becomes what it
should always have been: display labels and tier names, consumed by `prompt_sections` and
`render`, never by an arithmetic instruction.

The general rule for porting a v2 `mechanics` block: **any field whose only consumer is an
f-string inside a schema instruction is a smell.** It is configuration for a decision the
engine should be making.

---

## 4. The turn pipeline

```
  player action
      │
      ├─[engine]  pre-action gate check (§7.4)
      │             blocked → render refusal, no observation pass, no state change
      │
      ├─[engine]  prompt assembly: each bound engine contributes prompt_sections()
      │
      ├─[LLM]     narration + OPTIONS                          ← loose paint
      │
      ├─[LLM]     observation pass(es): typed events only (§5)  ← the sensor
      │
      ├─[engine]  resolve(): every bound engine, in order, pure ← tight rails
      │
      ├─[engine]  apply effects, append to the event log (§8.2)
      │
      ├─[engine]  render(): deterministic substitution into the stored turn
      │
      └─          save
```

Unchanged from v2: two LLM calls on a typical turn, narration first, state second, and
`call_llm` still runs before any save write so no turn is ever half-persisted
(`LLMUnavailableError` stays the single stable failure type).

New: the pre-action gate check. It costs nothing — no LLM call — and it is the first point
in the system's history where the world can tell the player *no* and mean it.

---

## 5. Observation budget

This section answers the open question from the design discussion: **how to keep the
observation pass from re-growing the prompt-bloat problem in a new shape.** Four mechanisms,
applied in order of cheapness.

### 5.1 Delete before you move

The largest saving is not architectural. Many v2 observation fields exist only because the
engine had no other way to know a thing, and an engine that owns the mechanic does know it.

| v2 field | v3 |
|---|---|
| `items_lost` (exact string match against inventory) | inventory engine resolves a `consume` event against its own records |
| `scene_update.location` (validated against `VALID LOCATION IDS`) | movement/gate engine owns position; the model reports *intent to move*, not the resulting id |
| `stat_changes` | priced from events by the resource engine |
| `relationship_changes` | priced from a social event's register |
| `threat_present` | pacing engine already derives arming from beats |

Porting an engine is a net deletion from the observation schema more often than an addition.
**Measure the field count before and after each port; a port that grows it needs a reason.**

### 5.2 Cadence — stagger in time

An engine returns `None` from `observations()` on any turn it has nothing to ask. The
decision is made **by code, from state**, not by a fixed round-robin:

- `gate` asks nothing, ever. It is pure adjudication.
- `revelation` asks only about unrevealed entries whose own preconditions are already met.
  A clue chain of twelve entries contributes one line, not twelve.
- `failure` asks only about conditions that are currently reachable, and nothing once
  `endgame.requested` is set (v2 already does this; v3 makes it the engine's own decision).
- `check` asks only on a turn where the narration contained an attempt it gated.

**Constraint.** An engine that can skip turns must phrase its question over a *window*
("since the last time you were asked") and must tolerate the window being longer than one
turn, because it will be. An engine whose question is only meaningful about the immediately
preceding turn may not declare a cadence. This is a real restriction and it should be
stated in each engine's entry in §7.

### 5.3 Sharding — split in space

E-3 guarantees observations are mutually independent. Therefore shards can run
**concurrently**, and wall-clock cost is `max(shards)` rather than `sum(shards)`.

An engine declares `observation_group`. The default is `"core"` and most engines should
stay there — sharding is opt-in, for when a group's field count exceeds budget. Each shard
receives the narration text, the player action, and **only its own group's context**.

Marginal cost of a second shard is the duplicated narration — a 470–500-word scene
(`example`'s `narration.scene_length`) is roughly 700 input tokens. That is cheap next to
sending every engine's context to one oversized call.

Three implementation constraints, all from existing invariants:

- **Timeouts.** `CLAUDE.md` requires provider timeouts sized so primary-plus-fallback fits
  inside gunicorn's `--timeout`. Concurrent shards *preserve* this: the budget is still one
  call's worth of wall clock, where sequential shards would have multiplied it. Sharding
  must therefore be concurrent, not merely split.
- **Status beacons.** `_status_ctx` is `threading.local`, and its comment states plainly
  that `_timed()` never crosses threads. Sharding would cross threads. Resolution: the
  **parent fan-out keeps the `state_update` beacon and its `DEFAULT_STEP_ESTIMATE_SECONDS`
  entry**; per-shard calls go through `_timed` on worker threads, where `_status_ctx` is
  unset and the beacon write already no-ops on its own `if ctx is not None` guard. Shard
  labels get `STATUS_LABELS` entries with no estimate entry — the same shape as the existing
  `steering_seed_generation` and `relationship_promotion` labels, which are in one dict and
  deliberately not the other. `test_status_labels.py`'s bidirectional mirror assertion holds
  without weakening.
- **The Gemini fail-safe** is per-request and therefore per-shard. A shard failing over does
  not drag its siblings; a shard raising `LLMUnavailableError` after its own fallback fails
  the whole turn, before any save write, exactly as today.

### 5.4 Field budget

- **One observation field per engine per turn.** An engine needing two is two engines, or
  one field with a richer type.
- A shard carrying more than **six** fields must split (§5.3).
- `prompt_sections` output is budgeted per engine in the registry, in characters, and the
  budget is asserted in tests. The disk record may grow forever; what reaches a prompt may
  not, and engine v2 adds a new way for that to go wrong.

---

## 6. Effects and resolution

### 6.1 Effects are data

`resolve()` returns a list of typed effects rather than mutating `ctx`. The engine core
applies them. This keeps `resolve()` pure (E-4), makes conflicts detectable rather than
order-dependent-by-accident, and makes the whole thing trivially testable:

```python
assert engine.resolve(cfg, ctx, [{"type": "travel", "distance": "long"}], []) == [
    Effect("resource.adjust", axis="fuel", delta=-3, reason="travel.long"),
]
```

Every effect carries a `reason`. That string is what makes a save auditable after the fact —
"why is my fuel 12" has an answer that is not "the model said so."

### 6.2 Order is declared, not incidental

Engines resolve in `resolve_order`. The ordering that matters is already visible in v2's
apply block, which is carefully sequenced by hand and comments its own reasoning: gains
before losses so a gain cashed in on the same turn resolves; failure conditions applied last
so a failing turn's subplot progress and items still land first. v3 makes that sequence
declared data instead of the physical order of statements in a 500-line function.

### 6.3 Conflicts

Two engines emitting contradictory effects on the same target is a **bug in the story's
configuration**, and should be loud in tests and last-writer-wins in production. Do not
build a conflict resolution system. A story that needs one is over-configured.

### 6.4 The judgement escape hatch

Accepted from the design discussion, with constraints. An engine may declare
`judgement: true` and call Tier C from `resolve()` for a **boolean against an authored
predicate**. Conditions, all mandatory:

1. **Gated by code first.** The engine must compute, from state, that the question is
   currently live. A predicate about a revelation already revealed is never asked.
2. **Boolean only.** A judgement returns true or false. It never returns a number, a
   delta, or a choice among options — those are E-3 observations or engine arithmetic.
3. **Logged verbatim.** The predicate text and the answer both go in the event log. An
   unexplainable state change is exactly what this architecture exists to eliminate; a
   judgement is allowed to be fallible but never allowed to be invisible.
4. **Budgeted.** At most one judgement call per turn across all engines. If two are live,
   the lower `resolve_order` wins and the other waits a turn.
5. **Monkeypatchable**, per `CLAUDE.md`'s standing rule for new LLM-calling functions, so
   the offline suite covers judgement-using engines without an API key.

`resolve()` is otherwise pure, and E-4 should be read as "pure except for a declared,
logged, budgeted judgement," which is a narrower hole than it sounds: an engine without
`judgement: true` cannot make the call at all.

Act advancement resembles a judgement and is deliberately not one — see §2.1 for why it
cannot be folded in here without breaking rule 2.

---

## 7. The engine catalogue

Ordered by ratio of new capability to implementation cost. Each entry records what it owns,
what it asks the model, and whether it may declare a cadence (§5.2).

### 7.1 `resource` — `bounded_counter`

**Owns.** Named numeric axes with bounds, event-priced deltas, and optional per-turn drift.
Absorbs v2's `mechanics.stats` wholesale, including `floor`/`ceiling`, `visible`, and
`readout` (which becomes this engine's `render()` and needs no other home).

**Observes.** Nothing directly. It prices events that other engines' vocabularies produce,
plus a shared `effort` event for exertion the story does not otherwise model.

**Cadence.** No — per-turn drift must tick every turn, and drift is code, not an observation.

**Note.** SCHEMA_V2_SPEC §3.6 deliberately declined to give clocks and deadlines their own
module, on the grounds that a countdown is just a stat the state pass decrements. That
reasoning was right about *shape* and wrong about *authority*: "the state pass decrements it"
means the model remembering to decrement it. `per_turn` drift makes a deadline arrive whether
or not the model was paying attention, which is the entire point of a deadline. The decision
against a separate `mechanics.clock` module still stands; a clock remains an axis.

### 7.2 `relationship` — `scored_axis`

**Owns.** Per-character scores, the scale (no longer hardcoded `±100`), tier thresholds and
their labels, per-character-per-window delta caps, and eviction — which keeps v2's rule
exactly: drop whatever sits closest to neutral, never the strongest bonds, never an authored
character.

**Observes.** One field: social interactions as `{target, register, reciprocated}`, where
`register` is an authored vocabulary (`confided_secret`, `public_slight`, `kept_faith`).
The engine prices the register; the model never picks a number.

**Cadence.** No.

**Unlocks.** Tiers that actually gate behaviour. "At `trusted` she will lie to the constable
for you" becomes a fact the narration prompt is handed, rather than a hope.

### 7.3 `inventory` — `tagged_items`

**Owns.** Items as records — id, label, tags, uses, consumable — rather than free strings.
Acquisition, consumption, capacity.

**Observes.** Acquisition and use intent, by tag or label.

**Cadence.** No.

**Unlocks.** "Do I still have the key" stops being a prose question. v2 matches `items_lost`
against inventory by exact string equality, which is fragile in precisely the way
`relationships[name]["npc_id"]` was found to be fragile, and for the same reason.

### 7.4 `gate` — `precondition`

**Owns.** Preconditions on actions, locations and topics: required items, tiers, flags, stat
thresholds, revelations. Evaluated **before** the narration call (§4).

**Observes.** Nothing. Pure adjudication.

**Cadence.** N/A.

**Why this one first among the new capabilities.** Every other engine raises the fidelity of
a mechanic that already exists. `gate` changes what the game *is*: it is the first time the
world can refuse the player. It is also the cleanest test of the mantra — a locked door is
entirely rails, and the prose of being turned away is entirely paint. The template supplies
`refusal_hint` (tone, a sentence of intent), never `refusal_text`; the engine decides *that*
the player is refused and the model writes how it feels. P-3 is intact.

### 7.5 `revelation` — `triggered_reveal`

**Owns.** v2's `mechanics.revelations`, with triggers upgraded from prose vibes to
predicates over engine state, plus optional ordering constraints so a clue chain cannot fire
out of sequence.

**Observes.** Only live entries (§5.2), and only those whose structural preconditions are
already satisfied. The residue that genuinely cannot be expressed as a predicate — "catches a
second contradiction" — is the canonical §6.4 judgement.

**Cadence.** Yes. Its question is naturally windowed.

### 7.6 `failure` — `triggered_ending`

**Owns.** v2's `mechanics.failure_conditions`, unchanged in effect: firing one sets
`endgame.requested` with `final_arc` built from `ending_prompt`, routing into the existing
endgame machinery rather than a new code path. Triggers become predicates, same as §7.5.

**Cadence.** Yes, and it asks nothing once the story is already ending.

### 7.7 `check` — `resolved_attempt`

**Owns.** Attempt resolution with declared odds and stat-derived modifiers. Outcome bands
(`fail` / `partial` / `succeed`) are handed to narration as a fact to describe.

**Observes.** One field: that an attempt of a declared kind was made, and against what.

**Cadence.** Yes — nothing to ask on a turn with no gated attempt.

**Caveat.** This is a **new genre axis**, not a port. Every other engine here has a v2
ancestor whose behaviour constrains the design. `check` does not, which makes it the easiest
one to over-build into a general-purpose RPG ruleset that no author wants to write. Ship it
last, minimal, and only once a story actually needs it.

### 7.8 `progression` and `pacing_loop`

Ported nearly as-is. `pacing_loop` is already the target shape (§1.3) and mostly needs
relocating behind the registry rather than redesigning. `progression`'s leverage ledger is
close: the model already emits labels and kinds rather than arithmetic, and the engine
already owns numbering, spend-matching and eviction. Both are good first ports precisely
because they should barely change — if the registry cannot host them without distorting
them, the registry is wrong.

### 7.9 Deliberately not an engine

`check_and_advance_act` stays outside the registry, per §2.1. It is listed here so that a
later reader looking for "the act engine" finds the reasoning instead of concluding it was
an oversight. The `gate` engine supplies the predicate evaluator that §2.2's
authored `requires` runs on, which does not make act advancement an engine — supplying an
evaluator is not owning the verdict. `check_subplot_status`'s progress arithmetic *does* come inside, as part of
phase 4.

---

## 8. Schema changes

### 8.1 Template (`mechanics`, v3)

```jsonc
"mechanics": {
  "resource": {
    "engine": "bounded_counter",
    "axes": {
      "fuel":      { "start": 40, "floor": 0, "ceiling": 60,
                     "costs": { "travel.long": 3, "travel.short": 1, "effort.hard": 2 } },
      "days_left": { "start": 7,  "floor": 0, "per_turn": -0.25 }
    },
    "visible": true,
    "readout": { "token": "[[STATS]]", "labels": { "fuel": "FUEL", "days_left": "DAYS" } }
  },

  "relationship": {
    "engine": "scored_axis",
    "scale": { "min": -50, "max": 50 },
    "axis":  { "negative": "wary", "positive": "confiding",
               "description": "trust and warmth" },
    "registers": { "confided_secret": 8, "kept_faith": 5, "public_slight": -12 },
    "cap_per_window": { "delta": 12, "turns": 3 },
    "tiers": [ { "at": 25, "label": "trusted" }, { "at": -25, "label": "closed off" } ],
    "limit": 20
  },

  "gate": {
    "engine": "precondition",
    "gates": [
      { "id": "lighthouse", "target": "loc_lighthouse",
        "requires": { "item_tag": "lighthouse_key" },
        "refusal_hint": "The door doesn't argue. It just doesn't move." }
    ]
  }
}
```

Every key under a slot except `engine` is that engine's own configuration, validated by that
engine rather than by a central schema. Adding an engine adds no code outside its module.

`refusal_hint` over `refusal_text` is the pattern to repeat everywhere: **the engine decides,
the template sets tone, the model writes the sentence.**

### 8.2 Save: the event log

The save gains one top-level key:

```jsonc
"events": [
  { "turn": 12, "type": "travel", "distance": "long", "terrain": "open_water" },
  { "turn": 12, "type": "social", "target": "Mrs. Abbott",
    "register": "confided_secret", "reciprocated": true },
  { "turn": 12, "type": "judgement", "engine": "revelation", "id": "rev_002",
    "predicate": "player catches a second contradiction about yesterday", "answer": false }
]
```

This is the raw observation stream, before resolution. It is the audit trail that makes
§6.1's `reason` strings checkable, and it enables **replay**: change a price list, re-resolve
the log from turn zero, and get a different save from the same story. That is a balancing
tool and a debugging tool, and it exists only because `resolve()` is pure (E-4).

**It is also unbounded, and `CLAUDE.md` is explicit that the disk record may grow forever but
what reaches a prompt must not.** The event log reaches no prompt — engines read *state*, not
history. Replay reads the log; prompts never do. Any future engine wanting to consult its own
history needs a bounded derived counter in its own state, not a scan.

Per-engine runtime state lives at `state.mechanics.<slot>`, initialised by `init_state`, and
is that engine's alone. No engine reads another's state; cross-engine dependencies go through
effects.

### 8.3 Migration: none

**Decision:** there is no migration from v2 saves. The only playtester is the author, and a
fresh start is acceptable. `backend/migrate_v1.py` is not the model to follow here.

Consequences, accepted deliberately:

- `schema_version: 3` templates and v2 saves are incompatible, and `load_state` says so
  plainly rather than attempting a partial upgrade.
- The event log starts empty, so **replay is only valid from a save created under v3**. A v3
  save with no log is a v3 save that cannot be replayed, and engines must not assume otherwise.
- The three shipped stories need their `mechanics` blocks rewritten. That is content work,
  and it is the first real test of the authoring burden accepted in §11.

---

## 9. Testing

The existing rules hold and gain a new, much easier case.

**Engine unit tests need no stubs.** `resolve()` is pure, so a mechanic's rules are testable
the way ordinary Python is testable — no `_llm_stubs.py`, no monkeypatched `call_llm`, no key.
This is the first part of the system with that property and it should be exploited hard: the
price lists, the tier thresholds, the eviction rules and the gate predicates are all
table-driven tests.

**Conformance fixtures get rewritten first, not last.** `test/fixtures/` currently holds three
genre templates exercising different subsets of the optional modules, asserted **in both
directions** — an absent module leaks no marker into either prompt, *and* an authored module
actually reaches them — with the absent-module lists written out in the test rather than
derived from the fixtures, so deleting a module from a fixture fails loudly. All of that
survives verbatim; only the module names change. Three fixtures selecting three disjoint
engine sets is the cheapest available proof that the registry is generic, and it should exist
**before** any real story is ported.

**New assertions engine v2 requires:**

- An unregistered engine name fails at load, loudly (§3.2).
- Observation field count per shard stays within §5.4's budget.
- `prompt_sections` output stays within each engine's declared character budget.
- `STATUS_LABELS` / `DEFAULT_STEP_ESTIMATE_SECONDS` mirroring survives sharding.
  `test_status_labels.py` already asserts the mirror both ways and must be taught the
  beacon-less shard labels of §5.3, following the existing `steering_seed_generation`
  precedent rather than relaxing the assertion.
- Replay determinism: resolving the same event log twice yields identical state.

**`test_app_routes.py` is unchanged in shape.** Turn-taking stays asynchronous — kickoff
returns `202`, the client polls, then fetches the result — so any new assertion there still
needs `wait_for_idle(...)` before reading save state, exactly as now.

---

## 10. Implementation phases

Summary only. **`docs/ENGINE_V2_PHASES.md` is the working plan** — per-phase work items,
acceptance gates, risks, and the story-content interleave.

| Phase | Work | Gate |
|---|---|---|
| 0 | Baseline measurement: observation field count, prompt sizes, per-call p50s | Numbers recorded; §5.1 and §12.4 have something to compare against |
| 1 | Registry, `Effect`, resolve ordering, event log plumbing. No mechanic ported | Full suite green; assembled prompts byte-identical for all three stories and all three fixtures |
| 2 | Port `resource` (v2's `stats`). **Schema v3 cutover** | Stat tests pass unmodified; readout still deterministic. **Stop-gate — see below** |
| 3 | Rewrite the three conformance fixtures against the registry | Both directions, three disjoint engine sets, absent-lists still written out in the test |
| 4 | Port `relationship`, `inventory`, `revelation`, `failure`, subplot progress | Per-engine tests; absent-engine tests; field count not grown against phase 0 |
| 5 | Relocate `pacing_loop` and `progression`, deliberately unchanged | `test_pacing_loop.py` passes unmodified |
| 6 | `gate`: pre-action check, refusal path, and §2.2's authored act `requires` | Refusal path test; predicate latching test; no reachable deadlock |
| 7 | Observation sharding (§5.3) — only if phase 4's measurement demands it | Concurrency; `test_status_labels.py` mirror intact |
| 8 | `check` (§7.7) — minimal, on demand only | A story actually wants it |

**Phase 2 is the stop-gate.** `resource` is the best-covered, least-surprising mechanic in
the system. If the registry cannot host it without distorting it, the registry is wrong and
the right move is to stop rather than port a second engine onto a bad seam.

---

## 11. Accepted costs

Recorded because they were raised as objections during design and accepted with eyes open.

**Authoring burden rises sharply, and that is the trade.** A price list is far more work than
`"description": "trust and warmth"`. Accepted: it is an up-front, one-time cost per story,
and templates can be drafted with LLM assistance — the template is data, and the model is
good at producing data it will not later be trusted to adjudicate. Two mitigations remain
obligatory regardless: engines ship sane defaults, and the **minimal template must still run
with an absent `mechanics` block entirely** (P-4 is not negotiable, and this architecture
makes it easier to honour, not harder).

**Fuzzy-to-typed translation loses something real.** Not every trigger decomposes into an
event vocabulary. §6.4 is the declared, bounded hole; the loss is accepted in exchange for
every *other* trigger becoming deterministic.

**The event vocabulary is a coupling surface.** Engines share one JSON schema in one prompt,
so an engine's vocabulary is not private in practice even though its state is. §5.4's
one-field-per-engine rule is the containment, and it should be treated as a hard limit rather
than a guideline.

**`check` risks becoming a ruleset nobody asked for.** Hence phase 8: minimal, on demand.

---

## 12. Open questions

1. **Does `world.locations` become a graph?** `gate` implies adjacency and reachability, and
   a location table with no edges can only gate the destination, never the route.
2. **Should a refused action consume a turn?** A refusal that costs nothing invites retry-spam;
   a refusal that costs a turn may feel punitive. Probably per-gate configuration, which is
   one more field in the authoring burden.
3. **Does the player ever see the rules?** E-7 makes the mechanics honest enough to show. A
   rules readout is newly *possible*; whether it is desirable is a creative decision, which by
   P-3 means it belongs in the template.
4. **The sharding threshold.** §5.4 proposes six fields per shard as the split point. That
   number is a guess and should be set from a real measurement once phase 4 lands.
5. **Should a generated act ever carry a `requires`?** Deferred, not rejected — §2.2 records
   the design that would work and the reason not to build it yet. Revisit only if playtesting
   shows acts advancing too early, and only after `gate` has landed.
