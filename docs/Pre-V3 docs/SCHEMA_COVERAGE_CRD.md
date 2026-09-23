# Change Requirements: Story Schema Prompt Coverage

**Scope:** `backend/story_engine.py` prompt builders, with knock-on changes to
`stories/*/template.json`, `backend/plot_manager.py`, and `backend/app.py`.

**Problem statement:** An audit compared every field in the story schema against
all six prompt builders (`build_system_prompt`, `generate_pacing_nudge`,
`update_progress_from_turn`, `generate_new_subplot`, `check_and_advance_act`,
`handle_end_story_request`). The audit found that roughly a third of the schema
never reaches any prompt. Some of this gap is deliberate and documented
(`history_log.full_transcript`, `player.flags_archive`). The rest falls into three
groups: authored worldbuilding that sits inert, steering machinery that silently
no-ops, and, in two cases, state that the prompt reads but the engine never
updates. That last group is actively harmful.

**Priority key**

| | Meaning |
|---|---|
| **P0** | Causes incorrect narration today. Fix first. |
| **P1** | Authored content or plot spine is missing from the prompt. This has a coherence cost. |
| **P2** | Calibration gaps and dead features. |
| **P3** | Cleanup, documentation, low-value fields. |

**Global constraints that apply to every change below**

- **G-1 — Backward compatibility.** A save clones its template once, at creation.
  It never re-reads the template after that. Every new schema field must be read
  with `.get()` or `setdefault()`. If a field is missing, it must degrade to empty,
  not raise an error. Existing saves must keep loading.
- **G-2 — Optional, not required.** Any new template field must be optional. A
  story that omits the field must get no prompt section at all, not an empty
  header. This follows the same pattern as the existing `stats_str` or
  `creation_str` conditionals.
- **G-3 — Cache-friendly ordering.** New stable content, such as the world summary
  or the main thread, must go near the *top* of the assembled prompt. It must sit
  above `STORY SO FAR` and `RECENT EXCHANGES`, inside a stable prefix for prompt
  caching. Volatile content must stay in its current position.
- **G-4 — Test coverage.** Every change must have a check in `test/`. Each check
  must use the existing monkeypatched `call_llm` or `call_llm_json` stubs
  (`test/_llm_stubs.py`). The tests must not make live API calls.
- **G-5 — Token budget.** The narration prompt is already input-heavy (about
  8,000–12,000 input tokens, compared to about 650 output tokens). The combined
  input added by P0 and P1 must stay under about 500 tokens per turn. That added
  input must also be mostly cacheable.

---

## P0 — Incorrect narration today

### CR-01 — `plot.current_scene` is read every turn but never written

**Current behaviour**
`build_system_prompt` emits `CURRENT SCENE ({scene['location']}): {scene['summary']}`
on every narration call. No code writes `plot.current_scene` anywhere in the
repo: not `story_engine.py`, not `app.py`, not `plot_manager.py`, and not
`subplot_manager.py`. The system clones the value from the template when it
creates the save. The value then stays frozen for the life of the playthrough.

**Impact**
By turn 60 the prompt still asserts the protagonist has just stepped off the ferry
at `loc_dock`. This is a stale, authoritative claim placed late in the prompt,
where it carries high recency weight. It directly contradicts `RECENT EXCHANGES`.
On a smaller narration model, this is a significant source of drift. The problem
will get worse if a local fiction fine-tune replaces the frontier model for the
narration pass.

No code writes or reads `current_scene.present_npcs` either.

**Required behaviour**
Extend the `update_progress_from_turn` diff schema with a scene block. This schema
already runs on an LLM call every turn, so the change needs no new call:

```json
"scene_update": {
  "location": "<location id from the known list, or the same id if unchanged>",
  "summary": "<1-2 sentences: where the protagonist is now and the immediate situation, as of the end of this turn>",
  "present_npcs": ["<character name>", "..."]
}
```

Apply rules:
- `location` must match a key in `world.locations` when that dict is non-empty.
  Otherwise, the system must reject the new value and keep the previous one. When
  `world.locations` is empty, the system must accept any string.
- `summary` replaces the previous value completely. If `summary` is empty or
  missing, the system must keep the previous value.
- `present_npcs` replaces the previous list.
- A missing `scene_update` key leaves `current_scene` unchanged. This means the
  turn did not move the scene.

Supply the current scene and the valid location ids to the state-update prompt so
the model has something to diff against.

**Acceptance criteria**
- After N turns, `state["plot"]["current_scene"]["summary"]` reflects the most
  recent narration, not the template seed.
- An invalid or unknown `location` id leaves the previous location in place.
- A diff with no `scene_update` key is a no-op, not a crash.
- `present_npcs` round-trips.

**Interim fallback (if CR-01 is deferred):** drop the `CURRENT SCENE` line from
`build_system_prompt` entirely. A missing scene line is strictly better than a
lying one.

---

### CR-02 — Scene location is emitted as a raw database key

**Current behaviour**
The prompt interpolates the raw `world.locations` key directly:
`CURRENT SCENE (loc_dock): ...`.

**Required behaviour**
Resolve the location through `world.locations[id]`. Emit the human-readable
`name`. If the key is absent (for example, in older saves or ad-hoc locations from
CR-01), fall back to the raw id.

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
No prompt ever interpolates `frag["content"]`. The only downstream
consumer of the reveal state is `check_and_advance_act`, which uses a *count* of
revealed fragments.

**Impact**
The mechanic can mark a fragment as revealed. But no prompt ever tells the
narrator what the fragment says. As a result, the narrator can never surface the
fragment's content. Fragments are write-once and unreadable. For `new_babel`
specifically, the entire fragmented-memory mechanic is currently inert.

**Required behaviour**
Add a revealed-fragments block to `build_system_prompt`. Add this block only when
at least one fragment is revealed (per G-2):

```
REVEALED MEMORIES (the protagonist already knows these; reference them naturally,
do not re-reveal them as though they were new):
- <content>
- <content>
```

Keep the existing split intact. The state-update pass continues to receive only
*unrevealed* `trigger` strings. The narration pass receives only *revealed*
`content` strings. Neither pass sees the other half.

**Bounding:** revealed fragments accumulate for the whole game. Cap the block at
the most recently revealed N fragments. A suggested value is
`MEMORY_FRAGMENT_PROMPT_LIMIT = 12`. This keeps the prompt bounded, consistent
with `SUBPLOT_TITLE_HISTORY_LIMIT`. This requires a `revealed_turn` field that the
engine writes at reveal time, so it can order the fragments. On older saves that
lack this field, the system must fall back to template order.

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
`world.rules` is the only part of `world` that reaches any prompt. No prompt
builder ever uses `world.setting_summary`, `world.locations` (names,
descriptions, and the `connected_to` adjacency graph), or `world.factions`
(name, goals, `relationship_to_player`).

**Impact**
The densest authored worldbuilding in the template is inert. The model cannot
know what is adjacent to the current location. It cannot know what any faction
wants. It also cannot know what the setting actually is, beyond what it can infer
from `rules` and the running summary.

**Required behaviour**
Add a `WORLD` block to `build_system_prompt`. Place the block above
`STORY SO FAR`, per G-3:

```
SETTING: {world.setting_summary}

HERE: {current location name} - {description}
ADJACENT: {name of each id in connected_to, comma-separated}

FACTIONS:
- {name}: {goals} (toward the player: {relationship_to_player})
```

Scoping rules:
- Emit **only** the current location and its direct `connected_to` neighbours, not
  the full location table. This keeps the block bounded on a story with 40
  locations. It also gives the model exactly the movement affordances it needs.
- Emit all factions. The count is small and authored. Faction posture is also
  globally relevant.
- Each sub-block is independently conditional (G-2): a story with no `factions`
  gets no `FACTIONS:` header.

**Acceptance criteria**
- `setting_summary` appears in the narration prompt.
- The current location's name and description appear. Unrelated locations do not
  appear.
- Every id in the current location's `connected_to` appears by `name`.
- The system silently skips a dangling `connected_to` id. It does not render the
  id as a raw key or raise an error.
- `stories/example`'s four locations and one faction all render correctly.
- A template with `"locations": {}` and `"factions": {}` produces no world sub-blocks.

**Note:** `setting_summary` and `factions` stay stable for the whole playthrough.
They sit in the cacheable prefix. `HERE` and `ADJACENT` are volatile. The engine
must position them so they do not invalidate the cached prefix. Place them with
the scene line rather than with `SETTING`.

---

### CR-05 — Main thread is absent from the narration prompt

**Current behaviour**
`plot.main_thread.title` and `.description` reach `generate_new_subplot` and
`handle_end_story_request`, but never `build_system_prompt`. The narrator learns
about the story's spine only indirectly, through `generate_pacing_nudge`'s act
description. That description fires once every `pacing_nudge_frequency` (8) turns.

**Impact**
Seven turns out of eight, the narrator has no statement of what the story is about
beyond the compressed summary.

**Required behaviour**
Add to `build_system_prompt`, above `STORY SO FAR`:

```
MAIN THREAD: {main_thread.title} - {main_thread.description}
CURRENT ACT {n}: {act.title} - {act.description}
```

Resolve the current act by `act_number == main_thread.current_act`. This matches
`generate_pacing_nudge`'s lookup. Do not use `acts[current_act - 1]`, the lookup
`generate_new_subplot` uses. That lookup breaks if act numbering is ever
non-contiguous (see CR-16).

This overlaps with the pacing nudge's `PACING: Currently in Act N` line. Keep both
lines. The nudge line's role is to trigger a pacing *change*. The new line's role
is to provide standing context. Alternatively, the implementer may trim the
nudge's act line to avoid the duplication. That choice is up to the implementer.
The implementer must not remove both lines.

**Cost:** ~40–60 tokens, stable across many turns except at act transitions.

**Acceptance criteria**
- Main thread title and description appear in every narration prompt.
- The current act shown matches `main_thread.current_act` after an act advance.
- A story mid-endgame shows the finale act.

---

### CR-06 — `characters` registry is disconnected from `player.relationships`

**Current behaviour**
No code path ever reads or writes the top-level `characters` dict. Meanwhile,
`player.relationships` accumulates character names that the state-update model
invents. Each name is a free-text key, and the dict has an eviction policy. The
engine ends up with two disconnected registries for the same concept.

**Impact**
Authored NPCs in a template are invisible to the engine. Relationship scores
attach to LLM-chosen name strings with no canonical identity. As a result, the
engine treats "Mrs. Abbott" and "the innkeeper" as different characters.

**Required behaviour**
`characters` becomes the canonical roster. `player.relationships` stays as the score
store, keyed by the roster's canonical name.

**Entry shape.** Authored entries may carry more fields. The engine requires only
these fields:

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

Key each entry on the canonical display name, not a synthetic id. This way, the
LLM-facing name, the `relationships` key, and the roster key are all the same
string. No mapping layer is needed.

**Narration prompt.** Add a roster block above `STORY SO FAR`. Add this block only
when the dict is non-empty (G-2):

```
KNOWN CHARACTERS (use these exact names; standing is -100 hostile to +100 devoted):
- Mrs. Abbott (+35): Innkeeper at the Harborlight. Warm, and the first to slip.
```

Omit the score for a character with no `relationships` entry yet. Do not show a
score of 0 for that character. An unmet authored character and a met-but-neutral
character are different states.

**State-update prompt.** Add the roster of known names to `update_progress_from_turn`.
Instruct the model to reuse an existing name verbatim when the character is already
known. The model must introduce a new name only for a genuinely new character:

```
KNOWN CHARACTER NAMES (reuse these exactly for anyone already known; only introduce a
new name for a character who has not appeared before): ["Mrs. Abbott", ...]
```

**Roster growth.** When a `relationship_changes` entry names someone who is not in
`characters`, append a stub:
`{name, description: "", first_seen_turn: turn_count, authored: false}`. This keeps
the roster in step with the story. Do not ask the model for a description. The name
is enough. A second field would invite drift.

**Bounding and eviction.** `characters` and `relationships` must stay in step.
Extend the existing `RELATIONSHIPS_LIMIT` eviction (closest-to-neutral first) to
also delete the matching `characters` entry, in the same pass. **Never evict an
`authored: true` entry.** Authored characters are part of the story's fixed
content. They must survive regardless of score. Only LLM-introduced stubs are
evictable. If pins fill the budget, the roster may exceed the limit. This is the
same tolerance given to pinned flags in `archive_stale_flags`.

The same limit bounds the narration roster block, so the engine needs no separate
cap.

**Acceptance criteria**
- Authored characters from a template appear in the narration prompt with description
  and, where a score exists, standing.
- A character with no `relationships` entry renders without a score, not as `(0)`.
- The state-update prompt lists known character names.
- A `relationship_changes` entry for an unknown name creates a `characters` stub with
  `authored: false` and the correct `first_seen_turn`.
- Eviction removes the `characters` entry and the `relationships` entry together.
- An `authored: true` character that sits at score 0 is never evicted, even when
  the roster is over budget.
- `stories/example`'s empty `characters: {}` produces no roster block. The existing
  `test_inventory_relationships.py` assertions still hold.
- A save that predates this change (with no `characters` key) loads and
  self-populates.

**Content follow-up (not blocking):** `stories/example/template.json` ships
`"characters": {}` while its prose and subplots reference an innkeeper. Populate
the `characters` dict as part of this change, so the feature has a working
reference story.

---

### CR-07 — "the Architect" is hardcoded in a multi-story engine

**Current behaviour**
Two prompts in `story_engine.py` name a story-specific entity:

- `update_progress_from_turn`: `"entity_interaction": <true if the Architect appeared or acted this turn, else false>`
- `check_and_advance_act`: `ARCHITECT ENCOUNTERS: {plot['entity_interaction_count']}`

**Impact**
When someone runs `stories/example` (a cozy mystery with no such entity), the
engine asks the state model about a character that does not exist. It also reports
encounters with that character to the pacing director. This is a correctness bug
for every story except `new_babel`. It also contradicts the engine's stated
multi-story design.

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
  state-update schema entirely. Also omit the encounters line from the act-check
  prompt. This is the same conditional pattern used for `stats` or `stat_changes`.
- When present, interpolate `name` into both prompts.
- The team may also add `entity_interaction_count` to the narration prompt, so the
  narrator can pace the entity's appearances (see CR-16).

**Acceptance criteria**
- `stories/example` produces a state-update prompt with no `entity_interaction`
  field. It also produces an act-check prompt with no encounters line.
- A template with `tracked_entity` produces both fields and interpolates its
  `name` into them.
- An older save without the key does not crash.

---

## P2 — Calibration gaps and dead features

### CR-08 — Subplot progress is not fed to the scoring model

**Current behaviour**
`update_progress_from_turn` sends `ACTIVE SUBPLOTS: {id: title}` and asks for
`"subplot_progress": {"<subplot_id>": <integer 0-100, progress made this turn>}`.
Neither `progress` nor `completion_threshold` is in the prompt.

**Impact**
The prompt asks the model for a delta. It never tells the model where the
subplot currently stands. As a result, the model cannot distinguish "this beat should
finish the thread" from "this only nudges it." When a subplot reaches
`completion_threshold`, the engine marks it complete, generates a replacement
subplot, and evaluates whether to advance the act. Any miscalibration here
propagates into the whole pacing layer.

**Required behaviour**
Include the current progress, threshold, and description for each active
subplot:

```
ACTIVE SUBPLOTS (id: title - description [progress/threshold]):
  subplot_001: Settling In - Get to know the Harborlight Inn... [40/100]
```

State explicitly, in the instruction, that the delta is *added* to the current
value. Also state that the subplot completes once its progress reaches the
threshold. This lets the model deliberately close a subplot out.

**Acceptance criteria**
- Progress and threshold appear per active subplot.
- Clamping behaviour is unchanged.
- Existing `test_subplot_act_endgame.py` still passes.

---

### CR-09 — `completion_signals` never reach the narrator

**Current behaviour**
The engine feeds `acts[].completion_signals` only to `check_and_advance_act`, which
judges whether the act has resolved. No prompt ever tells the narrator what would
resolve the act.

**Impact**
The pacing director grades against criteria the narrator has never seen. The act can
only advance by accident.

**Required behaviour**
Include the current act's `completion_signals` in `generate_pacing_nudge`, not in
every turn's prompt. This is directional steering, exactly what the nudge is for:

```
THIS ACT RESOLVES WHEN: {', '.join(completion_signals)}
```

Add this block only when the list is non-empty. Note that LLM-generated acts from
`check_and_advance_act` currently get `"completion_signals": []`. As a result, this
block will be absent for every act after Act 1.

**Related:** the team could change `check_and_advance_act` so it also generates
`completion_signals` for the act it creates. Then the block would not silently
disappear after Act 1. If this addition grows the current change too large, track
it as a follow-up instead.

---

### CR-10 — `plot.alternate_threads` is entirely write-only

**Current behaviour**
`plot_manager.create_alternate_thread` and `toggle_thread_focus` write
`alternate_threads`, `main_thread.is_primary_focus`, and per-thread `active`. No
prompt anywhere reads any of them. The CLI and `frontend/plot_manager.html` both
expose these two actions.

**Impact**
A user creates an alternate thread, switches focus to it, and sees confirmation
output. The story remains completely unaffected. It is worse to offer a UI
affordance that silently does nothing than to offer none at all.

**Required behaviour**
Remove the feature. `pivot` already redirects the story end-to-end. Alternate
threads are a second, non-functional path to the same goal. No code ever gave
their `stages` or `current_stage` fields a meaning: the code writes `[]` and `1`
to these fields but never reads them.

Delete:

| Location | Remove |
|---|---|
| `backend/plot_manager.py` | `create_alternate_thread()`, `toggle_thread_focus()` |
| `backend/plot_manager.py` | the `create-alt` and `focus` command branches in `main()`, and their two usage lines in the help text |
| `backend/plot_manager.py` | the alternate-threads section of `show_plot_overview()`, and the `Primary Focus:` and `Can Pivot:` lines |
| `backend/app.py` | the `create-alt` and `focus` branches in `plot_manager_view` |
| `frontend/plot_manager.html` | the "Create Alternate Thread" and "Switch Focus" cards |
| `stories/*/template.json` | `plot.alternate_threads`, `main_thread.is_primary_focus`, `main_thread.can_pivot` |
| `docs/Narrative_Engine_Spec.md`, `README.md` | the alternate-thread and switch-focus bullets under mid-adventure steering |

**Backward compatibility.** Per G-1, an existing save still carries these keys.
Nothing reads the keys after this change, so the engine needs no migration. The
keys simply become inert residue. Do not add code to strip the keys. A save that
quietly carries three unused keys is cheaper than a migration path.

**Guard against reintroduction.** `STEER_WARNING` in `story_engine.py` enumerates the
available `plot_manager` commands and currently lists `'create-alt'` and `'focus'`.
Update `STEER_WARNING`, or the CLI will keep advertising commands that no longer
exist.

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
`thread_steering.player_driven_goals`. The CLI exposes this action as `add-goal`.
The web UI exposes it as "Record Player Goal". Nothing reads
`thread_steering.player_driven_goals`.

**Required behaviour**
There are two options. Option one: feed active goals into `generate_pacing_nudge`:

```
PLAYER GOALS: {', '.join(g['description'] for g in goals if g.get('active'))}
```

Option two: remove the command, the UI card, and the field entirely.
Recommendation: wire the goals in. Unlike CR-10, this change is a one-line prompt
addition, and the feature is meaningful. The pacing nudge is the natural home for
player goals. If the team wires the goals in, add a way to mark a goal inactive.
Otherwise, the list will grow unbounded.

---

## P3 — Cleanup and low-value fields

### CR-12 — `thread_steering.emerging_themes` reaches only subplot generation

The engine currently feeds this field only to `generate_new_subplot`. As a result,
a noted theme influences new threads but never the prose. **Required:** add it to
`generate_pacing_nudge`, alongside CR-11's goals. Keep it to one line, and add it
only when the field is non-empty.

### CR-13 — `subplots[].ties_to_main_plot` is never fed to narration

The engine writes this field at generation, authors populate it in templates, and
`subplot_manager.html` renders it. No prompt ever receives it. **Required:**
include it with the primary active subplot in `generate_pacing_nudge`, where the
subplot's title and description already go. This is the field that tells the
narrator *why* the subplot matters.

### CR-14 — `meta.pov` is declared but never enforced

`"pov": "second-person"` is in every template. No prompt states POV. The correct
point of view currently holds only because the hand-authored opening scene
establishes the voice, and `RECENT EXCHANGES` then sustains that voice.
**Required:** add POV to the identity header:
`TITLE: ... | GENRE: ... | TONE: ... | POV: {meta['pov']}`. Add it only when the
key exists. This costs roughly four tokens and is fully cacheable. It also removes
a silent dependency on the opening scene's voice, a dependency that will cause
problems when a fine-tune drifts.

### CR-15 — `pacing.ready_for_main_plot_advancement` is a dead field

No code ever reads or writes this field, but it is present in every template.
**Required:** remove it from templates. No code change is needed.

### CR-16 — `plot.entity_interaction_count` is never fed to narration

The engine increments this field every turn the entity appears. Only
`check_and_advance_act` reads it. **Required (conditional on CR-07):** when a
template configures a `tracked_entity`, include the count in the narration prompt
so the narrator can pace appearances against prior contact. The narrator should not treat
each appearance as the first. This ties into the entity budget mechanism.

### CR-17 — Inconsistent current-act lookup

`generate_new_subplot` uses `main_thread["acts"][main_thread["current_act"] - 1]`
(positional). `generate_pacing_nudge` and `check_and_advance_act` use
`next(act for act in acts if act["act_number"] == current_act)` (by key). These two
approaches diverge if act numbering ever becomes non-contiguous. A `position`
argument passed to `plot_manager.add_act` can cause that. **Required:** standardise
on the by-key lookup everywhere. Extract a `_current_act(state)` helper.

### CR-18 — Document the intentional write-only fields

The following fields are write-only *by design*. `docs/Narrative_Engine_Spec.md`
must mark them explicitly as such, so a future audit does not re-flag them:

| Field | Purpose |
|---|---|
| `history_log.full_transcript` | Disk-only UI scrollback. Deliberately never prompted (`test_full_transcript.py` asserts this) |
| `player.flags_archive` | Retired flags. Kept for debugging |
| `player.flags_meta` | Eviction bookkeeping |
| `main_thread.act_history` | Audit trail |
| `thread_steering.pivot_history` and `last_pivot_turn` | Audit trail |
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
| 2 | CR-03, CR-05 | Highest coherence gain per token, prompt-only changes |
| 3 | CR-01 | Largest single correctness win, touches the state-update contract |
| 4 | CR-04, CR-07 | Schema additions, require template edits across both stories |
| 5 | CR-08, CR-09, CR-13, CR-12, CR-11 | Pacing-layer calibration |
| 6 | CR-10 | Pure deletion, land before CR-06 to shrink the surface first |
| 7 | CR-06 | Largest change, touches both prompts, the eviction pass, and template content |
| 8 | CR-16, CR-18 | Follow-ups and documentation |

## Estimated prompt cost

| Change | Added input tokens/turn | Cacheable? |
|---|---|---|
| CR-03 revealed memories | 0–250 (grows to cap) | Partially, stable between reveals |
| CR-04 setting + factions | ~120 | Yes, stable for the playthrough |
| CR-04 here + adjacent | ~60 | No, changes on movement |
| CR-05 main thread + act | ~50 | Yes, stable within an act |
| CR-06 character roster | ~100–200, bounded by `RELATIONSHIPS_LIMIT` | Partially, invalidated on score change |
| CR-06 known names (state pass) | ~30 | No |
| CR-14 POV | ~4 | Yes |
| CR-08 subplot progress | ~40 (state pass only) | No |
| CR-09/11/12/13 nudge additions | ~60, every 8th turn | No |

Against a current input-token profile of about 8,000–12,000, this is a 3–6%
increase, weighted toward the stable prefix. With prompt caching on the narration
provider, the marginal cost should be substantially lower.
