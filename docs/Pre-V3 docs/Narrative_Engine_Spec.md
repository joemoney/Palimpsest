# Narrative Engine Specification — CYOA Story Generation

## Core Model
Each turn is one LLM call. The call receives a fully-assembled system
prompt. The call returns free-form narration. The narration is optionally
followed by a fixed-format block of player choices. There is no persistent
LLM-side session or memory. Each call is stateless from the model's point
of view. All continuity comes from the prompt that `build_system_prompt`
reassembles each time from `story_engine.py`'s own state dict.

The assembled prompt has this order:
1. **Identity and tone header** — title, genre, tone, and `content_rules`.
2. **World rules** (`world.rules`) — non-negotiable constraints (magic
   limits, setting boundaries). The prompt repeats these near the end too
   (see "Reinforcement" below).
3. **Story so far** — the running `compressed_summary`, plus the last
   `RECENT_TURN_LIMIT` (10) turns verbatim.
4. **Pacing nudge** — the prompt includes this only every
   `pacing_nudge_frequency` turns (see "Pacing/Director Layer"), not every
   call.
5. **Current scene and player state** — location and summary, name,
   traits, inventory, relationships, active flags.
6. **Instruction footer** — target scene length (470-500 words) and the
   required output format for the turn.

## Turn Flow
1. The player submits an action. This can be typed free text, or the prose
   behind a selected choice button (see below).
2. `build_system_prompt` assembles the prompt from the current state.
   `call_llm` sends the prompt and returns raw narration text.
3. `parse_narration_and_options` splits the response into narration and,
   if present, a parsed choice list.
4. A second, separate LLM call (`update_progress_from_turn`) extracts a
   structured state diff from the same turn (see "State Tracking"). The
   system keeps this call apart from narration deliberately. One call that
   tries to both narrate prose and emit reliable JSON in the same response
   produces messier JSON than two separate calls.
5. The system applies the diff. It runs pacing, subplot, and act
   bookkeeping. It appends the turn to history. It writes the save. The
   system persists nothing until the narration call succeeds. If the LLM
   call fails, the save stays untouched, and the player can retry.

## Choice Format
Every non-endgame turn ends with a required block. The block has a blank
line, the literal heading `OPTIONS:`, then exactly 3 lines in the form
`<n>. <short third-person action label> || <first-person prose>`. The
label renders on the choice button. If the player picks a choice, the
system submits the prose as the player's action. As a result, the novel's
transcript always reads in first person, regardless of how the system
presented the option. A free-text box is always available alongside the 3
options. The player is never limited to only what the model offered.

If the model's response does not match this exact format (a malformed
line, a missing heading, or the wrong number of lines), the system parses
the whole response as narration with no options. The free-text box then
becomes the only way forward for that turn. The system does not raise an
error.

## Plot Structure
Two tiers match the "tight rails, loose paint" design (see `CLAUDE.md`):

- **Main thread** (`plot.main_thread.acts`) — an ordered list of acts.
  Each act has a title, a description, and `completion_signals`. Each
  story includes a pre-authored Act 1 only. The system generates every
  subsequent act on demand (see "Pacing/Director Layer"). There is no
  fixed act count and no ceiling.
- **Subplots** (`plot.subplots`) — a pool of parallel, independent
  threads. Each subplot has a `priority` (high, medium, or low), a
  `progress` value (0-100), and a status of `active`, `not_started`, or
  `completed`. The system automatically keeps the pool topped up to
  `pacing.max_parallel_subplots`. When a subplot completes (its `progress`
  reaches its `completion_threshold`), `generate_new_subplot` invents a
  replacement through its own LLM call. This call uses the world rules,
  the current act, the story summary, and existing subplot titles. The
  system dedupes titles against only the most recent
  `SUBPLOT_TITLE_HISTORY_LIMIT` completed titles, not the full lifetime
  list.

None of the above constrains scene-level execution (what actually happens
within a turn). Acts and subplots are waypoints. The pacing layer nudges
the narration toward each waypoint. The narration does not need to follow
a script beat-for-beat.

## Pacing/Director Layer
Two independent mechanisms keep the story moving. Neither mechanism needs
a full script for every branch:

- **Pacing nudges** (`generate_pacing_nudge`) — the system injects a
  pacing nudge into the prompt every `pacing_nudge_frequency` turns, not
  every turn, to avoid pushing the story too hard. Each nudge summarizes
  the current act, the highest-priority active subplot, and up to two
  background subplots. If the subplot pool has room, the nudge also names
  which not-yet-started subplot could come in next. This is pure prompt
  construction from existing state. The system makes no LLM call for this
  step.
- **Act advancement** (`check_and_advance_act`) — a pacing checkpoint, not
  a per-turn check. The system evaluates this only once at least one
  subplot has completed since the current act began
  (`pacing.subplots_completed_this_act >= 1`). When triggered, a dedicated
  LLM call judges whether the act feels narratively resolved. This
  judgment is qualitative, not a numeric or checklist threshold. The call
  weighs the act's `completion_signals`, the subplots completed this act,
  the memory fragments revealed, and recent exchanges. If the act feels
  resolved, the same call generates the next act's title and description.
  The story then continues into the next act immediately, with no gap.

## State Tracking
`update_progress_from_turn` is the second LLM call each turn. The system
always runs it immediately after narration. Given the player's action and
the resulting narration, it returns a single JSON diff:

| Field | Applied as |
|---|---|
| `subplot_progress` | An integer delta per active subplot id. The system clamps the result to `[0, completion_threshold]`. |
| `flags_set` | `{value, pinned}` per flag name. `pinned` marks a foundational fact that the system must never forget, as opposed to a situational fact that can age out over time. |
| `memory_fragments_revealed` | Fragment ids that the system marks as revealed in `player.origin.memory_fragments`. |
| `entity_interaction` | Increments a simple counter. The system feeds this counter into the judgment to advance the act. |
| `items_gained` / `items_lost` | The system appends `items_gained` to `player.inventory` and removes `items_lost` from it. `player.inventory` is a flat list of description strings. A loss must match an existing entry by exact string, as shown in `CURRENT INVENTORY` in the diff prompt. |
| `relationship_changes` | An integer delta per named character. The system adds this delta to the existing score and clamps the result to `[-100, 100]`. |

Only fields that actually changed must appear in the response. `{}` or
`[]` mean "nothing changed" for that field. The system does not ask the
model to restate unchanged state. The system catches a malformed or
non-JSON response and treats it as an empty diff. The turn does not fail
because of this.

Two eviction policies cap state fields that would otherwise grow without
limit.

`flags_active` evicts its oldest non-pinned entries first once the count
goes over `FLAGS_ACTIVE_LIMIT` (25). `archive_stale_flags` also
proactively retires any flag whose setting turn has aged out of the
`recent_turns` window, independent of the LLM.

`relationships` evicts whichever score sits closest to neutral first once
the count goes over `RELATIONSHIPS_LIMIT` (20). A story's strongest bonds
and rivalries are exactly the ones that must never silently disappear,
regardless of recency. This eviction rule reflects that premise.

## Context Management
The system deliberately bounds the prompt it sends to the model,
regardless of how long a playthrough runs. See `CLAUDE.md`'s "Keeping LLM
Context Bounded" for the full list of mechanisms. The main mechanism:
`history_log.recent_turns` caps at `RECENT_TURN_LIMIT` (10) full turns.
Whenever a turn would push the count past that limit, the system folds
the oldest overflow into `compressed_summary`, through a dedicated
summarization call. That call re-summarizes the existing summary plus the
new turns, and caps the result at `SUMMARY_MAX_WORDS`, rather than
appending to the summary forever. The system also archives the overflow
verbatim to `full_transcript` (disk-only; no prompt ever reads this back).
The dedup context for subplot generation and for act advancement is
similarly windowed. Neither one pulls from the entire game's history.

## Reinforcement
World rules appear twice in the assembled prompt. The rules appear once
in full under "WORLD RULES". The instruction footer repeats "stay
strictly within the established world, tone, and rules above" immediately
before the per-turn instructions. This repetition is deliberate, not
redundant. Smaller and cheaper models drift faster over a long context.
Recency bias also helps: constraints stated closer to the generation point
measurably improve enforcement on these models. The same drift risk is
also why `world.rules` itself must stay short. A long rules list dilutes
which constraints are actually the 1-2 that matter most.

## Ending the Story
There is no built-in stopping point. Acts and subplots regenerate
indefinitely. The story ends only when the player explicitly asks,
through a recognized phrase (`is_end_story_command`): `"end story"`,
`"end the story"`, `"conclude the story"`, or `"wrap up the story"`. This
phrase triggers `handle_end_story_request`, which generates a closing arc
through its own LLM call and appends the arc as a final,
`is_finale`-marked act. From that point:
- `generate_new_subplot` and `check_and_advance_act` both no-op. The
  system introduces no new threads once the ending begins.
- The prompt switches to an endgame instruction. The instruction directs
  the model to resolve the still-active subplots listed, introduce
  nothing new, and end the narration with the literal line `THE END` on
  its own line, once the story truly concludes. The system does not
  request an `OPTIONS:` block for this final stretch.
- Once "THE END" appears in a response after the player requests the
  ending, the system sets `plot.endgame.concluded`. The session then
  ends.

## Mid-Adventure Steering (Escape Hatch)
Everything above is what the model arrives at on its own. `steer
<command>` (CLI) or the web UI's Plot/Subplot Manager pages bypass
narration entirely. These tools edit plot state directly, such as adding,
modifying, or pivoting an act, or forcing subplot progress. Use these
tools only for cases where the model does not arrive at a needed
structural change by itself. This is a deliberately blunt, warning-gated
tool (see `plot_manager.py` and `subplot_manager.py`), not part of the
model's own decision loop. Reach for it only as a last resort, not as a
routine mechanism.

## Regeneration
The system snapshots every turn's pre-turn state in `pending_regenerate`.
This snapshot is bounded to exactly one entry; the system overwrites it
each turn. When the player regenerates a turn, the system restores that
snapshot. This undoes the subplot progress, flags, and pacing counters
that the original turn produced. The system then re-runs the same player
action through a fresh LLM call. This is a full state swap, not a diff or
a patch. As a result, a regenerated turn looks the same as one that simply
went differently the first time.

## Implementation Notes
- All narrative-facing prompts live in `backend/story_engine.py`:
  `build_system_prompt`, `update_progress_from_turn`,
  `generate_new_subplot`, `check_and_advance_act`,
  `handle_end_story_request`, and the summarization prompt in
  `update_state_after_turn`. Each one is independently testable.
  `call_llm` and `call_llm_json` are the only seams that touch the
  network. Developers can exercise every function above offline by
  monkeypatching those two functions (see `test/_llm_stubs.py`).
- `call_llm_json` tolerates markdown code fences around the JSON body.
  Models sometimes wrap structured output in ` ```json ` blocks, even
  when the prompt tells them not to. `call_llm_json` strips these fences
  before parsing.
- A malformed structured response from any secondary LLM call degrades
  gracefully, rather than failing the turn. This applies to the
  state-update diff, subplot generation, the verdict for act advancement,
  the ending arc, and the summary. Depending on the case, the system
  falls back to an empty diff, a no-op, or a hardcoded fallback title and
  description. Narration itself has no such fallback. If the narration
  call fails, the system raises it as `LLMUnavailableError`. This error
  surfaces to the player as a retryable error (see `CLAUDE.md`'s
  "Backend / Model Notes"). No reasonable synthetic narration exists to
  fall back to in this case.
