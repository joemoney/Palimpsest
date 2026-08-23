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
5. **Mid-adventure steering** (`plot_manager.py`, `subplot_manager.py`,
   or the in-session `steer` command) bypasses narration and edits plot
   state directly — see README for the command reference. Reach for it only
   when the model won't arrive at a needed structural change on its own.
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

## Backend / Model Notes
- Smaller/cheaper models drift faster, so `world.rules` should stay short,
  with the 1–2 most critical constraints repeated near the end of the prompt
  (recency bias helps enforcement on smaller models).
- State updates (flags, subplot progress, memory-fragment reveals, entity
  interactions) are a **separate LLM call** from narration
  (`update_progress_from_turn`) — one call trying to "narrate AND update
  state" tends to produce messier JSON than splitting the two. Keep this
  split if extending state-update coverage (e.g. inventory, relationships).
- Summarization of `history_log` into `compressed_summary` runs periodically
  (on `recent_turns` overflow), not every turn, to save cost.

## Multi-User, Multi-Story Architecture
Many users, each with independent progress, and many stories (not just one
the engine can ever run) — see `state_store.py`, the single storage layer
all four entry points (`story_engine.py`, `plot_manager.py`,
`subplot_manager.py`, `app.py`) go through. Don't read/write story state any
other way.

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
