# Phased Implementation Plan

**Scope:** Three approved specifications for the cyoa-app / Palimpsest engine.

| Document | Status | What it covers |
|---|---|---|
| `SCHEMA_COVERAGE_CRD.md` | Approved, CR-06 Option A, CR-10 Option B | 18 change requirements patching v1's prompt coverage gaps |
| `SCHEMA_V2_SPEC.md` | Approved, all open questions resolved | Story/state split, optional mechanics modules, genre-neutral schema |
| `Narrative_Pacing_Loop_Spec_v4.md` | Approved, pending §0 validation gates | Beat classification, counter-based correction, progression ledger |

**Principle:** work flows from "fix what's broken on v1" through "restructure the
foundation" to "build new capabilities on top." No phase assumes a later phase will
happen — if the project stops at phase 2 the engine is strictly better than today.

**Test constraint (global):** every phase gates on `test/` passing against the existing
`test/_llm_stubs.py` monkeypatch harness. No live API calls in CI. New tests are
written alongside code, not after.

---

## Dependency graph

```
Phase 0 ──────────────────────────────────────────────────────────
  Pacing validation gates (§0.1, §0.2, §0.3)    ← independent, run anytime
  Can run in parallel with everything through phase 3.
  MUST complete before phase 6.

Phase 1 ──────────────────────────────────────────────────────────
  v1 quick fixes: CR-02, CR-05, CR-08, CR-10, CR-14, CR-15, CR-17
  All independent of each other. No schema change. Existing tests green.

Phase 2 ──────────────────────────────────────────────────────────
  v1 prompt fixes: CR-03, CR-01, CR-04, CR-07
  ├── CR-03 (revelations) ──────────────────→ unblocks pacing §12
  ├── CR-01 (scene writer + threat_present) → unblocks pacing §10
  └── CR-04, CR-07 require template edits to both stories

Phase 3 ──────────────────────────────────────────────────────────
  Schema v2 core: state_store split, FrozenDict, migrator
  Depends on: phase 1 (surface is smaller after CR-10 deletion)

Phase 4 ──────────────────────────────────────────────────────────
  Prompt assembly refactor: SECTIONS pattern
  Depends on: phase 3 (needs to know which half each section reads from)
  Absorbs: CR-09, CR-11, CR-12, CR-13 (nudge additions become section builders)

Phase 5 ──────────────────────────────────────────────────────────
  Mechanics modules: narration, stats, relationships, revelations,
  tracked_entity, characters, failure_conditions
  Depends on: phase 4 (each module is a section builder)
  Absorbs: CR-03, CR-04, CR-06, CR-07, CR-14, CR-16 at their v2 paths

Phase 6 ──────────────────────────────────────────────────────────
  Pacing loop: beat classifier, counters, directives, progression ledger
  Depends on: phase 4 (SECTIONS), phase 5 (mechanics module pattern)
  Depends on: phase 0 (validation gates must pass)

Phase 7 ──────────────────────────────────────────────────────────
  Conformance, documentation, cleanup
  Depends on: phases 5 + 6
```

---

## Phase 0 — Pacing validation gates

**Timeline:** Start immediately. Runs in parallel with phases 1–3. Must complete before
phase 6 begins. If any gate fails, the pacing loop design changes before implementation;
everything else proceeds unaffected.

**Source:** Pacing spec §0

| Step | Work | Gate |
|---|---|---|
| 0.1 | Full-corpus baseline: classify every scene in `the_attention_economy.txt` by beat type | True release-beat rate and clustering known |
| 0.2 | Classifier agreement: hand-label ~30 scenes, run the classifier prompt, measure agreement | ≥70% overall; if `lull`/`resolution` or `crisis`/`escalation` pairs disagree, collapse to one beat per side before proceeding |
| 0.3 | Cross-genre vocabulary: repeat 0.2 against the `regency.json` fixture's vocabulary | If the classifier can only separate beats in violence-shaped stories, the module ships thriller-only with the limitation documented |

**Failure modes and what they change:**

- 0.2 below 70% → stop. Fix the classifier prompt. A pacing system on noisy
  classification fires at random and is worse than nothing. Re-run 0.2. Do not proceed
  to phase 6 without passing.
- 0.2 shows poor `lull`/`resolution` agreement → collapse to one releasing beat. Both
  feed the counter identically; intensity scoring (pacing spec §6.1) may already capture
  the distinction. Update New Babel's vocabulary from four beats to three.
- 0.3 fails → `example`'s pacing module (Appendix A) is removed, the pacing loop ships
  as New-Babel-specific rather than generic, and the conformance fixtures in phase 7
  don't include pacing. Everything else in the plan is unaffected.

**Deliverables:** a short report per gate with the numbers and the proceed/stop decision.
These are measurement tasks, not code — appropriate for a notebook or a markdown file in
`docs/`.

---

## Phase 1 — v1 quick fixes

**Timeline:** First code phase. Each item is independent and can land in any order, in a
single commit or separately. All are small, touch no schema, and fix live defects or
latent bugs on the current codebase.

**Source:** CRD suggested-sequencing orders 1 and 2, v2 spec §8 pre-work

**Gate:** existing `test/` suite green after each change.

### 1.1 — CR-02: resolve location key to human name

**Files:** `story_engine.py` (`build_system_prompt`)
**Size:** ~5 lines

Resolve `current_scene.location` through `world.locations[id]["name"]`, falling back to
the raw id if the key is absent. Applies to the `CURRENT SCENE` line and, once CR-04
lands, to the `HERE:` / `ADJACENT:` block.

**Test:** assert a known id renders as its `name`; an unknown id renders as the raw
string without raising.

### 1.2 — CR-05: main thread title and current act in every narration prompt

**Files:** `story_engine.py` (`build_system_prompt`)
**Size:** ~15 lines

Add above `STORY SO FAR`:

```
MAIN THREAD: {title} - {description}
CURRENT ACT {n}: {act title} - {act description}
```

Use the by-key lookup (`act_number == current_act`), not the positional one — see 1.5.

**Test:** assert both lines present; assert the act shown updates after an act advance.

**Cost:** ~50 tokens, stable within an act, cacheable.

### 1.3 — CR-08: subplot progress and threshold in state-update prompt

**Files:** `story_engine.py` (`update_progress_from_turn`)
**Size:** ~10 lines

Change the active-subplots block from `{id: title}` to include description, progress,
and threshold:

```
ACTIVE SUBPLOTS (id: title - description [progress/threshold]):
  subplot_001: Settling In - Get to know... [40/100]
```

State explicitly in the instruction that the delta is added to the current value and
that reaching the threshold completes the thread.

**Test:** assert progress and threshold appear; existing
`test_subplot_act_endgame.py` still passes.

### 1.4 — CR-10: remove alternate threads

**Files:** `plot_manager.py`, `app.py`, `frontend/plot_manager.html`,
`stories/*/template.json`, `docs/Narrative_Engine_Spec.md`, `story_engine.py`
(`STEER_WARNING`)
**Size:** pure deletion, ~150 lines removed

Delete `create_alternate_thread()`, `toggle_thread_focus()`, the CLI commands, the UI
cards, the template fields (`alternate_threads`, `is_primary_focus`, `can_pivot`), and
the doc bullets. Update `STEER_WARNING` so the CLI stops advertising removed commands.

Existing saves carrying these keys are left as inert residue — no migration.

**Test:** `plot_manager.py create-alt ...` reports unknown command; the Plot Manager page
renders without the two cards; `steer` output no longer lists the removed commands.

### 1.5 — CR-17: standardise current-act lookup

**Files:** `story_engine.py` (`generate_new_subplot` and any other positional lookups)
**Size:** ~10 lines

Extract a `_current_act(state)` helper using the by-key lookup
(`act_number == current_act`), replacing the positional
`acts[current_act - 1]` in `generate_new_subplot` and any other caller. The by-key
lookup is already used by `generate_pacing_nudge` and `check_and_advance_act`.

**Test:** assert the helper returns the correct act when numbering is non-contiguous
(simulate `plot_manager.add_act` with a `position` argument).

### 1.6 — CR-14: state POV in the narration prompt

**Files:** `story_engine.py` (`build_system_prompt`)
**Size:** ~3 lines

Add `POV: {meta.get('pov', 'second-person')}` to the identity header line, conditional
on the key existing.

**Test:** assert POV appears in prompt when `meta.pov` is set; absent key produces no
POV token.

**Cost:** ~4 tokens, fully cacheable.

### 1.7 — CR-15: remove dead `pacing.ready_for_main_plot_advancement`

**Files:** `stories/*/template.json`
**Size:** delete one key per template

Never read, never written. Remove from both templates. No code change.

**Test:** existing tests still pass.

### Phase 1 summary

| Item | Risk | Effort |
|---|---|---|
| 1.1 CR-02 | None | Trivial |
| 1.2 CR-05 | None | Small |
| 1.3 CR-08 | Low — changes state-update prompt wording | Small |
| 1.4 CR-10 | Low — pure deletion, but touches many files | Medium |
| 1.5 CR-17 | Low | Trivial |
| 1.6 CR-14 | None | Trivial |
| 1.7 CR-15 | None | Trivial |

**Cumulative prompt cost after phase 1:** ~100 tokens/turn added, heavily cacheable.
No schema changes. Both stories play identically modulo the fixed defects.

---

## Phase 2 — v1 prompt fixes (pre-split)

**Timeline:** After phase 1. These touch the state-update contract and both templates,
so they're larger than phase 1 and benefit from landing while the codebase is still v1's
single-dict shape — before the split adds a second dimension to every call site.

**Source:** CRD orders 2–4

**Gate:** existing tests green; new tests for each CR.

### 2.1 — CR-03: revealed revelation content in the narration prompt

**Files:** `story_engine.py` (`build_system_prompt`, `update_progress_from_turn`)
**Size:** ~30 lines

Add a `REVEALED MEMORIES` block to the narration prompt, conditional on at least one
revealed fragment. Only revealed `content` strings go to narration; only unrevealed
`trigger` strings go to the state-update prompt. Neither pass sees the other half.

Add `revealed_turn` to fragments at the point of reveal, for recency ordering. Cap the
block at `MEMORY_FRAGMENT_PROMPT_LIMIT = 12`, ordered by `revealed_turn` descending.
Older saves missing `revealed_turn` fall back to template order.

**Test:** revealed fragment content present in narration prompt; unrevealed content
absent; zero fragments produces no header; cap enforced.

**Why now:** hard prerequisite for pacing §12 (reveal placement). Until this lands, the
reveal queue in the pacing loop would schedule reveals into a pipe that isn't connected.

### 2.2 — CR-01: write `current_scene` on every turn

**Files:** `story_engine.py` (`update_progress_from_turn`, `build_system_prompt`)
**Size:** ~40 lines

Extend the state-update diff schema with:

```json
"scene_update": {
  "location": "<location id or free text>",
  "summary": "<1-2 sentences>",
  "present_npcs": ["..."],
  "threat_present": false
}
```

Application rules: `location` must match a `world.locations` key when that dict is
non-empty, otherwise accept any string; empty/missing `summary` keeps the previous
value; missing `scene_update` key is a no-op.

`threat_present` is added here rather than as a separate field because the scene writer
is already making the call. It costs nothing extra and unblocks pacing §10.

Feed the current scene and valid location ids to the state-update prompt so the model
has something to diff against.

**Interim behaviour until this lands:** if deferred, drop the `CURRENT SCENE` line from
`build_system_prompt` entirely. A missing scene line is strictly better than a lying one.

**Test:** after N turns, `state["plot"]["current_scene"]["summary"]` reflects the
narration, not the template seed; invalid location id leaves previous in place; missing
`scene_update` key is a no-op; `threat_present` round-trips.

**Why now:** hardest single correctness win in the CRD. Also unblocks pacing §10
(`threat_present` eligibility predicate).

### 2.3 — CR-04: world context block

**Files:** `story_engine.py` (`build_system_prompt`), `stories/*/template.json`
**Size:** ~40 lines

Add above `STORY SO FAR`:

```
SETTING: {world.setting_summary}

HERE: {current location name} - {description}
ADJACENT: {neighbour names, comma-separated}

FACTIONS:
- {name}: {goals} (toward the player: {relationship_to_player})
```

Scoping: only the current location and its direct `connected_to` neighbours. Each
sub-block is independently conditional — a story with no `factions` gets no header, a
story with no `locations` gets no `HERE:` block.

Place `SETTING:` and `FACTIONS:` in the stable prefix (cacheable); `HERE:` / `ADJACENT:`
with the scene line (volatile).

Resolve `connected_to` ids to names; dangling ids are skipped silently.

**Test:** `setting_summary` present; current location's name and description present;
unrelated locations absent; dangling `connected_to` id skipped; empty `locations` and
`factions` produce no sub-blocks.

**Cost:** ~180 tokens, ~120 of which are cacheable.

### 2.4 — CR-07: parameterize "the Architect"

**Files:** `story_engine.py` (`update_progress_from_turn`, `check_and_advance_act`),
`stories/new_babel/template.json`
**Size:** ~20 lines

Add to the template:

```json
"world": {
  "tracked_entity": {
    "name": "The Architect",
    "description": "..."
  }
}
```

When absent: omit `entity_interaction` from the state-update schema and the encounters
line from the act-check prompt. When present: interpolate `name` into both.

`stories/example` already has no such entity — confirm it produces clean prompts after
this change (currently it asks about "the Architect" in a cozy mystery).

**Test:** `example` produces no `entity_interaction` field and no encounters line;
`new_babel` produces both using its configured name; an older save without the key
doesn't crash.

### Phase 2 summary

| Item | Risk | Effort |
|---|---|---|
| 2.1 CR-03 | Medium — changes what the narrator sees | Medium |
| 2.2 CR-01 | Medium — extends the state-update contract | Medium |
| 2.3 CR-04 | Low — prompt-only, but template edits | Medium |
| 2.4 CR-07 | Low | Small |

**Cumulative prompt cost after phase 2:** ~530 tokens/turn added from phases 1+2,
~220 of which are cacheable. Against a ~10k input baseline that's a ~5% increase,
weighted toward the stable prefix.

**Pacing unblocked:** CR-03 (§12 reveal placement) and CR-01 (§10 threat_present
eligibility) are done. The pacing loop's prerequisites are met on the v1 codebase. Its
implementation still waits for phase 4's SECTIONS refactor.

---

## Phase 3 — Schema v2 core: the story/state split

**Timeline:** After phase 2. This is the highest-risk phase in the plan and the one most
likely to stall. Phase 2 was deliberately scoped to land the prompt fixes while the
codebase is still v1's single-dict shape, so that if phase 3 stalls, the engine is
already meaningfully better.

**Source:** Schema v2 spec §2, §4

**Gate:** round-trip test; a v1 save migrates and plays identically.

### 3.1 — `FrozenDict` and `StoryContext`

**Files:** new `backend/frozen_dict.py`, modified `backend/state_store.py`
**Size:** ~60 lines for the wrapper, ~80 for the load/save refactor

Implement `FrozenDict` — a `dict` subclass raising `TypeError` on `__setitem__`,
`__delitem__`, `update`, `pop`, `setdefault`, `clear`. Apply recursively to the
`story` half at load time.

`state_store.load_state()` returns `{"story": FrozenDict(template), "state": save_dict}`.

`save_state()` persists `ctx["state"]` only. Asserts nothing under `ctx["story"]` was
mutated during the request (belt-and-braces check independent of `FrozenDict` raising).

Records `story_slug` and `story_version` in the save.

**Test:** writing to `ctx["story"]` raises; writing to `ctx["state"]` succeeds;
round-trip load/save/load produces identical state; `story_version` persists.

### 3.2 — Call-site migration

**Files:** `story_engine.py`, `app.py`, `plot_manager.py`, `subplot_manager.py`, all
`test/` files
**Size:** mechanical — prepend `["story"]` or `["state"]` at every access site

This is the bulk of the churn. Each access becomes either `ctx["story"][...]` (authored
content) or `ctx["state"][...]` (runtime). Getting it wrong is a `KeyError` at best, or
a silent read of stale/absent data at worst.

**Strategy:** grep for every `state[` access in every file, classify each as authored or
runtime by consulting the schema v2 §3 (template) and §4 (save) definitions, and
prepend the correct key. There is no shortcut; this is line-by-line.

Recommended order: `story_engine.py` first (largest surface), then `app.py`, then the
managers, then tests. Run the test suite after each file.

### 3.3 — v1→v2 save migrator

**Files:** new `backend/migrate_v1.py`, modified `state_store.py`
**Size:** ~120 lines

One-shot converter: given a v1 save (flat dict, `schema_version` absent or 1), produce
a v2 save (runtime-only fields, `schema_version: 2`, `story_slug`, `story_version`).

Reconciliation at load per v2 spec §2.3: dangling `scene.location` kept and rendered
raw; generated subplots kept; removed template subplot left in-flight; removed
revelation silently dropped; removed stat kept.

Never fail a load over a reconciliation mismatch.

Invoked automatically by `load_state()` when `schema_version` is absent or < 2. Writes
the migrated save back to disk immediately so it's a one-time cost.

**Test:** migrate a real long-running v1 save (snapshot one from `data/saves/`); assert
it loads, plays one stubbed turn, and saves without error. Assert authored content is
not present in the migrated save file. Assert a save created by the migrator loads
identically to one created fresh from the same template.

### 3.4 — Template refactoring

**Files:** `stories/example/template.json`, `stories/new_babel/template.json`
**Size:** restructuring, not rewriting

Restructure both templates to the v2 shape (§3): `meta`, `narration`, `world`,
`protagonist`, `mechanics`, `character_creation`, `plot`. Move content to new locations:

- `player.origin.memory_fragments` → `mechanics.revelations`
- `player` seed fields → `protagonist`
- `meta.pov` → `narration.pov`
- `SCENE_WORD_MIN/MAX` → `narration.scene_length`
- `world.tracked_entity` (from CR-07, already landed in phase 2) → `mechanics.tracked_entity`
- Hardcoded style bullets from `build_system_prompt` → `narration.style` in each story's template

This is the point where the prose aesthetic leaves `story_engine.py` and becomes story
content. Move the current bullets to `new_babel`'s template verbatim; write a distinct
set for `example`.

**Test:** both templates load through the new `state_store`; `build_system_prompt`
produces output equivalent to pre-migration modulo the intended additions.

### Phase 3 summary

| Item | Risk | Effort |
|---|---|---|
| 3.1 FrozenDict + StoryContext | Low | Small |
| 3.2 Call-site migration | **High** — every file, silent wrong-half reads | **Large** |
| 3.3 Save migrator | Medium — reconciliation edge cases | Medium |
| 3.4 Template refactoring | Medium — two templates, many field moves | Medium |

**If phase 3 stalls:** the v2 spec §2.4 lighter fallback is available — keep a single
merged dict, re-merge authored sections from the template on every load, overwriting
them. Recovers the template-updates-reach-saves property without the call-site churn.
Does not fix the authored-vs-generated distinction, so CR-06's `authored: true` flag
stays. Take this path only if 3.2 proves intractable.

**Milestone:** both stories play identically pre- and post-migration. `ctx["story"]`
raises on write. Template edits reach existing saves.

---

## Phase 4 — Prompt assembly refactor

**Timeline:** After phase 3. This phase converts the prompt builders from monolithic
f-strings to declarative section lists, which is the prerequisite for every optional
module in phase 5 and every section builder in phase 6.

**Source:** Schema v2 spec §5

**Gate:** prompt output for both stories is byte-comparable to pre-refactor, modulo
whitespace.

### 4.1 — `SECTIONS` list for `build_system_prompt`

**Files:** `story_engine.py`
**Size:** ~150 lines refactored, net line count roughly flat

Replace the monolithic f-string with a declarative section list:

```python
SECTIONS = [
    (always,              _section_identity),
    (always,              _section_world_rules),
    (has_setting_summary, _section_setting),
    (has_factions,        _section_factions),
    (has_characters,      _section_roster),
    (always,              _section_main_thread),
    (always,              _section_story_so_far),
    (always,              _section_recent),
    (nudge_due,           _section_pacing_nudge),
    (always,              _section_scene),
    (always,              _section_protagonist),
    (has_revealed,        _section_revelations),
    (has_tracked_entity,  _section_entity),
    (has_style,           _section_style),
    (always,              _section_footer),
]
```

Each builder returns a string or `None`; `None` contributes nothing, not an empty
header. Ordering per v2 spec §5: stable content first (cacheable prefix), volatile
content last.

### 4.2 — Section builders for the state-update prompt

**Files:** `story_engine.py` (`update_progress_from_turn`)
**Size:** ~60 lines

Apply the same conditional-section pattern to the state-update prompt's schema field
list. Already conditionally built for `stats`; extend the same pattern to
`relationships`, `revelations`, `tracked_entity`, `scene_update`.

### 4.3 — Absorb CRD nudge additions as section builders

**Files:** `story_engine.py` (`generate_pacing_nudge`)

Land the four nudge-prompt additions from the CRD as section builders within the nudge:

- **CR-09:** `THIS ACT RESOLVES WHEN: {completion_signals}` — conditional on non-empty.
- **CR-11:** `PLAYER GOALS: {active goals}` — conditional on non-empty.
- **CR-12:** `EMERGING THEMES: {themes}` — conditional on non-empty.
- **CR-13:** `TIES TO MAIN: {ties_to_main_plot}` alongside the primary active subplot.

~60 tokens, every 8th turn.

**Test:** absent fields produce no prompt section; present fields render correctly.

### Phase 4 summary

| Item | Risk | Effort |
|---|---|---|
| 4.1 SECTIONS refactor | Medium — regression risk on prompt output | Medium |
| 4.2 State-update sections | Low | Small |
| 4.3 Nudge absorptions | Low | Small |

**Milestone:** every prompt is assembled from composable sections. Adding a new section
for an optional module is a one-function-and-one-line-in-SECTIONS change, not an edit to
a 200-line f-string.

---

## Phase 5 — Mechanics modules

**Timeline:** After phase 4. Each module is independently implementable — they don't
depend on each other. Order by value.

**Source:** Schema v2 spec §3.6, §3.4, §3.5; CRD CR-06 (characters), CR-16 (entity
count)

**Gate:** per-module tests; absent-module tests (template without the key produces no
prompt section and no state-update field).

### 5.1 — `narration` module

**Files:** `story_engine.py` (section builders, footer), both templates
**Size:** ~40 lines engine, template content moves

The module that moves the prose aesthetic out of `story_engine.py`:

- `narration.pov` → stated in identity header (already landed as CR-14 in phase 1;
  now reads from the v2 path)
- `narration.option_pov` → interpolated into the footer's option instruction (replaces
  the hardcoded "first-person")
- `narration.option_count` → threaded through the footer text *and*
  `parse_narration_and_options`'s minimum-count fallback
- `narration.scene_length` → replaces `SCENE_WORD_MIN` / `SCENE_WORD_MAX`
- `narration.style` → verbatim bullets, rendered as a `_section_style` builder

The existing style bullets move to `new_babel/template.json`; `example` gets its own.
A template omitting `narration.style` gets no style block — the model works from
`genre` + `tone` alone.

**Test:** `new_babel` prompt matches the current prose prescription; `example` prompt
carries its own; a minimal template with no `narration` key produces no style block and
uses defaults for `pov`, `option_count`, `scene_length`.

### 5.2 — `stats` bounds and `protagonist.default_name`

**Files:** `story_engine.py`, both templates
**Size:** ~15 lines

- `mechanics.stats.floor` / `.ceiling` replaces the global `STAT_FLOOR = 0`. Per-story
  bounds; `null` for unbounded.
- `protagonist.default_name` replaces the hardcoded `"Subject Zero"` in
  `apply_opening_name`.

**Test:** a stat with `floor: -10` clamps correctly; `null` ceiling allows unbounded
growth; `default_name` appears in the opening when no name is entered.

### 5.3 — `relationships` axis

**Files:** `story_engine.py` (narration and state-update prompts), both templates
**Size:** ~20 lines

`mechanics.relationships.axis.negative` / `.positive` / `.description` replace the
hardcoded "−100 hostile to +100 devoted" and "trust/warmth built" instruction text.
Interpolated into both prompts.

Absent `mechanics.relationships` means the story tracks no relationships at all — the
field vanishes from the state-update schema and the protagonist prompt section.

**Test:** custom axis labels render in both prompts; absent module produces no
relationship fields anywhere.

### 5.4 — `revelations` (v2 path)

**Files:** `story_engine.py`, both templates
**Size:** ~30 lines (mostly moving CR-03's work to the new path)

Move `player.origin.memory_fragments` to `mechanics.revelations`. Runtime reveal state
moves to `state.plot.revelations_revealed` (keyed by id, carries `turn`). The
`revealed` boolean is no longer mutated on authored content — the split forbids it.

CR-03's narration-prompt block and the state-update prompt's trigger list now read from
the v2 paths. Functionally identical to what phase 2 landed; this is a relocation.

Delete the `player.origin` wrapper from both templates — it existed solely for this one
key.

Access via `.get()` throughout — a template omitting `mechanics.revelations` produces no
prompt section and no `memory_fragments_revealed` field in the state-update schema.

**Test:** all CR-03 tests pass at the new paths; a template with no `revelations`
produces no prompt section; an older save with `player.origin.memory_fragments` migrates
cleanly.

### 5.5 — `tracked_entity` (v2 path)

**Files:** `story_engine.py`, `new_babel` template
**Size:** ~15 lines (relocating CR-07's work)

Move `world.tracked_entity` to `mechanics.tracked_entity`. Add
`pacing_note` (new field, fed to narration alongside the contact count per CR-16).

Feed `entity_contact_count` to the narration prompt so the narrator can pace appearances
against prior contact.

**Test:** CR-07 tests pass at the new path; `pacing_note` renders; contact count
renders; absent module produces no entity fields.

### 5.6 — `characters` roster (CR-06)

**Files:** `story_engine.py` (section builders, state-update prompt, eviction logic),
`state_store.py`, both templates
**Size:** ~80 lines — the largest single module

Authored characters live in `ctx["story"]["world"]["characters"]`. Discovered characters
live in `ctx["state"]["characters"]` with relationship score, `first_seen_turn`, and
description. At render time, merge both into one roster.

Narration prompt: `KNOWN CHARACTERS` block with name, score (omitted if no relationship
entry), and description. Bounded by `RELATIONSHIPS_LIMIT`.

State-update prompt: list of known character names with instruction to reuse existing
names verbatim.

Eviction: extends the existing closest-to-neutral eviction to delete the `state.characters`
entry in the same pass. Authored characters (present in the template) are **never
evicted** — the structural split enforces this without an `authored: true` flag.

A `relationship_changes` entry for an unknown name creates a stub in
`state.characters`.

Populate `example`'s template with its existing NPCs (the innkeeper at minimum).

**Test:** authored characters render with description and score; unknown character
creates a stub; eviction removes runtime entries and never template entries;
`stories/example`'s empty `characters: {}` produces no block; known names appear in the
state-update prompt.

**Cost:** ~130–230 tokens/turn, bounded by `RELATIONSHIPS_LIMIT`.

### 5.7 — `failure_conditions`

**Files:** `story_engine.py` (state-update prompt, application logic), `state_store.py`
**Size:** ~40 lines

Authored trigger list in `mechanics.failure_conditions`. Evaluated in the state-update
pass alongside revelations — same authored-trigger shape, different effect.

When a condition fires: set `state.plot.endgame.requested = true`,
`endgame.cause = <condition id>`, build `endgame.final_arc` from the condition's
`ending_prompt`, and route into the existing endgame machinery. No new code path for
endings — just a new way to enter the existing one.

**Test:** a fired condition triggers endgame; `endgame.cause` records the condition id
(vs. `"player_request"` for the existing path); absent module produces no condition
fields in the state-update schema.

### 5.8 — `opening_scene` optional name capture

**Files:** `story_engine.py` (`run_opening_scene`), template schema
**Size:** ~15 lines

When `opening_scene.narration` is present and the `narration_before_name` /
`narration_after_name` pair is absent, no name prompt is shown and
`protagonist.default_name` is used.

**Test:** a template with `"narration": "..."` and no before/after pair skips name
capture and uses the default name.

### Phase 5 summary

| Item | Risk | Effort | v1 predecessor |
|---|---|---|---|
| 5.1 narration | Low | Medium | CR-14 (phase 1) |
| 5.2 stats + name | Low | Small | — |
| 5.3 relationships | Low | Small | — |
| 5.4 revelations | Low | Small | CR-03 (phase 2) |
| 5.5 tracked_entity | Low | Small | CR-07 (phase 2) |
| 5.6 characters | **Medium** | **Medium** | CR-06 |
| 5.7 failure_conditions | Medium | Medium | New capability |
| 5.8 opening_scene | Low | Small | — |

**Recommended order within phase:** 5.1, 5.2, 5.3 first (smallest, establish the
module pattern). Then 5.4, 5.5 (relocations from phase 2). Then 5.6 (largest). Then
5.7, 5.8 (new capabilities, independent).

---

## Phase 6 — Pacing loop

**Timeline:** After phase 4 (SECTIONS refactor) and phase 5 (mechanics module pattern).
Also requires phase 0 validation gates to have passed.

**Source:** Pacing spec v4 §§4–12, 16

**Gate:** all pacing-specific tests pass; both story configs exercise the machinery
in opposite directions.

### 6.1 — Template authoring

**Files:** `stories/new_babel/template.json`, `stories/example/template.json`
**Size:** template content only, no Python

Add `mechanics.pacing_loop` and `mechanics.progression` to both templates.

- New Babel: §5.1 vocabulary (crisis/escalation/lull/resolution), one `force_release`
  rule watching `tension`, threshold 8, `suppress_when: [threat_present, just_fired]`.
  Progression label "leverage", kinds: knowledge/relationship/capability/material.
- `example`: Appendix A vocabulary (hospitality/reassurance/unsettling/confrontation),
  one `force_complication` rule watching `stasis`, threshold 6,
  `suppress_when: [just_fired]`. Progression label "footing", kinds:
  observation/trust/access/corroboration.

Both set `threshold_by_act: {"finale": null}`.

Ship directives and reduced directives per pacing spec §11 and Appendix A.

**Test:** both templates load and validate.

### 6.2 — Beat classification and leverage extraction

**Files:** `story_engine.py` (`update_progress_from_turn`)
**Size:** ~40 lines

Extend the state-update response schema with three new conditional fields (present only
when `mechanics.pacing_loop` / `mechanics.progression` are configured):

```json
"beat_type": "<one of the authored beat names>",
"intensity": "<1-3>",
"leverage_gained": [{"kind": "...", "label": "..."}]
```

Feed the beat definitions into the classifier prompt verbatim — these are the authored
`definition` strings.

Feed `progression.prompt_hint` into the extraction instruction.

**Test:** stubbed responses produce correct `beat_type`, `intensity`, and leverage
entries; absent module produces no fields in the schema.

### 6.3 — Counter update and ledger logic

**Files:** `story_engine.py` (post-state-update application block)
**Size:** ~50 lines

After the state-update response is parsed:

1. Look up the beat's `feeds` counter; add `intensity`.
2. For each counter in the beat's `resets` list: zero the counter, clear any
   `armed[rule_id]` entry that watches it, reset that rule's deferral count.
3. For each rule: if `counters[rule.watch] >= effective_threshold` (§13 act scaling),
   set `armed[rule.id] = {deferrals: 0}`.
4. Append any `leverage_gained` entries to `state.protagonist.leverage` with
   `acquired_turn` and `spent: false`.
5. Mark spent leverage when the state-update response indicates it (add a `leverage_spent`
   field to the schema).

Leverage retention and bounding: at `LEVERAGE_LIMIT` (40), evict spent entries
oldest-first, never unspent ones.

**Test:** counter arithmetic correct for every beat × intensity combination;
accumulation past threshold arms the rule; a releasing beat resets the counter and
clears the armed state; leverage appends, marks spent, and evicts correctly.

### 6.4 — Eligibility and directive injection

**Files:** `story_engine.py` (new `_section_pacing_directive` builder)
**Size:** ~40 lines

Before each turn's narration, evaluate eligibility for each armed rule:

| Predicate | Source | Effect if true |
|---|---|---|
| `threat_present` | `state.scene.threat_present` | Suppress, increment deferrals |
| `just_fired` | This rule fired on the previous turn | Suppress, increment deferrals |

If eligible: inject `directive` text as a prompt section. Interpolate `{counter_value}`,
`{deferrals}`, `{unspent_leverage}`, `{queued_reveal}`.

If suppressed and `deferrals >= max_deferrals`: inject `reduced_directive` instead.
This is the guaranteed floor — the deferral ceiling.

After injection: reset the counter and clear the armed state (the directive has fired).

Single rule per story in v1 — if two rules are somehow armed (shouldn't happen with
one rule, but guard against it), fire the one with the higher
`counters[watch] / threshold` ratio.

Register as a SECTIONS builder:

```python
(rule_armed_and_eligible, _section_pacing_directive),
```

Placed with the volatile sections, near the pacing nudge. Single-turn addition, dropped
after.

**Test:** armed + eligible → directive present in prompt; armed + suppressed → directive
absent, deferrals incremented; max deferrals exceeded → reduced directive fires;
directive text interpolates all four variables correctly.

### 6.5 — Reveal placement integration

**Files:** `story_engine.py`
**Size:** ~15 lines

When the state pass marks a revelation eligible, append its id to
`state.pacing.reveal_queue` rather than consuming it immediately. The directive
consumes one entry per firing, FIFO.

A time-critical revelation (one whose trigger hard-requires it on a specific turn)
overrides — this is a placement preference, not a gate.

**Test:** revealed revelation goes to queue; directive firing consumes one entry;
queue empty means no `{queued_reveal}` interpolation; time-critical reveal bypasses
queue.

### 6.6 — Playtest tuning

**Not a code phase — a calibration pass.** Requires a real playthrough, not stubs.

- Playtest New Babel's `threshold` at 6, 8, and 10 against the §14.1 acceptance sample.
  Tone judgment, not analytically derivable.
- Tune `example`'s threshold separately — its scale is different (lower-intensity beats
  accumulate slower).
- Set `threshold_by_act` with `"finale": null` before the first endgame playtest.

Record results in `docs/` for future reference.

### Phase 6 summary

| Item | Risk | Effort |
|---|---|---|
| 6.1 Template authoring | Low | Small |
| 6.2 Classification + extraction | Medium — classifier quality | Medium |
| 6.3 Counter + ledger logic | Low — pure arithmetic | Medium |
| 6.4 Eligibility + directive | Medium | Medium |
| 6.5 Reveal placement | Low | Small |
| 6.6 Playtest tuning | Medium — subjective | — |

**Milestone:** the engine can force a corrective beat in either direction, surface
leverage in the directive, and schedule reveals into release windows. Both stories
exercise the machinery with opposite correction directions.

---

## Phase 7 — Conformance, documentation, cleanup

**Timeline:** After phases 5 and 6. Not gated on playtest tuning (6.6) — the
conformance fixtures test the machinery, not the threshold values.

**Source:** Schema v2 spec §7; CRD CR-18; pacing spec §15

### 7.1 — Genre conformance fixtures

**Files:** `test/fixtures/regency.json`, `test/fixtures/courtroom.json`,
`test/fixtures/survival.json`, `test/test_conformance.py`
**Size:** three minimal templates + one test file

| Fixture | Modules used | Modules absent |
|---|---|---|
| `regency.json` | relationships (disregard → devotion), characters, revelations | locations, factions, stats, tracked_entity, failure_conditions, character_creation |
| `courtroom.json` | characters, revelations (as testimony), failure_conditions | locations, factions, tracked_entity |
| `survival.json` | stats (floor -10), tracked_entity, failure_conditions, locations | relationships, characters, character_creation |

Assertions per fixture: template loads; `build_system_prompt` produces no empty headers
for absent modules; the state-update schema omits the corresponding fields; one stubbed
turn applies cleanly.

**The fourth check:** no fixture requires a Python change. If adding one does, the schema
isn't done — go back to phase 5.

### 7.2 — Document write-only fields (CR-18)

**Files:** `docs/Narrative_Engine_Spec.md`

Explicitly list every field that is persisted but never read into a prompt, with its
justification:

| Field | Purpose |
|---|---|
| `history.full_transcript` | Disk-only UI scrollback |
| `protagonist.flags.archive` | Retired flags, debugging |
| `protagonist.flags.meta` | Eviction bookkeeping |
| `plot.act_history` | Audit trail |
| `pacing.last_direction` | Debug echo |
| `plot.endgame.requested_turn` | Audit trail |
| `thread_steering.pivot_history` / `last_pivot_turn` | Audit trail |
| `plot.main_thread.emergent_directions` | Human staging area |
| `plot.main_thread.plot_notes` | Author's note, UI only |
| `meta.synopsis` | Story-picker copy |

Future audits check this list rather than re-deriving it.

### 7.3 — Documentation rewrite

**Files:** `CLAUDE.md`, `README.md`, `docs/Narrative_Engine_Spec.md`,
`docs/UI_SPEC.md`

Rewrite schema sections to reflect v2. Document:

- The story/state split and how to add a new story (content change, not code change)
- Each mechanics module with opt-in/opt-out behaviour
- The SECTIONS prompt assembly pattern
- Time/deadlines as the intended `stats` pattern
- The pacing loop module with both worked examples
- The conformance test as the genericity guarantee

### 7.4 — `CLAUDE.md` update

**Files:** `CLAUDE.md`

Update the automatic project context file for Claude Code sessions:

- Schema v2 paths replace v1 paths
- New files: `frozen_dict.py`, `migrate_v1.py`, conformance fixtures
- Module list with opt-in behaviour
- Updated test patterns
- Link to this implementation plan as the design history

### Phase 7 summary

| Item | Risk | Effort |
|---|---|---|
| 7.1 Conformance fixtures | Low | Medium |
| 7.2 Write-only register | None | Small |
| 7.3 Doc rewrite | None | Medium |
| 7.4 CLAUDE.md update | None | Small |

---

## Risk register

| Risk | Phase | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| Call-site migration introduces silent wrong-half reads | 3.2 | **High** | High | File-by-file migration with test run after each file; `FrozenDict` catches writes but not reads |
| Phase 3 stalls entirely | 3 | Medium | High | Lighter fallback (v2 spec §2.4): re-merge authored sections on load without the structural split. Phases 1–2 already landed the prompt fixes. |
| Pacing classifier accuracy below 70% | 0.2 | Medium | Medium | Fix the classifier prompt and re-run. If unfixable, the pacing loop doesn't ship, but everything else does. |
| Cross-genre vocabulary fails (0.3) | 0.3 | Medium | Low | `example`'s pacing config is removed; module ships thriller-only. No impact on phases 1–5 or 7. |
| Beat counter fires too early/late in practice | 6.6 | Medium | Low | Threshold tuning in playtest. The machinery is correct; only the calibration is uncertain. |
| Template edit invalidates live save reference | 3.3 | Low | Medium | Reconciliation at load, never rejection. Every mismatch type has a specified recovery per v2 §2.3. |
| `completion_signals` quality on generated acts | 5 (Q4) | Medium | Low | Validation failure on empty list forces the state pass to produce *something*; quality is a prompt-tuning concern. |

---

## Effort estimate

| Phase | Effort | Parallel? |
|---|---|---|
| 0 Validation gates | 1–2 sessions | Yes — runs alongside 1–3 |
| 1 v1 quick fixes | 1 session | No — first code |
| 2 v1 prompt fixes | 2 sessions | No — after 1 |
| 3 Schema v2 core | 3–4 sessions | No — after 2, highest risk |
| 4 Prompt refactor | 1–2 sessions | No — after 3 |
| 5 Mechanics modules | 3–4 sessions | No — after 4, but modules are independent within the phase |
| 6 Pacing loop | 2–3 sessions + playtest | No — after 4+5, also after 0 |
| 7 Conformance + docs | 1–2 sessions | No — after 5+6 |

**Total:** 14–19 sessions, with the critical path running through phases 1→2→3→4→5→6→7.
Phase 0 runs in parallel and is the only parallelism in the plan.

"Session" here means a Claude Code working session or equivalent — a focused block of
implementation, test, and commit. Not a wall-clock estimate, since it depends on session
length and how many items land per session.

---

## Stop points

The plan is designed so that stopping after any phase leaves the engine strictly better
than today. Each is a legitimate stopping point, not a phase gate that must be cleared.

| Stop after | State of the engine |
|---|---|
| Phase 1 | v1 with live defects fixed, dead code removed, main thread and POV in prompt |
| Phase 2 | v1 with scene writer, world context, revelations working, Architect parameterized |
| Phase 3 | v2 schema live, template edits reach saves, authored content immutable |
| Phase 4 | Prompt assembly composable, nudge additions landed |
| Phase 5 | All mechanics modules live, genre-neutral, failure conditions available |
| Phase 6 | Pacing loop operational in both directions, progression ledger tracking |
| Phase 7 | Conformance proven, documentation complete |
