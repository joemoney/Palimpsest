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
| Failure ending | `mechanics.endings.entries[]`, `kind: "terminal"` (CR-05; D5). Replaces `mechanics.failure_conditions`. | **Not built** (today's `failure_conditions` engine is deleted in S5) |
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

**Goal.** Stat progression and funnel timing are visible and editable.

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
- **Lore view** on `mechanics.lore` (CR-06).
- **Full linter** L01–L16, with a CLI twin at `scripts/lint_template.py`. The repository has
  `scripts/`, not the `tools/` package that §6 names.

**Gate.**
- For a sample state, the preview matches byte for byte the prompt a real turn assembles in
  that state.
- The leak test fails when `canon.truth` is pasted into a character's `description`, and
  passes on all three stories.

**Risk.** A preview that approximates the prompt. Once the author trusts it, an approximation
is worse than no preview.

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
