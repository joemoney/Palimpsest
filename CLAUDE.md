# Palimpsest

See `README.md` for the project's background, setting, and how to run it.

This file is **invariants only** — the things a change could break without
realising they were deliberate. It deliberately does not explain how any of it
works. For that:

| Topic | Where |
|---|---|
| How the engine actually works, as built | `docs/ARCHITECTURE.md` |
| The mechanic registry, as built | `docs/ARCHITECTURE.md` § *The Mechanic Registry* |
| Template/save schema and its design principles | `docs/SCHEMA_V2_SPEC.md` |
| Pacing loop, beats, counters, directives | `docs/Narrative_Pacing_Loop_Spec_v4.md` |
| Web UI design intent | `docs/Web_UI_Spec.md` |
| What the engine stores but never prompts | `docs/Narrative_Engine_Spec.md` |
| Engine v2 / schema v3 — design, not yet built | `docs/ENGINE_V2_SPEC.md` |
| Engine v2 phase order, gates and risks | `docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md` |

If you are about to change something below, read the matching section of
`docs/ARCHITECTURE.md` first — every rule here has a reason recorded there, and
most of them were written after something broke.

---

## Design Philosophy: "Tight Rails, Loose Paint"
Decided against both extremes: fully scripted branches lose the reactive,
emergent feeling that makes AI-driven CYOA worth building; theme-and-
worldbuilding-only causes drift (plot holes, forgotten stakes, tonal
inconsistency, especially on cheaper/smaller models). Instead, a hybrid:
1. **World rules = strict, non-negotiable** (`world.rules`) — magic limits,
   tone, content boundaries. Should almost never bend.
2. **Plot structure = adaptive waypoints, not fixed paths** (`plot.main_thread`,
   `plot.subplots`) — acts provide direction but can be added, modified, or
   pivoted mid-adventure. The model decides *how* the player gets there.
3. **Scene-level execution = fully free** (`plot.current_scene`) — this is
   where the "alive" feeling comes from.
4. **A pacing/director layer** — every N turns, inject a meta-instruction
   nudging the story toward the next waypoint, preventing infinite wandering
   without scripting every branch.
5. **Mid-adventure steering** (`plot_manager.py`, `subplot_manager.py`, the
   in-session `steer` command, or the web UI's Plot/Subplot Manager pages —
   `app.py` calls the same functions directly, no subprocess) bypasses
   narration and edits plot state directly — see README for the command
   reference. Reach for it only when the model won't arrive at a needed
   structural change on its own.
6. **Continuous, not finite** — no built-in stopping point. Subplots and
   acts are generated on demand rather than pulled from a fixed pool — see
   *Continuous / Long-Running Structure* in `docs/ARCHITECTURE.md`.

Start stricter than feels necessary — it's easier to loosen constraints once
the model proves it handles structure well than to rein in a session that's
already gone off the rails.

---

## Invariants

### Structure and pacing
- **No fixed act or subplot count.** Subplots regenerate on completion, acts are
  generated on demand, and there is no ceiling. Don't reintroduce a number that
  would give an open-ended story a definite endpoint.
- **Act advancement must not depend on subplot completion.** It fires on *either*
  a subplot completing this act *or* `act_check_frequency` turns elapsing.
  Requiring the first alone structurally forced every subplot to single-act
  length, which is exactly what a `multi_act` subplot needs not to happen.
- **The story ends only when the player asks.** Once `plot.endgame.requested` is
  set, `generate_new_subplot` and `check_and_advance_act` both no-op. Manual
  steering still works; only the automatic systems stop.

### Keeping LLM context bounded
- **The disk record may grow forever. What reaches a prompt must not.** Any new
  accumulating state needs the same treatment as the existing ones.
- **Summary rollover triggers on a batch threshold, not plain overflow.**
  Triggering on "longer than `RECENT_TURN_LIMIT`" means a rollover every turn
  past the tenth, costing a Tier A call each time and re-compressing the summary
  ~16 times by turn 26 instead of ~2.
- **The summary word cap is enforced in code, not merely requested in the
  prompt.** A real save reached 2,912 words against a 2,000-word instruction.
- **`pending_regenerate` holds exactly one entry** — a full pre-turn snapshot,
  restored by a whole-state swap rather than a diff.

### LLM backend
- **Tier A and Tier B are the same model**, distinguished only by the
  `reasoning` flag threaded per call site. Tier C is its own model. Don't
  collapse them into two tiers or split A/B into separate env vars.
- **Google/Gemini is not a real tier.** It is reserved for the offline test
  suite and for `call_llm`'s own fail-safe retry.
- **The fail-safe only ever falls back *to* Gemini, never away from it**, and
  only on a request-level failure — never a silent retry because output looked
  malformed. Its "already tried this" check must compare against the model
  actually attempted, not the raw argument, since `TESTING_FORCE_GOOGLE`
  substitutes silently.
- **Narration and state-update are separate LLM calls.** One call trying to do
  both produces messier JSON. Keep the split when extending state coverage.
- **`LLMUnavailableError` is the single stable failure type**, including for a
  200 with empty content. A failure here is always recoverable: `call_llm` runs
  before any save write, so no turn is ever half-persisted.
- Provider timeouts stay sized so that primary-plus-fallback fits inside
  gunicorn's `--timeout`.

### State shape
- **The model can never introduce a new stat axis.** Stats are seeded at save
  creation from `protagonist.stats` plus any `character_creation`
  `starting_stats`, and the observation pass may only move an existing one.
  §8.1's `axes.<axis>.start` is deliberately **not** implemented — a third seeding
  source would break the stories that seed through character creation.
- **Authoring `axes.<axis>.costs` switches the whole story to priced stats.** The model then
  names events from a closed vocabulary and the engine does the arithmetic; authoring none
  keeps the v2 delta map. It is all-or-nothing per story, not per axis, and an axis with no
  `costs` entry can then only move by `per_turn` drift.
- **Relationship scores are deltas, not absolutes**, and eviction drops whatever
  sits *closest to neutral* — a story's strongest bonds must never silently
  disappear.
- **`relationships[name]["npc_id"]` is the only link** between a relationship and
  a character record. Exact-name matching alone is fragile and caused a real bug;
  once a link exists, the stored id is the source of truth.
- **Every NPC record is created through `story_engine.insert_character()`**, and
  every subplot through `insert_subplot()`.
- **Adding a `character_creation` step retroactively halts every live save** for
  that story until the player answers it. Usually right, never opt-in.

### Mechanic registry (see `docs/ARCHITECTURE.md` § *The Mechanic Registry* for how)
- **Declare-to-bind: a `mechanics` block with no `"engine"` key binds nothing.** That is not
  an error — it is a mechanic the registry does not own yet — but a block that *should*
  declare one and doesn't loads **inert**: no state, no prompt line, no observation field,
  and a story that looks fine and never progresses. Silent inertness is the failure this
  architecture exists to remove, so an engine declared with an empty config raises rather
  than adjudicating nothing, and `mechanics.validate()` warns for seeded stats, seeded
  inventory and authored subplots with no engine. Don't quiet those warnings.
- **A mechanics block must live inside `mechanics`.** Obvious until it isn't: a block placed
  one level up parses fine, seeds nothing, binds nothing, and reads exactly like a story that
  never authored the mechanic. This has happened once.
- **An engine contributes at most one observation field per turn**, and may contribute none
  (a cadence). An engine that needs two is two engines, or one field with a richer type —
  both existing cases were merged rather than granted an exception. The budget is `core + 7`
  and is *measured* by `scripts/measure_baseline.py`, not asserted.
- **An engine that contributes prompt text must declare a `prompt_budget`.** Zero means "no
  narration text at all" and is legitimate; zero *with* text raises, and so does exceeding it.
- **`resolve()` is pure and effects are absolute.** It returns `Effect`s and never touches
  `ctx`. "Set it to 12" replays; "subtract 3" depends on what already applied this turn, so
  an engine pricing several events against one axis projects locally and emits the final value.
- **An engine call is Tier C unless its module records why not.** Stricter than the rest of
  the codebase on purpose: the registry makes call sites cheap to add. Measured — Tier C is
  the *more* self-consistent tier on 8 of 11 classification fields.
- **Gate predicates fail open, never closed.** An unknown referent, a malformed predicate, an
  empty `any`, a stat axis the save lacks — all read as *satisfied*. A typo should cost a
  locked door, never a save whose main thread can never advance.
- **Flag predicates read `flags.active ∪ flags.archive`.** `archive_stale_flags` retires a
  flag out of `active` on a 10-turn window while `act_check_frequency` defaults to 12, so
  reading `active` alone is consulted on a cadence longer than the flag's own lifetime there
  and is reliably false exactly when it matters.
- **The refusal rail is the location veto, not the detector.** The Tier C detector decides
  whether to raise the modal and is ~90% accurate; `blocking()` vetoes a gated
  `scene_update.location` regardless. Trade recall for precision and never the reverse — a
  miss has a backstop, a false positive refuses a legitimate action and nothing catches it.
- **Saves are still schema version 2.** Every phase kept storage where it was, so the v2→v3
  cutover was never performed and existing saves load unchanged. What went to v3 is the
  template shape. Don't relocate save state without a migration and a version bump.

### Schema (see `docs/SCHEMA_V2_SPEC.md` §1 for the full principles)
- **An absent optional module means the feature does not exist** — no state, no
  prompt section, no schema field, no empty header, no zeroed counter.
- **Read paths never assume optional structure.** `.get()`/`setdefault()`
  throughout; the minimal template must run.
- **No engine constant may encode a creative decision.** If a novelist would have
  an opinion about it, it belongs in the template.
- **Determinism belongs to the engine, never to the prompt** (P-7). If a feature
  must be correct *every* time rather than usually, code produces it; the template
  only opts in and configures how it looks. The test is what failure costs: a
  slightly worse scene is a template concern, a broken promise to the player is
  an engine one. Asking a model to transcribe its own stat block drifted 8 points
  over a real save, which is why `mechanics.stats.readout` exists — the model
  emits a token and never a number.
- `test/fixtures/` + `test_genre_conformance.py` are what make these enforceable
  rather than aspirational. A new optional module needs a fixture that omits it.

### Web UI
- **Built with HTMX, declaratively.** Don't introduce a parallel
  `fetch`/DOM-patch implementation alongside it.
- **Turn-taking is asynchronous** — kickoff returns `202`, the client polls, then
  fetches the result. This exists because the Cloudflare tunnel cancels a
  long-held response at ~100–125s even though the turn succeeds.
- **`STATUS_LABELS` and `DEFAULT_STEP_ESTIMATE_SECONDS` mirror the `_timed()`
  call sites.** Adding an LLM call to the turn path means adding it to both;
  `test_status_labels.py` asserts the mirror both ways. This has drifted once.
- **Narration markup is exactly three markers** (`**bold**`, `*italic*`,
  `__underline__`), and escaping always runs before the marker regexes, so LLM
  output can never inject real markup.
- Label sheets need their `filelock` (unlocked, concurrent POSTs silently dropped
  labels) and stay gated on `LABEL_SHEETS_USER`.

### Storage and layout
- **All state access goes through `state_store.py`.** All four entry points
  (`story_engine`, `plot_manager`, `subplot_manager`, `app`) use it. Don't read or
  write story state any other way.
- The five backend modules live under `backend/` and import each other **flat**
  (`import state_store`). Every entry point puts `backend/` on `sys.path` itself;
  cwd stays the repo root, which is what lets `STORIES_DIR`/`DATA_DIR` be plain
  relative strings. `app.py` passes `template_folder`/`static_folder` explicitly
  because Flask would otherwise resolve them relative to `backend/`.
- **Adding a story is a content change, not a code change** — drop in
  `stories/<slug>/template.json`.
- **A story meant to stay private never goes in this repo's history.** It goes in
  the single private submodule at `stories/private/<slug>/`, which is a second story
  root, not a story — `state_store.story_roots()` scans both, and a slug in both
  resolves to the public one. Mounted inside `stories/` so docker-compose's existing
  bind mount covers it; don't move it out without adding a second mount.
- `data/` is runtime-only and gitignored. Accounts are provisioned server-side;
  there is deliberately no self-service registration route.

---

## Testing
`test/` is offline-first: `test/_llm_stubs.py` stubs `dotenv`,
`google.generativeai`, `filelock`, and `werkzeug.security` so most of the
suite runs with zero pip-installed dependencies and no network access —
useful in sandboxes without pip access. `test_app_routes.py` is the one
exception (needs real `flask`); it skips gracefully (exit 0) rather than
failing when `flask` isn't importable. Run the whole suite with
`python test/run_all.py`. When adding a new engine function that calls the
LLM, follow the existing pattern: accept the prompt-building/parsing as
something `call_llm`/`call_llm_json` can be monkeypatched around, so it stays
testable without a real API key.

`test/fixtures/` holds three genre-conformance templates (`regency.json`,
`courtroom.json`, `survival.json`), exercised by `test_genre_conformance.py`.
They are the executable form of `docs/SCHEMA_V2_SPEC.md`'s P-6 - the claim that
an author can write a wholly different genre without touching Python - and each
uses a deliberately different subset of the optional `mechanics` modules. They
live outside `stories/` on purpose: anything under `stories/<slug>/` is picked
up by `state_store.list_stories()` and becomes startable by a real player, so
the test loads them through `freeze`/`new_save_state` directly.

Two things about them are easy to weaken by accident. The assertions run **in
both directions** - an absent module must leak no marker into either prompt,
*and* an authored module must actually reach them; one-directional absence
testing passes happily for a module that was never wired up at all. And the
per-fixture "modules absent" lists are written out in the test rather than
derived from the fixture files, so deleting a module from a fixture fails
loudly instead of silently shrinking what's covered. If a new optional module
is added to the schema, it needs a marker entry and a fixture that omits it,
or nothing is guarding P-2 for it.

Tests that load `se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)`
should derive their expectations from whatever that template actually
contains (subplot count, memory fragments, etc.) rather than hardcoding
assumptions from one specific story - `DEFAULT_STORY_SLUG` has changed once
already (New Babel → example) and content-specific assumptions silently
broke two tests when it did.

`test_app_routes.py` exercises `POST /api/turn`/`/api/regenerate` through
Flask's real (synchronous) test client, but the routes themselves now kick
the actual turn off on a background thread and return `202` almost
immediately (see "Asynchronous turn-taking" under "Web UI") — the test
client's `.post(...)` call returning does *not* mean `story_engine.take_turn`
has run yet. `wait_for_idle(user_id, ...)` (defined in that file, polling
`state_store.read_turn_status` directly rather than over HTTP) has to be
called before asserting on save-file state or fetching `GET
/api/turn/result` — any new turn-taking assertion added to that file needs
to follow the same poll-then-fetch shape the real client uses, not assume
the kickoff POST already did the work.
