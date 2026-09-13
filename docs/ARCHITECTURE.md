# Palimpsest — Architecture Notes

As-built reference for how the engine actually works. Split out of `CLAUDE.md`,
which had grown to ~740 lines and is loaded into every session; it now carries
only the invariants a change could break, and points here for the mechanics
behind them.

See `README.md` for background, setting, and how to run the project, and
`docs/SCHEMA_V2_SPEC.md` for the template/save schema and its design principles.

---

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
- **Most subplots resolve within roughly one act, but some are deliberately
  longer.** Every subplot carries a `span`: `"single_act"` (the default,
  `completion_threshold: 100`) or `"multi_act"`
  (`completion_threshold: MULTI_ACT_SUBPLOT_THRESHOLD`, currently 250) — set
  once at creation by whichever LLM call invented it
  (`generate_new_subplot`, or a subplot-type steering seed via
  `generate_steering_seed`/`plot_manager.apply_steering_seed`) and passed to
  `story_engine.insert_subplot`, the single place that actually decides the
  threshold. `update_progress_from_turn`/`check_subplot_status` are generic
  over whatever threshold a subplot has, so nothing else needed to change
  for a `multi_act` subplot to correctly take longer to resolve. Each
  generation prompt is steered to make `multi_act` the exception, not the
  rule ("I would like *some* subplots to span multiple acts," not most).
- **Acts are endless chapters, not a fixed 3-act structure.** Only Act 1
  ships pre-authored. At each pacing checkpoint,
  `story_engine.check_and_advance_act()` asks the LLM to judge —
  qualitatively, against `completion_signals` context, not a numeric
  threshold — whether the act feels resolved, and if so generates the next
  one in the same call. There is no ceiling. The checkpoint itself fires on
  *either* of two conditions, not just the first: at least one subplot
  completed this act (`pacing.subplots_completed_this_act >= 1`, the fast
  path), or it's simply been `pacing.act_check_frequency` turns since the
  last check (`pacing.turns_since_last_act_check`, mirroring
  `pacing_nudge_frequency`/`turns_since_last_pacing_nudge`'s existing
  pattern) — reset to 0 whenever the check actually runs, whether or not it
  results in advancement. Requiring only the first condition used to mean
  no act could ever advance without a subplot completing inside it first,
  which structurally forced every subplot toward single-act length — this
  is exactly what a `multi_act` subplot needs *not* to happen, so don't
  reintroduce a hard dependency between act advancement and subplot
  completion. The prompt also lists any currently-active `multi_act`
  subplots explicitly, so the director doesn't read one's ongoing
  non-completion as a sign the act hasn't resolved.
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

- **`history_log.recent_turns`** — every prompt that reads it slices
  `[-RECENT_TURN_LIMIT:]` (10) itself, so that, not the stored length, is what
  bounds context. Storage deliberately runs ahead: a rollover only fires once
  the list reaches `RECENT_TURN_LIMIT + ROLLOVER_BATCH_TURNS` (20), then rolls
  everything past the last 10 into `compressed_summary` in one batch. Triggering
  on "longer than 10" instead — as this did originally — means a rollover on
  *every* turn past the tenth, since each turn appends exactly one and the trim
  puts the list straight back on the boundary. Don't reintroduce that: it cost a
  Tier A call (~18s measured) per turn and re-compressed the summary ~16 times by
  turn 26 instead of ~2.
- **`history_log.compressed_summary`** — capped at `SUMMARY_MAX_WORDS`, and the
  cap is *enforced* (`_enforce_word_cap`), not merely requested in the prompt —
  a real save reached 2,912 words against a 2,000-word instruction. Each rollover
  re-summarizes *the existing summary plus the new turns* back under that cap and
  replaces it, rather than appending forever. Since that is lossy and compounds,
  how *often* it runs is a quality lever and not just a cost one — see the
  batching note above.
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
- **Three cost/latency/capability tiers, not two**, matched to what each call
  site actually needs:
  - **Tier A** — cheap flagship, reasoning **off**. Style/format adherence
    matters most here, and a model's reasoning phase swallowing the final
    answer (see the `reasoning` bullet below) would be a visible,
    player-facing failure: narration (`_generate_and_apply_turn`'s
    `call_llm`), the `compressed_summary` rollover, and
    `handle_end_story_request`'s closing arc.
  - **Tier B** — the *same* cheap flagship model as Tier A, reasoning **on**.
    Rarer, judgment-heavy calls where a bit of latency/failure risk is worth
    it for a better decision: `check_and_advance_act`, `generate_new_subplot`,
    `generate_steering_seed`, `generate_character_from_relationship`.
  - **Tier C** — fastest available model. Used only for
    `update_progress_from_turn`: a closed-vocabulary classification/diff
    extraction that runs every single turn, where speed and cost matter far
    more than reasoning depth.

  Tier A and Tier B are therefore the *same* provider/model pair —
  `TIER_AB_PROVIDER`/`TIER_AB_MODEL` (both default to OpenRouter/DeepSeek) —
  and are distinguished only by the `reasoning: bool` flag threaded through
  `call_llm`/`call_llm_json` per call site, not by a separate env var. Tier C
  gets its own, independent pair: `TIER_C_PROVIDER`/`TIER_C_MODEL` (also
  defaults to OpenRouter/DeepSeek, a cheaper/faster sibling model). Google/
  Gemini is deliberately **not** a real tier choice — it's reserved for the
  offline test suite (`TESTING_FORCE_GOOGLE` below) and `call_llm`'s own
  fail-safe retry — though an operator can still point either tier at
  `google` explicitly if they want a real Gemini model in the mix
  (`test/test_mixed_provider.py` exercises exactly this opt-in case, with
  `TIER_C_PROVIDER=google`). Both SDKs can be live in the same process at
  once regardless — `genai.configure()` runs unconditionally, and the
  `OPENROUTER_API_KEY` presence check trips if *either* tier's provider is
  `"openrouter"` (skipped entirely under `TESTING_FORCE_GOOGLE`, since no
  OpenRouter call is ever actually reached in that mode).

  `call_llm(prompt, model=TIER_AB_MODEL, provider=None, reasoning=False,
  json_mode=False)` and `call_llm_json(prompt, model=TIER_C_MODEL,
  provider=None, reasoning=False)` each default to their own tier, so a bare
  `call_llm(prompt)` (narration) or `call_llm_json(prompt)`
  (`update_progress_from_turn`) needs no explicit override; every Tier B call
  site passes `model=TIER_AB_MODEL, provider=TIER_AB_PROVIDER,
  reasoning=True` explicitly, and `handle_end_story_request` (Tier A, but via
  `call_llm_json`) passes the same model/provider with `reasoning` left at
  its default `False`. `call_llm_json` always passes `json_mode=True`
  underneath, regardless of tier (see the `response_format` bullet below).

  `TESTING_FORCE_GOOGLE` (not a per-tier setting) is a whole-process testing/
  debug override: when true, `call_llm` overwrites *both* `provider` and
  `model` with `"google"`/`GEMINI_MODEL` regardless of what a call site
  passed in. `test/_llm_stubs.py` sets this for the general offline suite,
  since Google is the side with a stubbable SDK (`google.generativeai`).
  `test/test_openrouter.py` and `test/test_failsafe.py` both set
  `TESTING_FORCE_GOOGLE=false` instead, to exercise the real
  `_call_llm_openrouter` path (both tiers already default to
  `TIER_AB_PROVIDER`/`TIER_C_PROVIDER = "openrouter"`, so no provider
  override is needed beyond disabling the testing force) via a mocked
  `requests.post`. Each test file runs in its own subprocess (see
  `run_all.py`), so none of this leaks between files.
- **`response_format: json_object`**: every `call_llm_json` call passes
  `json_mode=True` down to `_call_llm_openrouter`, which sets OpenRouter's
  `response_format: {"type": "json_object"}` — a guarantee of syntactically
  valid JSON from the model, cheap insurance against a reply wrapped in
  prose or broken JSON syntax. Applies uniformly across Tier B and Tier C
  (every `call_llm_json` call site), not gated behind a specific tier; a
  no-op under the `google` provider, which has no equivalent knob in this
  codebase.
- **OpenRouter provider routing**: every OpenRouter call sends `"provider":
  {"sort": "throughput"}` by default — `TIER_C_MODEL` alone is resold through
  ~29 upstreams with measured throughput from 6-109 tok/s, and a request
  landing on the slow end once dominated a 132s turn, so the default asks
  OpenRouter for whichever upstream is fastest right now rather than leaving
  it to chance. `TIER_AB_OPENROUTER_PROVIDER` (`.env`, unset by default) is an
  opt-in override of that default, scoped to `TIER_AB_MODEL` only — set it to
  a specific OpenRouter provider slug (e.g. `baidu/fp8`) to pin routing to one
  named upstream via `{"order": [slug], "allow_fallbacks": false}` instead of
  throughput-sorting, a deliberate throughput-for-cost tradeoff made per
  deployment rather than a new default (verified 2026-09-13: Baidu's
  `baidu/fp8` endpoint for `deepseek-v4-pro-20260813` priced at
  $0.00000058/$0.00000173 per prompt/completion token, the cheapest of ~19
  listed upstreams, next cheapest ~$0.00000066). `allow_fallbacks: false`
  means a pinned-but-down upstream fails the request outright
  (`LLMUnavailableError`) rather than silently rerouting to a pricier
  upstream — the Gemini fail-safe below is what actually recovers that case,
  same as any other primary-call failure. Never applies to `TIER_C_MODEL`,
  which always throughput-sorts regardless of this setting.
- **Gemini fail-safe**: if a tier's primary call raises `LLMUnavailableError`,
  `call_llm` retries once against the operator's own free-tier `GEMINI_MODEL`
  via a direct Google call, before giving up. This IS a genuine runtime
  fallback — the point is to let `TIER_AB_MODEL`/`TIER_C_MODEL` be freely
  swapped to whatever's being tried (an experimental OpenRouter model, say)
  without an unreachable or misconfigured model taking the whole app down.
  Narrow by design: only ever falls back *to* Gemini, never away from it, and
  only on a request-level failure — never a silent retry just because output
  looks malformed (`call_llm_json`'s caller still decides what to do with bad
  JSON, same as before). Skips the retry (raises immediately) if the primary
  call *was already* Gemini/`GEMINI_MODEL` — nothing left to fall back to.
  `test/test_failsafe.py` covers the fallback actually rescuing a call (both
  tiers) plus a regression guard for a real bug caught during development: the
  "already tried this" check has to compare against the model actually
  attempted, not some separate raw argument, since `TESTING_FORCE_GOOGLE`
  silently substitutes `GEMINI_MODEL` in — comparing the wrong one caused a
  wasted duplicate retry.
  `OPENROUTER_TOTAL_TIMEOUT` (100s) and `GOOGLE_TOTAL_TIMEOUT` (60s) are both
  sized so the worst case — primary times out, then the fail-safe also times
  out — stays under gunicorn's `--timeout` (Dockerfile `CMD`, 220s), for the
  same reason the single-call version of this margin mattered before: a
  double-timeout needs to be caught here first, cleanly, rather than by
  gunicorn's harder `SIGABRT`. (`app.py`'s `take_turn`/`regenerate_turn` now
  run this whole call chain on a background thread rather than inline in the
  request — see "Asynchronous turn-taking" under "Web UI" — so gunicorn's
  own `--timeout` no longer has a request to measure this against at all;
  these constants stay in place regardless, since they're still what makes
  an individual call give up and hand off to the fail-safe rather than
  hanging indefinitely.)
- `call_llm` wraps any provider-level failure (an HTTP error or
  `requests.exceptions.RequestException` from OpenRouter, or a
  `google.api_core.exceptions.GoogleAPIError` from Gemini) as `story_engine.
  LLMUnavailableError` — a single stable type regardless of which provider
  raised it. Also raised now if a provider returns a 200 with empty/`None`
  narration content (observed in production: an OpenRouter model returning
  `message.content: null`, which used to flow straight through and get
  saved verbatim as `"Narrator: None"` — see `_call_llm_openrouter`'s and
  `_call_llm_google`'s empty-content guards). `app.py`'s `take_turn`/
  `regenerate_turn` routes (the only web-reachable paths that ever call
  `call_llm`) hand `story_engine.take_turn`/`regenerate_last_turn` to
  `_start_turn_job`, which catches it on the background thread and records
  it via `state_store.write_turn_result(..., ok=False, error=str(e))`; the
  `GET /api/turn/result` route (see "Asynchronous turn-taking") is what actually
  returns `(str(e), 503)` to the browser, once the client polls its way to
  fetching that result — not `take_turn`/`regenerate_turn` themselves
  anymore, since those now return `202` before the LLM call even starts.
  Since that route is also an htmx endpoint, a non-2xx response is still
  never swapped into the page, so the previous scene/choices are left
  completely untouched and the player can just retry. `play.html`'s
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
- `player.relationships` is `{name: {"score": int, "npc_id": str|None}}`
  (score -100 to 100; `npc_id` is `None` until the name is linked to a
  `characters` entry - see "Characters, NPC creation, and relationship
  linking" below). Score is diffed as a **delta per turn**, not an absolute
  value - `relationship_changes` in the diff is added to the existing score
  and clamped; only the score is shown to either LLM prompt that touches it
  (`build_system_prompt`'s `PLAYER:` line, `update_progress_from_turn`'s
  `CURRENT RELATIONSHIPS` line) - `npc_id` is internal bookkeeping and never
  leaks into a prompt. Bounded to `RELATIONSHIPS_LIMIT` (20), but unlike
  `flags_active` (evicts oldest non-pinned first) it evicts whichever
  relationships sit **closest to neutral** first, by `abs(score)` - a
  story's strongest bonds/rivalries are exactly the ones that should never
  silently disappear, regardless of how long ago they were set. A save from
  before this field existed only has bare `{name: score}` ints;
  `state_store.load_state()`'s `_migrate_relationships()` upgrades those to
  the `{"score", "npc_id": None}` shape in place on load - the only
  migration step this project has, scoped narrowly to this one field, since
  there's no general schema-version mechanism.

### Characters, NPC creation, and relationship linking
Every path that creates an NPC record goes through `story_engine.
insert_character()` (mirroring `insert_subplot()` for subplots) - it mints
the next `char_NNN` id, builds the full record, and tags it with an
`origin` (`"seed" | "subplot" | "act" | "narration" | "relationship"`)
purely so `show_plot_overview` can tell a human later how each NPC entered;
nothing else reads `origin` back. Four ways a `characters` entry gets
created, in increasing order of automation-vs-review:
- **`plot_manager.py seed`/`seed-apply`** (`origin: "seed"`) - the original,
  freeform, player-reviewed path: a note becomes a draft via
  `generate_steering_seed`, staged in `pending_seeds`, and only committed
  on `seed-apply`. Unchanged by the additions below.
- **A new subplot or act can name its own required character**
  (`origin: "subplot"`/`"act"`) - `generate_new_subplot`'s and
  `check_and_advance_act`'s prompts each include an `EXISTING CHARACTERS`
  list and an optional `new_character` field in their JSON schema,
  instructed to stay `null` unless the subplot/act genuinely can't work
  without a specific new named person (most don't need one). When present
  and not a duplicate of an existing name, `_maybe_insert_generated_character`
  commits it immediately via `insert_character(..., introduced=False)` -
  auto-committed like the rest of the automatic pipeline, no staging step.
  `introduced=False` is correct: they haven't appeared on the page yet, so
  they correctly surface through `generate_pacing_nudge`'s existing
  "CHARACTERS TO WEAVE IN" line with no changes needed there.
- **Narration can introduce someone by name mid-turn** (`origin:
  "narration"`) - `update_progress_from_turn`'s diff schema has an optional
  `new_characters` list, deliberately gated on the model having given the
  character an actual proper name this turn (e.g. "Marlowe"), never a
  generic/descriptive handle (e.g. "the advocate", "a guard") - a
  generic-label character still gets a normal `relationship_changes` entry,
  just no automatic NPC record, to avoid spinning one up for every
  incidental relationship. When a real name is given, `insert_character(...,
  introduced=True)` runs immediately (they're already on the page this
  turn) and the matching `relationships[name]["npc_id"]` is set right away.
- **Manual promotion for an existing generic-label relationship**
  (`origin: "relationship"`) - `plot_manager.list_unlinked_relationships()`
  lists every relationship with no `npc_id` yet (surfaced in
  `show_plot_overview` and the web Plot Manager page's "Unlinked
  Relationships" section); `plot_manager.promote_relationship_to_npc()`
  drafts a full record via `story_engine.
  generate_character_from_relationship()` (same "never mutates state,
  returns a draft or `None`" contract as `generate_steering_seed`, fed the
  name/score/recent history), applies any CLI/web field overrides, commits
  it via `insert_character(..., introduced=True)`, and links it.

Separately from creation, **`relationships[name]["npc_id"]` is the only
thing that links a relationship entry to a `characters` entry** -
`update_progress_from_turn`'s `relationship_changes` loop only does the
old exact-name scan over `characters` the *first* time a given name is
unlinked (`entry["npc_id"] is None`); once linked, `npc_id` is used
directly and the scan is skipped on every later turn. This replaced a real
bug: a steering-seeded character named `"Salome Vence (the Advocate)"`
never had her `introduced` flag flip, because the narration only ever
called her "the advocate" and nothing tied that string back to her record
- exact-name matching alone is fragile in exactly this way, which is why an
explicit stored id (not a re-derived string match) is now the source of
truth once a link exists.
- Summarization of `history_log` into `compressed_summary` runs periodically —
  once per `ROLLOVER_BATCH_TURNS`, not every turn — to save cost and to limit how
  many times the summary is lossily re-compressed. See the `recent_turns` bullet
  under "Keeping LLM Context Bounded" for why the trigger is a batch threshold
  rather than a plain overflow check.
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
  backward-compatible with every save that predates the feature: an old save
  simply lacks `creation_choices` and reads as "no steps answered yet".
  Note what that does *not* mean - the step list itself lives on the story
  side, which `load_state` re-reads fresh from the template on every single
  load, so **adding a step to a story retroactively applies to saves already
  in progress**: `next_pending_creation_step` returns the first key absent
  from `creation_choices`, so an in-flight save is asked the new step the
  next time it loads, whatever turn it's on. That's usually what you want (a
  save can't play on missing an answer the prompt now depends on), but it is
  not opt-in, so add a step knowing every live save for that story will stop
  and ask. A stat can
  only ever be adjusted turn-to-turn (via `update_progress_from_turn`'s
  `stat_changes`, same delta-then-clamp pattern as `relationship_changes`)
  **if it's already in `player.stats`** - the model can never introduce a new
  stat axis. That constraint stands; what changed is that creation steps are no
  longer the only way to seed one. `protagonist.stats` in the template is the
  baseline, merged into the save at creation, and a chosen step's
  `starting_stats` merges on top of it - so a story wanting a stat scale but no
  class picker (survival, horror) can have one, and a story using both is
  unchanged. Bounds are per-story via `mechanics.stats.floor`/`.ceiling`
  (global across that story's stats, not per-stat; `ceiling` absent means
  unbounded), which replaced the old global `STAT_FLOOR = 0`.
  `mechanics.stats.visible` (default `false`) decides whether the narrator may
  quote a stat's raw number to the player: false keeps the original behaviour
  of reflecting it narratively as strain or risk, true is for a story whose
  premise is an in-world system reporting the player's own figures back at
  them, and it flips both the `PLAYER:` line label and the prompt footer
  instruction together - a prompt carrying one of each contradicts itself. `build_system_prompt`
  builds the `PLAYER:` line's per-step "Label: chosen option name" segments
  generically off `character_creation` + `creation_choices` - a new step type
  needs no engine changes, just a new entry in the story's step list. Those
  segments persist in **every** prompt for the rest of the game, so a step
  recording a one-time *event* rather than a permanent trait needs past-tense
  wording or the narrator reads it as a standing, present-tense fact. An
  optional `prompt_label` overrides `label` for that line only; `label` stays
  the player-facing wording (the CLI creation loop's step heading, and
  `app.py`'s fallback when a step authors no `prompt`), where past tense would
  be wrong since it's shown *before* the choice is made.
  `new_babel` is the first story to use this: a `class` step
  (`ghost_runner`/`cordon_asset`/`fractured_adept`, each weighting the
  pre-existing but previously-inert `health`/`neural_load`/`attention_level`
  stats differently) followed by a flavor-only `starting_place` step
  (`spire`/`lowmarket`/`drowned_quarter` - deliberately doesn't touch
  `player.stats` or branch the actual fixed opening scene, which stays the
  one hand-authored constant every playthrough starts from per this doc's
  "Continuous / Long-Running Structure" section; it just seeds a
  `creation_choices` entry the narration prompt can reference). That
  `starting_place` step is also the reason `prompt_label` exists: labelled
  "Heading" for the player (it asks where you go *first*), it rendered as
  "Heading: The Drowned Quarter" on turn 50 as readily as turn 1 - a
  permanent pull back toward a place the player may have long since left. It
  carries `"prompt_label": "Started out"` for that reason.

## Web UI
The `/play/<slug>` page is a single, continuously-appending transcript, not
a page-per-turn form flow: submitting a choice/free-text action or hitting
regenerate never triggers a full navigation. This is built with **HTMX**
(vendored at `static/htmx.min.js`, loaded once from `base.html`'s `<head>`
— not pulled from a CDN, since the app is self-hosted/Dockerized and
shouldn't depend on an external host at page-load time), not hand-written
`fetch`/DOM-patch JS — declarative `hx-*` attributes in `frontend/
_controls.html` drive submission, and `app.py`'s `turn_result`/
`turn_history` views return HTML fragments (rendered from the same
`frontend/_scene_block.html`/`_controls.html` partials used for the initial
page load), not JSON. An out-of-band swap (`hx-swap-oob`) on `<fieldset
id="controls">` is what makes a single response both append the new scene
and refresh the choices in one round trip — `#controls` is a `<fieldset>`
so disabling cascades to every descendant control (a plain `<div>` doesn't
cascade `disabled` to its children); see "Asynchronous turn-taking" below
for how that disabling is actually driven now. Scrollback is "reverse
infinite scroll": `/play/<slug>/api/history` follows htmx's standard
self-chaining sentinel pattern (`#scroll-sentinel`, `hx-trigger="revealed"`)
rather than any client-tracked pagination state — each response either
includes a fresh sentinel pointing at the next-older batch, or omits it once
`history_log.full_transcript` (concatenated with `recent_turns` via
`app.py`'s `_all_turns`) is exhausted. New UI work on this page should
extend this pattern rather than introducing a parallel fetch-based one.

### Asynchronous turn-taking (kickoff → poll → fetch)
`POST /play/<slug>/api/turn` and `POST /play/<slug>/api/regenerate` do
**not** run the turn inline and return its result — they only *start* it.
Each checks `state_store.read_turn_status` for an in-flight beacon (409 if
one exists), then calls `app.py`'s `_start_turn_job`, which writes a
`"queued"` turn-status beacon and runs `story_engine.take_turn`/
`regenerate_last_turn` on a background `threading.Thread`, and the route
returns `202` with an empty body almost immediately — regardless of how
long the turn's own LLM pipeline (narration + state-update + optionally
an options-repair follow-up/subplot generation/act-advancement/summary-
rollover, several of which can individually be slow) ends up taking.
This exists because a real production incident showed a single long-held
HTTP response isn't safe end-to-end: the Cloudflare tunnel in front of
this app (see `docker-compose.yml`) cancels its connection to the origin
past roughly 100-125s ("context canceled" in `cloudflared`'s logs) even
though gunicorn keeps running and the turn saves correctly a few seconds
later — the player just sees a spurious connection-lost error on an
otherwise-successful turn.

The client closes the loop itself, entirely through polling:
1. `_controls.html`'s buttons/form POST with `hx-swap="none"` (no swap from
   this response) and carry `data-status-poll="true"` plus
   `data-result-target`/`data-result-swap` (take_turn appends to
   `#scene-list`; regenerate replaces `#scene-list > .scene-block:last-child`
   in place with `outerHTML` — the same distinction the old direct-swap
   attributes used to encode).
2. `play.html`'s `htmx:beforeRequest` handler, seeing `data-status-poll`,
   captures those two data attributes and starts polling `GET
   .../api/status` (the same beacon-driven endpoint that already fed the
   busy indicator's step label/progress bar) every `STATUS_POLL_MS` (400ms).
3. Once that poll sees the label go back to `null` — the background thread
   is done — it fetches `GET .../api/turn/result` via `htmx.ajax(...)`,
   targeting whichever element/swap step 1 captured. Being a real htmx
   request, a non-2xx response there flows through the exact same
   `htmx:responseError` → `#llm-error-modal` path any other htmx call uses.
4. `app.py`'s `turn_result` view answers that fetch: it retries briefly (up
   to ~1s) reading `state_store.read_and_clear_turn_result` — a one-shot,
   bounded-to-one-entry disk handoff (`write_turn_result`/
   `read_and_clear_turn_result` in `state_store.py`, same shape/tradeoffs as
   the existing turn-status beacon) that `_start_turn_job`'s background
   thread writes right after `fn()` returns or raises
   `story_engine.LLMUnavailableError` — rather than assuming it's already
   there the instant the status beacon clears, since story_engine's
   `finally` block clears that beacon a couple of Python statements before
   the result gets written, and the two aren't atomic together. On success
   it returns the same scene+controls fragment `take_turn`/`regenerate_turn`
   used to return directly; on failure it returns `(error, 503)`, same as
   the old synchronous path.

The popup's step vocabulary is `story_engine.STATUS_LABELS` (raw `_timed()`
label → the evocative word the player reads) plus
`DEFAULT_STEP_ESTIMATE_SECONDS` (the seed a step's progress bar uses until
`state_store.p50_duration` has real samples for that label). **Both are
mirrors of the `_timed()` call sites, so adding an LLM call to the turn
path means adding it to both** — for a label it doesn't know,
`/api/status` falls back to displaying the raw `snake_case` key and to no
progress bar at all. This drifted once already: `generate_missing_options`'
repair call was added to the turn path without either entry, so a turn
whose narration skipped its OPTIONS block showed the player
"options_generation…". `test/test_status_labels.py` asserts the mirror both
ways. The two manager-path labels (`steering_seed_generation`,
`relationship_promotion`) are the deliberate exception — `plot_manager.py`
reaches them off the turn path, where `_status_ctx` was never set, so no
beacon is ever written and they carry a display word but no estimate.

Because `hx-disabled-elt`/`hx-indicator` are scoped to a single request's
lifecycle, and the *kickoff* POST/GET pair here settles almost instantly
while the actual turn is still running, `#controls` and `#busy-indicator`
are no longer driven by htmx's automatic wiring at all —
`disableControlsOnClick`/`disableControlsOnSubmit` (capture-phase,
unchanged from before) do the disabling, and `play.html`'s own
`showBusyIndicator`/`hideBusyIndicator`
(manually toggling the same `.htmx-request` CSS class htmx used to manage)
do the indicator. Re-enabling `#controls` happens either via the OOB
`<fieldset id="controls" hx-swap-oob="true">` in a successful
`/api/turn/result` response (a fresh, non-disabled element replacing the
disabled one), or explicitly in the `htmx:responseError`/`htmx:sendError`
handlers on failure (`recoverControlsAfterError`) — there's no OOB swap to
do it there. See `frontend/play.html`'s and `frontend/_controls.html`'s own
comments for the exact mechanics.

Side effect worth knowing: since gunicorn's own `--timeout` (Dockerfile
`CMD`, 220s) only measures a worker's request-handling responsiveness, and
the kickoff request now returns in well under a second, that timeout no
longer applies to the turn's own processing at all — closing a second,
separate historical failure mode where a turn that ran long enough to hit
it got the whole worker `SIGABRT`-killed mid-turn, losing the save
entirely. The turn-status beacon's own `TURN_STATUS_STALE_SECONDS` (240s,
`state_store.py`) backstop still exists for the cases that remain (a
container restart, an unhandled exception in the background thread, an OOM
kill).

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

### Beat-labelling worksheets (operator tool, off by default)
`backend/label_sheet.py` + `/labels/<sheet>` + the inline row under the play page's
choices exist to run the pacing spec's classifier-agreement gates (see
`docs/PHASE_0_GATE_REPORT.md`), not as a player feature. The worksheet markdown
file under `data/` is the single artifact `scripts/gate_02.py` scores, and three
callers write it - `scripts/make_label_sheet.py`, the standalone page, and the
inline row - so the block format lives in `label_sheet.py` and a save patches
only the three field lines inside one turn's fence.

Two things are deliberate and easy to undo by accident. Saves take a `filelock`
around the whole read-modify-write, because labelling fires a burst of
independent POSTs that gunicorn spreads across worker *processes* and an
unlocked version silently dropped labels. And the whole feature is gated on
`LABEL_SHEETS_USER` naming one operator id: worksheets are a single global file
per sheet, not per-user, so without the gate any logged-in player could read and
patch them, and any player far enough into a live-sheet story would get
labelling controls and append their turns to someone else's measurement run.
Unset means every route 404s and no row renders.

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
- **Public repo, private story content, via one git submodule.** This repo is
  public but story *content* isn't necessarily meant to be — `stories/
  example/` is committed directly (public, and the CLI/web default so the
  repo works out of the box), while every private story lives in a single
  **git submodule** at `stories/private/`, one folder per story, pointing at
  a separate private repo. Don't put a story meant to stay private directly
  in this repo's history — add it to that submodule instead. `.gitmodules`
  (which is public) reveals the submodule's name/URL either way, just not its
  content. The engine finds both roots via `state_store.story_roots()`; the
  submodule is mounted *inside* `stories/` on purpose, so docker-compose's
  existing `./stories:/app/stories` bind mount carries it with no second
  mount, and a slug present in both roots resolves to the public one.
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
