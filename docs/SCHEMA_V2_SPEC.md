# Story Schema v2 — Specification

**Status:** Approved for implementation. This spec supersedes the implicit v1 schema
documented across `README.md`, `CLAUDE.md`, and `docs/Narrative_Engine_Spec.md`. This
document resolves every open design question — see §10.

**Motivation.** Two reviews found the same underlying problem from different angles.
The prompt-coverage audit found two problems: a third of the schema never reaches a
prompt, and the engine reads two fields but never writes them. The genre audit found
that creative decisions exist as engine constants rather than story content. These
decisions include prose aesthetic, scene length, option count, relationship semantics,
and the protagonist's default name. Both problems trace to one root cause: **v1 has no
boundary between what the author writes and what the engine tracks.** v1 clones the
template wholesale into the save. As a result, authored content and runtime state have
the same shape. The same code mutates both, so nothing can tell them apart.

v2 draws that boundary explicitly and rebuilds the schema around it.

---

## 1. Design principles

**P-1 — Authored content and runtime state are separate stores.**
A story's template is immutable. The engine never mutates it at runtime. The save holds
only what changed. This principle is the load-bearing one. Most of the other principles
follow from it.

**P-2 — Every genre-specific mechanic is an optional module.**
If a key is absent, the feature does not exist: no state, no prompt section, no schema
field in the state-update JSON, and no UI affordance. The engine does not add an empty
header or a zeroed counter either. `character_creation` already works this way. It is
the reference implementation.

**P-3 — No engine constant may encode a creative decision.**
If a novelist would have an opinion about it, it belongs in the template. Examples
include prose style, scene length, POV, option count, the protagonist's fallback name,
stat bounds, and relationship semantics.

**P-4 — Read paths never assume optional structure.**
Code must use `.get()` or `setdefault()` throughout. A minimal template — `meta`,
`world.rules`, `plot.main_thread`, `plot.opening_scene` — must run.

**P-5 — Write-only state must be justified in writing.**
If the engine saves something but never reads it into a prompt, the author must list it
explicitly as intentional (audit trail, UI, debug) in `docs/Narrative_Engine_Spec.md`.
Anything not on that list is a bug.

**P-6 — Three-genre conformance.**
The schema is generic if an author can write a regency romance, a single-room courtroom
drama, and a survival horror story, each without touching Python. Fixtures enforce this
— see §7.

---

## 2. The story/state split

### 2.1 The problem it solves

In v1, `state_store.load_state()` clones `template.json` on first play. After that, the
save is authoritative for everything. This creates several consequences:

- **Authored content is frozen at save-creation.** For example, an author might fix a
  typo in `world.rules`, adjust a faction's goals, or rewrite an act description. No
  existing save ever sees the change. For a project in active development, this is the
  single most annoying property of the current design.
- **"Authored vs. generated" has to be faked.** v1 must fake the distinction between
  authored and generated content. CR-06 needed an `authored: true` flag on character
  entries purely so eviction would not delete hand-written NPCs. That flag is a
  workaround for a missing structural boundary. The same question recurs for subplots
  (seeded vs. LLM-invented) and acts (Act 1 vs. everything after).
- **Saves are bloated.** Every save carries a full copy of every location description,
  faction, opening scene, and character-creation option. None of this content ever
  changes.
- **`current_scene` is authored seed content that becomes runtime state.** As CR-01
  found, nothing ever writes to it. The category confusion explains why the review
  missed this bug.

### 2.2 The split

The engine loads two documents together and exposes them as a plain two-key dict — see
§2.2.1 for the shape decision.

```
stories/<slug>/template.json     immutable authored content, git-tracked
data/saves/<user>/<slug>.json    runtime deltas only
```

`state_store.load_state()` returns a working object that exposes both parts:

```python
ctx = state_store.load_state(user_id, story_slug)
ctx["story"]    # authored, FrozenDict — raises on write
ctx["state"]    # runtime, plain dict, mutable
```

`save_state()` persists `ctx["state"]` only. The save records `story_slug` and
`story_version` (see §3.1) so the system can detect a template revision.

#### 2.2.1 Shape decision: plain dicts and a frozen wrapper on `story`

The team considered three shapes for this decision. They resolved it now, rather than
deferring it:

| Option | Shape | Verdict |
|---|---|---|
| A | Plain nested dicts, `ctx["story"]["world"]["rules"]` | **Chosen** |
| B | Attribute access via `__getattr__` wrapper, `ctx.story.world.rules` | Rejected |
| C | Schema-generated dataclasses | Rejected |

**C is out on cost.** Real autocomplete and static type checking would catch a
wrong-half access at edit time instead of at runtime. That is the actual failure this
decision prevents. But every schema addition would require a change to a class
definition. This cuts directly against P-3 ("adding a story is a content change, not a
code change"). It also cuts against the requirement that `mechanics` modules must vary
freely per template.

**B is out because it improves readability but not safety.** `ctx.story.world.rules`
reads better than the dict form. However, `__getattr__` does not catch a typo any
earlier than a dict subscript does. It also does not stop code from writing into
`ctx.story` by accident. The one mistake that actually matters here is when code reaches
into the wrong half of the split. Only static typing (Option C) catches that mistake
before runtime. B pays a real complexity cost: a wrapper layer, serialization work in
both directions, and an extra stack frame. It buys only an aesthetic gain.

**The spec adds one change to option A: it wraps `ctx["story"]` in a `FrozenDict`** — a
thin `dict` subclass that raises an error on `__setitem__`, `__delitem__`, `update`, and
similar methods. This is the part that is actually load-bearing for P-1. It turns
"authored content is immutable" from a convention into an enforced property. It does
this without the `__getattr__` layer or the serialization cost that B would have added.
`ctx["state"]` stays a plain mutable dict.

Practical consequences:

- The migration diff at every call site is mechanical. Each site must prepend
  `["story"]` or `["state"]`. The access chain beneath it needs no change.
- `json.load` and `json.dump` work directly on both halves. The code applies
  `FrozenDict` after it loads the data, then strips it before it dumps the data (or
  simply reads it as a plain dict, since subclasses serialize fine either way).
- **Test requirement:** `save_state()` must assert that nothing under `ctx["story"]`
  changed during the request. This is a belt-and-braces check, independent of the error
  that `FrozenDict` raises at the point of mutation. It catches any code path that swaps
  in a fresh dict instead of writing through the wrapper.
- A wrong-half read (`ctx["state"]["world"]["rules"]` when it should be
  `ctx["story"]["world"]["rules"]`) still only fails at runtime, as either a `KeyError`
  or a silent read of stale/absent data. This is an accepted cost of the decision to
  reject Option C.

### 2.3 Template revisions reaching live saves

Because the engine re-reads authored content on every load, template edits reach
existing saves for free. The only hazard is a template edit that invalidates a runtime
reference. Examples include a deleted location that `state.scene.location` points at, or
a removed subplot id present in `state.plot.subplots`.

The system must handle this by reconciliation at load, never by rejection:

| Dangling reference | Behaviour |
|---|---|
| `scene.location` not in `story.world.locations` | Keep the id, render it raw, log once |
| Runtime subplot id absent from template | It is a generated subplot; expected, no action |
| Seeded subplot removed from template | Leave the runtime copy in place; it is in-flight |
| `revelation` id absent from template | Drop the runtime reveal record silently |
| Stat name absent from `character_creation` | Keep the value; a story may have removed the step |

The system must never fail a load because of a reconciliation mismatch. If a player
loses a save mid-story because of an author's typo, that is far worse than a slightly
stale reference.

### 2.4 Migration cost, honestly

Every call site that currently uses `state["plot"]["subplots"]` must change to either
`ctx["story"]["plot"]["subplots"]` or `ctx["state"]["plot"]["subplots"]`. A wrong choice
here creates a silent bug rather than a crash — see §2.2.1 for why the team accepted
this residual risk instead of eliminating it with static typing. This change affects
`story_engine.py`, `app.py`, `plot_manager.py`, `subplot_manager.py`, and every file in
`test/`.

**Lighter fallback if this proves too large:** keep a single merged dict. Add
`story_version`. Re-merge the authored sections from the template on every load,
overwriting them. This approach recovers the property that template updates reach
existing saves — the most valuable single benefit — without the churn at every call
site. It does not fix the authored-vs-generated distinction, so CR-06's `authored: true`
flag stays. Take this path only if the full split stalls.

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

`story_version` is an opaque string. The author bumps it on any meaningful edit. The
engine uses it for logging and to decide whether to run reconciliation. The engine never
parses it for ordering.

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

This block stays unchanged from v1, except that `pov` moves to `narration`. `synopsis`
remains story-picker copy. The engine does not prompt it. P-5 lists this omission as
intentional.

`content_rules` are out-of-character safety and rating boundaries.
`world.rules` are in-fiction physics. The separation between them is deliberate. The
author must document this separation.

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
| `scene_length` | `{470, 500}` | Replaces `SCENE_WORD_MIN` and `SCENE_WORD_MAX`. |
| `style` | `[]` → no style block at all | Verbatim bullets. If absent, the model works from `genre` and `tone` alone, which is a sane default. |

**The engine retains only universal instructions:** stay within world, tone, and rules;
use the three emphasis markers (`**`, `*`, `__`); avoid other markdown; and follow the
`OPTIONS:` block format. Everything about *how the prose should feel* moves out.

Migration note: v1's current style bullets are `new_babel`'s voice. Move them there
verbatim. Write a distinct set for `example`. There is an existing conflict: the engine
says to never use two consecutive descriptive sentences, but `example`'s own
`world.rules` say that strangeness accumulates through detail. This conflict resolves
itself.

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
  or empty means the story has no spatial model. The prompt gets no `HERE:` block and no
  `ADJACENT:` block, and the engine treats `scene.location` as free text. A courtroom
  drama, a chamber piece, or an epistolary story requires this module to work at all.
- `factions` — **optional module.** Unchanged shape, now prompted.
- `characters` — **authored roster.** `{name: {name, description, ...}}`, keyed on
  canonical display name so the roster key, the `relationships` key, and the name the
  model sees are one string. This absorbs CR-06. The engine no longer needs the
  `authored: true` flag, because authored characters live in the template and
  discovered characters live in the save.

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

v2 deletes the `player.origin` wrapper. It existed solely to hold `memory_fragments`,
which moves to `mechanics.revelations` (§3.6).

### 3.6 `mechanics` — optional modules *(new)*

Every key here is optional. If a key is absent, the mechanic does not exist: no state,
no prompt section, no field in the state-update schema, and no UI control.

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
`character_creation` starting stats. This constraint is correct, and it stays.

**This spec documents time and deadlines as an intended use of `stats`, not as a
separate mechanic.** An author can write a heist countdown, a disaster timer, a shift
schedule, or a season-bound romance as an ordinary starting stat, for example
`"days_remaining": 7`. This needs no engine changes. The design deliberately does not
give this pattern its own `mechanics.clock` module. A clock is simply a number that the
state-update pass increments or decrements each turn, exactly like any other stat. A
second, parallel system for the same behaviour would only fragment where "numbers that
change over time" live. Record this pattern in `docs/Narrative_Engine_Spec.md`, alongside
`character_creation`'s worked example, so that an author reaches for a countdown stat
instead of inventing a new field.

**`relationships`** — replaces the hardcoded "−100 hostile to +100 devoted" scale and the
"trust/warmth built" instruction. The engine interpolates pole labels into both prompts,
so horror can track *unaware → fixated*, and a political thriller can track *indebted →
owed*, without pretending those are warmth. If this block is absent, the story tracks no
relationships at all, and the field vanishes from the state-update schema.

**`revelations`** — replaces `player.origin.memory_fragments`. It has the same shape and
a more generic name. Unlike the old field, it no longer sits on a hard-subscripted
required path. That old path currently raises a `KeyError` on any template that omits
it. `stories/example` carries an empty one purely to avoid the crash, for a mechanic
that its own README says it does not use. The pattern covers amnesia fragments, clues,
evidence, lore drops, prophecies, and backstory reveals. CR-03's fix to surface content
applies here, at the new path.

**`tracked_entity`** — absorbs CR-07. If this block is absent, the state-update schema
has no `entity_interaction` field, and the act-check prompt has no encounters line.
`pacing_note` is new. The engine feeds it to narration, along with the contact count, so
the narrator can pace appearances against prior contact.

**`failure_conditions`** — a new capability that closes the genre gap identified in §5
of the genre review. In v1, the only ending happens when the player types "end story."
Horror, survival, tragedy, and most thrillers need a story that can end badly, even if
the player never asks for an ending. The state-update pass evaluates `failure_conditions`
alongside revelations. They share the same authored-trigger shape but produce a
different effect: firing one sets `endgame.requested`, with `final_arc` built from
`ending_prompt`. This routes the story into the existing endgame machinery, instead of a
new code path.

This does not violate `CLAUDE.md`'s "continuous, not finite" principle. That principle
only means the design must not fix the number of acts in advance. The team never
intended it to imply that the story can end only by request.

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

**`completion_signals` are required on generated acts, not just Act 1.** In v1, only
Act 1 carries them, and an author writes them by hand. Every act that the state pass
generates afterward gets `"completion_signals": []`. As a result, CR-09's pacing-nudge
block silently disappears the moment the story leaves Act 1. Whenever
`check_and_advance_act` generates a new act, its response schema must include
`completion_signals` as a required field, alongside `title` and `description`. These
signals need the same specificity as the authored Act 1 signals. They must not simply
restate the act description. If a generated act is saved with an empty list, the state
pass must treat this as a validation failure, equivalent to a missing `title`.

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

When `narration` is present and the before/after pair is absent, the engine captures no
name and uses `protagonist.default_name` instead. This unblocks stories about an
established character, a historical figure, or a character whose name is itself a later
revelation.

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

- **`scene` is top-level and runtime-only.** This resolves CR-01's category confusion at
  the root. The engine never treated `scene` as authored content that happens to
  persist — it is actually the most volatile thing in the system. `present` replaces
  `present_npcs`, a field the old code never wrote to.
- **`characters` holds only the delta** — relationship score and discovery metadata.
  Authored description comes from the template at render time. A discovered character
  carries its own description instead.
- **`plot.subplots`** holds runtime state for both seeded and generated subplots
  (progress, status, active). The title and description for a seeded subplot resolve
  from the template. A generated subplot carries its own title and description, since
  there is nothing in the template to resolve against.
- **`generated_acts` and `act_completion`** keep template acts immutable. They also
  track which acts the engine has added and finished.
- **The schema promotes `pacing` out of `plot`** — `turn_count` is global session state,
  not a plot property.
- **The schema promotes `pending_regenerate` out of `history`** — it is a save slot, not
  history.
- **`endgame.cause`** — `"player_request"` or a `failure_conditions` id, so the UI and
  the closing prompt can distinguish a requested wrap-up from a loss.
- **`revelations_revealed`** replaces the old design, which changed a `revealed`
  boolean inside authored content. The split forbids that kind of mutation.
  `revelations_revealed` also carries the turn number, which CR-03 needed for recency
  ordering.

---

## 5. Prompt assembly

The current `build_system_prompt` is one long f-string with conditionals threaded
through it. Once every mechanic becomes optional, this approach becomes unmanageable.
The team must restructure it as a declarative section list:

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

Each builder returns a string or `None`. `None` contributes nothing — not an empty
header. Ordering rules:

- **Stable content first.** Identity, world rules, setting, factions, roster, and main
  thread all stay stable across many turns and form a cacheable prefix. Given the
  input-heavy token profile (~8–12k in vs. ~650 out), this is where the design achieves
  the most cost savings.
- **Volatile content last.** Scene, protagonist line, recent turns, nudge.
- **Reinforcement preserved.** The existing "stay strictly within the established world,
  tone, and rules above" line before the footer stays. See the Reinforcement section of
  `Narrative_Engine_Spec.md`. Recency measurably helps constraint adherence on smaller
  models. This matters if a local fine-tune takes over narration.

The state-update prompt receives the same treatment. Its schema already builds the field
list conditionally for `stats`. The team must extend the same pattern to
`relationships`, `revelations`, `tracked_entity`, `failure_conditions`, and
`scene_update`.

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

The schema keeps `thread_steering` but moves it wholly to the save. Nothing in it is
authored content.

---

## 7. Genre conformance fixtures

Three minimal templates live under `test/fixtures/`. Each one exercises a different
subset of optional modules. These fixtures are the executable form of P-6. They exist to
fail loudly when someone reintroduces a genre assumption.

| Fixture | Modules used | Modules absent | What it proves |
|---|---|---|---|
| `regency.json` | relationships (axis: *disregard → devotion*), characters, revelations | locations, factions, stats, tracked_entity, failure_conditions, character_creation | Interiority-heavy `narration.style`; no map; no combat; relationship-driven |
| `courtroom.json` | characters, revelations (as testimony), failure_conditions | locations, factions, tracked_entity | Single setting, no spatial model at all; a real loss condition |
| `survival.json` | stats (floor `-10`), tracked_entity, failure_conditions, locations | relationships, characters, character_creation | Negative stat scale; entity axis is not warmth; death is an ending |

Each fixture needs the following test assertions: the template loads; `build_system_prompt`
produces no empty headers for absent modules; the state-update schema omits the
corresponding fields; and one stubbed turn applies cleanly. All of these run against the
existing `test/_llm_stubs.py` harness. None require a live call.

Add a fourth check across all fixtures: **no fixture may require a Python change.** If a
fixture requires a Python change, the schema is not done.

---

## 8. Relationship to the existing CRD

`SCHEMA_COVERAGE_CRD.md` remains the correct plan for v1. This document absorbs several
items from that plan. The team should land several other items on v1 first, regardless
of whether v2 proceeds, since they are cheap and fix live bugs.

| CR | Under v2 |
|---|---|
| CR-01 scene never written | **Absorbed** — `state.scene` is runtime-only by construction; the team still must implement the `scene_update` diff field |
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

**Recommended v1 pre-work before v2 begins:** CR-02, CR-05, CR-08, CR-10, CR-17. All of
these are small and independent of the split. CR-10's deletion also meaningfully reduces
what the team must migrate.

---

## 9. Implementation phases

| Phase | Work | Gate |
|---|---|---|
| 0 | v1 pre-work: CR-02, CR-05, CR-08, CR-10, CR-17 | Existing tests green |
| 1 | `state_store` split: dual-document load, `StoryContext`, reconciliation, `save_state` writes deltas only | Round-trip test; a v1 save migrates |
| 2 | v1→v2 migrator: one-shot converter for existing saves and both templates | `example` and `new_babel` play identically pre- and post-migration |
| 3 | Prompt assembly refactor to `SECTIONS` | Prompt for `example` matches byte-for-byte, except for intended additions |
| 4 | Modules: `narration`, `stats` bounds, `relationships` axis, `revelations`, `tracked_entity` | Per-module tests; absent-module tests |
| 5 | `failure_conditions` and `endgame.cause` | New capability test |
| 6 | Conformance fixtures (§7) | All three author-only, no Python changes |
| 7 | Docs: rewrite the schema sections of `CLAUDE.md`, `README.md`, `Narrative_Engine_Spec.md`; write the P-5 write-only register | — |

Phase 2 is the risk point. Write the migrator before the split lands. Test it against a
real long-running save. Keep a v1 branch playable until phase 6 passes.

---

## 10. Decisions log

This spec resolves all four open questions. The team records them here rather than
deleting them, so the reasoning survives for anyone who wants to revisit one later.

| Q | Question | Decision | Where it landed |
|---|---|---|---|
| 1 | `StoryContext` shape? | **Plain dicts** (`ctx["story"]` and `ctx["state"]`), with `ctx["story"]` wrapped in a `FrozenDict` that raises on write. The team rejected both attribute access and dataclasses. | §2.2, §2.2.1, §2.4 |
| 2 | Multi-axis relationships? | **Single axis, configurable poles.** The design needed no change — §3.6 already specified this. The team now marks the question as settled, not deferred. | §3.6 |
| 3 | Time and deadlines? | **Documented as the intended pattern**, not a new mechanic: a countdown is an ordinary `stats` entry (`"days_remaining": 7`). No `mechanics.clock` module — one system for "numbers that change over time," not two. | §3.6 |
| 4 | Generate `completion_signals` for LLM-created acts? | **Yes.** Required field on `check_and_advance_act`'s response schema, same as `title` and `description`. An empty list on a generated act is a validation failure. | §3.8 |

None of these decisions reopen a design question elsewhere in the spec. Q2 only
confirms existing text. Q3 is a documentation instruction, not a schema change. Q1 and
Q4 are both narrow enough that they do not ripple into §4–§9.
