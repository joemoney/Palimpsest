# Palimpsest

See `README.md` for the project's background, setting, and how to run it.
This file covers only what's needed to work on the code directly: design
invariants and conventions that a change could easily violate without
knowing they're intentional.

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
   acts are generated on demand rather than pulled from a fixed pool. See
   below.

Start stricter than feels necessary — it's easier to loosen constraints once
the model proves it handles structure well than to rein in a session that's
already gone off the rails.

## Continuous / Long-Running Structure
No pre-set number of acts or subplots is required to "finish" — a fixed
count baked into a story's template would put a definite endpoint on what's
meant to be open-ended. Don't reintroduce one.

- **Subplots regenerate automatically.** Each story's template seeds a small
  starting pool (`plot.subplots`), but whenever one completes,
  `story_engine.generate_new_subplot()` invents a replacement via its own LLM
  call so the pool stays topped up to `pacing.max_parallel_subplots`. Also
  fires from the manual `subplot_manager.py progress` path, so manual and
  automatic play stay consistent.
- **Acts are endless chapters, not a fixed 3-act structure.** Only Act 1
  ships pre-authored. At each pacing checkpoint (once at least one subplot
  has completed this act), `story_engine.check_and_advance_act()` asks the
  LLM to judge — qualitatively, against `completion_signals` context, not a
  numeric threshold — whether the act feels resolved, and if so generates
  the next one in the same call. There is no ceiling.
- **The story only ends when the player asks.** A recognized phrase ("end
  story", "end the story", "conclude the story", "wrap up the story")
  triggers `story_engine.handle_end_story_request()`, which generates a
  closing `final_arc`, appends it as a finale act, and sets
  `plot.endgame.requested = True`. From that point on, `generate_new_subplot`
  and `check_and_advance_act` both no-op — the prompt instead directs the
  model to resolve open threads and end the narration with the literal line
  `THE END` once truly concluded. Manual `plot_manager.py` steering still
  works during the ending; only the *automatic* systems stop.

### Keeping LLM Context Bounded
The *disk* record (each user's save file under `data/saves/`) is allowed to
grow forever — nothing is deleted, only archived. What must **not** grow
unboundedly is what gets stuffed into a prompt, since that's real cost and
real drift risk on every call. Any new accumulating state needs the same
treatment as these existing ones:

- **`history_log.recent_turns`** — capped at `RECENT_TURN_LIMIT` (10) turns;
  older ones roll into `compressed_summary`.
- **`history_log.compressed_summary`** — capped at `SUMMARY_MAX_WORDS`. Each
  rollover re-summarizes *the existing summary plus the new turns* back under
  that cap and replaces it, rather than appending forever.
- **Subplot dedup context** (`generate_new_subplot`) and **act-advancement
  context** (`check_and_advance_act`) both used to pull from
  `plot.completed_subplots`, which accumulates for the whole game. Now
  bounded: subplot generation only sees the most recent
  `SUBPLOT_TITLE_HISTORY_LIMIT` (15) completed titles plus current live ones;
  act advancement only sees subplots completed *since the current act began*
  (via `pacing.subplots_completed_this_act`) — also a correctness fix, since
  judging "is this act done" on subplots from three acts ago never made
  sense.
- **`player.flags`** is split into `flags_active` (bounded, fed into every
  prompt) and `flags_archive` (unbounded, disk-only, never sent to the LLM).
  The state-update pass tags each new flag `pinned: true/false` (foundational
  fact that should never be forgotten vs. situational). `archive_stale_flags()`
  runs every turn and retires non-pinned flags once their setting turn falls
  outside the `recent_turns` window — by then the turn that set it has
  already passed through a `compressed_summary` rollover, so nothing
  important is silently lost. `FLAGS_ACTIVE_LIMIT` (25) is a hard-cap
  fallback that evicts the oldest non-pinned flags first.
- **`history_log.full_transcript`** — unbounded, disk-only verbatim archive of
  every turn's full text, for a human to read back later. Populated only at
  the moment a batch of turns overflows `recent_turns` and would otherwise be
  lossy-summarized into `compressed_summary` (`update_state_after_turn`) —
  not appended every turn, since turns still inside the `recent_turns` window
  are already present there in full. Never read by `build_system_prompt` or
  any other LLM call, so it costs nothing in context regardless of game
  length.
- **`history_log.pending_regenerate`** — bounded to exactly one entry,
  overwritten every turn (not a growing stack): `{state, player_action}`,
  where `state` is a full deep-copied snapshot of everything *before* the
  most recent turn. `regenerate_last_turn()` restores that snapshot and
  re-runs `player_action` through a fresh LLM call, which correctly
  undoes that turn's subplot progress, flags, and pacing counters before
  reapplying fresh ones — not a diff/patch, a full state swap. `take_turn()`
  always overwrites this with a fresh snapshot, so regenerating only ever
  targets the single latest turn. Also disk-only, never read by any prompt.

## Backend / Model Notes
- **LLM provider is chosen per tier, not once for the whole process**:
  `LLM_PROVIDER` (`"openrouter"` or `"google"`, default `"openrouter"`)
  governs the narration tier; `STATE_UPDATE_PROVIDER` (same two values,
  default `"google"`) governs the state-update tier independently.
  Narration defaults to OpenRouter/DeepSeek; state-update defaults to
  calling Google's Gemini API **directly** (the operator's own
  `GOOGLE_API_KEY`, not routed through OpenRouter) — each tier's *primary*
  provider is still a fixed, deliberate per-tier choice, not automatic
  failover between the two on its own terms (see the fail-safe bullet below
  for the one place an actual runtime fallback now exists). Both SDKs can
  therefore be live in the same process at once — `genai.configure()` and
  the `OPENROUTER_API_KEY`/`GOOGLE_API_KEY` presence checks at module load
  now trigger if *either* `LLM_PROVIDER` or `STATE_UPDATE_PROVIDER` needs
  that provider, not just `LLM_PROVIDER` alone. `call_llm(prompt, model,
  provider=None)` defaults `provider` to `LLM_PROVIDER`;
  `call_llm_json`/the `compressed_summary` rollover call site (the one
  place besides `call_llm_json` that calls `call_llm` directly) both pass
  `provider=STATE_UPDATE_PROVIDER` explicitly. The offline test suite
  forces `LLM_PROVIDER=google` (see `test/_llm_stubs.py`) specifically
  because that's the side with a stubbable SDK (`google.generativeai`) -
  `STATE_UPDATE_PROVIDER` isn't set there and so also defaults to
  `"google"`, matching `LLM_PROVIDER` in that environment (no mixed-mode
  behavior kicks in during the general offline suite).
  `test/test_openrouter.py` overrides *both* env vars to `"openrouter"` to
  exercise `_call_llm_openrouter` for both tiers via a mocked
  `requests.post`; `test/test_mixed_provider.py` is the one file that
  actually exercises the real default combination (narration via
  OpenRouter, state-update via Google) together, with its own smarter
  `google.generativeai` stub (records the model name it's constructed
  with, rather than the generic stub's "return `None`"). Each test file
  runs in its own subprocess (see `run_all.py`), so none of this leaks
  between files.
- **Two model tiers**, picked per call by `call_llm`'s/`call_llm_json`'s own
  default parameter values so most call sites never pass `model=` at all:
  `NARRATION_MODEL` (a bigger/pricier model - the one big creative
  generation per turn, `build_system_prompt`) vs `STATE_UPDATE_MODEL` (a
  cheaper/faster model - every other call: `update_progress_from_turn`,
  `generate_new_subplot`, `check_and_advance_act`,
  `handle_end_story_request`, and the `compressed_summary` rollover in
  `update_state_after_turn`). `STATE_UPDATE_MODEL` defaults to a real
  Gemini model name (`gemini-3.5-flash-lite`, no `"google/"` prefix - that's
  OpenRouter's slug convention, not the direct API's), matching
  `STATE_UPDATE_PROVIDER`'s default. Under the whole-process
  `LLM_PROVIDER=google` testing override specifically, `model` is ignored
  in favor of `GEMINI_MODEL` regardless of which tier or provider triggered
  the call - `NARRATION_MODEL` defaults to an OpenRouter slug that isn't a
  valid Gemini name, so respecting it there would break that path; outside
  of that override (i.e. real `STATE_UPDATE_PROVIDER=google` production
  use), `model` **is** respected, since the operator deliberately set
  `STATE_UPDATE_MODEL` to a real Gemini name in that case. See
  `_call_llm_google`'s and `call_llm`'s docstrings for the exact logic.
- **Gemini fail-safe**: if a tier's primary call raises `LLMUnavailableError`,
  `call_llm` retries once against the operator's own free-tier `GEMINI_MODEL`
  via a direct Google call, before giving up. This IS a genuine runtime
  fallback (the one exception to the "no automatic failover" framing above) —
  the point is to let `NARRATION_MODEL`/`STATE_UPDATE_MODEL` be freely swapped
  to whatever's being tried (an experimental OpenRouter model, say) without an
  unreachable or misconfigured model taking the whole app down. Narrow by
  design: only ever falls back *to* Gemini, never away from it, and only on a
  request-level failure — never a silent retry just because output looks
  malformed (`call_llm_json`'s caller still decides what to do with bad JSON,
  same as before). Skips the retry (raises immediately) if the primary call
  *was already* Gemini/`GEMINI_MODEL` — nothing left to fall back to.
  `test/test_failsafe.py` covers the fallback actually rescuing a call (both
  tiers) plus a regression guard for a real bug caught during development: the
  "already tried this" check has to compare against the model actually
  attempted, not the raw `model=` argument, since the whole-process
  `LLM_PROVIDER=google` testing override silently substitutes `GEMINI_MODEL`
  in — comparing the wrong one caused a wasted duplicate retry.
  `OPENROUTER_TOTAL_TIMEOUT` (100s) and `GOOGLE_TOTAL_TIMEOUT` (60s) are both
  sized so the worst case — primary times out, then the fail-safe also times
  out — stays under gunicorn's `--timeout` (Dockerfile `CMD`, 220s), for the
  same reason the single-call version of this margin mattered before: a
  double-timeout needs to be caught here first, cleanly, rather than by
  gunicorn's harder `SIGABRT`.
- `call_llm` wraps any provider-level failure (an HTTP error or
  `requests.exceptions.RequestException` from OpenRouter, or a
  `google.api_core.exceptions.GoogleAPIError` from Gemini) as `story_engine.
  LLMUnavailableError` — a single stable type regardless of which provider
  raised it. `app.py`'s `take_turn`/`regenerate_turn` views (the only two
  web-reachable paths that ever call `call_llm`) each catch it directly and
  return `(str(e), 503)` instead of swapping any content — since both are
  htmx endpoints (see "Web UI" below), a non-2xx response is never swapped
  into the page, so the previous scene/choices are left completely
  untouched and the player can just retry. `play.html`'s
  `htmx:responseError` listener reads the response body into the
  `#llm-error-modal` `<dialog>` and shows it. Safe to treat as fully
  recoverable either way: `call_llm` always runs before
  `update_state_after_turn`/`save_state` for that turn, so a failure here
  never leaves a save partially written. The import of `GoogleAPIError`
  inside `_call_llm_google` (not at module level) is what lets the offline
  test suite import `story_engine` without the real `google-api-core`
  package installed - it stubs `google.generativeai` but not that
  transitive dependency.
- State updates (flags, subplot progress, memory-fragment reveals, entity
  interactions, inventory, relationship scores) are a **separate LLM call**
  from narration (`update_progress_from_turn`) — one call trying to
  "narrate AND update state" tends to produce messier JSON than splitting
  the two. Keep this split if extending state-update coverage further.
- `player.inventory` is a flat list of item-description strings, diffed via
  `items_gained`/`items_lost` each turn (`items_lost` matches by exact
  string, so a lost entry must echo an existing one verbatim - the model is
  shown `CURRENT INVENTORY` in the prompt for this reason). Left unbounded,
  same as `player.traits` - a story-appropriate item list doesn't grow the
  way flags or subplots do, so no cap has been needed.
- `player.relationships` (`{name: score}`, -100 to 100) is diffed as a
  **delta per turn**, not an absolute value - `relationship_changes` in the
  diff is added to the existing score and clamped. Bounded to
  `RELATIONSHIPS_LIMIT` (20), but unlike `flags_active` (evicts oldest
  non-pinned first) it evicts whichever relationships sit **closest to
  neutral** first - a story's strongest bonds/rivalries are exactly the ones
  that should never silently disappear, regardless of how long ago they
  were set.
- Summarization of `history_log` into `compressed_summary` runs periodically
  (on `recent_turns` overflow), not every turn, to save cost.
- **Character creation is an opt-in, N-step mechanic, per story** - not a
  required part of every template, and not hardcoded to "class" specifically.
  A story authors a top-level `character_creation` list: an ordered sequence
  of steps, each `{key, label, prompt, options}`, where each option is
  `{id, name, tagline, starting_stats}` - `starting_stats` is a free-form
  `{stat_name: int}` dict (optional; a flavor-only step like "where do you
  start" typically omits it) with no fixed schema across stories, since one
  story's scale might be 0-10 attributes and another's a 0-100 meter like
  `health`. `next_pending_creation_step(state)` finds the first step the
  player hasn't completed yet (`player.creation_choices` is `{step_key:
  option_id}`); `apply_creation_choice(state, step_key, option_id)` records
  the pick and merges that option's `starting_stats` (if any) into
  `player.stats` - later steps merge on top of earlier ones for any stat name
  both touch, in step order. One request/prompt handles exactly one step; a
  multi-step story naturally chains through them one screen at a time since
  each pick re-enters the same gate and `next_pending_creation_step` just
  returns the next one. Web: `app.py`'s `play()` route (`creation_step.html`,
  generic - renders whichever step it's given), gated independently of the
  `opening_scene.played` check so it still applies to a save whose opening
  already played but hasn't finished every step; CLI: `run_opening_scene()`'s
  loop. A story that doesn't define `character_creation` (the `example` story
  doesn't - a cozy mystery doesn't need an RPG-style class/origin pick) skips
  this entirely, same as before the mechanic existed - `state.get
  ("character_creation", [])`/`player.get("creation_choices", {})` degrade to
  falsy/empty rather than raising, which is also what keeps this fully
  backward-compatible with every save that predates the feature (a save
  clones its template once at creation and never re-reads it, so an old save
  simply lacks the key rather than being retroactively migrated). A stat can
  only ever be adjusted turn-to-turn (via `update_progress_from_turn`'s
  `stat_changes`, same delta-then-clamp pattern as `relationship_changes`)
  **if it's already in `player.stats`** - the model can't introduce a new
  stat axis outside whatever the story's own steps seeded. Only a floor
  (`STAT_FLOOR = 0`) is enforced generically; no fixed ceiling, since each
  story's own options imply their effective scale. `build_system_prompt`
  builds the `PLAYER:` line's per-step "Label: chosen option name" segments
  generically off `character_creation` + `creation_choices` - a new step type
  needs no engine changes, just a new entry in the story's step list.
  `new_babel` is the first story to use this: a `class` step
  (`ghost_runner`/`cordon_asset`/`fractured_adept`, each weighting the
  pre-existing but previously-inert `health`/`neural_load`/`attention_level`
  stats differently) followed by a flavor-only `starting_place` step
  (`spire`/`lowmarket`/`drowned_quarter` - deliberately doesn't touch
  `player.stats` or branch the actual fixed opening scene, which stays the
  one hand-authored constant every playthrough starts from per this doc's
  "Continuous / Long-Running Structure" section; it just seeds a
  `creation_choices` entry the narration prompt can reference).

## Web UI
The `/play/<slug>` page is a single, continuously-appending transcript, not
a page-per-turn form flow: submitting a choice/free-text action or hitting
regenerate never triggers a full navigation. This is built with **HTMX**
(vendored at `static/htmx.min.js`, loaded once from `base.html`'s `<head>`
— not pulled from a CDN, since the app is self-hosted/Dockerized and
shouldn't depend on an external host at page-load time), not hand-written
`fetch`/DOM-patch JS — declarative `hx-*` attributes in `frontend/
_controls.html` drive submission, and `app.py`'s `take_turn`/
`regenerate_turn`/`turn_history` views return HTML fragments (rendered from
the same `frontend/_scene_block.html`/`_controls.html` partials used for
the initial page load), not JSON. An out-of-band swap (`hx-swap-oob`) on
`<fieldset id="controls">` is what makes a single response both append the
new scene and refresh the choices in one round trip — `#controls` is a
`<fieldset>` specifically so `hx-disabled-elt="#controls"` can disable
every descendant control during a request (a plain `<div>` doesn't cascade
`disabled` to its children). Scrollback is "reverse infinite scroll":
`/play/<slug>/api/history` follows htmx's standard self-chaining sentinel
pattern (`#scroll-sentinel`, `hx-trigger="revealed"`) rather than any
client-tracked pagination state — each response either includes a fresh
sentinel pointing at the next-older batch, or omits it once
`history_log.full_transcript` (concatenated with `recent_turns` via
`app.py`'s `_all_turns`) is exhausted. New UI work on this page should
extend this pattern rather than introducing a parallel fetch-based one.

**Inline narration formatting** (`frontend/play.html`): the LLM is instructed
(`story_engine.py`'s `build_system_prompt`) to mark up emphasis with exactly
three plain-text markers — `**bold**`, `*italic*`, `__underline__` — not full
Markdown and not a parsing library, kept small and dependency-free like the
rest of the project. `data-text` on each `.story` element (`_scene_block.html`)
always carries the *raw* narration text; client-side JS renders it, for both
the freshly-animated turn and every already-read one loaded via SSR/pagination
(`processStoryElements`, gated by a `data-md-done` marker so a document-wide
rescan on `htmx:afterSwap` never reprocesses the same element twice). Escaping
always runs before the marker regexes (`escapeHtml`, via the DOM's own
`textContent` → `innerHTML`, not a hand-rolled entity list) — LLM output can
never inject real markup this way, only the three fixed tags we add ourselves.
The typewriter reveal can't just slice a live HTML string once tags are
involved (slicing mid-tag would produce invalid markup) — it builds the final
`<strong>`/`<em>`/`<u>` DOM structure up front, then grows each text node's
content in document order, so formatting is visually correct from the first
revealed character rather than snapping in retroactively. The one subtlety:
`reveal_from` (see `app.py`'s `play()` route — the opening scene's already-read
"before name" prefix) is computed server-side as a *raw*-text character
offset, but marker characters don't survive rendering, so `revealNarration`
re-derives the equivalent *rendered* offset (`stripMarkdownMarkers`) before
using it — this only stays correct if `narration_before_name` itself never
contains markdown, which is true for every story's hand-authored opening today
but isn't otherwise enforced.

## Multi-User, Multi-Story Architecture
Many users, each with independent progress, and many stories (not just one
the engine can ever run) — see `state_store.py`, the single storage layer
all four entry points (`story_engine.py`, `plot_manager.py`,
`subplot_manager.py`, `app.py`) go through. Don't read/write story state any
other way.

All five files referenced by bare name throughout this doc (`app.py`,
`story_engine.py`, `state_store.py`, `plot_manager.py`, `subplot_manager.py`)
live together under `backend/` — everything else (`stories/`, `frontend/`,
`static/`, `data/`, `test/`) stays at the repo root. Their imports of each
other (`import state_store`, etc.) stay flat, not package-relative - this
works because every entry point that loads them puts `backend/` on
`sys.path` itself: `python backend/story_engine.py` gets it for free (Python
adds a directly-run script's own directory to `sys.path[0]`), gunicorn's
Dockerfile `CMD` passes `--pythonpath backend` for the same reason, and
`test/_llm_stubs.py` inserts it manually. None of that changes the process's
cwd, which is what lets `state_store.py`'s `STORIES_DIR`/`DATA_DIR` stay
plain relative strings (`"stories"`, `"data"`) resolved against the repo
root rather than `backend/` - cwd stays at the repo root everywhere this
runs. `app.py` is the one exception that needs an explicit fix rather than
relying on cwd: Flask resolves `template_folder`/`static_folder` from the
module's own file location by default, so `app.py` passes both explicitly,
pointed back at the repo-root `frontend/`/`static/` (`frontend` instead of
Flask's usual `templates` purely by naming convention, to pair with
`backend/` - Flask itself doesn't care what the directory is called). If
you add a sixth backend module, it only needs the same flat `import` - no
new wiring.

- **`stories/<slug>/template.json`** — authored seed content. `state_store.
  list_stories()` scans this directory directly (no separate catalog to keep
  in sync), so **adding a story is a content change, not a code change** —
  drop in a new `stories/<slug>/template.json` and it's picked up
  automatically, whether that directory is committed directly or checked
  out from a submodule (see next point).
- **Public repo, private story content, via git submodules.** This repo is
  public but story *content* isn't necessarily meant to be — `stories/
  example/` is committed directly (public, and the CLI/web default so the
  repo works out of the box); `stories/new_babel/` is a **git submodule**
  pointing at a separate private repo, not committed here. Don't put a story
  meant to stay private directly in this repo's history — give it its own
  repo and `git submodule add` it instead. `.gitmodules` (which is public)
  reveals a submodule's name/URL either way, just not its content.
- **`data/`** — runtime-only, gitignored:
  - `data/saves/<user_id>/<story_slug>.json` — one live save per user per
    story, cloned from the matching template on first play. Plain JSON
    files, not a database — each save has exactly one owner and needs no
    cross-user queries. Writes are wrapped in a per-`(user_id, story_slug)`
    file lock (`filelock`) so concurrent requests for the same save
    serialize correctly across gunicorn's worker *processes*, not just
    threads within one.
  - `data/accounts.db` — SQLite, `users(id, username, password_hash,
    created_at)`. The one place a database earns its keep: checking "does
    this username already exist" needs an atomic, race-free answer (a
    `UNIQUE` constraint), which flat files can't give you.
- **Accounts are provisioned server-side, not through the web UI** — no
  self-service registration route exists on purpose.
- **CLI defaults** — `story_engine.py`, `plot_manager.py`, and
  `subplot_manager.py` all default to `user_id="local-cli",
  story_slug="example"` when run without `--user`/`--story` flags (the
  public story, not the private `new_babel` submodule, precisely so the
  default keeps working on a fresh clone with no submodule access), so the
  CLI behaves exactly like a single-story app unless told otherwise.
  `story_engine.py`'s in-session `steer ...` command passes `--user`/
  `--story` through automatically so it targets the right save.

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

Tests that load `se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)`
should derive their expectations from whatever that template actually
contains (subplot count, memory fragments, etc.) rather than hardcoding
assumptions from one specific story - `DEFAULT_STORY_SLUG` has changed once
already (New Babel → example) and content-specific assumptions silently
broke two tests when it did.
