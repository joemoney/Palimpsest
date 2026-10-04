# Palimpsest

Read `README.md` for the project background, the setting, and how to run it.

This file contains invariants only. An invariant is a rule that a change can break without
the author knowing it was deliberate. This file does not explain how the system works. Find that
in `docs/` (start with `docs/Design_Overhaul.md`).

These five documents describe the overhaul that is now under construction. They override
everything below and everything in `docs/Pre-V3 docs/` where they conflict:
`docs/Design_Overhaul.md`, `docs/Authoring_Tool_Spec.md`, `docs/Story_Mechanics_Update.md`
(CR-01–CR-12; §0 lists what is superseded), `docs/analysis_and_plans/AUTHORING_TOOL/AUTHORING_TOOL_PHASES.md`
(phases, gates, D1–D7), and `docs/How_Threads_Work.md`. The `docs/Pre-V3 docs/` files describe the
engine before the overhaul. They are reference, not a live spec.

Before you change anything below, read the matching section of `docs/Pre-V3 docs/ARCHITECTURE.md`.
Then check if an Authoring tool decision (D1–D7, below) changed it. Every rule here has a recorded
reason. Most rules exist because something broke.

**If a request conflicts with a decision recorded here, tell the user and give the resolution
options.** Do not comply silently: the conflict grows unseen. Do not refuse silently: the request
can be the override that is needed. The user makes the decision after they see the trade-off.

---

## Build order: the storyboard leads, the engine follows
Decided 2026-09-24, during the overhaul. The authoring tool (the storyboard) is the upstream
design surface. It can write anything a story needs, including fields and `mechanics` blocks
that no engine can use yet. The engine catches up to what is authored. The engine never leads.

This extends D1 ("final paths, always"). A story can be unplayable, but it must fail *loudly*
(`UnknownEngineError`; `load_template()` refuses a template with no endings block; a field
that round-trips but has no reader). This is correct while the engine piece does not exist.
Do not water down the authored content to avoid it.

**Engine work (Phase S5) is demand-driven, not batch-planned.** Do not plan and build a whole
numbered phase step before real authored content uses it. Engine work lands piece by piece, in
the order that real storyboard authoring needs. See the Phase S5 note in
`AUTHORING_TOOL_PHASES.md`.

- A request that only waits for "the engine does not do that yet" is not a conflict. This is the
  expected state. Build the engine piece that it needs.
- A request that conflicts with a *decided* invariant (this file, a D-numbered decision, a
  locked CR) is a conflict. Use the conflict rule above.

---

## Design Philosophy: "Tight Rails, Loose Paint"
Fully scripted branches lose the reactive, emergent feel that makes AI-driven CYOA worth
building. Theme-only design causes drift (plot holes, forgotten stakes, tone changes),
especially on cheaper models. This project uses a hybrid:
1. **World rules are strict and non-negotiable** (`world.rules`): magic limits, tone, content
   limits. They almost never bend.
2. **Plot structure is adaptive waypoints, not fixed paths** (`plot.main_thread`,
   `plot.subplots`). Acts give direction. They can be added, changed, or pivoted during play.
   The model decides *how* the player gets there.
3. **Scene-level execution is fully free** (`plot.current_scene`). The "alive" feel comes from here.
4. **A pacing/director layer.** Every N turns, the engine injects a meta-instruction that moves
   the story toward the next waypoint. This stops endless wandering without scripting every branch.
5. **Mid-adventure steering** (`plot_manager.py`, `subplot_manager.py`, the in-session `steer`
   command, or the web UI Plot/Subplot Manager pages) edits plot state directly and skips
   narration. `app.py` calls the same functions directly (no subprocess). See the README for
   commands. Use steering only when the model does not reach a needed structural change by itself.
6. **Continuous, not finite.** There is no built-in stopping point. The engine generates subplots
   and acts on demand, not from a fixed pool (see *Continuous / Long-Running Structure* in
   `docs/Pre-V3 docs/ARCHITECTURE.md`). The overhaul supersedes this (see D6): a story with an
   authored `mechanics.endings` block has a designed endpoint. Only the engine reaches it.
   The player never does.

Start stricter than necessary. It is easier to loosen the constraints after the model proves it
handles structure than to recover a session that already went wrong.

---

## Invariants

### Structure and pacing
- **No fixed act or subplot count.** Subplots regenerate when they complete. Acts are generated
  on demand. There is no ceiling. Do not add a number that gives an open-ended story a definite end.
- **Act advancement must not depend on subplot completion.** It fires on *either* a subplot that
  completes this act *or* `act_check_frequency` turns that elapse. If it needs the first
  condition alone, every subplot is forced to a single act. A `multi_act` subplot must not be.
- **A story ends only through `mechanics.endings`:** a committed destination (including a forced
  commit at `commit_by`) or a confirmed terminal (decision D6).
  - The player has no command to end a story. `end story` and its variants are retired, with
    `handle_end_story_request`.
  - After an ending is committed, `generate_new_subplot` and `check_and_advance_act` both do nothing
    (as `plot.endgame.requested` did before).
  - A story with no `mechanics.endings` block cannot end. No such story should exist after the
    overhaul. Every story must author an endings block with a catch-all.

### Keeping LLM context bounded
- **The disk record can grow forever. What reaches a prompt must not.** Give any new accumulating
  state the same limits as the existing state.
- **Summary rollover triggers on a batch threshold, not on plain overflow.** If it triggers on
  "longer than `RECENT_TURN_LIMIT`", it runs every turn after the tenth. Each run costs one Tier A
  call. The summary is then compressed about 16 times by turn 26, not about 2 times.
- **Code enforces the summary word cap. A prompt request is not enough.** A real save reached
  2,912 words against a 2,000-word instruction.
- **`pending_regenerate` holds exactly one entry:** a full pre-turn snapshot. Restore it with a
  whole-state swap, not a diff.

### LLM backend
- **Tiers are roles, not models.** Configure each tier for the strength of the model that fills it.
  The engine must stay model-agnostic. (It started on DeepSeek. That is no longer assumed.)
  Current assignment (decided 2026-10-03):
  - **Narration** (Tier A): `claude-sonnet-5-5`, low effort.
  - **Judgment** (Tier B: commit judge, terminal confirmation, act generator): `claude-opus-5-5`,
    thinking always on.
  - **Extraction** (Tier C: classification and state extraction): `claude-haiku-4-5-20251001`,
    no extended thinking.
- Thread per-call behavior (effort, thinking) through each call site. Never hardcode it to a
  provider. Never hardcode a model name outside configuration.
- Tier A and Tier B can be different models. Code must not assume that they share one.
  (This supersedes "A and B are the same model".)
- **Keep each role's model distinct from the others.** `_tier_reasoning()` finds the reasoning level
  from the *model name*. If two roles use one model, they silently share the setting of whichever
  role matches first.
- Env vars are `NARRATION_*`, `JUDGMENT_*`, `EXTRACTION_*` (each with `PROVIDER`, `MODEL`,
  `REASONING`). The old `TIER_AB_*` and `TIER_C_*` do not exist.
- **Google/Gemini is not a real tier.** Use it only for the offline test suite and for the
  `call_llm` fail-safe retry.
- **The fail-safe falls back only *to* Gemini, never away from it.** It runs only after a
  request-level failure. Never retry silently because the output looked malformed. Its "already
  tried this" check must compare against the model that was actually attempted, not the raw
  argument. `TESTING_FORCE_GOOGLE` substitutes the model silently.
- **Narration and state-update are separate LLM calls.** One call that does both gives messier
  JSON. Keep the split when you add state coverage.
- **`LLMUnavailableError` is the single stable failure type.** It includes a 200 response with
  empty content. This failure is always recoverable: `call_llm` runs before any save write, so no
  turn is half-saved.
- Size provider timeouts so that the primary call plus the fallback fit inside the gunicorn
  `--timeout`.

### State shape
- **The model can never add a new stat axis.** Stats are seeded at save creation from
  `protagonist.stats` and any `character_creation` `starting_stats`. The observation pass can
  only move an existing axis. §8.1 `axes.<axis>.start` is deliberately **not** implemented. A
  third seeding source would break stories that seed through character creation.
- **Authoring `axes.<axis>.costs` switches the whole story to priced stats.** The model then names
  events from a closed vocabulary, and the engine does the arithmetic. If you author no `costs`,
  the story keeps the v2 delta map. The switch is per story, not per axis. After it, an axis with
  no `costs` entry can move only by `per_turn` drift.
- **Relationship scores are deltas, not absolutes.** Eviction drops the score *closest to
  neutral*. The strongest bonds of a story must never disappear silently.
- **A character's name is its only identity.** `world.characters` (authored) and
  `state.characters` (discovered) use the same canonical name as key. There is no `npc_id`.
  (An older version of this rule described an `npc_id` link. The v2 code never worked that way.)
  Exact-name matching breaks if names diverge. `_existing_character_names` and the generation
  prompts stop the model from creating a second name for the same character.
- **Create every NPC record with `story_engine.insert_character()`.** Create every subplot with
  `insert_subplot()`.
- **A new `character_creation` step halts every live save.** This does not matter during the
  overhaul (saves are disposable at cutover, see below). The rule is correct for any later story:
  it is usually right, and it is never opt-in.

### Mechanic registry (see `docs/Pre-V3 docs/ARCHITECTURE.md` § *The Mechanic Registry*)
- **Declare-to-bind: a `mechanics` block with no `"engine"` key binds nothing.** This is not an
  error. It is a mechanic that the registry does not own yet. But a block that *should* declare
  an engine and does not loads **inert**: no state, no prompt line, no observation field. The
  story looks fine and never progresses. The architecture exists to remove this silent failure.
  - An engine declared with an empty config raises. It does not adjudicate nothing.
  - `mechanics.validate()` warns for seeded stats, seeded inventory, and authored subplots that
    have no engine. Do not silence these warnings.
- **A mechanics block must be inside `mechanics`.** A block one level up parses without error,
  seeds nothing, binds nothing, and looks like a story that never authored the mechanic. This
  has happened once.
- **An engine contributes at most one observation field per turn.** It can contribute none (a
  cadence). An engine that needs two fields is two engines, or one field with a richer type. Both
  existing cases were merged. No exception was given. The budget is `core + 7`.
  `scripts/measure_baseline.py` *measures* it. It is not asserted.
- **An engine that contributes prompt text must declare a `prompt_budget`.** Zero means "no
  narration text" and is valid. Zero *with* text raises. Exceeding the budget raises.
- **`resolve()` is pure, and effects are absolute.** It returns `Effect`s and never touches `ctx`.
  "Set it to 12" replays correctly. "Subtract 3" depends on what already applied this turn. An
  engine that prices several events against one axis projects locally and emits the final value.
- **An engine call is Tier C unless its module records why not.** This rule is stricter than the
  rest of the code on purpose, because the registry makes call sites cheap to add. Measured: Tier C
  is the *more* self-consistent tier on 8 of 11 classification fields.
- **Gate predicates fail open, never closed.** An unknown referent, a malformed predicate, an
  empty `any`, or a stat axis that the save lacks all read as *satisfied*. A typo must cost a
  locked door. It must never cost a save whose main thread cannot advance.
- **Flag predicates read `flags.active ∪ flags.archive`.** `archive_stale_flags` moves a flag out
  of `active` after a 10-turn window. `act_check_frequency` defaults to 12. If you read `active`
  only, the check runs less often than the flag lives, so it is false exactly when it matters.
- **The refusal rail is the location veto, not the detector.** The Tier C detector decides if the
  modal is raised. It is about 90% accurate. `blocking()` vetoes a gated `scene_update.location`
  in all cases. Trade recall for precision, never the reverse. A miss has a backstop. A false
  positive refuses a legitimate action and nothing catches it.
- **Saves are disposable during the overhaul.** Engine v2 kept saves at schema version 2 while
  only the template shape moved. The Authoring Tool overhaul does not give that benefit.
  - Templates that the board writes are `schema_version: 3` (see *Authoring tool*).
    `load_template_raw` accepts 2 and 3, and upgrades 2 in memory.
  - The save format can change freely with a version bump. The engine refuses old saves at load.
    It does not migrate them.
  - The operator discards `data/saves/` at cutover. This is not a code change. Nothing in the
    repository deletes saves.
  - After the overhaul ships, restore the old rule: a change to save state needs a migration and
    a version bump.

### Authoring tool (D1–D7; reasoning is in `AUTHORING_TOOL_PHASES.md`)
- **D1: the board writes final template paths, inside `mechanics`, before the engine that reads
  them exists.** No staging namespace. A story with an unregistered module fails `load_template()`
  loudly (`UnknownEngineError`). This is correct. The tool reads with `load_template_raw()`, never
  depends on `validate()`, and builds preview and playtest from a *playable projection* (the
  template without unregistered `mechanics` blocks). An unbuilt module shows as left out.
- **D2: the CR-02 condition grammar is in `backend/conditions.py`, separate from `gate.satisfied()`.**
  Gates and `activate_when` fail open. `ready_when`, `done_when` and `fail_when` fail **closed**,
  because a typo there can commit or prune an ending permanently. Each condition call site
  declares its polarity. Lint rule L10 (unknown flag/stat/fragment/character) blocks save for
  every condition field.
- **D3: the server computes conditions, lint and prompt preview, through the real engine modules.**
  Do not write a parallel JS implementation. The deployment has no JS runtime to test one.
- **D4: the board page (`/author/<slug>/board`) is a scoped exception to "built with HTMX".** The
  client renders its canvas (SVG, drag, pan, zoom) from a JSON model. Every server exchange still
  uses HTMX (`hx-vals`; `HX-Trigger` payloads or fragments). No hand-written `fetch` elsewhere.
- **D5: `mechanics.failure_conditions` / `triggered_ending` is retired.** A failure ending is a
  `mechanics.endings` entry with `kind: "terminal"` (stat `ready_when` plus judge confirmation).
- **D6: the player cannot end the story.** See *Structure and pacing*.
- **D7: `world.characters[name].relationship_to_player` is renamed `first_contact`.** The narrator
  sees it only until that character's first scored relationship interaction (a character with
  score `null`, for example from `insert_character`, still shows it). `role` is author-only: never
  prompt it, because it often states where an arc goes. `world.factions[].relationship_to_player`
  is a different field and keeps its name.

### Schema (principles: `docs/Pre-V3 docs/SCHEMA_V2_SPEC.md` §1)
- **An absent optional module means the feature does not exist.** No state, no prompt section, no
  schema field, no empty header, no zeroed counter.
- **Read paths never assume optional structure.** Use `.get()` and `setdefault()` everywhere. The
  minimal template must run.
- **No engine constant may encode a creative decision.** If a novelist can have an opinion about
  it, it belongs in the template.
- **Determinism belongs to the engine, never to the prompt** (P-7). If a feature must be correct
  *every* time, and not only usually, code produces it. The template only opts in and sets how it
  looks. The test is the cost of a failure: a slightly worse scene is a template concern. A broken
  promise to the player is an engine concern. A model that transcribed its own stat block drifted
  8 points over a real save. For this reason `mechanics.stats.readout` exists: the model emits a
  token and never a number.
- `test/fixtures/` and `test_genre_conformance.py` make these rules enforceable. A new optional
  module needs a fixture that omits it.

### Web UI
- **Built with HTMX, declaratively.** Do not add a parallel `fetch`/DOM-patch implementation.
- **Turn-taking is asynchronous.** Kickoff returns `202`, the client polls, then it fetches the
  result. The Cloudflare tunnel cancels a long-held response at about 100–125 s, even when the
  turn succeeds.
- **`STATUS_LABELS` and `DEFAULT_STEP_ESTIMATE_SECONDS` mirror the `_timed()` call sites.** When you
  add an LLM call to the turn path, add it to both. `test_status_labels.py` checks the mirror in
  both directions. This has drifted once.
- **Narration markup is exactly three markers:** `**bold**`, `*italic*`, `__underline__`. Escaping
  always runs before the marker regexes. LLM output can then never inject real markup.
- Label sheets need their `filelock`. Without it, concurrent POSTs silently dropped labels. They
  stay gated on `LABEL_SHEETS_USER`.

### Storage and layout
- **All state access goes through `state_store.py`.** All four entry points (`story_engine`,
  `plot_manager`, `subplot_manager`, `app`) use it. Do not read or write story state any other way.
- The five backend modules are in `backend/` and import each other **flat** (`import state_store`).
  Each entry point puts `backend/` on `sys.path` itself. The cwd stays the repo root, so
  `STORIES_DIR` and `DATA_DIR` can be plain relative strings. `app.py` passes
  `template_folder` and `static_folder` explicitly, because Flask would resolve them relative to
  `backend/`.
- **Adding a story is a content change, not a code change.** Add `stories/<slug>/template.json`.
- **Stories do not need to stay out of the repo history.** Decided 2026-10-03: the private-submodule
  split (`stories/private/<slug>/` as a second story root) is retired. Story content, including
  content that was in `palimpsest-stories`, can be public in this repo. Do not add a second story
  root. A story is `stories/<slug>/template.json`.
- `data/` is runtime-only and gitignored. The operator provisions accounts on the server. There is
  no self-service registration route, on purpose.
- **`.env`: lines 1–26 are off-limits (they hold secrets). From line 27, it is plain
  configuration. You can read and edit it.** The `NARRATION_*`/`JUDGMENT_*`/`EXTRACTION_*` tier
  settings, feature flags, and their comments are there. Read it with an offset (`Read` from
  line 26). Never read the whole file. Never print or copy anything above line 26.

---

## Testing
- `test/` is offline-first: `test/_llm_stubs.py` stubs `dotenv`, `google.generativeai`, `filelock`
  and `werkzeug.security`. `test_app_routes.py` needs real `flask` and skips (exit 0) without it.
  Run everything with `python test/run_all.py`.
- A new engine function that calls the LLM must accept prompt-building and parsing that
  `call_llm`/`call_llm_json` can monkeypatch.
- `test/fixtures/` (`regency.json`, `courtroom.json`, `survival.json`) are the executable form of P-6.
  They are outside `stories/` on purpose: `state_store.list_stories()` would make them playable.
  Tests load them through `freeze`/`new_save_state`.
- Fixture assertions run **in both directions**: an absent module leaks no marker into a prompt,
  and an authored module reaches it. The "modules absent" lists are written out in the test, not
  derived from the fixtures. A new optional module needs a marker entry and a fixture that omits it.
- Tests that use `load_template(DEFAULT_STORY_SLUG)` must take expectations from that template's
  content. Do not hardcode one story's details.
- After `POST /api/turn` or `/api/regenerate` in `test_app_routes.py`, the turn is not done (the
  route returns `202`). Call `wait_for_idle(user_id, ...)` before you assert on save state or fetch
  `GET /api/turn/result`.
