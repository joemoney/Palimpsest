# Story Schema v2 — Specification

**Status:** Approved for implementation. Supersedes the implicit v1 schema documented
across `README.md`, `CLAUDE.md`, and `docs/Narrative_Engine_Spec.md`. All open design
questions resolved — see §10.

**Motivation.** Two reviews found the same underlying problem from different angles.
The prompt-coverage audit found a third of the schema never reaching a prompt, plus two
fields read-but-never-written. The genre audit found creative decisions — prose
aesthetic, scene length, option count, relationship semantics, the protagonist's default
name — living as engine constants rather than story content. Both trace to one root
cause: **v1 has no boundary between what the author writes and what the engine tracks.**
The template is cloned wholesale into the save, so authored content and runtime state
are the same shape, mutated by the same code, and impossible to tell apart.

v2 draws that boundary explicitly and rebuilds the schema around it.

---

## 1. Design principles

**P-1 — Authored content and runtime state are separate stores.**
A story's template is immutable and never mutated at runtime. The save holds only what
changed. This is the load-bearing principle; most of the rest follows from it.

**P-2 — Every genre-specific mechanic is an optional module.**
Absent key means the feature does not exist: no state, no prompt section, no schema
field in the state-update JSON, no UI affordance. Not an empty header, not a zeroed
counter. `character_creation` already works this way and is the reference implementation.

**P-3 — No engine constant may encode a creative decision.**
If a novelist would have an opinion about it, it belongs in the template. Prose style,
scene length, POV, option count, the protagonist's fallback name, stat bounds,
relationship semantics.

**P-4 — Read paths never assume optional structure.**
`.get()` / `setdefault()` throughout. A minimal template — `meta`, `world.rules`,
`plot.main_thread`, `plot.opening_scene` — must run.

**P-5 — Write-only state must be justified in writing.**
Anything persisted but never read into a prompt must be explicitly listed as
intentional (audit trail, UI, debug) in `docs/Narrative_Engine_Spec.md`. Anything not on
that list is a bug.

**P-6 — Three-genre conformance.**
The schema is generic when a regency romance, a single-room courtroom drama, and a
survival horror can each be authored without touching Python. Enforced by fixtures, see
§7.

---

## 2. The story/state split

### 2.1 The problem it solves

In v1, `state_store.load_state()` clones `template.json` on first play and the save is
thereafter authoritative for everything. Consequences:

- **Authored content is frozen at save-creation.** Fix a typo in `world.rules`, adjust a
  faction's goals, rewrite an act description — no existing save ever sees it. For a
  project in active development this is the single most annoying property of the current
  design.
- **"Authored vs. generated" has to be faked.** CR-06 needed an `authored: true` flag on
  character entries purely so eviction wouldn't delete hand-written NPCs. That flag is
  a workaround for a missing structural boundary. Same question recurs for subplots
  (seeded vs. LLM-invented) and acts (Act 1 vs. everything after).
- **Saves are bloated.** Every save carries a full copy of every location description,
  faction, opening scene, and character-creation option — none of which ever changes.
- **`current_scene` is authored seed that becomes runtime state** and, as CR-01 found,
  nothing ever writes it. The category confusion is exactly why it was missed.

### 2.2 The split

Two documents, loaded together, exposed as a plain two-key dict — see §2.2.1 for the
shape decision.

```
stories/<slug>/template.json     immutable authored content, git-tracked
data/saves/<user>/<slug>.json    runtime deltas only
```

`state_store.load_state()` returns a working object exposing both:

```python
ctx = state_store.load_state(user_id, story_slug)
ctx["story"]    # authored, FrozenDict — raises on write
ctx["state"]    # runtime, plain dict, mutable
```

`save_state()` persists `ctx["state"]` only. The save records `story_slug` and
`story_version` (see §3.1) so a template revision can be detected.

#### 2.2.1 Shape decision: plain dicts + a frozen wrapper on `story`

Three shapes were considered — resolved, not deferred:

| Option | Shape | Verdict |
|---|---|---|
| A | Plain nested dicts, `ctx["story"]["world"]["rules"]` | **Chosen** |
| B | Attribute access via `__getattr__` wrapper, `ctx.story.world.rules` | Rejected |
| C | Schema-generated dataclasses | Rejected |

**C is out on cost.** Real autocomplete and static type checking would catch a
wrong-half access at edit time rather than runtime, which is the actual failure this
decision is trying to prevent. But every schema addition would mean touching a class
definition, which cuts directly against P-3 ("adding a story is a content change, not a
code change") and against the fact that `mechanics` modules are meant to vary freely
per template.

**B is out because it buys readability without buying the safety.** `ctx.story.world.rules`
reads better than the dict form, but `__getattr__` doesn't catch a typo any earlier than a
dict subscript does, and it doesn't stop code from writing into `ctx.story` by accident —
the one mistake that actually matters here is reaching into the wrong half of the split,
and only static typing (Option C) catches that before runtime. B pays a real complexity
cost — a wrapper layer, serialization marshalling both ways, an extra stack frame — for
an aesthetic gain only.

**A is chosen, with one addition: `ctx["story"]` is wrapped in a `FrozenDict`** — a thin
`dict` subclass raising on `__setitem__`, `__delitem__`, `update`, etc. This is the part
that's actually load-bearing for P-1: it turns "authored content is immutable" from a
convention into an enforced property, without the `__getattr__` layer or the
serialization cost that B would have added. `ctx["state"]` stays a plain mutable dict.

Practical consequences:

- Migration diff at every call site is mechanical: prepend `["story"]` or `["state"]`,
  no restructuring of the access chain beneath it.
- `json.load` / `json.dump` work directly on both halves; `FrozenDict` is applied after
  load, stripped (or just read as a plain dict — subclasses serialize fine) before dump.
- **Test requirement:** `save_state()` asserts nothing under `ctx["story"]` was mutated
  during the request, as a belt-and-braces check independent of the `FrozenDict` raising
  at the point of mutation — catches any path that swaps in a fresh dict rather than
  writing through the wrapper.
- A wrong-half read (`ctx["state"]["world"]["rules"]` when it should be
  `ctx["story"]["world"]["rules"]`) still only fails at runtime, as either a `KeyError`
  or a silent read of stale/absent data. Accepted cost of not choosing C.

### 2.3 Template revisions reaching live saves

Because authored content is re-read on every load, template edits reach existing saves
for free. The only hazard is a template edit that invalidates runtime references — a
deleted location that `state.scene.location` points at, a removed subplot id present in
`state.plot.subplots`.

Handle by reconciliation at load, never by rejection:

| Dangling reference | Behaviour |
|---|---|
| `scene.location` not in `story.world.locations` | Keep the id, render it raw, log once |
| Runtime subplot id absent from template | It's a generated subplot; expected, no action |
| Seeded subplot removed from template | Leave the runtime copy in place; it's in-flight |
| `revelation` id absent from template | Drop the runtime reveal record silently |
| Stat name absent from `character_creation` | Keep the value; a story may have removed the step |

Never fail a load over a reconciliation mismatch. A player mid-story losing their save to
an author's typo is far worse than a slightly stale reference.

### 2.4 Migration cost, honestly

Every call site currently doing `state["plot"]["subplots"]` becomes either
`ctx["story"]["plot"]["subplots"]` or `ctx["state"]["plot"]["subplots"]`, and getting that
wrong is a silent bug rather than a crash — see §2.2.1 on why that residual risk was
accepted rather than eliminated with static typing. Affected: all of `story_engine.py`,
`app.py`, `plot_manager.py`, `subplot_manager.py`, and every file in `test/`.

**Lighter fallback if this proves too large:** keep a single merged dict, but add
`story_version` and re-merge the authored sections from the template on every load,
overwriting them. This recovers the template-updates-reach-saves property — the most
valuable single benefit — without the call-site churn. It does not fix the
authored-vs-generated distinction, so CR-06's `authored: true` flag stays. Take this
path only if the full split stalls.

---

## 3. Template schema (authored, immutable)

### 3.1 Top level

```json
{
  "schema_version": 2,
  "story_version": "2026-09-03.1",
  "meta": { },
  "narration": { },
  "world": { },
  "protagonist": { },
  "mechanics": { },
  "character_creation": [ ],
  "plot": { }
}
```

`story_version` is an opaque string bumped by the author on any meaningful edit. Used for
logging and for deciding whether to run reconciliation; never parsed for ordering.

### 3.2 `meta` — identity and out-of-character constraints

```json
"meta": {
  "title": "The Last Ferry to Millbrook",
  "genre": "Cozy small-town mystery with a touch of the uncanny",
  "tone": "Warm on the surface, with a quiet accumulating wrongness underneath",
  "synopsis": "...",
  "content_rules": ["PG-13", "no explicit sexual content"]
}
```

Unchanged from v1 except that `pov` moves to `narration`. `synopsis` remains
story-picker copy and is not prompted — listed as intentional under P-5.

`content_rules` are out-of-character safety and rating boundaries.
`world.rules` are in-fiction physics. Keeping them separate is deliberate; document it.

### 3.3 `narration` — the prose contract *(new)*

This block is where the Tier 1 finding lands: the aesthetic prescription currently
hardcoded in `build_system_prompt` becomes story content.

```json
"narration": {
  "pov": "second-person",
  "option_pov": "first-person",
  "option_count": 3,
  "scene_length": { "min": 470, "max": 500 },
  "style": [
    "Let atmosphere accumulate through small concrete details that don't quite add up.",
    "Dialogue should be warm and unhurried; the wrongness lives in what isn't said.",
    "..."
  ]
}
```

| Field | Default if absent | Notes |
|---|---|---|
| `pov` | `"second-person"` | Stated explicitly in the prompt header. Fixes CR-14. |
| `option_pov` | value of `pov` | v1 hardcodes first-person options against second-person narration. Now derived, and overridable. |
| `option_count` | `3` | Must thread through both the footer text and `parse_narration_and_options`'s minimum-count fallback. |
| `scene_length` | `{470, 500}` | Replaces `SCENE_WORD_MIN` / `SCENE_WORD_MAX`. |
| `style` | `[]` → no style block at all | Verbatim bullets. Absent means the model works from `genre` + `tone` alone, which is a sane default. |

**The engine retains only universal instructions:** stay within world/tone/rules, the
three emphasis markers (`**`/`*`/`__`) and the prohibition on other markdown, and the
`OPTIONS:` block format. Everything about *how the prose should feel* moves out.

Migration note: v1's current style bullets are `new_babel`'s voice. Move them there
verbatim; write a distinct set for `example`. The existing conflict — engine says never
two consecutive descriptive sentences, `example`'s own `world.rules` say strangeness
accumulates through detail — resolves itself.

### 3.4 `world`

```json
"world": {
  "setting_summary": "...",
  "rules": ["..."],
  "locations": { },
  "factions": { },
  "characters": { }
}
```

- `setting_summary` — **now prompted** (CR-04). The densest authored context in the file.
- `rules` — unchanged. Required.
- `locations` — **optional module.** `{id: {name, description, connected_to[]}}`. Absent
  or empty means the story has no spatial model: no `HERE:` / `ADJACENT:` prompt block,
  and `scene.location` is treated as free text. Required for a courtroom drama, a
  chamber piece, or an epistolary story to work at all.
- `factions` — **optional module.** Unchanged shape, now prompted.
- `characters` — **authored roster.** `{name: {name, description, ...}}`, keyed on
  canonical display name so the roster key, the `relationships` key, and the name the
  model sees are one string. Absorbs CR-06; the `authored: true` flag is no longer
  needed because authored characters live in the template and discovered ones live in
  the save.

### 3.5 `protagonist` *(renamed from `player`)*

Authored seed only. Runtime protagonist state lives in the save.

```json
"protagonist": {
  "default_name": "Traveller",
  "traits": ["observant", "slow to trust"],
  "starting_inventory": ["a letter, twice-folded"]
}
```

`default_name` replaces the hardcoded `"Subject Zero"` in `apply_opening_name`.

The `player.origin` wrapper is deleted — it existed solely to hold `memory_fragments`,
which moves to `mechanics.revelations` (§3.6).

### 3.6 `mechanics` — optional modules *(new)*

Every key here is optional. Absent means the mechanic does not exist: no state, no prompt
section, no field in the state-update schema, no UI control.

```json
"mechanics": {
  "stats": { "floor": 0, "ceiling": null },
  "relationships": {
    "axis": {
      "negative": "hostile",
      "positive": "devoted",
      "description": "trust and warmth"
    },
    "limit": 20
  },
  "revelations": [
    { "id": "rev_001", "trigger": "player catches a second contradiction about yesterday",
      "content": "The innkeeper's guest book has your name in it, dated last week." }
  ],
  "tracked_entity": {
    "name": "The Architect",
    "description": "...",
    "pacing_note": "Appears rarely and never explains itself."
  },
  "failure_conditions": [
    { "id": "fail_001", "trigger": "the player boards the ferry without learning what the lighthouse is",
      "ending_prompt": "Close on departure — safe, intact, and permanently unsatisfied." }
  ]
}
```

**`stats`** — replaces the global `STAT_FLOOR = 0`. Per-story bounds; `null` for
unbounded. Unblocks negative scales (±3 modifiers, debt, temperature, a sanity meter
that can go below zero). Stat *names* continue to come only from
`character_creation` starting stats — that constraint is correct and stays.

**Time and deadlines are documented as an intended use of `stats`, not a separate
mechanic.** A heist countdown, a disaster timer, a shift schedule, or a season-bound
romance is authored as an ordinary starting stat — `"days_remaining": 7` — with no
engine changes required. This is deliberately *not* given its own `mechanics.clock`
module: a clock is a number the state-update pass increments or decrements each turn
exactly like any other stat, and a second parallel system for the same behaviour would
only fragment where "numbers that change over time" live. Record this pattern in
`docs/Narrative_Engine_Spec.md` alongside `character_creation`'s worked example, so an
author reaches for a countdown stat rather than inventing a new field.

**`relationships`** — replaces the hardcoded "−100 hostile to +100 devoted" and the
"trust/warmth built" instruction. Pole labels are interpolated into both prompts, so
horror can track *unaware → fixated*, a political thriller *indebted → owed*, without
pretending those are warmth. Absent block means the story tracks no relationships at
all; the field vanishes from the state-update schema.

**`revelations`** — replaces `player.origin.memory_fragments`. Same shape, generic name,
and no longer on a hard-subscripted required path (which currently raises `KeyError` on
a template that omits it — `stories/example` carries an empty one purely to avoid the
crash, for a mechanic its own README says it doesn't use). The pattern covers amnesia
fragments, clues, evidence, lore drops, prophecies, and backstory reveals. CR-03's
content-surfacing fix applies here at the new path.

**`tracked_entity`** — absorbs CR-07. Absent means no `entity_interaction` field in the
state-update schema and no encounters line in the act-check prompt. `pacing_note` is new
and gets fed to narration alongside the contact count, so the narrator can pace
appearances against prior contact.

**`failure_conditions`** — new capability, closing the genre gap in §5 of the genre
review. v1's only ending is the player typing "end story." Horror, survival, tragedy,
and most thrillers need a story that can end badly without being asked to. Evaluated in
the state-update pass alongside revelations — same authored-trigger shape, different
effect: firing one sets `endgame.requested` with `final_arc` built from `ending_prompt`,
routing into the existing endgame machinery rather than a new code path.

This does not violate `CLAUDE.md`'s "continuous, not finite" principle. That principle
is about not pre-setting an act count; it was never meant to imply the story can only
end by request.

### 3.7 `character_creation`

Unchanged. Already correct, already the reference pattern for P-2.

### 3.8 `plot`

```json
"plot": {
  "main_thread": {
    "title": "...",
    "description": "...",
    "acts": [ { "act_number": 1, "title": "...", "description": "...",
                "completion_signals": ["..."] } ]
  },
  "subplots": { },
  "pacing": { "nudge_frequency": 8, "max_parallel_subplots": 3 },
  "opening_scene": { }
}
```

Authored seed only — Act 1, the starting subplot pool, the fixed opening. Everything
generated at runtime (acts 2+, replacement subplots, progress, current act) lives in the
save.

`acts[].completed` and `optional` move to runtime. `is_primary_focus`, `can_pivot`,
`act_history`, `emergent_directions`, and `plot_notes` are addressed in §6.

**`completion_signals` are required on generated acts, not just Act 1.** In v1 only
Act 1 carries them, authored by hand; every act the state pass generates afterward gets
`"completion_signals": []`, and CR-09's pacing-nudge block silently disappears the moment
the story leaves Act 1. `check_and_advance_act`'s response schema must include
`completion_signals` as a required field alongside `title` and `description` whenever it
generates a new act, with the same specificity expected of the authored Act 1 signals —
not a restatement of the act description. A generated act saved with an empty list is a
validation failure of the state pass, equivalent to a missing `title`.

**`opening_scene`** gains an optional shape:

```json
"opening_scene": {
  "narration_before_name": "...",
  "narration_after_name": "...",
  "name_prompt": "what should I put you down as, in the guest book?"
}
```

or, for a story with a fixed protagonist:

```json
"opening_scene": { "narration": "..." }
```

When `narration` is present and the before/after pair is absent, no name is captured and
`protagonist.default_name` is used. Unblocks stories about an established character, a
historical figure, or one whose name is itself a later revelation.

---

## 4. Save schema (runtime, mutable)

```json
{
  "schema_version": 2,
  "story_slug": "example",
  "story_version": "2026-09-03.1",

  "protagonist": {
    "name": "Vesper Kade",
    "traits": [],
    "inventory": [],
    "stats": {},
    "creation_choices": {},
    "flags": { "active": {}, "meta": {}, "archive": {} }
  },

  "characters": {
    "Mrs. Abbott": { "relationship": 35, "first_seen_turn": 3, "description": "" }
  },

  "scene": {
    "location": "loc_inn",
    "summary": "...",
    "present": ["Mrs. Abbott"]
  },

  "plot": {
    "current_act": 2,
    "generated_acts": [ { "act_number": 2, "title": "...", "description": "..." } ],
    "act_completion": { "1": true },
    "act_history": [ ],
    "subplots": { },
    "completed_subplots": [ ],
    "revelations_revealed": { "rev_001": { "turn": 14 } },
    "entity_contact_count": 0,
    "endgame": { "requested": false, "requested_turn": null,
                 "final_arc": null, "concluded": false, "cause": null }
  },

  "pacing": {
    "turn_count": 22,
    "turns_since_nudge": 3,
    "subplots_completed_this_act": 1,
    "last_direction": "..."
  },

  "history": {
    "recent_turns": [ ],
    "compressed_summary": "...",
    "full_transcript": [ ]
  },

  "pending_regenerate": { }
}
```

Notable changes:

- **`scene` is top-level and runtime-only.** Resolves CR-01's category confusion at the
  root: it was never authored content that happens to persist, it's the most volatile
  thing in the system. `present` replaces the never-written `present_npcs`.
- **`characters` holds only the delta** — relationship score and discovery metadata.
  Authored description comes from the template at render time; a discovered character
  carries its own.
- **`plot.subplots`** holds runtime state for both seeded and generated subplots
  (progress, status, active). Title/description for a *seeded* subplot resolve from the
  template; a *generated* one carries its own, since there's nothing to resolve against.
- **`generated_acts` / `act_completion`** keep template acts immutable while tracking
  what's been added and finished.
- **`pacing` promoted out of `plot`** — `turn_count` is global session state, not a plot
  property.
- **`pending_regenerate` promoted out of `history`** — it's a save slot, not history.
- **`endgame.cause`** — `"player_request"` or a `failure_conditions` id, so the UI and
  the closing prompt can distinguish a requested wrap-up from a loss.
- **`revelations_revealed`** replaces mutating a `revealed` boolean inside authored
  content, which the split forbids. Carries the turn, which CR-03 needed anyway for
  recency ordering.

---

## 5. Prompt assembly

The current `build_system_prompt` is one long f-string with conditionals threaded
through it. With every mechanic optional, that becomes unmanageable. Restructure as a
declarative section list:

```python
SECTIONS = [
    (always,              _section_identity),      # title, genre, tone, pov, content rules
    (always,              _section_world_rules),
    (has_setting_summary, _section_setting),
    (has_factions,        _section_factions),
    (has_characters,      _section_roster),
    (always,              _section_main_thread),   # CR-05
    (always,              _section_story_so_far),
    (always,              _section_recent),
    (nudge_due,           _section_pacing_nudge),
    (always,              _section_scene),         # CR-02, + HERE/ADJACENT if locations
    (always,              _section_protagonist),
    (has_revealed,        _section_revelations),   # CR-03
    (has_tracked_entity,  _section_entity),
    (has_style,           _section_style),
    (always,              _section_footer),
]
```

Each builder returns a string or `None`; `None` contributes nothing, not an empty
header. Ordering rules:

- **Stable content first.** Identity, world rules, setting, factions, roster, main
  thread — all stable across many turns, forming a cacheable prefix. Given the
  input-heavy token profile (~8–12k in vs. ~650 out), this is where the cost savings
  are.
- **Volatile content last.** Scene, protagonist line, recent turns, nudge.
- **Reinforcement preserved.** The existing "stay strictly within the established world,
  tone, and rules above" line before the footer stays — see the Reinforcement section of
  `Narrative_Engine_Spec.md`; recency measurably helps constraint adherence on smaller
  models, which matters if a local fine-tune takes over narration.

The state-update prompt gets the same treatment: its schema field list is already built
conditionally for `stats`, so extend the same pattern to `relationships`,
`revelations`, `tracked_entity`, `failure_conditions`, and `scene_update`.

---

## 6. Fields removed

| v1 field | Disposition |
|---|---|
| `plot.alternate_threads` | **Removed** — CR-10, never read by any prompt |
| `main_thread.is_primary_focus`, `can_pivot` | **Removed** — only meaningful with alternate threads |
| `pacing.ready_for_main_plot_advancement` | **Removed** — never read *or* written |
| `player.origin` | **Removed** — wrapper for one key; contents → `mechanics.revelations` |
| `thread_steering.player_driven_goals` | **Kept, now prompted** — CR-11, fed to the nudge |
| `thread_steering.emerging_themes` | **Kept, now prompted** — CR-12, fed to the nudge |
| `thread_steering.pivot_history`, `last_pivot_turn` | **Kept** — audit trail, listed under P-5 |
| `main_thread.emergent_directions` | **Kept** — human staging area for manual promotion |
| `main_thread.plot_notes` | **Kept** — author's note, `plot_manager` display only |
| `subplots[].ties_to_main_plot` | **Kept, now prompted** — CR-13 |
| `subplots[].completion_threshold` | **Kept** — always 100 in practice, but a legitimate per-subplot dial |
| `meta.synopsis` | **Kept** — story-picker copy, intentionally unprompted |

`thread_steering` itself is retained but moves wholly to the save; nothing in it is
authored.

---

## 7. Genre conformance fixtures

Three minimal templates under `test/fixtures/`, each exercising a different subset of
optional modules. These are the executable form of P-6 — they exist to fail loudly when
someone reintroduces a genre assumption.

| Fixture | Modules used | Modules absent | What it proves |
|---|---|---|---|
| `regency.json` | relationships (axis: *disregard → devotion*), characters, revelations | locations, factions, stats, tracked_entity, failure_conditions, character_creation | Interiority-heavy `narration.style`; no map; no combat; relationship-driven |
| `courtroom.json` | characters, revelations (as testimony), failure_conditions | locations, factions, tracked_entity | Single setting, no spatial model at all; a real loss condition |
| `survival.json` | stats (floor `-10`), tracked_entity, failure_conditions, locations | relationships, characters, character_creation | Negative stat scale; entity axis isn't warmth; death is an ending |

Test assertions per fixture: the template loads; `build_system_prompt` produces no empty
headers for absent modules; the state-update schema omits the corresponding fields; one
stubbed turn applies cleanly. All against the existing `test/_llm_stubs.py` harness — no
live calls.

Add a fourth check across all fixtures: **no fixture may require a Python change.** If
adding one does, the schema isn't done.

---

## 8. Relationship to the existing CRD

`SCHEMA_COVERAGE_CRD.md` remains the correct plan for v1. Several items are absorbed
here; several are worth landing on v1 first regardless of whether v2 proceeds, since
they're cheap and fix live bugs.

| CR | Under v2 |
|---|---|
| CR-01 scene never written | **Absorbed** — `state.scene` is runtime-only by construction; the `scene_update` diff field still needs implementing |
| CR-02 raw location key | **Land on v1 first.** Trivial, fixes a live defect |
| CR-03 revelation content unprompted | **Absorbed**, at the new `mechanics.revelations` path |
| CR-04 world block | **Absorbed** into §3.4 + §5 |
| CR-05 main thread in prompt | **Land on v1 first.** ~50 tokens, pure gain |
| CR-06 character roster | **Absorbed** — the `authored: true` flag becomes structural |
| CR-07 hardcoded Architect | **Absorbed** into `mechanics.tracked_entity` |
| CR-08 subplot progress to state pass | **Land on v1 first.** Independent of the split |
| CR-09 completion_signals to nudge | **Absorbed** |
| CR-10 remove alternate threads | **Land on v1 first.** Pure deletion, shrinks the surface to migrate |
| CR-11/12/13 nudge additions | **Absorbed** |
| CR-14 pov | **Absorbed** into `narration.pov` |
| CR-15 dead pacing field | **Absorbed** (removed) |
| CR-16 entity count to narration | **Absorbed** into `tracked_entity` |
| CR-17 inconsistent act lookup | **Land on v1 first.** Latent bug either way |
| CR-18 document write-only fields | **Absorbed** into P-5 |

**Recommended v1 pre-work before starting v2:** CR-02, CR-05, CR-08, CR-10, CR-17. All
small, all independent of the split, and CR-10's deletion meaningfully reduces what has
to be migrated.

---

## 9. Implementation phases

| Phase | Work | Gate |
|---|---|---|
| 0 | v1 pre-work: CR-02, CR-05, CR-08, CR-10, CR-17 | Existing tests green |
| 1 | `state_store` split: dual-document load, `StoryContext`, reconciliation, `save_state` writes deltas only | Round-trip test; a v1 save migrates |
| 2 | v1→v2 migrator: one-shot converter for existing saves and both templates | `example` and `new_babel` play identically pre- and post-migration |
| 3 | Prompt assembly refactor to `SECTIONS` | Prompt for `example` is byte-comparable modulo intended additions |
| 4 | Modules: `narration`, `stats` bounds, `relationships` axis, `revelations`, `tracked_entity` | Per-module tests; absent-module tests |
| 5 | `failure_conditions` + `endgame.cause` | New capability test |
| 6 | Conformance fixtures (§7) | All three author-only, no Python changes |
| 7 | Docs: rewrite the schema sections of `CLAUDE.md`, `README.md`, `Narrative_Engine_Spec.md`; write the P-5 write-only register | — |

Phase 2 is the risk point. Write the migrator before the split lands, test it against a
real long-running save, and keep a v1 branch playable until phase 6 passes.

---

## 10. Decisions log

All four open questions are resolved. Recorded here rather than deleted, so the
reasoning survives for anyone who later wants to revisit one.

| Q | Question | Decision | Where it landed |
|---|---|---|---|
| 1 | `StoryContext` shape? | **Plain dicts** (`ctx["story"]` / `ctx["state"]`), with `ctx["story"]` wrapped in a `FrozenDict` that raises on write. Attribute access and dataclasses both rejected. | §2.2, §2.2.1, §2.4 |
| 2 | Multi-axis relationships? | **Single axis, configurable poles.** No change needed — §3.6 already specified this; the question is now marked settled rather than deferred. | §3.6 |
| 3 | Time and deadlines? | **Documented as the intended pattern**, not a new mechanic: a countdown is an ordinary `stats` entry (`"days_remaining": 7`). No `mechanics.clock` module — one system for "numbers that change over time," not two. | §3.6 |
| 4 | Generate `completion_signals` for LLM-created acts? | **Yes.** Required field on `check_and_advance_act`'s response schema, same as `title`/`description`. An empty list on a generated act is a validation failure. | §3.8 |

None of these reopen a design question elsewhere in the spec — Q2 confirms existing
text, Q3 is a documentation instruction rather than a schema change, and Q1/Q4 are both
narrow enough that they don't ripple into §4–§9.
