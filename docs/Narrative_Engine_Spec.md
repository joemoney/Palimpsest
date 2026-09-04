# Narrative Engine Specification — CYOA Story Generation

## Core Model
Each turn is one LLM call that receives a fully-assembled system prompt and
returns free-form narration, optionally followed by a fixed-format block of
player choices. There is no persistent LLM-side session or memory — every
call is stateless from the model's point of view; all continuity comes from
what's re-assembled into the prompt from `story_engine.py`'s own state dict
each time (`build_system_prompt`).

The assembled prompt is, in order:
1. **Identity/tone header** — title, genre, tone, and `content_rules`.
2. **World rules** (`world.rules`) — non-negotiable constraints (magic
   limits, setting boundaries), repeated near the end of the prompt too
   (see "Reinforcement" below).
3. **Story so far** — the running `compressed_summary`, plus the last
   `RECENT_TURN_LIMIT` (10) turns verbatim.
4. **Pacing nudge** — injected only every `pacing_nudge_frequency` turns
   (see "Pacing/Director Layer"), not on every call.
5. **Current scene + player state** — location/summary, name, traits,
   inventory, relationships, active flags.
6. **Instruction footer** — target scene length (470-500 words) and the
   required output format for the turn.

## Turn Flow
1. Player submits an action (typed free text, or the prose behind a
   selected choice button - see below).
2. `build_system_prompt` assembles the prompt from current state;
   `call_llm` sends it and returns raw narration text.
3. `parse_narration_and_options` splits the response into narration and
   (if present) a parsed choice list.
4. A **second, separate LLM call** (`update_progress_from_turn`) extracts a
   structured state diff from the same turn - see "State Tracking". Kept
   apart from narration deliberately: one call trying to both narrate prose
   and emit reliable JSON in the same response produces messier JSON than
   splitting the two concerns.
5. The diff is applied, pacing/subplot/act bookkeeping runs, the turn is
   appended to history, and the save is written. Nothing is persisted until
   after the narration call succeeds - a failed LLM call leaves the save
   untouched, so the player can just retry.

## Choice Format
Every non-endgame turn ends with a required block: a blank line, the
literal heading `OPTIONS:`, then exactly 3 lines in the form
`<n>. <short third-person action label> || <first-person prose>`. The
label is what renders on the choice button; the prose is what actually
gets submitted as the player's action if that choice is picked, so the
novel's transcript reads in first person regardless of how the option was
presented. A free-text box is always available alongside the 3 options, so
the player is never limited to only what the model offered.

If the model's response doesn't match this format exactly (malformed line,
missing heading, wrong count), parsing falls back to treating the whole
response as narration with no options - the free-text box becomes the only
way forward for that turn rather than erroring.

## Plot Structure
Two tiers, matching the "tight rails, loose paint" design (see
`CLAUDE.md`):

- **Main thread** (`plot.main_thread.acts`) — an ordered list of acts, each
  a title + description + `completion_signals`. Only Act 1 is pre-authored
  per story; every subsequent act is generated on demand (see "Pacing/
  Director Layer"). There is no fixed act count and no ceiling.
- **Subplots** (`plot.subplots`) — a pool of parallel, independent threads,
  each with `priority` (high/medium/low), `progress` (0-100), and an
  `active`/`not_started`/`completed` status. The pool is kept topped up to
  `pacing.max_parallel_subplots` automatically: whenever one completes
  (progress reaches its `completion_threshold`), `generate_new_subplot`
  invents a replacement via its own LLM call, conditioned on the world
  rules, current act, story summary, and existing subplot titles (deduped
  against the most recent `SUBPLOT_TITLE_HISTORY_LIMIT` completed titles,
  not the full lifetime list).

Scene-level execution (what actually happens within a turn) is otherwise
unconstrained by any of the above - acts and subplots are waypoints the
narration is nudged toward, not a script it must follow beat-for-beat.

## Pacing/Director Layer
Two independent mechanisms keep the story moving without hand-scripting
every branch:

- **Pacing nudges** (`generate_pacing_nudge`) — injected into the prompt
  every `pacing_nudge_frequency` turns (not every turn, to avoid over-
  steering). Summarizes the current act, the highest-priority active
  subplot, up to two background subplots, and - if the pool has room -
  which not-yet-started subplot could be hooked in next. Pure prompt
  construction from existing state, no LLM call of its own.
- **Act advancement** (`check_and_advance_act`) — a pacing *checkpoint*,
  not a per-turn check: only evaluated once at least one subplot has
  completed since the current act began (`pacing.subplots_completed_this_act
  >= 1`). When triggered, a dedicated LLM call judges - qualitatively,
  against the act's `completion_signals`, subplots completed this act,
  memory fragments revealed, and recent exchanges - whether the act feels
  narratively resolved (not a numeric/checklist threshold). If so, it
  generates the next act's title and description in the same call and the
  story continues into it immediately, with no gap.

## State Tracking
`update_progress_from_turn` is the second LLM call every turn, always run
immediately after narration. Given the player's action and the narration
that resulted, it returns a single JSON diff:

| Field | Applied as |
|---|---|
| `subplot_progress` | integer delta per active subplot id, clamped to `[0, completion_threshold]` |
| `flags_set` | `{value, pinned}` per flag name — `pinned` marks a foundational fact that should never be forgotten vs. a situational one that can eventually age out |
| `memory_fragments_revealed` | fragment ids marked revealed in `player.origin.memory_fragments` |
| `entity_interaction` | increments a simple counter, fed back into act-advancement judgment |
| `items_gained` / `items_lost` | appended to / removed from `player.inventory` (a flat list of description strings; loss matches by exact string against what's shown as `CURRENT INVENTORY` in the diff prompt) |
| `relationship_changes` | integer delta per named character, added to the existing score and clamped to `[-100, 100]` |

Only fields that actually changed need appear in the response (`{}`/`[]`
for "nothing changed") - the model isn't asked to restate unchanged state.
A malformed or non-JSON response is caught and treated as an empty diff
rather than failing the turn.

Two eviction policies keep unbounded-shaped state from unbounded growth:
`flags_active` evicts its *oldest* non-pinned entries first once over
`FLAGS_ACTIVE_LIMIT` (25) (`archive_stale_flags` also proactively retires
anything whose setting turn has aged out of the `recent_turns` window,
independent of the LLM); `relationships` evicts whichever score sits
*closest to neutral* first once over `RELATIONSHIPS_LIMIT` (20), on the
premise that a story's strongest bonds/rivalries are exactly the ones that
should never silently disappear regardless of recency.

## Context Management
The prompt sent to the model is deliberately bounded regardless of how long
a playthrough runs (see `CLAUDE.md`'s "Keeping LLM Context Bounded" for the
full list of mechanisms). The headline one: `history_log.recent_turns` caps
at `RECENT_TURN_LIMIT` (10) full turns; whenever a turn would push past that,
the oldest overflow is folded into `compressed_summary` via a dedicated
summarization call (itself re-summarizing *the existing summary plus the
new turns*, capped at `SUMMARY_MAX_WORDS`, rather than appending forever)
and archived verbatim to `full_transcript` (disk-only, never read back into
any prompt). Subplot-generation and act-advancement dedup context are
similarly windowed rather than pulling the entire game's history.

## Reinforcement
World rules appear twice in the assembled prompt: once in full under
"WORLD RULES", and the instruction footer repeats "stay strictly within the
established world, tone, and rules above" immediately before the per-turn
instructions. This is deliberate, not redundant - smaller/cheaper models
drift faster over a long context, and recency bias (constraints stated
closer to the generation point) measurably helps enforcement on them. This
is also why `world.rules` itself is kept short: a long rules list dilutes
which constraints are actually "the 1-2 that matter most."

## Ending the Story
There is no built-in stopping point - acts and subplots regenerate
indefinitely. The story only ends when the player explicitly asks, via a
recognized phrase (`is_end_story_command`: "end story" / "end the story" /
"conclude the story" / "wrap up the story"). That triggers
`handle_end_story_request`, which generates a closing arc via its own LLM
call and appends it as a final, `is_finale`-marked act. From that point:
- `generate_new_subplot` and `check_and_advance_act` both no-op - no new
  threads are introduced once the ending has begun.
- The prompt switches to an endgame instruction: resolve the still-active
  subplots listed, introduce nothing new, and end the narration with the
  literal line `THE END` on its own line once truly concluded - no
  `OPTIONS:` block is requested for this final stretch.
- Once `"THE END"` appears in a response after endgame was requested,
  `plot.endgame.concluded` is set and the session is over.

## Mid-Adventure Steering (Escape Hatch)
Everything above is what the model arrives at on its own. `steer <command>`
(CLI) or the web UI's Plot/Subplot Manager pages bypass narration entirely
and edit plot state directly (add/modify/pivot an act, force subplot
progress, etc.) - for the cases where the model won't
arrive at a needed structural change by itself. This is a deliberately
blunt, warning-gated tool (see `plot_manager.py`/`subplot_manager.py`), not
part of the model's own decision loop - reach for it as a last resort, not
a routine mechanism.

## Regeneration
Every turn's *pre-turn* state is snapshotted (`pending_regenerate`, bounded
to exactly one entry, overwritten each turn). Regenerating restores that
snapshot - undoing the subplot progress, flags, and pacing counters the
original turn produced - and re-runs the same player action through a
fresh LLM call. This is a full state swap, not a diff/patch, so a
regenerated turn is indistinguishable from one that had simply gone
differently the first time.

## Implementation Notes
- All narrative-facing prompts (`build_system_prompt`,
  `update_progress_from_turn`, `generate_new_subplot`,
  `check_and_advance_act`, `handle_end_story_request`, the summarization
  prompt in `update_state_after_turn`) live in `backend/story_engine.py`
  and are independently testable: `call_llm`/`call_llm_json` are the only
  seams that touch the network, so every function above can be exercised
  offline by monkeypatching those two (see `test/_llm_stubs.py`).
- `call_llm_json` tolerates markdown code fences around the JSON body
  (models sometimes wrap structured output in ` ```json ` blocks despite
  being told not to) before parsing.
- A malformed structured response from any of the secondary LLM calls
  (state-update diff, subplot generation, act-advancement verdict, ending
  arc, summary) degrades gracefully - an empty diff, a no-op, or a
  hardcoded fallback title/description - rather than failing the turn.
  Narration itself has no such fallback: if that call fails, it's raised as
  `LLMUnavailableError` and surfaces to the player as a retryable error
  (see `CLAUDE.md`'s "Backend / Model Notes"), since there's no reasonable
  synthetic narration to fall back to.
