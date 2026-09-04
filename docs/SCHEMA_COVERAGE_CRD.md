# Change Requirements: Story Schema Prompt Coverage

**Scope:** `backend/story_engine.py` prompt builders, with knock-on changes to
`stories/*/template.json`, `backend/plot_manager.py`, and `backend/app.py`.

**Problem statement:** An audit of every field in the story schema against all six
prompt builders (`build_system_prompt`, `generate_pacing_nudge`,
`update_progress_from_turn`, `generate_new_subplot`, `check_and_advance_act`,
`handle_end_story_request`) found that roughly a third of the schema never reaches
any prompt. Some of that is deliberate and documented (`history_log.full_transcript`,
`player.flags_archive`). The rest is either authored worldbuilding that sits inert,
steering machinery that silently no-ops, or — in two cases — state that is read into
the prompt but never updated, which is actively harmful.

**Priority key**

| | Meaning |
|---|---|
| **P0** | Causes incorrect narration today. Fix first. |
| **P1** | Authored content or plot spine is missing from the prompt; coherence cost. |
| **P2** | Calibration gaps and dead features. |
| **P3** | Cleanup, documentation, low-value fields. |

**Global constraints that apply to every change below**

- **G-1 — Backward compatibility.** A save clones its template once at creation and
  never re-reads it. Every new schema field must be read with `.get()` / `setdefault()`
  and degrade to empty rather than raising. Existing saves must keep loading.
- **G-2 — Optional, not required.** Any new template field must be optional. A story
  that omits it gets no prompt section at all, not an empty header — same pattern as
  the existing `stats_str` / `creation_str` conditionals.
- **G-3 — Cache-friendly ordering.** New stable content (world summary, main thread)
  goes near the *top* of the assembled prompt, above `STORY SO FAR` and
  `RECENT EXCHANGES`, so it sits inside a stable prefix for prompt caching. Volatile
  content stays where it is.
- **G-4 — Test coverage.** Every change gets a check in `test/`, using the existing
  monkeypatched `call_llm` / `call_llm_json` stubs (`test/_llm_stubs.py`). No live
  API calls.
- **G-5 — Token budget.** The narration prompt is already input-heavy (~8–12k input
  tokens vs ~650 output). Total added input from P0+P1 should stay under ~500 tokens
  per turn, and should be predominantly cacheable.

---

## P0 — Incorrect narration today

### CR-01 — `plot.current_scene` is read every turn but never written

**Current behaviour**
`build_system_prompt` emits `CURRENT SCENE ({scene['location']}): {scene['summary']}`
on every narration call. No writer for `plot.current_scene` exists anywhere in the
repo — not `story_engine.py`, not `app.py`, not `plot_manager.py` or
`subplot_manager.py`. The value is cloned from the template at save creation and
frozen for the life of the playthrough.

**Impact**
By turn 60 the prompt still asserts the protagonist has just stepped off the ferry
at `loc_dock`. This is a stale, authoritative claim placed late in the prompt — high
recency weight — directly contradicting `RECENT EXCHANGES`. On a smaller narration
model this is a significant drift source, and it will get worse if a local fiction
fine-tune replaces the frontier model for the narration pass.

`current_scene.present_npcs` is likewise never written and never read.

**Required behaviour**
Extend the `update_progress_from_turn` diff schema (already an LLM call every turn,
so no new call is needed) with a scene block:

```json
"scene_update": {
  "location": "<location id from the known list, or the same id if unchanged>",
  "summary": "<1-2 sentences: where the protagonist is now and the immediate situation, as of the end of this turn>",
  "present_npcs": ["<character name>", "..."]
}
```

Apply rules:
- `location` must match a key in `world.locations` when that dict is non-empty;
  reject and keep the previous value otherwise. When `world.locations` is empty,
  accept any string.
- `summary` replaces the previous value wholesale. Empty/missing → keep previous.
- `present_npcs` replaces the previous list.
- A missing `scene_update` key leaves `current_scene` untouched (turn didn't move).

Supply the current scene and the valid location ids to the state-update prompt so
the model has something to diff against.

**Acceptance criteria**
- After N turns, `state["plot"]["current_scene"]["summary"]` reflects the most
  recent narration, not the template seed.
- An invalid/unknown `location` id leaves the previous location in place.
- A diff with no `scene_update` key is a no-op, not a crash.
- `present_npcs` round-trips.

**Interim fallback (if CR-01 is deferred):** drop the `CURRENT SCENE` line from
`build_system_prompt` entirely. A missing scene line is strictly better than a
lying one.

---

### CR-02 — Scene location is emitted as a raw database key

**Current behaviour**
`CURRENT SCENE (loc_dock): ...` — the raw `world.locations` key is interpolated
directly.

**Required behaviour**
Resolve through `world.locations[id]` and emit the human-readable `name`, falling
back to the raw id if the key is absent (older saves, ad-hoc locations from CR-01).

```
CURRENT SCENE (The Ferry Dock): ...
```

**Acceptance criteria**
- A known id renders as its `name`.
- An unknown id renders as the raw string, no exception.
- Applies to CR-04's location block too, not just this line.

**Depends on:** nothing. Ships independently of CR-01.

---

### CR-03 — Revealed memory fragment content never reaches the narrator

**Current behaviour**
The reveal path is: `frag["trigger"]` → `update_progress_from_turn` prompt →
model returns `memory_fragments_revealed` → `frag["revealed"] = True` → nothing.
`frag["content"]` is never interpolated into any prompt. The only downstream
consumer of the reveal state is `check_and_advance_act`, which uses a *count* of
revealed fragments.

**Impact**
The mechanic can mark a fragment revealed, but the narrator was never told what it
says, so it cannot have surfaced it. Fragments are write-once and unreadable. For
`new_babel` specifically, the entire fragmented-memory mechanic is currently inert.

**Required behaviour**
Add a revealed-fragments block to `build_system_prompt`, conditional on there being
at least one revealed fragment (per G-2):

```
REVEALED MEMORIES (the protagonist already knows these; reference them naturally,
do not re-reveal them as though they were new):
- <content>
- <content>
```

Keep the existing split intact: the state-update pass continues to receive only
*unrevealed* `trigger` strings, and the narration pass receives only *revealed*
`content` strings. Neither pass sees the other half.

**Bounding:** revealed fragments accumulate for the whole game. Cap the block at
the most recently revealed N (suggest `MEMORY_FRAGMENT_PROMPT_LIMIT = 12`) to keep
the prompt bounded, consistent with `SUBPLOT_TITLE_HISTORY_LIMIT`. Requires a
`revealed_turn` field written at reveal time to order them; absent that field on
older saves, fall back to template order.

**Acceptance criteria**
- A fragment with `revealed: true` has its `content` present in the narration prompt.
- A fragment with `revealed: false` has its `content` absent from the narration prompt
  and its `trigger` present in the state-update prompt.
- A story with zero fragments produces no `REVEALED MEMORIES` header at all.
- With more than the limit revealed, only the most recent N appear.

---

## P1 — Missing authored content and plot spine

### CR-04 — World context block: `setting_summary`, `locations`, `factions`

**Current behaviour**
`world.rules` is the only part of `world` that reaches any prompt.
`world.setting_summary`, `world.locations` (names, descriptions, and the
`connected_to` adjacency graph), and `world.factions` (name, goals,
`relationship_to_player`) are never fed anywhere.

**Impact**
The densest authored worldbuilding in the template is inert. The model cannot know
what is adjacent to the current location, what any faction wants, or what the setting
actually is beyond what it can infer from `rules` and the running summary.

**Required behaviour**
Add a `WORLD` block to `build_system_prompt`, placed above `STORY SO FAR` per G-3:

```
SETTING: {world.setting_summary}

HERE: {current location name} - {description}
ADJACENT: {name of each id in connected_to, comma-separated}

FACTIONS:
- {name}: {goals} (toward the player: {relationship_to_player})
```

Scoping rules:
- Emit **only** the current location and its direct `connected_to` neighbours, not
  the full location table. This keeps the block bounded on a story with 40 locations
  and gives the model exactly the movement affordances it needs.
- Emit all factions — the count is small and authored, and faction posture is
  globally relevant.
- Each sub-block is independently conditional (G-2): a story with no `factions`
  gets no `FACTIONS:` header.

**Acceptance criteria**
- `setting_summary` appears in the narration prompt.
- The current location's name and description appear; unrelated locations do not.
- Every id in the current location's `connected_to` appears by `name`.
- A dangling `connected_to` id is skipped silently, not rendered as a raw key or raised.
- `stories/example`'s four locations and one faction all render correctly.
- A template with `"locations": {}` and `"factions": {}` produces no world sub-blocks.

**Note:** `setting_summary` and `factions` are stable for the whole playthrough and
sit in the cacheable prefix. `HERE`/`ADJACENT` are volatile and should be positioned
so they do not invalidate the cached prefix — place them with the scene line rather
than with `SETTING`.

---

### CR-05 — Main thread is absent from the narration prompt

**Current behaviour**
`plot.main_thread.title` and `.description` reach `generate_new_subplot` and
`handle_end_story_request`, but never `build_system_prompt`. The narrator learns
about the story's spine only indirectly, via `generate_pacing_nudge`'s act
description, which fires once every `pacing_nudge_frequency` (8) turns.

**Impact**
Seven turns out of eight, the narrator has no statement of what the story is about
beyond the compressed summary.

**Required behaviour**
Add to `build_system_prompt`, above `STORY SO FAR`:

```
MAIN THREAD: {main_thread.title} - {main_thread.description}
CURRENT ACT {n}: {act.title} - {act.description}
```

Resolve the current act by `act_number == main_thread.current_act`, matching
`generate_pacing_nudge`'s lookup (not `acts[current_act - 1]`, which
`generate_new_subplot` uses and which breaks if act numbering is ever non-contiguous —
see CR-16).

This overlaps with the pacing nudge's `PACING: Currently in Act N` line. Keep both;
the nudge line's role is to trigger a pacing *change*, this one's is standing context.
Alternatively, trim the nudge's act line to avoid the duplication — implementer's call,
but do not remove both.

**Cost:** ~40–60 tokens, stable across many turns except at act transitions.

**Acceptance criteria**
- Main thread title and description appear in every narration prompt.
- The current act shown matches `main_thread.current_act` after an act advance.
- A story mid-endgame shows the finale act.

---

### CR-06 — `characters` registry is disconnected from `player.relationships`

**Current behaviour**
The top-level `characters` dict is never read and never written by any code path.
Meanwhile `player.relationships` accumulates character names invented by the state-update
model, keyed by free-text name, with an eviction policy. Two disconnected registries
for the same concept.

**Impact**
Authored NPCs in a template are invisible to the engine. Relationship scores attach
to LLM-chosen name strings with no canonical identity, so "Mrs. Abbott" and
"the innkeeper" are different characters.

**Required behaviour**
`characters` becomes the canonical roster. `player.relationships` stays as the score
store, keyed by the roster's canonical name.

**Entry shape.** Authored entries may carry more; the engine requires only:

```json
"characters": {
  "Mrs. Abbott": {
    "name": "Mrs. Abbott",
    "description": "Innkeeper at the Harborlight. Warm, and the first to slip.",
    "first_seen_turn": 0,
    "authored": true
  }
}
```

Key on the canonical display name (not a synthetic id) so the LLM-facing name, the
`relationships` key, and the roster key are the same string and no mapping layer is
needed.

**Narration prompt.** Add a roster block above `STORY SO FAR`, conditional on the dict
being non-empty (G-2):

```
KNOWN CHARACTERS (use these exact names; standing is -100 hostile to +100 devoted):
- Mrs. Abbott (+35): Innkeeper at the Harborlight. Warm, and the first to slip.
```

Omit the score for a character with no `relationships` entry yet, rather than showing 0
— an unmet authored character and a met-but-neutral one are different states.

**State-update prompt.** Add the roster of known names to `update_progress_from_turn`
and instruct the model to reuse an existing name verbatim when the character is already
known, introducing a new name only for a genuinely new character:

```
KNOWN CHARACTER NAMES (reuse these exactly for anyone already known; only introduce a
new name for a character who has not appeared before): ["Mrs. Abbott", ...]
```

**Roster growth.** On a `relationship_changes` entry whose name is not in `characters`,
append a stub — `{name, description: "", first_seen_turn: turn_count, authored: false}`
— so the roster tracks the story. Do not ask the model for a description; the name is
enough, and a second field invites drift.

**Bounding and eviction.** `characters` and `relationships` must stay in step. Extend
the existing `RELATIONSHIPS_LIMIT` eviction (closest-to-neutral first) to delete the
matching `characters` entry in the same pass — but **never evict an `authored: true`
entry**. Authored characters are part of the story's fixed content and must survive
regardless of score; only LLM-introduced stubs are evictable. If pins fill the budget,
the roster is allowed to exceed the limit, same tolerance as pinned flags in
`archive_stale_flags`.

The narration roster block is bounded by the same limit, so no separate cap is needed.

**Acceptance criteria**
- Authored characters from a template appear in the narration prompt with description
  and, where a score exists, standing.
- A character with no `relationships` entry renders without a score, not as `(0)`.
- The state-update prompt lists known character names.
- A `relationship_changes` entry for an unknown name creates a `characters` stub with
  `authored: false` and the correct `first_seen_turn`.
- Eviction removes the `characters` entry and the `relationships` entry together.
- An `authored: true` character sitting at score 0 is never evicted, even when the
  roster is over budget.
- `stories/example`'s empty `characters: {}` produces no roster block; the existing
  `test_inventory_relationships.py` assertions still hold.
- A save predating this change (no `characters` key) loads and self-populates.

**Content follow-up (not blocking):** `stories/example/template.json` ships
`"characters": {}` while its prose and subplots reference an innkeeper. Populate it as
part of this change so the feature has a working reference story.

---

### CR-07 — "the Architect" is hardcoded in a multi-story engine

**Current behaviour**
Two prompts in `story_engine.py` name a story-specific entity:

- `update_progress_from_turn`: `"entity_interaction": <true if the Architect appeared or acted this turn, else false>`
- `check_and_advance_act`: `ARCHITECT ENCOUNTERS: {plot['entity_interaction_count']}`

**Impact**
Running `stories/example` (a cozy mystery with no such entity) asks the state model
about a character that does not exist, and reports encounters with it to the pacing
director. This is a correctness bug for every story that isn't `new_babel`, and it
contradicts the engine's stated multi-story design.

**Required behaviour**
Move the entity into the template:

```json
"world": {
  "tracked_entity": {
    "name": "The Architect",
    "description": "<optional, one line>"
  }
}
```

- When `tracked_entity` is absent, omit the `entity_interaction` field from the
  state-update schema entirely and omit the encounters line from the act-check
  prompt — same conditional pattern as `stats` / `stat_changes`.
- When present, interpolate `name` into both prompts.
- Consider also feeding `entity_interaction_count` to the narration prompt so the
  narrator can pace the entity's appearances (see CR-16).

**Acceptance criteria**
- `stories/example` produces a state-update prompt with no `entity_interaction` field
  and an act-check prompt with no encounters line.
- A template with `tracked_entity` produces both, using its `name`.
- An older save without the key does not crash.

---

## P2 — Calibration gaps and dead features

### CR-08 — Subplot progress is not fed to the scoring model

**Current behaviour**
`update_progress_from_turn` sends `ACTIVE SUBPLOTS: {id: title}` and asks for
`"subplot_progress": {"<subplot_id>": <integer 0-100, progress made this turn>}`.
Neither `progress` nor `completion_threshold` is in the prompt.

**Impact**
The model is asked for a delta with no knowledge of where the subplot currently
stands, so it cannot distinguish "this beat should finish the thread" from "this
nudges it." Since reaching `completion_threshold` triggers completion, replacement
subplot generation, and act-advancement evaluation, miscalibration here propagates
into the whole pacing layer.

**Required behaviour**
Include current progress and threshold per active subplot, and its description:

```
ACTIVE SUBPLOTS (id: title - description [progress/threshold]):
  subplot_001: Settling In - Get to know the Harborlight Inn... [40/100]
```

State explicitly in the instruction that the delta is *added* to the current value
and that reaching the threshold completes the thread, so the model can deliberately
close one out.

**Acceptance criteria**
- Progress and threshold appear per active subplot.
- Clamping behaviour is unchanged.
- Existing `test_subplot_act_endgame.py` still passes.

---

### CR-09 — `completion_signals` never reach the narrator

**Current behaviour**
`acts[].completion_signals` is fed only to `check_and_advance_act`, which judges
whether the act has resolved. The narrator is never told what would resolve it.

**Impact**
The pacing director grades against criteria the narrator has never seen. The act can
only advance by accident.

**Required behaviour**
Include the current act's `completion_signals` in `generate_pacing_nudge` (not in
every turn's prompt — this is directional steering, which is exactly what the nudge
is for):

```
THIS ACT RESOLVES WHEN: {', '.join(completion_signals)}
```

Conditional on the list being non-empty — note that LLM-generated acts from
`check_and_advance_act` currently get `"completion_signals": []`, so this block will
be absent for every act after Act 1.

**Related:** consider having `check_and_advance_act` generate `completion_signals`
for the act it creates, so this block does not silently disappear after Act 1. Track
as a follow-up if it grows the change too far.

---

### CR-10 — `plot.alternate_threads` is entirely write-only

**Current behaviour**
`plot_manager.create_alternate_thread` and `toggle_thread_focus` write
`alternate_threads`, `main_thread.is_primary_focus`, and per-thread `active`. No
prompt anywhere reads any of it. Both are exposed in the CLI and in
`frontend/plot_manager.html`.

**Impact**
A user creates an alternate thread, switches focus to it, sees confirmation output,
and the story is completely unaffected. A UI affordance that silently does nothing is
worse than not offering it.

**Required behaviour**
Remove the feature. `pivot` already covers redirecting the story and it works
end-to-end; alternate threads are a second, non-functional path to the same goal, and
their `stages` / `current_stage` fields were never given a meaning (written as `[]`
and `1`, never read).

Delete:

| Location | Remove |
|---|---|
| `backend/plot_manager.py` | `create_alternate_thread()`, `toggle_thread_focus()` |
| `backend/plot_manager.py` | the `create-alt` and `focus` command branches in `main()`, and their two usage lines in the help text |
| `backend/plot_manager.py` | the alternate-threads section of `show_plot_overview()`, and the `Primary Focus:` / `Can Pivot:` lines |
| `backend/app.py` | the `create-alt` and `focus` branches in `plot_manager_view` |
| `frontend/plot_manager.html` | the "Create Alternate Thread" and "Switch Focus" cards |
| `stories/*/template.json` | `plot.alternate_threads`, `main_thread.is_primary_focus`, `main_thread.can_pivot` |
| `docs/Narrative_Engine_Spec.md`, `README.md` | the alternate-thread and switch-focus bullets under mid-adventure steering |

**Backward compatibility.** Per G-1, an existing save still carries these keys. Nothing
reads them after this change, so no migration is needed — they become inert residue.
Do not add code to strip them; a save that quietly carries three unused keys is
cheaper than a migration path.

**Guard against reintroduction.** `STEER_WARNING` in `story_engine.py` enumerates the
available `plot_manager` commands and currently lists `'create-alt'` and `'focus'`.
Update that string, or the CLI will keep advertising commands that no longer exist.

**Acceptance criteria**
- `python backend/plot_manager.py` help output no longer lists `create-alt` or `focus`.
- `python backend/plot_manager.py create-alt ...` reports an unknown command.
- The Plot Manager page renders without the two cards and without errors.
- `steer` output no longer advertises the removed commands.
- A save created before this change still loads and plays.

---

### CR-11 — `thread_steering.player_driven_goals` is write-only

**Current behaviour**
`plot_manager.add_player_goal` appends `{description, turn, active}` to
`thread_steering.player_driven_goals`. Exposed as `add-goal` in the CLI and as
"Record Player Goal" in the web UI. Nothing reads it.

**Required behaviour**
Either feed active goals into `generate_pacing_nudge` —

```
PLAYER GOALS: {', '.join(g['description'] for g in goals if g.get('active'))}
```

— or remove the command, the UI card, and the field. Recommendation: wire it in.
Unlike CR-10 this is a one-line prompt addition and the feature is meaningful; the
nudge is the natural home for it. If wired in, add a way to mark a goal inactive,
or the list grows unbounded.

---

## P3 — Cleanup and low-value fields

### CR-12 — `thread_steering.emerging_themes` reaches only subplot generation

Currently fed to `generate_new_subplot` and nowhere else, so a noted theme influences
new threads but never the prose. **Required:** add to `generate_pacing_nudge` alongside
CR-11's goals. One line, conditional on non-empty.

### CR-13 — `subplots[].ties_to_main_plot` is never fed to narration

Written at generation, authored in templates, rendered in `subplot_manager.html`,
never sent to a prompt. **Required:** include it with the primary active subplot in
`generate_pacing_nudge`, where the subplot's title and description already go. This
is the field that tells the narrator *why* the subplot matters.

### CR-14 — `meta.pov` is declared but never enforced

`"pov": "second-person"` is in every template; no prompt states POV. It currently
holds only because the hand-authored opening scene establishes the voice and
`RECENT EXCHANGES` sustains it. **Required:** add POV to the identity header —
`TITLE: ... | GENRE: ... | TONE: ... | POV: {meta['pov']}` — conditional on the key
existing. Roughly four tokens, fully cacheable, and it removes a silent dependency on
the opening scene's voice that will bite when a fine-tune drifts.

### CR-15 — `pacing.ready_for_main_plot_advancement` is a dead field

Never read, never written, present in every template. **Required:** remove from
templates. No code change needed.

### CR-16 — `plot.entity_interaction_count` is never fed to narration

Incremented every turn the entity appears, read only by `check_and_advance_act`.
**Required (conditional on CR-07):** when a `tracked_entity` is configured, include
the count in the narration prompt so the narrator can pace appearances against
prior contact rather than treating each as the first. Ties into the entity budget
mechanism.

### CR-17 — Inconsistent current-act lookup

`generate_new_subplot` uses `main_thread["acts"][main_thread["current_act"] - 1]`
(positional). `generate_pacing_nudge` and `check_and_advance_act` use
`next(act for act in acts if act["act_number"] == current_act)` (by key). These
diverge if act numbering ever becomes non-contiguous — which `plot_manager.add_act`
with a `position` argument can cause. **Required:** standardise on the by-key lookup
everywhere; extract a `_current_act(state)` helper.

### CR-18 — Document the intentional write-only fields

The following are write-only *by design* and should be explicitly marked as such in
`docs/Narrative_Engine_Spec.md`, so a future audit does not re-flag them:

| Field | Purpose |
|---|---|
| `history_log.full_transcript` | Disk-only UI scrollback; deliberately never prompted (asserted by `test_full_transcript.py`) |
| `player.flags_archive` | Retired flags; retained for debugging |
| `player.flags_meta` | Eviction bookkeeping |
| `main_thread.act_history` | Audit trail |
| `thread_steering.pivot_history` / `last_pivot_turn` | Audit trail |
| `main_thread.emergent_directions` | Human-facing staging area for manual promotion |
| `main_thread.plot_notes` | Author's note, `plot_manager` display only |
| `pacing.last_pacing_direction` | Debug echo of the last nudge |
| `endgame.requested_turn` | Audit trail |
| `meta.synopsis` | Story-picker UI copy, not narration context |

---

## Suggested sequencing

| Order | Items | Rationale |
|---|---|---|
| 1 | CR-02, CR-14, CR-15, CR-17 | Trivial, no schema change, land immediately |
| 2 | CR-03, CR-05 | Highest coherence gain per token; prompt-only changes |
| 3 | CR-01 | Largest single correctness win; touches the state-update contract |
| 4 | CR-04, CR-07 | Schema additions; require template edits across both stories |
| 5 | CR-08, CR-09, CR-13, CR-12, CR-11 | Pacing-layer calibration |
| 6 | CR-10 | Pure deletion; land before CR-06 to shrink the surface first |
| 7 | CR-06 | Largest change; touches both prompts, the eviction pass, and template content |
| 8 | CR-16, CR-18 | Follow-ups and documentation |

## Estimated prompt cost

| Change | Added input tokens/turn | Cacheable? |
|---|---|---|
| CR-03 revealed memories | 0–250 (grows to cap) | Partially — stable between reveals |
| CR-04 setting + factions | ~120 | Yes — stable for the playthrough |
| CR-04 here/adjacent | ~60 | No — changes on movement |
| CR-05 main thread + act | ~50 | Yes — stable within an act |
| CR-06 character roster | ~100–200, bounded by `RELATIONSHIPS_LIMIT` | Partially — invalidated on score change |
| CR-06 known names (state pass) | ~30 | No |
| CR-14 POV | ~4 | Yes |
| CR-08 subplot progress | ~40 (state pass only) | No |
| CR-09/11/12/13 nudge additions | ~60, every 8th turn | No |

Against a current ~8–12k input-token profile this is a 3–6% increase, weighted toward
the stable prefix. With prompt caching on the narration provider the marginal cost
should be substantially below that.
