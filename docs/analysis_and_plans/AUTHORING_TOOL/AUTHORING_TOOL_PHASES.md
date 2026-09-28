# Authoring tool: phased implementation plan

**Written for whoever picks this up, including a fresh agent.** The design documents say
what to build and why. This file says in what order, what each phase has to prove before the
next one starts, and where this codebase forces a different choice from the one the design
documents assumed.

Primary sources, in priority order when they conflict:

1. `CLAUDE.md`: engine invariants. Some of them are amended by this overhaul (Phase 0 lists
   which). The rest stand.
2. `docs/Design_Overhaul.md`: build order (S1–S6) and the governing premise, *the schema
   serialises what the storyboard can express*.
3. `docs/Authoring_Tool_Spec.md`: what the tool is (views, inspector, linter IDs, assist,
   simulator, playtest). Section references below (§n) are to it.
4. `docs/Story_Mechanics_Update.md`: the target shapes (CR-01 to CR-10) that board fields
   serialise to.
5. `docs/Missing_Core_Storyboard_Reference_Design.html`: the interaction reference. It is a
   prototype. Its interactions carry over; its data model and its `localStorage` persistence
   do not.
6. This file.

**Status: Phase 0 done.** CLAUDE.md amended (D1–D7 recorded, three invariants retired/rewritten,
the `npc_id` correction, the documentation map). New Babel and `example` reformatted to
canonical JSON. `state_store.write_template`/`_dumps_template`/`_next_story_version` and the
`TEMPLATE_SCHEMA_VERSIONS` load guard are in, covered by `test/test_write_template.py`
(round trip, atomicity, version bumping, real-file formatting check, schema_version guard).
`jsonschema==4.23.0` added to `requirements.txt`. Phase S1 is next.
**Decisions:** D1–D7 decided on 2026-09-22, and CR-11 on 2026-09-23. See the table at the end.

**Scope decision: this is a total overhaul.** Existing saves are discarded at cutover. Nothing
in this plan preserves live play, migrates a save, or keeps a template loadable by the
current engine while it is being rebuilt.

---

## How to read a phase

Each phase has **Goal / Work / Gate / Risk**, as in `ENGINE_V2_PHASES.md`. A phase that
cannot pass its gate has found something the design got wrong. The response is to amend
the design document, not to proceed with a note.

Two rules hold across every phase:

- **The suite stays green at every phase boundary.** `python3 test/run_all.py`. Tests that
  encode behaviour this overhaul deliberately removes are deleted in the same change that
  removes it, with the reason in the commit message. They are never skipped.
- **A board save on a story that was not edited changes nothing on disk.** The tool must never
  lose data. Every phase re-runs this.

---

## What the codebase already decides

With live play out of scope, four facts about the current code still shape the build.

### 1. The engine refuses modules it doesn't have. Keep that, and route around it (decision D1)

`state_store.load_template()` runs `mechanics.validate()`, which raises
`UnknownEngineError` for a `mechanics.<slot>.engine` this build doesn't register.
`mechanics.bind()` does the same through a registry lookup. A board that writes
`mechanics.endings: {engine: "ending_funnel", …}` before S5 therefore makes the story
unplayable. **That is the correct behaviour.** It is loud, which is the property CLAUDE.md's
declare-to-bind invariants exist to protect.

**Decision D1 (decided): the board writes final paths directly.** No staging namespace.
CR shapes go where the CRs put them, and each block goes **inside `mechanics`**. CLAUDE.md's
"a block placed one level up parses fine, seeds nothing, binds nothing" still applies.
Design_Overhaul's top-level `endings` becomes `mechanics.endings`, with CR-05's shape
unchanged otherwise.

What routes around the refusal:
- **The tool never depends on `validate()`.** It reads with `load_template_raw()` and
  validates against the JSON Schema. Authoring a module the engine doesn't have yet is the
  normal state of affairs from S1 to S5.
- **Preview and playtest use a *playable projection*.** This is the template minus every
  `mechanics` block whose engine isn't registered, computed by one function
  (`author_model.playable_projection`). The inspector lists what was left out, so the
  omission is shown and never silent.
- **Fields on existing blocks** (`role`, `delivers`, `activate_when`, `fail_when` on
  `plot.subplots.<id>`) don't trip `validate()`; the current engine just ignores them.
  - Add a `validate()` warning that names every CR field present with no reader. This follows
    the same pattern as the existing warnings for inert stats, inventory and subplots, so an
    author who plays a half-built story is told why a thread never activates.
- The board marks every field and node whose engine isn't built with a **"not built"** chip.
  S5 removes the chips one module at a time.

### 2. What the board edits, and what the engine reads today

| Board element | Template path (final) | Engine reads it today? |
|---|---|---|
| Start node: creation steps | `character_creation[]` | Yes |
| Start node: opening | `plot.opening_scene`, `protagonist` | Yes |
| Thread card: title, theme, priority, span, threshold | `plot.subplots.<id>` | Yes |
| Solid "opens" edge | `plot.subplots.<id>.starts_active: true` | Yes |
| Dashed "unlocks" edge plus condition | `plot.subplots.<id>.activate_when` (CR-10) | **Not built** (today the model activates `starts_active: false` threads) |
| Thread `role`, delivers edge, matrix dot | `plot.subplots.<id>.role`, `.delivers` (CR-10) | **Not built** |
| Destination ending plus waypoints | `mechanics.endings.entries[]` (CR-05) | **Not built** |
| Failure ending | `mechanics.endings.entries[]`, `kind: "terminal"` (CR-05; D5). Replaces `mechanics.failure_conditions`. | **Built**; the `failure_conditions` engine is deleted |
| Cast tab | `world.characters.<name>` (`description`, `hook`, `first_contact`, `role`, `canon`) | Yes, except `first_contact`, which is new in S1 (D7) |
| Stat tiers | `mechanics.stats.axes.<axis>.tiers` | Yes for the current shape (QUORUM uses it). `on_enter` (CR-01) is **not built**. |
| Main thread and acts | `plot.main_thread` | Yes, not on the board. Goes in the inspector's Forms tab. |
| Lore | `mechanics.lore` (CR-06) | **Not built** |
| Bond grid (Cast tab), side-thread recipes, vignettes, player threads | `mechanics.bonds`, `mechanics.side_threads` (CR-11, CR-12) | **Not built** |
| Board positions | `_storyboard.positions` | Never (author metadata) |

### 3. The condition evaluator exists, and fails in only one direction (decision D2)

`mechanics/gate.py`'s `satisfied()` evaluates `all`/`any`/`not` over four leaf kinds, and
by invariant **fails open**: an unknown referent reads as satisfied. Its docstring names "this
evaluator growing into a general expression language" as the risk to avoid. CR-02 is that
general language.

Failing open is right for a gate or an `activate_when`: a typo opens a door early. It is
wrong for `ready_when`, `done_when` and `fail_when`: a typo in `ready_when` commits an ending
at the next check, and a typo in `fail_when` prunes one. Both are permanent (CR-05). This has
nothing to do with live saves. It is how the rebuilt engine will behave in every new
playthrough.

**Decision D2 (decided):**
- CR-02 gets its own module, `backend/conditions.py`. Gates become one caller, with fail-open
  as their policy.
- Every call site declares what an unknown referent means there: `open` for gates and
  `activate_when`, `closed` for `ready_when`, `done_when` and `fail_when`.
- Runtime polarity is only the backstop. The mechanism is lint rule **L10** (unknown flag,
  stat, fragment or character), which is **save-blocking** for every condition field.

### 4. The tool must not keep its own copy of engine logic (decision D3)

Design_Overhaul §6 says to write the evaluator in JS first and port it to Python. §2 says
the tool imports the real engine functions "so the tool can never disagree with the
engine". These conflict, and this machine has no JS runtime, so a JS evaluator could not be
tested by the offline suite at all.

**Decision D3 (decided): the server evaluates.** The sample-state bar posts
`{model, sample_state}` (debounced) to `/author/<slug>/api/evaluate`. The response is every
condition's truth value and proximity, computed by `backend/conditions.py`. Lint and preview
work the same way: the browser never reimplements engine rules. The prototype's client-side
health checks are replaced by server lint in S1.

### 5. The board is the first client-side application in an HTMX codebase (decision D4)

CLAUDE.md: "Built with HTMX, declaratively. Don't introduce a parallel fetch/DOM-patch
implementation alongside it." The board's canvas (SVG edges, drag, pan, zoom, in-memory
model) cannot be HTMX fragments. Design_Overhaul §6 already accepts this.

**Decision D4 (decided), recorded in CLAUDE.md in Phase 0:**
- `/author/<slug>/board` renders its canvas client-side from a JSON model.
- Every server exchange still goes through HTMX. JSON is sent with
  `hx-vals='js:{model: board.serialise()}'`.
- Results that are shown (diff, lint list, preview) come back as fragments.
- Results the canvas needs (evaluate) come back through an `HX-Trigger` response header
  carrying JSON.
- No hand-written `fetch`.

This scopes the exception to one page.

---

## Phase 0: Cutover and prerequisites

**Goal.** The repository is officially mid-overhaul, and can write a template without losing
data.

**Work.**
- **Amend CLAUDE.md for the overhaul.** Record D1–D4 under a new *Authoring tool* heading.
  Then retire or rewrite the invariants that existed to protect saves:
  - *"Saves are still schema version 2 … don't relocate save state without a migration."*
    This becomes: saves are disposable until the overhaul ships; save format may change
    freely with a version bump; old saves are refused at load rather than migrated.
  - *"Adding a `character_creation` step retroactively halts every live save."* This is moot
    during the overhaul. Say so explicitly, so that nobody preserves the behaviour by
    accident.
  - *"The story ends only when the player asks."* This is replaced (D6) by: **a story ends
    only through `mechanics.endings`**, meaning a committed destination (including a forced
    commit at `commit_by`) or a confirmed terminal. The player has no command to end it.
    Once an ending is committed, `generate_new_subplot` and `check_and_advance_act` no-op,
    exactly as they do on `endgame.requested` today.

  Leave every other invariant as it is. Most exist because of how models behave, not because
  of saves.
- **Correct the stale `npc_id` invariant.** CLAUDE.md says `relationships[name]["npc_id"]`
  is the only link between a relationship and a character. The v2 code keys both by name
  and says no id indirection is needed (`insert_character`'s docstring). Rewrite the
  invariant to match: the name is the identity.
- **Fix the documentation map.** CLAUDE.md's table points at `docs/ARCHITECTURE.md`,
  `SCHEMA_V2_SPEC.md` and others, which the working tree has moved to `docs/Pre-V3 docs/`.
  Point the table at the new locations, and mark which documents the overhaul supersedes.
- **Template version.** Templates written by the board are `schema_version: 3`.
  `load_template_raw` accepts 2 and 3. The board's loader upgrades a v2 template in memory,
  so the first save writes v3.
- **`state_store.write_template(story_slug, raw)`.** This becomes the only write path, since
  all state access goes through `state_store`.
  - Resolves the path through `story_roots()`.
  - Bumps `story_version` (`YYYY-MM-DD.N`) and writes atomically (temp file plus
    `os.replace`). Atomic writes are cheap, and they stop a crash mid-write from corrupting
    a template.
- **Canonical formatting.** The writer emits `json.dumps(indent=2, ensure_ascii=False)` plus
  a trailing newline. That already matches The Missing Core and all three fixtures. New Babel
  and `example` do not match it. Reformat both in one commit with no content change, so that
  later diffs are semantic.
- **Add `jsonschema>=4.18` to `requirements.txt`.** Draft 2020-12 needs 4.x; version 3.2.0
  is what happens to be installed locally. Schema tests skip gracefully without it, as
  `test_app_routes.py` does without Flask.
- **Discarding saves under `data/saves/`** is an operator action at cutover (the server's
  `data/` volume). It is not a code change, and nothing in the repository deletes them.

**Gate.**
- `write_template(slug, load_template_raw(slug))`, in dry-run mode without the version bump,
  produces a byte-identical file for all three stories and three fixtures. **Met** —
  `test_write_template.py` checks this against the real files on disk, not a copy.
- CLAUDE.md has no invariant that contradicts D1–D4 or the discard-saves decision. **Met.**

**Risk.** Low. The formatting commit (New Babel, `example`) was kept separate from the
CLAUDE.md and state_store changes, so its diff is reviewable as whitespace only — confirmed
by `git diff --stat`, and by the full suite (`test/run_all.py`, 45 files) passing unchanged
before and after.

**Not done in Phase 0, deliberately:** no `/author` route, no board, no schema file yet
(`schema/template.v3.schema.json` is S1 work) - Phase 0 is prerequisites only.

---

## Phase S1: The board becomes the source of truth

**Status: CLOSED (2026-09-24), with the acceptance run narrowed to The Missing Core.**
Every mechanical gate item is built and tested - schema, lint, the `/author` routes, the
board frontend, the save flow, and README sync - and the acceptance run passed on The Missing
Core, authored on the board by the user and saved: see "Acceptance run," below. The original
plan called for all three real stories; that was narrowed on the user's decision (`example`
and New Babel are not covered by this gate - see below).

**Done:**
- **D7** (`relationship_to_player` → `first_contact`), fully shipped end to end - engine,
  CLI, web forms, migration code, and the content of all three real stories plus both
  fixtures that author characters. Not staged, not partial. See its bullet below.
- **`backend/author_model.py`** - `to_board_model`/`from_board_model`/`playable_projection`
  (D1), Python, offline-testable, no Flask/HTMX dependency. Round-trips all 3 real stories
  and 3 fixtures losslessly. See its bullet below for what shipped and the one real design
  change from what this document originally said about failure-ending conversion. A real bug
  was found and fixed while building the schema against it: `_apply_endings` wasn't
  stripping the node-only `title` field before writing an ending entry back, so every
  destination/terminal ending failed schema validation on save.
- **The reference-design prototype's Cast tab, health checks, cross-links, dark mode and
  export were exercised live in headless Chromium (Playwright)** - not just syntax-checked.
  Every interaction (add/remove/reorder characters, canon rows, the live leak check,
  bidirectional cross-links between a character and the threads/endings that name them,
  keyboard delete, dark-mode rendering verified by raw pixel sampling rather than a
  screenshot glance) behaved as designed, with zero console/page errors. This de-risked the
  interaction design the real board's frontend went on to implement.
- **`schema/template.v3.schema.json`** - JSON Schema draft 2020-12, exhaustive against every
  field the real stories and fixtures use today plus the CR-05/CR-10 fields the board writes
  as final paths from day one (D1). Declared `mechanics.<slot>` blocks stay valid without an
  `"engine"` key (CLAUDE.md's declare-to-bind invariant: that is not an error), so the schema
  doesn't turn an already-documented, warn-only gap into a hard failure - it does still reject
  the unbound v2 `mechanics.revelations` bare-list shape outright, and the pre-D7
  `relationship_to_player` key on `world.characters.*`, both real gaps in The Missing Core's
  current content that step 8 needs to close.
- **`backend/author_lint.py`** - the S1 lint subset (L01, L08, L09, L16, the structural-flow
  checks, and the Cast checks), ported from the prototype's `issues()`/`canonLeaks()`. Pure,
  offline-testable, no engine imports (none of this subset needs condition truth - that's
  L10, still S2).
- **The `/author` blueprint - all five routes**, in `backend/app.py`, gated on
  `AUTHOR_ENABLED=1` + `AUTHOR_USER_IDS` exactly like `_labels_enabled_or_404` (404-not-403).
  Thin wrappers over `author_model.py`/`author_lint.py`/`state_store.py`, as planned.
- **The board frontend**, `frontend/author_board.html` - the prototype ported onto real data:
  `SAMPLE`/`localStorage` removed, seeded server-side from
  `author_model.to_board_model(state_store.load_template_raw(slug))`, Export repointed at the
  raw-JSON escape hatch, Validate/Save added as the only two HTMX calls the canvas makes (D4).
- **The save flow**: schema → lint → `write_template`. **Changed from the original plan** (which
  had errors block the save, behind a diff-and-confirm step): Save now *always* writes the full
  edited template, lint errors or not - see "Save always writes" below. Validate is the
  preview-only path (same lint, no write, no diff).
- **README synopsis sync** (`backend/readme_sync.py`) - regenerates a story's `## Synopsis`
  section from `meta.synopsis` on every successful save, only ever touching that one marked
  section. Broader than originally scoped here on explicit request: backfilled onto all three
  real stories, including creating a README for The Missing Core, which had none.
- **`test_author_model.py`, `test_author_lint.py`, `test_author_routes.py`,
  `test_readme_sync.py`** - all offline (bar the Flask-gated route test, which follows
  `test_app_routes.py`'s skip-gracefully contract), all passing.
- **Play is closed for the duration of the overhaul** - see its own note below. Not part of
  the original S1 plan, but it removes what would otherwise be the real blocker on step 8.

**Acceptance run (step 8): passed on The Missing Core only.** The user authored a storyboard
for it on the board (8 nodes, 12 edges, two `destination` endings) and saved it. Checked
against the saved file: a no-op `to_board_model` → `from_board_model` round trip is
byte-identical; `author_lint.lint` returns 0 errors and 0 warnings (schema L01 included);
`state_store.load_template` refuses it with `UnknownEngineError` naming
`mechanics.endings.engine: 'ending_funnel'` - the D1 loud failure, correct while the engine
piece doesn't exist; full offline suite green (51/51).
**Not covered by the S1 gate:** `example` and New Babel. Neither has a `mechanics.endings`
block; they load and play on the v2 engine as-is. Authoring their CR-05 endings is
storyboard-led work that lands under D6 / S5, demand-driven.

**Play closed during the overhaul.** `backend/app.py`'s `_close_play_during_overhaul`
(`before_request` hook, `PLAY_ENABLED` env var, default off) returns 503 with an explanatory
page for `/`, `/stories` and everything under `/play/...`, for every logged-in user, until
flipped. Decided rather than worked around: while building S1's routes, `_story_save_stats`
was found to call `state_store.load_template()` unconditionally for every story in the
catalog to build its save-stats card, with no guard - so the moment any story authors
`mechanics.endings` (which D1 has the board do routinely, and which `load_template()`
correctly rejects with `UnknownEngineError` until S5 registers `ending_funnel`), `/stories`
and `/play` break for that story, for everyone, not just its author. Patching that one call
site was the smaller fix; closing play outright was the decision actually made, since no
story is reliably playable end to end during the schema-v3 migration regardless (CLAUDE.md:
"saves are disposable for the duration of the overhaul"). Flip `PLAY_ENABLED=1` once a
playable v3 system exists - nothing else about the gate changes. Covered by
`test_play_closure.py`.

**Save always writes; lint gates whether a story is shown to players, not whether an author can
save. (Supersedes an earlier "layout-only save".)** The original plan had lint errors block
`/api/save`. Every real story fails lint (L08) until it authors `mechanics.endings`, so an
author mid-edit - adding and removing threads and endings, rewiring connections - could not land
anything on disk. A first fix built `author_model.apply_layout_only` and let a blocked save write
just `_storyboard.positions`; that was then superseded by the simpler and more general rule:
`_author_validate_response` writes the full edited template on every Save, and reports lint
errors alongside "Saved" instead of refusing. What lint errors gate instead is player
visibility - `stories()` filters out any story where `_story_blocked_by_lint` is true, so a
half-wired story can't be started, while its author can always save and come back to it.
`apply_layout_only`, `_author_save_layout_only` and the diff against the on-disk file are gone;
Validate still offers a "Confirm & Save" button, which now just saves. Not exercised end-to-end yet: `/stories` currently serves the "closed for the V3
overhaul" page (play is disabled until a minimum working appV3 build exists), so the filter
only runs once `PLAY_ENABLED` is on; `test_app_routes.py` covers it against a deliberately
lint-failing story. The client-side layout autosave to `localStorage`
(`frontend/author_board.html`, keyed per story) is unaffected and still smooths refreshes.

**Goal.** Open a real story on the board, edit everything in fact 2's table, and save without
losing anything.

**Work.**

- **Blueprint `/author`**, gated on `AUTHOR_ENABLED=1` and `AUTHOR_USER_IDS`. Returns 404
  rather than 403, following the `LABEL_SHEETS_USER` precedent (`_labels_enabled_or_404`).
  The app is reachable through the tunnel, and these routes write files.
- **Routes:**
  - `GET /author`: the story catalog, reusing `list_stories()`, which already scans both roots.
  - `GET /author/<slug>/board`
  - `POST /author/<slug>/api/validate`
  - `POST /author/<slug>/api/save`
  - `GET|POST /author/<slug>/raw`: the JSON escape hatch.
- **Loader/writer: `backend/author_model.py`. Done.** `to_board_model`/`from_board_model`,
  covering every fact-2 row that has a template-side field today (start, threads + opens/
  unlocks/delivers edges, terminal endings, destination endings + waypoints, cast) plus
  `_storyboard.positions`. Stat tiers, `plot.main_thread`/lore/timeline are out of scope for
  this module (S3/S4/S6 concerns) and pass through untouched, same as any key it's never
  heard of.
  - **The writer patches, it never regenerates** - every node is seeded with a full,
    deep-copied `dict(source)`, and the writer overlays only the fields it actually derives
    or edits back onto that copy. A field with no UI yet (`completion_threshold`,
    `ties_to_main_plot`, an ending's `criteria`/`arc`/`epilogue`...) survives by
    construction, not by a maintained preserve-list. This is what let the round-trip gate
    catch two real bugs before either shipped: an explicit `starts_active: false` with no
    edge was being dropped (fixed - edges now only *override* the field they concern, per
    subplot), and `plot.subplots` was being introduced as `{}` for a story that authors none
    at all (fixed - P-2/P-4, `courtroom.json`).
  - **No `_storyboard` staging namespace** (superseded the draft-namespace language an
    earlier revision of this document had, before D1 was decided as "final paths, always").
    `mechanics.endings`/CR-10 fields round-trip to their real paths unconditionally; whether
    the engine will accept them is `load_template()`'s problem, not this module's (D1).
  - **Drops the prototype's `SAMPLE` and its `localStorage` model.** Not reused anywhere -
    besides being superseded, it embeds Missing Core canon in a public-repo file.
  - **`playable_projection(raw, registered_engines)` (D1) is built.** Strips any
    `mechanics.<slot>` block whose engine isn't registered and reports what it left out;
    this is what S1's (future) preview/playtest will build a ctx from instead of
    `load_template()`, which stays loud everywhere else.
  - **Tests:** `test_author_model.py` - round trip on all 3 stories + 3 fixtures (byte
    identical except `schema_version` and `_storyboard.positions`), a real single-field
    edit touching nothing else, opens/unlocks/delivers edges, a synthetic destination
    ending exercising every judge/author-only field, cast round trip, and both
    `playable_projection` cases.
- **Inspector detail tab** for every node kind in §4.3. Each field shows its visibility badge
  and, where it applies, the "not built" chip (D1).
  - **Gap closed after initial ship**, per UI feedback, in two passes. First: a destination
    ending's waypoint rows only exposed `plant` - `detect` and `done_when` had no editor
    (L09 exists specifically to catch a waypoint with neither). Second, larger pass ("a fully
    functional storyboard" - every judge/author/narrator field a CR-05/CR-10 story actually
    needs, not just what happened to get built first): destination endings now also expose
    `viable_while` (kept in sync both ways with the Catch-all checkbox - checking it clears
    `viable_while`, and typing a condition unchecks it), `ready_when`, `hint`, `criteria`,
    `arc.title`/`arc.description`, `epilogue`; terminals gained the equivalent CR-05 fields
    (`min_turn`, `ready_when`, `criteria`, `arc`, `epilogue`) alongside the pre-existing legacy
    `trigger` - filling in any of the new terminal fields sets `source:"endings"` on the node,
    which is what makes `author_model._apply_endings` write it under `mechanics.endings`
    instead of the legacy `mechanics.failure_conditions` shape (the "promotion" the loader's
    own docstring already described as "a real board action... once S2's condition editor
    exists" - built here ahead of S2, narrowly, the same call made for AI assist). Subplots
    gained `fail_when` and `on_complete.stat_events` (CR-07). The Overview panel gained an
    "Ending funnel settings" section for `mechanics.endings`'s own block-level config
    (`check_every`/`budget`/`steer_top`/`finale_turns`) - story-wide, not per-node, so
    `author_model.to_board_model`/`from_board_model` gained a new `endings_settings` key in
    the board model to carry it (tested in `test_author_model.py`).

    **Condition builder (pulled forward from S2, on request).** Every condition field
    (`viable_while`, `ready_when`, `fail_when`, waypoint `done_when`, an unlock's
    `activate_when`) started as a raw-JSON textarea; they are now one shared builder -
    dropdowns for the common leaves, all/any/not groups capped at depth 3 (CR-02), options
    fed by `model.refs` (stat axes, revelations, declared flags; built by
    `author_model._condition_refs`, read-only, never written back) plus the Cast tab and
    thread cards - with a Raw JSON toggle behind it. A node the builder can't express is
    shown as an inline JSON editor, never dropped. What it does *not* do is evaluate anything
    (D3 stands: conditions are still evaluated server-side by engine code) or check that a
    flag exists (L10, still S2).

    **Grammar decision: CR-02's canonical spelling.** The builder and `schema/
    template.v3.schema.json` use `{"stat": "reach", "gte": 50}` (also `lte`/`between`,
    `revealed`, `relationship` + `tier_gte`/`tier_lte`/`peak_gte`, `subplot_status`,
    `waypoints_done`, `turn_gte`, `act_gte`, `tier_reached`). The pre-overhaul gate spellings
    (`{"stat": {"axis", "at_least"}}`, `revelation`) stay valid - CR-02 says the loader
    rewrites them, no template edits. **Consequence to keep in view:** `mechanics/gate.py`
    evaluates only the gate spellings, and reads an unknown leaf as satisfied (fail-open). Any
    field it evaluates today (`plot.main_thread.acts[].requires`, `mechanics.gate.gates[]`) must
    keep using the gate spelling until `conditions.py` (D2) exists - the board doesn't edit
    those fields yet, so nothing is exposed today.

    **Bug fixed alongside:** the old free-text "Unlocks when" box wrote
    `{"condition": "<prose>"}` (or `{"condition": "TODO"}` when blank), which is not grammar
    and failed L01 on every save. The writer no longer invents placeholders, and a condition-
    less unlock link is its own lint error.
- **Cast tab**, carried over from the prototype and wired to `world.characters`.
- **D7: `relationship_to_player` becomes `first_contact`. Done**, in one change with its
  engine reader (the same "no field without a reader" rule as D1).
  - **Roster.** `_section_roster` renders a character's `first_contact` **only until their
    first scored interaction**, e.g. `- Lark Ferris (not yet met: wary professional
    respect …): <description>`. Once the scored axis holds a number, the tiers speak
    instead. In a story with no relationship engine there is never a score, so the stance is
    always shown. That is intended: it is then the only stance the narrator gets.
  - **Generated characters start unscored.** `insert_character` creates the relationship at
    `null`, not 0; at 0 their stance would never show. CR-11 relies on this for NPCs that
    side threads create.
  - **Rename across the character pipeline:** `_character_record`, `insert_character`,
    the character-generation and promote-relationship prompts (`story_engine.py` ~901,
    1100, 1165, 1228, 1426), `plot_manager.py`'s field lists and CLI flags, `app.py`'s
    `seed-apply` / `promote-relationship` field lists, `frontend/plot_manager.html`,
    `migrate_v1.py`, and the tests that construct characters.
  - **Not factions.** `world.factions[].relationship_to_player` is a different field and is
    already prompted (`story_engine.py` ~1603). It keeps its name.
  - **`role` becomes author visibility.** It is shown as the cast card's subtitle and in the
    Plot Manager, and never prompted. It often states where an arc is going (Lark: "the one
    person who might become a partner"), and sent every turn that would steer the narrator
    toward the arc from the first scene.
  - **The key was renamed directly** in the three real stories and the two fixtures that
    author characters (regency, courtroom) - a straight value-preserving rewrite, not a
    runtime upgrade shim, since nothing about the field's *shape* changed, only its name.
  - **Gate. Met.** With no score, an unmet authored character's roster line carries the
    stance (verified against the real `example`/Missing Core content, not just a synthetic
    case - see `test_character_roster.py`). After one scored interaction it doesn't. `role`
    appears in no assembled prompt. Also fixed in the same change: the Missing Core's own
    `canon_note` on Lark, which claimed four fields reached the narrator every turn -
    `first_contact` reaches it only pre-score, `role` never does, and the note now says so.
- **Failure-ending nodes: built, in `failure_conditions`' *current* shape - not the CR-05
  terminal shape this document originally specified here.** `author_model.py` surfaces each
  `failure_conditions` entry as a `terminal` board node (`title`, `trigger` as free prose,
  `ending_prompt`) and writes it straight back into `mechanics.failure_conditions`,
  unconverted. **Why the plan changed:** converting `trigger` (free prose - "the protagonist
  spends a scene at WARMTH -10 without shelter") into a real `ready_when` condition needs an
  author to actually write one; a mechanical conversion could only produce a TODO
  placeholder, and writing that automatically on load would make the S1 round-trip gate a
  lie about round-tripping for `survival.json`/`courtroom.json` (both author
  `failure_conditions` today). The board *can* already author a CR-05 terminal directly
  under `mechanics.endings` (kind: terminal) side by side with the legacy ones -
  `_terminal_node_from_entry` in `author_model.py` - so promoting one is possible now by
  hand through the raw-JSON escape hatch; it becomes a real board action once S2's condition
  editor exists to write `ready_when` with. D5's actual engine-side retirement of
  `failure_conditions`/`triggered_ending` is unaffected - still S5 step 3.
- **Health panel:** the server-side lint subset (D3). S1's rules are:
  - L01 (schema)
  - L08 (no catch-all)
  - L09 (waypoint with neither `done_when` nor `detect`)
  - L16 (dangling ids)
  - the §4.6 structural flow checks
  - the prototype's Cast checks
  **Done** - `backend/author_lint.py`.
- **Schema** `schema/template.v3.schema.json`. With no legacy templates to protect, L01's
  "any unknown non-`_` key is an error" applies to the whole template from the start. Every
  key the three stories use today gets modelled in S1, or deleted from the story because no
  engine reads it. **Done.**
- **Save flow:** schema → lint → `write_template`. **Done, then changed** - Save always writes
  and lint gates player visibility instead of blocking the save; see "Save always writes",
  above.
- **README synopsis sync (§2).** Only rewrite a marked section of an existing `README.md`.
  Never create one; of today's stories only New Babel has a README. **Done** -
  `backend/readme_sync.py`. Creating a README for a story with none was out of this bullet's
  original scope; done anyway, once, as an explicit backfill (see "Done," above) - the
  automatic per-save sync still only ever touches a README that already exists.
- **Migrate the three stories** onto the board as the S1 acceptance run. For The Missing
  Core, CR-10's own mapping table (roles, activations and deliveries for all five subplots)
  and CR-05's proposed ending set are the source. **Done for The Missing Core only** - see
  "Acceptance run," above.

**Gate.**
- **Round trip: met.** For every real story and fixture, `load → board model → write` with
  no edits is byte-identical to the file on disk (only `schema_version` and
  `_storyboard.positions` differ) - `test_author_model.py`, run against the files
  themselves, not copies.
- **Real edit: met.** A single-field edit (thread title, waypoint `plant`, a terminal's
  title) changes only that field in the written template - `test_author_model.py` checks
  this at the dict level. **Not yet checked against an assembled narration prompt**, since
  that needs `playable_projection` wired into a ctx builder, which nothing calls yet (no
  preview, no playtest exists in S1's current scope) - written here as originally scoped, to
  flag as still open rather than silently narrowed.
- **Loud failure: met, verified against the real registry.** A template with
  `mechanics.endings.engine: "ending_funnel"` raises `UnknownEngineError` from
  `mechanics.validate()` (confirmed directly, not just asserted by this document) - and
  `author_model.to_board_model` loads the same content cleanly, exercised by
  `test_author_model.py`'s synthetic-ending tests.
- **Offline test: met.** `test_author_model.py`, `test_author_lint.py`,
  `test_author_routes.py` and `test_readme_sync.py` all exist and pass - including the
  404-for-non-author-account check, previously open.
- **Met:** the board frontend rendering a real template, the health panel's server-side
  lint, the schema file, README sync, and the save flow - all previously
  "not yet gated at all, because nothing exists to gate," now built and covered by the tests
  above.
- **Acceptance run: met, narrowed to The Missing Core** - see "Acceptance run," above.

**Risk.** The writer. Every lossy template editor loses data by rebuilding from its own
model, and the resulting diff looks like a formatting change. The byte-identical round trip
is the defence; don't weaken it to "semantically equal". **This risk is now retired** - the
writer is built and the round-trip gate caught and fixed three real instances of exactly
this bug class before any UI existed to hide them (see the loader/writer bullet, above). The
remaining risk in S1 is schedule/scope, not data loss.

**To close S1, in dependency order:**
1. ~~`schema/template.v3.schema.json`~~ **Done.**
2. ~~The `/author` Flask blueprint and its five routes~~ **Done.**
3. ~~The board frontend at `/author/<slug>/board`~~ **Done.**
4. ~~The health panel's server-side lint subset~~ **Done.**
5. ~~The save flow itself~~ **Done.**
6. ~~README synopsis sync~~ **Done**, plus the one-time backfill onto all three real stories.
7. ~~`test_author_routes.py`~~ **Done**, alongside `test_author_lint.py` and
   `test_readme_sync.py`.
8. ~~Migrate the real stories' CR-10/CR-05 content onto the board by hand, as the S1
   acceptance run~~ **Done for The Missing Core** (the gate was narrowed to it); `example`
   and New Babel are not covered - see "Acceptance run," above.

---

## Phase S2: Conditions and sample state

**Goal.** Conditions on the board are real CR-02 grammar, evaluated by engine code against a
sample state.

**Status: CLOSED (2026-09-25), with the CR-11 authoring surfaces explicitly deferred.**
Every gate item is met, including the run on the real Missing Core board - see "Gate result,"
below. The CR-11 surfaces are not part of this gate: they are "not built" until S5 step 4 as
this phase always said, and under the build-order decision (the engine follows the storyboard,
demand-driven) they land when a real story needs them.

**Done:**
- **`backend/conditions.py` (D2).** Every CR-02 leaf, `all`/`any`/`not` to depth 3, proximity,
  legacy-form `normalize()`, a plain-English `describe()`, `check()` (the static, template-side
  half of the unknown-referent rule) and `iter_conditions()` - the one list of every condition
  field in a template and the polarity each is read at. Polarity is a required argument with no
  default. `test/test_conditions.py`: one test per leaf, unknown referents under both
  polarities, purity, proximity.
- **`gate.satisfied()` is a thin caller** (`OPEN`); the old evaluator is deleted, not kept
  alongside. `test_gate_precondition.py` passes unchanged, including the latching test.
- **`mechanics.flags.declared`** is in the schema, and **L10** (`author_lint.condition_issues`)
  is save-blocking for every condition field. `test/fixtures/courtroom.json` had to start
  declaring its two flags - a real template that was quietly relying on free-form flag names.
- **Sample-state bar and `POST /author/<slug>/api/evaluate` (D3).** The board's "Sample state"
  button opens inputs for stats, relationship scores and peaks, flags, revealed fragments,
  thread status, planted waypoints, turn and act; Evaluate posts the board's model plus the
  sample and gets back a table of every condition with its polarity, result and proximity.
  `backend/author_evaluate.py` builds the same `{story, state}` the engine would; an unbuilt
  engine is named in the result and its leaves read *unknown* - never silently dropped. Edge
  labels on the canvas use `describe()` (server) and a matching `condLabel` (client, for edits
  the server hasn't seen).
- **Everything S2 needs is authorable on the board, none of it by hand-editing JSON.**
  - **Story flags:** the overview panel (health badge, top right) declares
    `mechanics.flags.declared` - an id and a `detect` text per flag. A flag leaf in the
    condition builder is then a dropdown of declared flags, not free text.
  - **Viable while:** it has its own heading and explanation in a destination ending's
    inspector (it used to sit unlabelled under the catch-all checkbox).
  - **Negate (NOT):** a lone condition can be wrapped in NOT, so `not flag lark_departed` is
    buildable; before, NOT only existed as an extra clause inside an AND/OR group.
  - Lint: a declared flag with no `detect` text warns (nothing could ever set it); a duplicate
    id is an error.
- The board's condition builder (dropdowns for every condition field) predates S2.

**Decisions made in passing - flag if wrong:**
- **`viable_while` is `OPEN` (unknown reads true).** D2 names `ready_when`, `done_when` and
  `fail_when` as fail-closed and says nothing of `viable_while`. Reading a typo there as false
  would prune an ending, which is permanent - the same test D2 applies to the other three, with
  the opposite answer. If you want it closed, it is one word in `conditions.iter_conditions`.
- **A flag is only "unknown" when the story authors a `mechanics.flags` block.** With no block,
  flags are the pre-CR-02 free-form names and a flag nobody set simply reads false, which is
  what gates already did. L10 still rejects an undeclared flag either way, so a story authored on
  the board always declares.
- **State the engine does not write yet, with a sound lower bound so a condition still means
  something meanwhile:** `tier_reached` reads `mechanics.stats.tier_log`, falling back to
  "the current value is at or above that tier"; `peak_gte` reads a relationship's `peak`,
  falling back to the current score; `waypoints_done` reads `endings_state.waypoints_done` and
  is empty until CR-05's ledger exists. `bond` (CR-11) reads *unknown* everywhere.

**Gate result: met on the real Missing Core board.** The saved storyboard declares
`lark_departed` (`mechanics.flags.declared`) and The Handover (`ending_2`) authors
`viable_while: not flag lark_departed`, all built on the board and saved by the author with no
JSON hand-edit; lint returns 0 issues. Evaluating the saved file through the Sample state path:
with nothing set, `viable_while` holds; with `lark_departed` set (and SYNC 85), it does not
hold - that is the prune. The gate's wording adds a SYNC 85 clause; the authored
`viable_while` prunes on the flag alone, which is the spec's own form for The Handover (CR-05),
and the sample run set both. Same mechanism, no SYNC clause to test.

**Deferred, and not blocking:**
- ~~All the CR-11 surfaces~~ **Landed 2026-09-26** (see "CR-11 on the board", after the diagram
  rework note below), and CR-12's `player_threads` settings (2026-09-26, same note), except the
  sample bar's list of eligible (recipe, cast) bindings.
- The loader does not yet pass declared flags' `detect` text to the state-update pass, so a
  declared flag cannot be *set* in play - engine work (S5), demand-driven.
- Rewriting stored gate `requires` from the legacy spellings into the canonical grammar. The
  evaluator accepts both, so nothing breaks; the rewrite waits until a template is next edited
  on the board.

**Work.**
- **`backend/conditions.py` (D2).**
  - Implements CR-02's leaves: stat, tier, tier_reached, revealed, flag, relationship,
    creation, turn_gte, act_gte, subplot_status and waypoints_done.
  - Combinators `all`/`any`/`not`, with depth capped at 3.
  - Returns proximity alongside the truth value.
  - Unknown-referent polarity is a required argument. Pure: no `ctx` mutation, no LLM.
- **Gates move onto it.** `gate.satisfied()` becomes a thin caller with polarity `open`.
  Existing gate `requires` sugar is rewritten into the grammar by the board's v2 upgrade
  rather than supported forever at load, since there are no legacy templates to keep reading.
- **`mechanics.flags.declared`.** L10 checks every flag a condition names against it.
- **L10 becomes save-blocking** for every condition field.
- **Condition editor** in the inspector (leaf rows, comparators, nesting to depth 3), with a
  plain-English rendering. Edge labels on the canvas use the same rendering.
- **Sample-state bar and `/api/evaluate` (D3).**
- **CR-11 authoring surfaces:**
  - the `bond` leaf in the condition editor;
  - the directed bond grid and `protected` toggles in the Cast tab;
  - recipe cards for `mechanics.side_threads`, with eligible bindings highlighted from the
    sample state;
  - location and item cast slots, `may_move` pickers limited to the story's axes, tags and
    ledger kinds, and `follows` for callbacks;
  - a vignette seed list with leak-check badges;
  - `player_threads` settings (CR-12).

  All of these are "not built" until S5 step 4.

**Gate.**
- The gate engine's behavioural tests (fail-open on unknown referents, and flags read from
  `active ∪ archive`) pass against the new evaluator.
- There is one test per leaf and per polarity. In particular, an unknown flag in `ready_when`
  evaluates **false**, and the same flag in a gate evaluates **true**.
- On the real Missing Core board, "SYNC 85 and `lark_departed` set" prunes The Handover's
  `viable_while`.

**Risk.** Grammar growth. The leaf list is CR-02's and nothing else. gate.py's original
warning about becoming a general expression language still applies; it just moves to
`conditions.py`.

---

## Phase S3: Stats, tiers and the timeline

**Status: CLOSED (2026-09-25).** The gate is met against the real Missing Core template
(`test/test_tier_ladder.py`), and every piece below was also driven end to end in headless
Chromium: drag a QUORUM boundary, drag a budget boundary, Evaluate a sample, Save, and read the
file back.

**Done:**
- **`stat_axes` in the board model** (`author_model._stats_to_board` / `_apply_stat_tiers`). One
  entry per seeded axis - `mechanics.stats.axes`, then `protagonist.stats`, then creation
  `starting_stats`, via the new `author_model.stat_axis_names` (the condition builder's stat
  list uses it too now, so it matches `conditions._stat_axes`). `label`/`floor`/`ceiling` are
  read-only display data; only `tiers` is written back, verbatim and in the order sent. An axis
  whose list is unchanged is not touched, so an authored `tiers: []` survives; emptying a ladder
  removes `tiers`, then the axis entry and `axes` if they are left empty (P-2). Adding tiers never
  adds `costs`, so it can never switch a story to priced stats. Absent entirely when the story
  has no `mechanics.stats` block.
- **Stat sidebar** (canvas overlay, top left): every axis, its tier count, and after Evaluate the
  sample value and tier. The tier comes from the server - `author_evaluate.stat_tiers`, through
  the bound `BoundedCounter.tier_for` - carried on an `author-stat-tiers` `HX-Trigger` payload
  from `/api/evaluate` (D3, D4). There is no tier lookup in JS.
- **Tier ladder** (inspector, on clicking an axis): a vertical strip from floor to ceiling with
  draggable boundaries (also arrow keys), each drag clamped between its neighbours so it can
  never create an L07 error. Bands show label and narration, a hatched band marks the range below
  the lowest tier, `on_enter` shows as a pin, and the last Evaluate's sample value as a dashed
  line. Each tier row edits `at`, `label`, `narration`, `clause_max_words` and `on_enter`
  (`directive`, `once`), badged by visibility; `on_enter` carries the first **"not built"** chip.
- **CR-01 `on_enter` in the schema**, and the D1 "field with no reader" warning for it in
  `mechanics.validate()` - to delete when S5 step 2 gives it a reader.
- **L06** (warning: an axis with no tiers, or a lowest tier above the axis's floor) and **L07**
  (error: tiers out of order, or a duplicate `at`) in `author_lint.stat_tier_issues`, read from
  the raw template so the floor resolves the engine's way (per axis, falling back to the block).
  The Missing Core now carries four L06 warnings (FRAME, REACH, SYNC and TRACE have no tiers,
  which is CR-01's own migration still to do); errors are unchanged everywhere.
- **Timeline bar** (toolbar toggle, canvas overlay, "not built" chip since `ending_funnel` is):
  Open / Narrow / Commit window / Forced phases with draggable `open_until`/`narrow_until`/
  `commit_by` boundaries (clamped to stay ordered), `check_every` cadence ticks, `check_every`
  and `steer_top` inputs, one band per destination (can commit from `open_until`, drive nudges
  from `narrow_until`), and a tick per terminal at its `min_turn` - dashed at 0 when it has none,
  which is every legacy `failure_conditions` terminal. It edits the same `endings_settings` the
  overview's funnel fields do. With no complete budget it offers CR-05's placeholder 40 / 90 /
  140 as a starting point rather than inventing an engine default.

**Decisions made in passing - flag if wrong:**
- **An unsorted ladder is an error (L07), as the spec says, even though the engine sorts before
  it scans.** The ladder the author reads would not be the ladder that runs. The inspector offers
  a one-click sort when it finds one.
- **Destination bands are the same span for every destination** until the simulator (S6) has
  commit-turn distributions to draw on them. Destinations have no per-ending timing field today.

**Work.**
- **Stat sidebar and tier ladder** on `mechanics.stats.axes.<axis>.tiers`, including CR-01's
  `on_enter` (not built until S5). L06 and L07 are added to lint.
- **Timeline bar:** `mechanics.endings.budget`, `check_every`, `steer_top`.
- **Terminal tick marks** at each terminal's `min_turn`.

**Gate.**
- Editing QUORUM's tier boundary and saving changes the tier line in a QUORUM = boundary ± 1
  prompt exactly as a hand edit would.

**Risk.** Low. Most of this phase is UI over fields that S1's schema already defines.

---

**Diagram rework (2026-09-25, after S3).** The Diagram tab's default is now a **lanes** view,
because the free canvas modelled threads as a chain (Start -> thread -> thread), which is not how
the engine runs them (`docs/How_Threads_Work.md`: acts are the only sequence; threads run in
parallel and feed ending waypoints). Lanes: an acts strip (main thread, authored acts - now
editable, with `max_acts` marked not built - generated acts, finale), thread lanes grouped by
role with activation/fail/cast badges, endings on the right, and one kind of line: a thread
carrying a waypoint (`delivers`). Activation moved off drawn links onto the thread's inspector
("Becomes active": at start / when a condition holds / started by hand), always written as a
single edge from Start, which is how `author_model` already reads it. The free canvas stays
behind a toggle. Lint now reports an explicit `starts_active: false` with no condition as a
warning ("started by hand"), not the "never becomes active" error.

**CR-11 on the board (2026-09-26).** Board surfaces only; `scored_bonds` and
`episodic_threads` are not built, so a story authoring either block fails `load_template()`
loudly until S5 builds them (D1).
- **Schema:** `engine_bonds` (`axis`, `registers`, label-only `tiers`, `seed`,
  `max_generated_bonds`, `cap_per_window`) and `engine_side_threads` (settings, `protected`,
  `default_recipe`, `recipes` with `side_cast_slot`s of kind character/location/item,
  `eligible_when`, `premise`, `may_move`, `may_create_npc`, `follows`, and `vignettes`). The
  `bond` leaf is typed as `[from, to]`.
- **Board:** a Bonds section at the foot of the Cast tab (registers, tiers, a directed seed grid,
  limits); a Protected switch on each character; a Side threads tab (settings, protected list,
  recipe cards, vignettes) with a recipe inspector (slots, a slot-aware condition builder,
  `may_move` offered from the story's slots, stats, tags and leverage kinds, callbacks). A
  character rename carries into seeds, the protected list, recipe slots that name them and every
  bond leaf; a slot rename carries into its recipe's condition and `may_move`. Both blocks are
  carried whole and written back only when changed, blanks pruned.
- **Conditions:** the `bond` leaf evaluates for real (a pair with no entry reads 0, per CR-11),
  reading `state.mechanics.bonds[from][to].score` and the block's tiers. `bind_slots()`
  substitutes a casting into a recipe condition. The sample bar sets bond scores; seeds apply.
- **Lint:** `bond_issues` and `side_thread_issues` (L10 for every dangling name, id, beat, tag,
  stat or leverage kind; errors for a protected character named in a recipe and a bond block with
  no registers; warnings for setups that can never start anything). Recipe conditions go through
  L10 with their slot names legal.

**Decisions made in passing - flag if wrong:**
- **Confirmed by the author, 2026-09-26:** the next two decisions.
- **`eligible_when` is CLOSED.** An unknown referent must not start an episode on a casting it
  was never written for; failing closed costs a side thread that never starts.
- **Callback outcomes are `resolved`, `failed`, `expired` (reached `max_turns`) and `finale`.**
  CR-11 names `resolved` and `finale` and implies the others; `expired` is a new name.
- **Recipe conditions are left out of the sample-state table**: they name slots, and there is no
  single answer until the engine enumerates castings. Listing eligible bindings is engine logic,
  so it waits for `episodic_threads`.
- **`start_after_beats` has no default (decided by the author, 2026-09-26).** CR-11 said
  `respite`, which encodes a creative decision in an engine constant (CLAUDE.md rules that out)
  and `example` has no such beat. It is now required: the schema gives it `minItems: 1` and
  lint errors on an absent or empty list. The engine's loader will refuse it too, once
  `episodic_threads` is built; nothing can refuse it at load today, because the block already
  refuses to load for want of an engine.
- **The server-side leak check covers recipe premises and vignette seeds (2026-09-26).** The
  character check was already server-side (`cast_issues`; the board's `canonLeaks()` is only its
  instant-feedback mirror). A recipe's `premise` and each vignette seed are checked against
  every character's canon too - first by a side-thread-only check, then by the general
  `visibility.leak_issues` that replaced it later the same day (see S4), as L03 warnings.
  Decision D3 needs no recorded exception.

**CR-12 on the board (2026-09-26).** `mechanics.side_threads.player_threads` (`max_active`,
`confirm.reports`/`within_turns`, `abandon_after_offers`, `may_move`) in the schema and as a
section of the Side threads tab. `reports` has a schema minimum of 2 (CR-12's acceptance: a
single report never opens a thread); lint errors when `reports > within_turns` (at most one
report a turn, so it could never confirm). `may_move` takes `relationship:cast` in place of
slots and refuses `bond:` (a pursuit has no slots to name a bond between). Callbacks gain the
outcome `abandoned` and can follow `player_pursuit`, the synthetic recipe CR-12 names; an
authored recipe by that id is an error.
**Not in this piece:** retiring `player_driven_goals` (`plot_manager add-goal`, the Plot Manager
page's goal list and the `PLAYER GOAL:` nudge line). That is engine work: the goals keep working
until `episodic_threads` exists to take them over, rather than being removed with nothing in
their place.

**CR-04 on the board (2026-09-26).** Top-level `derived` (not under `mechanics`: CR-04 puts it
there, and it is not an engine block - there is no `engine` key to declare). `backend/derived.py`
holds the pure rule, shared with the engine to come: `resolve()` (first match, `when` CLOSED),
`combinations()`/`table()` (every way to finish character creation, capped at 512), `uses()`
(every `{name}` in text a model would be sent, skipping author-only fields, the stat readout's own
format fields and `_` notes) and the built-in placeholders legal per location (`{player_name}` in
the opening; the pacing directive's four). The board edits the rules on the Protagonist card,
with a new **Creation choice** leaf in the condition builder, and **Check every combination**
posts to `/author/<slug>/derived` for the table. Lint: `derived_issues`.
- **Loud until built.** Nothing substitutes `{name}` yet, so `mechanics.validate()` raises
  `UnknownEngineError` for a template that authors `derived` - the alternative is a narrator
  handed `{lark_is}` verbatim. The playable projection does not drop it (it drops `mechanics`
  blocks only); preview/playtest (S6) will need to, or the engine piece lands first.
- **Engine piece, when demanded:** resolve once when the last creation step completes (or at
  save creation for a story with none), store the values in the save, and substitute `{name}`
  wherever narrator- and judge-facing text is assembled, including the pacing directive's
  `.format()` and the opening narration.
- **Content, for the rework:** CR-04's acceptance deletes Lark's gender rule from The Missing
  Core's `world.rules`, but The Missing Core's creation has no gender step (trade and arrival
  only), so the rule the spec sketches cannot be written against it as it stands.

**CR-13 on the board (2026-09-26).** `plot.pacing.story_clock` (`free_idle_streak`, required;
`push_directive`, narrator, optional) in the schema, edited from an **Idle turns** section under
Ending funnel settings (Story health panel) - the same object the Forms tab's Pacing section
edits, so the two can't disagree. The budget fields relabel to "story-clock turn" when it is on.
`mechanics.validate()` warns while nothing reads it. The engine piece (two clocks, idle detection
from existing observations, the streak, lean-forward options, the push) is specified in
`Story_Mechanics_Update.md` CR-13 and waits for demand like the rest of S5.

**CR-07 on the board (2026-09-26).** The thread panel's `on_complete.stat_events` already
existed; this adds the rest. `mechanics.subplots` gained `completion_rewards` (events by priority)
and `near_completion_margin` in the schema, edited from Forms › Thread progress by the generic
form. A thread box shows a ★ reward chip - its own, or the one inherited from its priority's row,
and the panel says which. `thread_reward_issues` (and the board's mirror in the health panel)
errors on an event no axis's `costs` prices, and warns on a margin with no reward anywhere.
`mechanics.validate()` warns while nothing pays it. The engine half - pre-arm line, applying the
events once, the `reward_narrated` catch-up - is specified in `Story_Mechanics_Update.md` CR-07
and waits for demand.

**CR-08 on the board (2026-09-26).** `mechanics.relationships.transitions` in the schema
(`relationship_transition`, `relationship_when`), edited by the generic form under Forms ›
Relationships. A new `x-not-built` schema annotation puts the *not built* chip on a generic form
field (also on CR-07's two fields). `transition_issues` lints duplicate ids, an inverted
`between`, an unknown tier label, a directive without `{name}`, undeclared resulting flags and
two characters with the same first name. `{name}` and `{id}` are registered as engine-filled
placeholders for that path in `derived.BUILTIN`, so CR-04's scanner accepts them and a derived
value can't shadow them. Design points settled here, since the spec left them open: `when`
is flat (the `relationship_self` wrapper added nothing), and `{id}` is the first name because
the only real story keys `Lark Ferris` but flags `lark_departed`. The engine half (per-character
peak already exists as a condition input; evaluating after deltas, the `exiting` mark, `departed`)
waits for demand. **CR-09** was not built on the board: `Design_Overhaul.md` marks it deferred
pending measurement, and its own spec says to A/B the token cost before adopting.

**CR-14 on the board (2026-09-26).** `narration.scene_length_by_moment` (`beats`, `directive`,
`finale`, `inquiry`, each a `word_range`) in the schema, edited from a **Scene length** table at
the foot of Forms › Narration - one table for every range including the existing
`scene_length` default, which the generic form now skips so each field has one editor.
`scene_length_issues` lints inverted ranges, per-beat ranges for beats the pacing loop doesn't
define (L10) and per-beat ranges with no pacing loop. `mechanics.validate()` warns while nothing
reads it.

---

## Phase S4: Lore, visibility and the narrator preview

**Goal.** The author can see exactly what the narrator receives, and nothing leaks.

**Work.**
- **`x-visibility` on every schema field**, including `world.characters`:
  - `description`: narrator, every turn
  - `hook`: narrator, pacing nudge, until introduced
  - `first_contact`: narrator, until scored
  - `role` and `canon`: author
  - The CR-03 leak test (40+ character sentinels, extending `test_full_transcript.py`'s
    pattern) and lint rules L03/L04 share one implementation.
- **Preview tab (§4.5).**
  - Builds a ctx from the playable projection with `freeze` + `new_save_state`, as
    `test_genre_conformance.py` does, applies the sample state, and renders the **real**
    prompt builders with token counts.
  - Lists the modules the projection left out.
- **Lore view** on `mechanics.lore` (CR-06). **Built early (2026-09-25), inside the board's World
  tab** alongside editors for `meta`, `world.setting_summary`/`rules`/`locations`/`factions`
  and the opening location. `mechanics.lore` is in the schema (`engine_lore`) and written at its
  final path with `engine: keyed_lore` (D1) - not registered yet, so a story authoring lore fails
  `load_template()` until S5 builds it. Lore `also_when`/`unlock` are read CLOSED (an unknown
  referent must never inject staged knowledge early). L13 (generic or shared keys) is in, plus
  L16 for dangling location ids (connections, opening scene, gate targets). Sample-state highlighting
  of which entries would inject landed later (below).
- **Full linter** L01–L16, with a CLI twin at `scripts/lint_template.py`. The repository has
  `scripts/`, not the `tools/` package that §6 names.

**Gate.**
- For a sample state, the preview matches byte for byte the prompt a real turn assembles in
  that state.
- The leak test fails when `canon.truth` is pasted into a character's `description`, and
  passes on all three stories.

**Risk.** A preview that approximates the prompt. Once the author trusts it, an approximation
is worse than no preview.

**Landed (2026-09-26).** Everything above except the CR-11 eligible-castings preview, which
needs the engine's binding enumeration and waits for `episodic_threads`.

- **`x-visibility` in the schema** (`backend/visibility.py` reads it; the board's `VIS` table is
  gone and every badge is a schema lookup, on the Forms tab too). Values `narrator` / `judge` /
  `author`, an optional `x-visible-when` badge text, and `x-secret` for text the narrator must not
  learn. A field with no annotation reaches no model and is not a secret (a synopsis is `author`
  but not secret; `role` and `plot_notes` likewise, since a working note may restate public
  wording). About 70 prose fields are annotated. `test/test_visibility.py` checks them against the
  real prompts in both directions over the three stories: no secret reaches a prompt, and every
  `narrator` / `every turn` field the stories author does. It found a real leak in `new_babel`
  (the tracked entity's description repeats its secret `dialogue_style`), pinned as a known leak in
  the test until the story is fixed. Annotations that describe an unbuilt reader (an ending's
  `plant` and `hint`, until steering is built) follow the design, not today's engine.
- **Preview tab** (`backend/author_preview.py`, `POST /author/<slug>/api/preview`). The narrator
  prompt is built section by section from `story_engine.SECTIONS`, with a check that the sections
  rejoin into exactly `build_system_prompt` (the S4 gate; also tested byte for byte against a fresh
  save for all three fixtures). The state-update prompt is *captured* from
  `update_progress_from_turn` with its model call swapped for a recorder, because the engine
  assembles it inline beside the call; a lock keeps two previews from seeing each other's recorder.
  `derived` is dropped and named (its substitution is not built, so a raw `{var}` would otherwise
  show). Not shown: the act-generator prompt, which has no single text for a sample state. Token
  counts are characters over four.
- **Lore highlighting** (`author_evaluate.lore_injection`): key match (case-insensitive, whole word
  or phrase), `also_when` and `unlock` read CLOSED through the real condition code, priority
  ranking and the `max_active` cutoff (the spec's 3 when unset). The sample bar gained **Text on
  the page**; the result rides an `author-lore-injection` HX-Trigger to light the World tab's
  cards, and a table joins the Evaluate result. It evaluates the *design* (keyed_lore is unbuilt),
  so the whole-word rule is this tool's reading of the spec's "key match"; the engine should match
  it or the spec be amended. `sticky_turns` is not simulated.
- **Linter.** L02 (`x-assist: fragment` on hint, plant, refusal hint), L05, L11 (the unresolved
  `{var}` check that already existed, relabelled from L10), L12, L14, L15, and L03/L04 as one
  implementation (`visibility.leak_issues`, which also replaced the recipe/vignette check written
  earlier the same day; the character-only `cast_issues` check stays as the board's own).
- **Decisions made in passing - flag if wrong:**
  - **L03/L04 (general) are warnings, not errors.** A lint error takes a story off the player-facing
    list, and the check found two shared 40-character phrases in `the_missing_core` on day one
    (an act description and a plant repeating canon wording). `visibility.LEAK_SEVERITY` is the one
    word to change once the author has read them. The character-only check is still an error.
  - **Only `x-secret` text is a leak source.** L04 as specced ("judge text in a narrator field")
    fired on ordinary `trigger` / `detect` wording a scene may echo, so only an ending's `criteria`
    is a judge secret.
  - **L05 is a warning** (an error only for a gated opening location): a room behind a locked door
    is ordinary design.
  - **`narration.option_count`** is in the schema, an integer of at least 2 (the engine already read
    it, defaulting to 3).

---

## Phase S5: Engine implementation

**Planning approach changed 2026-09-24: demand-driven, not batch-planned.** The board (S1)
is done and being used for real authoring now, which is exactly the state D1 anticipated -
"a story is unplayable (loudly) until S5 builds its engines." Rather than pre-planning and
building a whole numbered step of this phase at once (e.g. "step 3, CR-05+CR-10 together")
before any of it is exercised, engine work now happens piece by piece as real storyboard
authoring surfaces a concrete need - `activate_when` doing nothing in play is what prompted
this, not a phase-completion deadline. The step order and CR grouping below stays as the
reference for what the complete picture looks like and why CR-05/CR-10 are coupled; it is no
longer a gate that has to be fully satisfied before any of it is touched. Expect this phase
to land out of order and partially, tracked here after the fact rather than planned ahead of it.

**Landed so far** (newest last, tracked after the fact as the note above says):
- **CR-10, the thread lifecycle half: `activate_when` and `fail_when` read in play**
  (2026-09-25). The need that prompted the demand-driven decision, closed first.
  `story_engine.apply_thread_conditions`, run in the post-turn pass after
  `check_subplot_status` (so a thread completing this turn can unlock its successor on the same
  turn) and before subplot generation (so an authored thread fills the pool before an invented
  one). Through `conditions.satisfied` at the polarity `iter_conditions` declares: `fail_when`
  CLOSED, `activate_when` OPEN. `failed` is a new runtime status: it leaves the live pool
  (`_CLOSED_THREAD_STATUSES`), and the Subplot Manager shows it as ✗. The pacing nudge's
  "SUBPLOT OPPORTUNITY" no longer offers a thread gated by `activate_when`. Covered by
  `test/test_thread_conditions.py`.
  **Decisions made in passing - confirmed by the author, 2026-09-26:**
  - Activation ignores `max_parallel_subplots`, like `starts_active` and a manual Subplot
    Manager activation. The cap governs how many threads the engine invents; an authored
    unlock the story has earned should not wait on a generated thread finishing.
  - Nothing activates once `endgame.requested` is set, matching `generate_new_subplot`.
    `fail_when` still applies then.
  - Failure runs before activation, so a thread whose `fail_when` already holds fails rather
    than starting for one turn.
  **Not in this piece** (ending funnel, still unbuilt): a failed carrier pruning a destination,
  early carrier activation from the Narrow phase, and generation limited to texture.
- **CR-05, the reaching half: `ending_funnel` registered** (2026-09-25). The Missing Core, the
  one story authoring `mechanics.endings`, now loads through `load_template()` instead of
  raising `UnknownEngineError`, and a stubbed 214-turn run force-commits at its authored
  `commit_by` (205). `backend/mechanics/endings.py` owns the state (`mechanics.endings` in the
  save: `waypoints_done`, `pruned`, `scores`, `steered`, `judge_nulls`, `terminal_cooldown`,
  `committed`) and every pure decision; `story_engine.check_ending_funnel` makes the two Tier C
  calls (`ending_commit_judge`, `terminal_confirm`, both in `STATUS_LABELS`) and routes every
  commit through `_begin_endgame`, whose `cause` is now `committed` / `forced` / `terminal` for
  funnel endings. Waypoints: `done_when` checked in code every turn, `detect` through the
  engine's one observation field `waypoints_hit` (numbered detect texts; ending ids and names
  never reach the state-update pass). CR-10's carrier prune is in: a non-catch-all destination
  whose every remaining waypoint is carried only by failed threads is pruned at the next check.
  `finale_turns` bounds the finale; the ENDGAME prompt no longer says the player asked to end
  when they didn't. Load refuses an endings block with no catch-all (`check_config`, a new
  hook on the engine base class that `validate()` calls), and `validate()` warns for endings
  authored with no engine. The board's timeline "not built" chip is gone. Covered by
  `test/test_ending_funnel.py`.
  **Decisions made in passing - confirmed by the author, 2026-09-26:**
  - **Waypoint ledger keys are qualified**, `"<ending id>.<waypoint id>"`, as `delivers`
    spells them, since a waypoint id is only unique within its ending; `conditions.
    waypoints_done` now reads the engine's bucket (`mechanics.endings`) rather than the
    top-level `endings_state` the spec sketched.
  - **A blank budget boundary means that phase never begins**, not an engine default: when a
    story may end is a creative decision. No `open_until` means the commit window is open
    from the start. `check_every` (6) and `steer_top` (2) do have defaults - they are cadence.
  - **A destination with no `ready_when` is never ready**; it can only be reached by a forced
    commit.
  - **The carrier prune is the conservative reading** of CR-10: it fires only when *no*
    remaining waypoint has a live or unstarted carrier, and a waypoint with no carrier at all
    never counts. A catch-all is never pruned by it.
  - **An ending with no `arc` enters the finale on its name alone** - never its `criteria`,
    `hint` or a `_`-prefixed author note (The Missing Core's `_theme` is one).
  - **A forced commit's bridging note is built in code** from the unplanted waypoints' `plant`
    texts (already narrator-facing), not asked of a model as CR-05 sketched.
  - **Both judges are Tier C** (the registry default). CR-05's open question 1 - a stronger
    model for the flagship commit judge - stays open until something measured says so.
  - Split `resolve()` / `settle()`: `resolve()` sees the state from before the turn's effects,
    so everything checked in code (`done_when`, pruning, scoring) runs in `settle()`, after
    them, applied by `check_ending_funnel`. Only `detect` hits go through `resolve()`.
  **Not in this piece:** steering (waypoints into act generation and pacing nudges, `hint`s,
  drive nudges, carrier priority and early activation, generation limited to texture), the
  `epilogue` shown after THE END, `max_acts`, and D5/D6 (retiring `failure_conditions` and the
  player's end-story command).

- **Declared flags' `detect` reaches the state-update pass** (2026-09-27). The top of the
  engine list: a declared flag was set only if the model happened to use its exact id, and every
  thread, act or ending gated on a flag waited on that. `story_engine._pending_declared_flags`
  lists the declared flags that have a `detect` and are not yet set (`active` union `archive`,
  the reading conditions use); `update_progress_from_turn` adds them to the prompt as
  `DECLARED FLAGS not yet set (id: event)` with an instruction to evaluate each by what happens
  in the scene and never force a match (the revelations' lesson, PHASE_0_GATE_REPORT §4). No new
  observation field: the answer is `flags_set`, already asked every turn, so the observation
  budget is unchanged (measured by `scripts/measure_baseline.py`). Implemented inline rather
  than as a registered engine: `mechanics.flags` has no `engine` key, like `tracked_entity`.
  Cost on `the_missing_core`: about 365 tokens of Tier C prompt at turn 0 (10 flags pending),
  falling as flags are set. Covered by `test/test_declared_flags.py` and, in both directions, by
  `test_genre_conformance.py` (courtroom authors flags; regency and survival must not leak the
  section).
  **Decisions made in passing - flag if wrong:**
  - **A declared flag reported `false` is dropped, not stored.** A `flag` condition reads a flag as
    set once it is in `active` or `archive` whatever its value, and a declared flag gates ending
    pruning permanently, so "the model said false" must not open that door. Undeclared flags are
    unchanged, false included.
  - **Declared flags with a blank `detect` are never asked about** (lint already warns).
  - **Undeclared flags stay free-form.** Only declared ids get event text; nothing rejects other
    names.
  - **Not measured against a live model.** The prompt follows the revelations precedent; whether
    Tier C sets the flags reliably, and how often it sets one wrongly, needs a real playthrough.

- **D5 / D6 retired** (2026-09-27). `backend/mechanics/failure.py` (the `triggered_ending` engine,
  its `failure_triggered` observation field and its `failure.trigger` effect), `END_STORY_PHRASES`,
  `is_end_story_command`, `handle_end_story_request` and its generated-arc fallback, the
  `end_story_final_arc` entries in `STATUS_LABELS` / `DEFAULT_STEP_ESTIMATE_SECONDS` (the mirror
  test holds), the CLI's `end story` line, the help page's *Ending the story* entry and the
  README's copy of it are gone. The ENDGAME prompt no longer says the player asked. In the schema,
  `mechanics.failure_conditions` and the endings entry's legacy `trigger` are removed; in
  `author_model` and the board the legacy `failure_conditions` terminal plumbing (the `Trigger`
  field and the "promotion" of a node to a CR-05 terminal) is removed, so a terminal is always a
  `mechanics.endings` entry. `mechanics.validate()` refuses a template still carrying
  `mechanics.failure_conditions`, in either shape, naming the replacement; lint's schema hint says
  the same. `_begin_endgame` is now the one way into an ending (causes `committed`, `forced`,
  `terminal`), and the two fixtures that used the old engine (courtroom, survival) are migrated.
  No real story used it. Covered by `test/test_failure_retirement.py`; the two tests of the old
  engine were deleted (the terminal path is `test_ending_funnel.py`'s).
  **How a prose failure migrates:** the old `trigger` was free prose judged by the state-update
  pass; a terminal's `ready_when` is a code condition. An event failure becomes a declared flag
  whose `detect` text is the old trigger (which now reaches the update pass, the piece above) with
  `ready_when: {"flag": ...}`; a compound one is a flag and a negated flag; a stat failure is a
  stat threshold, optionally with `criteria` for a judge. The old `ending_prompt` becomes the
  terminal's `arc.description`.
  **Decisions made in passing - flag if wrong:**
  - **The loader does not yet refuse a template with no `mechanics.endings`**, though the plan
    above says it should. Doing it now would fail `example` (the default story, used across the
    suite) and `new_babel`, whose endings are the author's to write, and would break the
    minimal-template guarantee (P-4). Lint L08 already errors on it, which keeps such a story off
    the player-facing list. It flips to a refusal in `mechanics.validate()` when those stories have
    endings; a warning at load was tried and dropped, since it tripped P-2 for every minimal
    template.
  - **A terminal with no `criteria` is the condition alone** (existing behaviour), so a migrated
    prose failure has no judge unless the author adds `criteria`. That trades the old design's
    one detection call (the model said the trigger happened) for the flag's, which the state-update
    pass makes and which cannot be unset; a wrongly set failure flag ends the story. Add `criteria`
    to any failure whose flag could be set by accident.
  - **The `endgame.requested` flag keeps its name.** Nobody requests an ending any more, but
    `generate_new_subplot`, `check_and_advance_act` and the saves all read it, and the saves are
    not being migrated for a rename.

- **Ending steering, first slice: `PLANT` in act generation and early carrier activation, with an engine
  trace** (2026-09-27). Two pieces of CR-05/CR-10 plus the instrument to measure them; the rest of steering
  waits on what the data says (see `docs/Steering_Review.md`).
  - **`PLANT`** (`endings.plant_candidates`, `check_and_advance_act`): the act director's prompt gets up to two
    unplanted waypoints' `plant` text, drawn from *steered* destinations only, deduplicated by plant text,
    ranked by how often each has already been offered (so every destination is set up in turn, not the first
    two forever), with an instruction to shape the next act so it sets them up as events, never quoting them.
    Only the `plant` reaches the prompt: never a destination's id, name, arc, criteria or hint, nor a
    waypoint's `detect` (tested). An offer is recorded (`mechanics.endings.offers`) only when the check
    produced an act.
  - **Early activation** (`endings.early_carriers`, `story_engine.activate_carriers_early`): on a funnel
    check, from the Narrow phase, a steered destination's unplanted waypoint whose carriers include nothing
    running gets its first dormant authored carrier started, even though its `activate_when` has not held.
    At most one per check; never a thread with no `activate_when`, a texture thread, or one that failed or
    completed; ignores `max_parallel_subplots`; stops once the story is ending.
  - **The trace** (`backend/engine_trace.py`, `scripts/steering_report.py`): one JSONL file per save under
    `data/traces/`, one event per decision (funnel checks with scores, steered set and carrier map; each
    waypoint planted and by which route; each act check with the plants shown and the prompt sizes; declared
    flags asked, set and dropped; carrier scans and early starts; thread transitions and why; nudges and
    pacing directives; commit judge calls and outcomes; per-turn step timings and prompt sizes). Bounded
    lines, never able to fail a turn, off by `PALIMPSEST_TRACE=0`, silent for the Preview tab. Saves gain
    two additive keys (`run_id`, and `offers` in the endings bucket); the save version does not change.
  **Decided by the author, 2026-09-27** (they accepted the recommendations put to them):
  - **No generated fallback spine thread.** When a steered waypoint has no authored carrier, nothing is
    invented for it; an uncarried waypoint is a lint warning, so the gap goes back to the author. Texture
    generation stays. (Story_Mechanics_Update.md Open question 6.)
  - **`max_acts` has no default.** Unset means unbounded, the way the budget boundaries work: a default
    would be an engine constant encoding a creative decision. Not built yet.
  - **Waypoint progress feeds the act judge and does not gate act advancement**, keeping the invariant that
    an act advances on either a subplot completing or `act_check_frequency` turns elapsing. Not built yet.
  - **An armed pacing-loop rule wins over a drive nudge on the turn it fires.** Not built yet; the trace
    records how often the two collide today.
  **Decisions made in passing - flag if wrong:**
  - **Early activation skips a thread with no `activate_when`.** The spec says "an authored carrier that
    isn't active yet". A thread with neither `starts_active` nor `activate_when` is one the author left to
    be started by hand (lint warns about it), so it is not started for them.
  - **One early activation per check** (`MAX_EARLY_ACTIVATIONS`), so a leader with several waypoints behind
    several dormant threads does not open them all at once. Spec silent.
  - **`MAX_PLANTS = 2`** is the spec's "up to two", kept as a cadence constant.
  - **Plants ride only on act checks**, which fire on `act_check_frequency` (12) or a subplot completing, so
    a plant reaches the generator about once per act. The nudge carries them too once piece 2 lands; the
    trace shows how often an act check happens at all before deciding that matters.
  - **Not measured against a live model.** Everything above is tested for what it puts in a prompt and what
    it writes to the trace, not for whether steering shortens the path to an ending.

- **Ending steering, second slice: the nudge consumers** (2026-09-27; trace build `steering-2`). Everything the
  pacing nudge carries for steering, built to the accepted recommendations:
  - **Carrier priority and `plant`** (`endings.nudge_plan`, `generate_pacing_nudge`): a *running* thread that
    delivers an unplanted waypoint of a steered destination is raised one priority step for the nudge (it wins a
    tie), and its line carries the waypoint's `plant` (`SET UP THROUGH THIS THREAD:` on the lead thread,
    `(set up: ...)` on a background one), at most two per nudge. Each thread gets the waypoint it has been named
    for least (`nudge_offers`); a plant already given to another thread is not repeated. With no carriers the
    order is exactly the old one (priority, stable among equals).
  - **Hints**: one steered destination's `hint`, the one shown least (`hints_shown`), as `A DETAIL TO WORK IN,
    lightly and in passing...`. At most one per nudge, so one per cycle. Shown from the Open phase, when every
    viable destination is steered.
  - **The drive nudge**: from `narrow_until` (the `commit` phase on), the leader among viable destinations by
    stored score and up to two of its unplanted waypoints with a `plant`, as `PRIORITY THIS SCENE:`, each with
    the running thread that could deliver it. **It yields on a turn a pacing-loop rule is about to fire**
    (decision D): `_pacing_directive_will_fire` is a dry run of the directive's own decision (no `last_fired_rule`
    popped, no deferral counted), and a deferred or unarmed rule does not take the floor.
  - Only `plant` and `hint` text is added: never an ending's id, name, arc, criteria or `detect` (tested). The
    counters (`nudge_offers`, `hints_shown`) are additive endings-state keys; a regenerate restores them with the
    snapshot and rebuilds the same nudge.
  - **Trace**: the `nudge` event now says what steering did (mode, primary vs the unboosted primary, plants named
    and to which threads, hint, drive leader and waypoints, what it yielded to, characters added), and the report
    gained a `nudge steering` section and counts nudge offers alongside act offers in the steering association
    (with a per-surface split). The assumptions are #15-#19 in `docs/Steering_Review.md`.
  **Decisions made in passing - flag if wrong:**
  - **The boost is one step and a tie goes to the carrier**, not "carriers first": a low carrier does not outrank a
    high thread, a medium one ties a high one. The spec says only "raised".
  - **"Become drive nudges" is read as adding a priority line to the nudge**, not replacing it: the act line, the
    lead thread, hooks and the rest stay. Replacing them would have dropped content other systems put there.
  - **A hint appears from the Open phase**, when all viable destinations are steered, one at a time. The alternative
    is holding hints back until Narrow, which would make them meaningless in a story whose budget puts the whole
    middle in Open.
  - **Drive uses the leader among viable destinations**, not only the steered set, since a destination can lead on
    score without being among the top `steer_top` of the last check.
  - **Only running threads carry a plant on their line.** A dormant carrier is started by early activation, not
    nudged about.
  - **Not measured against a live model**; whether the narrator acts on any of this is what the trace is for.

- **CR-13, the story clock** (2026-09-27; trace build `steering-3`). `backend/clock.py` is the pure half and
  `story_engine` the wiring. A story that authors `plot.pacing.story_clock` gets `pacing.story_clock`,
  `idle_streak`, `push_fired` and `clock_step` (an old save adopts them at its current turn); one that does not
  gets nothing and every reader falls back to `turn_count`.
  - **Idle** is decided in code before the pipeline resolves (stat drift reads the clock), from the engines' own
    typed events (`subplot_beat` past `touched`, stat events and changes, social, items, leverage, fragments
    revealed, waypoint hits) plus what the core update applied (a flag actually set, a location actually changed, a
    newly named character). A finale turn is never idle, and a state-update pass that failed counts as moving.
  - **The streak**: idle turns 1..`free_idle_streak` leave the clock unchanged, the next advances it and so does
    each after; a turn that moves resets the streak and re-arms the push.
  - **On the story clock**: the ending budget and phases, funnel checks, terminal `min_turn` and the funnel's ledger
    stamps (`endings.turn`), `turn_gte`, stat `per_turn` drift, and the act-check cadence (`turns_since_act_check`
    grows only on a turn the clock moved). **Still on `turn_count`**: the nudge cadence, flag staleness, summary
    rollover, recent turns, relationship caps, timestamps, `first_seen_turn` and the finale's own length.
  - **A parked clock re-fires nothing.** A funnel check is "the clock is a multiple of `check_every`" and drift is
    "a multiple of `per_turn_interval`"; on a free idle turn the clock sits where it was, so both now require that
    the clock *moved this turn* (`clock.advanced`). Without it a parked clock would have re-run the commit judge
    and re-ticked a deadline on every idle turn.
  - **Prompt effects**: the options instruction gains a lean-forward sentence while the streak is used up (footer and
    the missing-options repair), and `push_directive` joins the narration prompt once per streak as `PACING
    DIRECTIVE: ...` from `_section_pacing_directive`. An armed pacing rule the same turn wins and the push is spent
    for that streak anyway; never in the finale.
  - The board's *not built* chip and the load warning are gone; the sample bar's Turn sets the clock too. The trace
    gains a `clock` event per turn, a `push` event and `story_clock` / `idle_streak` / `lean_forward` on the `turn`
    event; the report has a `story clock` section; `Steering_Review.md` adds #20-#24.
  **Decisions made in passing - flag if wrong:**
  - **Idle is read from validated events, not the model's raw diff**, so an id the engine rejected or a declared
    flag reported false moves nothing. The spec lists "a `done_when` newly true"; that is not counted on its own,
    because it is always the consequence of an observed change or of time, and neither should make a turn count.
  - **Funnel timestamps are story-clock values** (`waypoints_done`, `pruned`, `committed`, terminal cooldowns), since
    they share `endings.turn` with the budget. The trace's own `turn` stays the raw turn count.
  - **A failed state-update pass counts as a turn that moved.** The safe direction: an unknown turn is never free.
  - **The lean-forward sentence and the `PACING DIRECTIVE:` prefix are engine wording.** Both are plain framing of
    an authored or specified instruction, not story content.
  - **Not measured against a live model.** Whether the state-update pass reports enough for idle detection to be
    right is exactly what the `story clock` report section and the transcript are for.

- **L17: a budget for the whole narration prompt, not just the always-on slice** (2026-09-27). L14
  only ever covered rules/style/tracked entity, which stay a fixed size; `RECENT EXCHANGES` and
  `STORY SO FAR` are the two sections that grow toward `RECENT_TURN_LIMIT` turns and
  `SUMMARY_MAX_WORDS` as a save gets older, and L14 said nothing about them. `NARRATION_TOKEN_BUDGET`
  (20,000) is the author's own operating ceiling, set after reading real OpenRouter usage on
  `the_missing_core` (~16-17k input tokens observed, comfortable to 20k); `prompt_issues` now
  projects those two sections at their authored maximum (using a flat word estimate for the
  player's own unauthored action, sized to overestimate) and adds everything else at its real,
  already-live size. The projection matched observed usage on `the_missing_core` almost exactly
  (16,837 projected vs. ~16-17k real). `scripts/steering_report.py`'s `cost` section reports the
  same budget against actual logged narration prompt sizes from the trace, so a story can be
  checked against it both before and after real play; the two copies of the number are asserted
  equal (the STATUS_LABELS mirror pattern, since steering_report.py stays stdlib-only and cannot
  import author_lint). A pacing nudge's occasional text is not projected - bounded but
  intermittent, not part of what every turn pays.

**Goal.** Every "not built" chip disappears, eventually.

Order, as `Story_Mechanics_Update.md` §5 justifies (reference, not a build queue - see above):

1. **CR-03** visibility enforcement in the loader (allowlists from `x-visibility`).
2. **CR-01** tier `on_enter` hooks.
3. **CR-05 + CR-10 together.** Endings without carriers compete with `weighted_threads` for
   the same scenes (CR-10's own problem statement), so they ship as one change. This step
   also carries out D5 and D6:
   - **D5:** `mechanics.failure_conditions` and the `triggered_ending` engine are deleted.
     Terminals are `mechanics.endings.entries[]` with `kind: "terminal"`.
     - `_begin_endgame` stays as the shared finale machinery. It is the seam `failure.py`
       already routes through.
   - **D6: remove the player's end-story path.**
     - Delete `END_STORY_PHRASES`, `handle_end_story_request` and its generated-arc fallback,
       and the *Ending the story* entry in `frontend/help.html`.
     - `end_story_final_arc` is a `_timed` label, so it comes out of `STATUS_LABELS` and
       `DEFAULT_STEP_ESTIMATE_SECONDS` in the same change. `test_status_labels.py` asserts
       the mirror in both directions.
     - CR-05's "Player end story" touch point is dropped. The `endgame.cause` values become
       `committed`, `forced` and `terminal`.
   - **Consequence: every story must author `mechanics.endings`** with at least one
     catch-all destination. With no player command, a story without one can never end.
     - The loader refuses a template with no endings block, and lint L08 already errors on a
       missing catch-all.
     - The `example` story therefore needs endings authored. CR-10's "`example` keeps today's
       behaviour" acceptance criterion is withdrawn; its roleless subplots are still treated
       as `spine` with no `delivers`.
4. **CR-11** bonds, then side threads, including r5 (wider casts and `may_move`, vignettes,
   callbacks). Then **CR-12** player-started side threads, which extends the same engine's
   single observation field and retires `player_driven_goals`. This comes after step 3 because it needs the `bond`
   leaf (CR-02), the commit and finale hooks (CR-05), and CR-10's removal of generated
   texture. The generation call's `_timed` label goes into `STATUS_LABELS` and
   `DEFAULT_STEP_ESTIMATE_SECONDS` in the same change.
5. **CR-04** derived variables, then **CR-06** keyed lore.

Each step registers its engine, deletes the matching `validate()` "no reader" warning, and
drops the board's chip for those fields. When that step lands, the playable projection stops
omitting that module.

**Invariants that still bind, overhaul or not:**
- **One observation field per engine.** CR-05's `waypoints_hit` is the `endings` engine's
  single observation field. The total is measured against `core + 7` by
  `scripts/measure_baseline.py`, not asserted.
- **Tier C by default.** The commit judge and terminal confirmation are Tier C unless the
  module records why not. CR-05's open question 1 (flagship commit judge) is exactly such a
  "why not", and needs to be written down in the module.
- **Declare-to-bind warnings.** Add one for "authors ending destinations but no engine", as
  with stats, inventory and subplots.
**Gate:** per step, the CR's acceptance criteria. After step 3:
- all three stories load through `load_template()` with no warnings and play a scripted
  20-turn run with the stubs;
- no code path, prompt or help text lets the player end the story;
- a stubbed run past `commit_by` ends through a forced commit.

**Risk.** Promoting CR-05 without CR-10, because the funnel is the more exciting half.

---

## Phase S6: Simulator, playtest and AI assist

As §8, §9 and §7, plus CR-07 and CR-08 and the full Forms tab. Engine changes this phase
depends on:

- `take_turn(..., trace=True)`
- a per-call model override (`AUTHOR_MODEL`, `AUTHOR_PLAYTEST_MODEL`)
- per-turn stat-event logging for simulator calibration
- playtest saves under the reserved user id `author:<slug>`, built from the playable
  projection

Detailed phasing waits until S5 settles what the simulator is simulating.

**One §7 recipe pulled forward, on request, ahead of the rest of this phase.**
`backend/author_assist.py` implements exactly the "Ending → waypoints" `derive` recipe -
given a destination ending's `arc`/`criteria`/`hint`, propose 2-4 waypoints. Not the full §7
spec: one recipe, no Assist tab (a plain button on the ending's Detail panel instead), no
`instruction` param, no diff/Accept-Edit-Discard UI (a flat Accept per suggestion), no
40-entry `assist_log.json`. Still matches the spec's real constraints that matter for safety:
canon is never in the assembled context (the input-side leak protection §7 describes), and
it calls Gemini directly with `GOOGLE_API_KEY`/`GEMINI_MODEL` (`AUTHOR_ASSIST_MODEL` overrides
just the model) - never `story_engine.py`'s `call_llm`/`call_llm_json` or the gameplay tiers'
prompt-building, since a storyboard suggestion has nothing to do with a live turn. Originally
used its own OpenRouter key (`OPENROUTER_API_KEY_TOOL_ASSIST`) to keep budget/rate limits
separate from the gameplay tiers; retired after that key's free-tier workspace guardrail
404'd the default model and every guardrail-allowed free model left was itself 429ing from
its own shared upstream pool. It now shares Gemini account quota with `story_engine.py`'s own
fail-safe path - an accepted trade-off given this module has no fail-safe of its own. `POST
/author/<slug>/assist` takes `{model, ending_id}`, returns a suggestion fragment; accepting
one is pure client-side (pushes onto the board model, same as any other edit) - nothing is
saved until the author hits Save. Covered by `test_author_assist.py` (offline,
`google.generativeai` stubbed) and `test_author_routes.py`.

---

## Decisions for the author

| # | Decision | Status |
|---|---|---|
| D1 | The board writes final paths, inside `mechanics`. A story is unplayable (loudly) until S5 builds its engines, and the tool uses a playable projection meanwhile. | **Decided** |
| D2 | New `conditions.py`; per-call-site unknown-referent polarity; L10 save-blocking | **Decided** |
| D3 | Conditions, lint and preview are computed on the server, through the real engine modules | **Decided** |
| D4 | Scoped HTMX exception: client-rendered canvas, all server I/O through HTMX | **Decided** |
| D5 | `failure_conditions` is replaced by CR-05 terminals inside `mechanics.endings` (S5 step 3) | **Decided** |
| D6 | Players can no longer end the story. Stories end only through `mechanics.endings` (commit, forced commit or terminal), and every story must author endings with a catch-all. | **Decided** |
| D7 | `relationship_to_player` → `first_contact`, prompted only until the first scored interaction; `role` is author-only (S1) | **Decided** |
| — | CR-11: one-way bonds, and side threads as a parallel track with their own end conditions (S2 board, S5 step 4) | **Decided** 2026-09-23 |
| — | CR-11 r5 (location and item casts, vignettes, callbacks) and CR-12 (player-started side threads); faction life and off-screen news deferred | **Decided** 2026-09-23 |
