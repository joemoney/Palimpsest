# Endless mode: cyclical, LLM-generated chapter climaxes

**Status: planned, not yet implemented.** Design document, not a record of what is built.
**Revised 2026-10-03 for the AppV3 overhaul.** The first draft was written against the pre-overhaul
engine (`failure_conditions`, a player "end story" command, `meta.endless`, `world.ending_lore`).
Every one of those premises has since been retired or superseded; this revision rebuilds the
design on what the engine actually does now. Read `CLAUDE.md` first, then
`docs/Story_Mechanics_Update.md` CR-05 and `docs/analysis_and_plans/AUTHORING_TOOL/AUTHORING_TOOL_PHASES.md`.

## What changed since the first draft

| First draft assumed | AppV3 reality | Consequence for this plan |
|---|---|---|
| Two ways to end: player phrase, `failure_conditions` | One way: `mechanics.endings` (`ending_funnel`), via `_begin_endgame` (D5, D6). The player has no end command; `failure_conditions` and `handle_end_story_request` are deleted | The "player-request and failure endings stay permanent" guard becomes "terminals and authored destinations stay permanent" |
| A story with no endings can end by player request | Every story authors endings with a catch-all (lint L08, `check_config`); a story with none cannot end | Endless is no longer "the story that has no end"; it is a story whose funnel *also* gets periodic generated climaxes |
| Endings are invented by the engine | CR-05: **"the engine selects among authored endings. It never invents one for a template that authors them"**; r2 retired endless continuation for exactly this reason (drift) | **Direct conflict with a decided invariant.** See the next section |
| Opt-in flag `meta.endless` | A mechanic lives inside `mechanics` and binds by declaring an `engine`; `meta` has `additionalProperties: false`, and `meta` is narrator-visible | The setting moves into the `mechanics.endings` block |
| Lore at `world.ending_lore` | `world` is a narrator-visible namespace; judge-only text carries `x-visibility: judge` / `x-secret` and is guarded by the CR-03 leak test | Lore moves into the endings block with `x-visibility: judge` |
| A new plain function `plan_ending` in `story_engine.py` | Pure decisions live in the engine module; the model call lives in `story_engine` and routes into the one ending path (the `check_ending_funnel` / `_confirm_terminal` / `_judge_commit` seam) | Same split here |
| Acts: model invents the next act | `check_and_advance_act` first consumes the *next authored act*, then falls back to a generated one; it also takes a `PLANT` block from the funnel | Waypoint consumption goes only in the generated-act branch |
| Free to edit `state_store`/`story_engine` first | Build order: the storyboard leads, the engine follows | The board fields and lint land with (or before) the engine |
| Pacing counted in turns | CR-13 story clock: budget, checks and cooldowns read `clock.story_turn(ctx)` | Cadence stays in acts (not turns), so it is clock-independent; the cooldown rule below uses the story clock |

## Decision: this conflicts with decided invariants, and Option A is chosen

**Decided 2026-10-03: Option A.** CR-05 (r2) was written for AppV3; this work moves the engine to a new version, so r2's retirement of endless continuation is scoped, not binding on it. The conflict analysis is kept below as the record of what is being overridden.

Per `CLAUDE.md`, a request that conflicts with a recorded decision gets its conflict laid out,
not silently complied with. What conflicts:

1. **CR-05 / r2 (2026-09-22):** endless continuation retired for any story that authors endings,
   because unanchored generated acts compound drift (each is conditioned on the last plus a lossy
   summary). Endless mode reintroduces generated arcs.
2. **`CLAUDE.md`, Structure and pacing / D6:** "A story ends only through `mechanics.endings`."
   A chapter climax is an ending-shaped event that does not end the story.
3. **`CLAUDE.md`, "No fixed act or subplot count… don't reintroduce a number that would give an
   open-ended story a definite endpoint."** Not violated (endless removes an endpoint), but the
   cadence setting must not become one.

**Resolution options**

| Option | What it is | Trade-off |
|---|---|---|
| **A. Scoped carve-out inside the funnel (recommended, and what the rest of this plan builds)** | A story opts in with `mechanics.endings.endless`. The funnel stays the only thing that can *end* the story. The new path only produces a **chapter climax** that the story survives. Terminals and authored destinations remain permanent. Generated arcs are anchored by authored `lore` and the prior climaxes' titles, which is the drift control r2 asked for | Smallest conflict; still adds a second finale-shaped path that must be kept from colliding with the funnel (see *Interaction with the funnel*) |
| B. Author the chapters | No generation: the author writes recurring "chapter" destinations the funnel re-arms after each commit (`cycles: true`) | Honours "engine never invents an ending" fully, but loses the point of the request (a story that keeps generating its own climaxes) and needs the author to pre-write a pool, which is the fixed-pool problem the continuous-structure rule rejects |
| C. Drop endless mode | Keep CR-05 as it stands | No conflict; no endless play |

**Amendments to make** (when the work lands, not before):
`CLAUDE.md` Structure-and-pacing bullet gains "…or, in a story that opts in via
`mechanics.endings.endless`, passes a chapter climax that it survives"; a new D-numbered decision
(D8 in `AUTHORING_TOOL_PHASES.md`) records the carve-out and its reason; `Story_Mechanics_Update.md`
CR-05 gets an r-note that r2's retirement is scoped, not repealed.

## Design (Option A)

### Authored fields (all inside `mechanics.endings`)

```json
"endings": {
  "engine": "ending_funnel",
  "endless": {
    "lore": ["...", "..."],
    "acts_between": 4,
    "min_waypoints": 1,
    "max_waypoints": 4
  },
  "entries": [ ... ]
}
```

- **`endless`** — presence is the opt-in (declare-to-bind: no key, no feature; P-2: no state, no
  prompt line, no observation field, no counter). An `endless` object that is empty raises, the same
  as an engine declared with an empty config.
- **`endless.lore`** — the pool read by exactly one call site (the planner below). Schema:
  `x-visibility: judge`, `x-visible-when: "the chapter planner only; never narrated"`, `x-secret: true`
  (it is canon the planner may be told and the narrator must not be). It never reaches
  `build_system_prompt`, so it costs nothing on the per-turn input budget. Rejected: the first
  draft's `world.ending_lore`, because `world` is the narrator-facing namespace.
- **`endless.acts_between`** — acts that must complete since the last chapter climax (or the start)
  before the planner is asked. Default is a structural cadence constant like `DEFAULT_CHECK_EVERY`,
  not a creative decision (named `DEFAULT_ACTS_BETWEEN_CHAPTERS = 4`). Same family as
  `act_check_frequency`, so it lives with the endings settings, not on `plot.pacing`.
- **`min_waypoints` / `max_waypoints`** — bound how long an approach the planner may ask for;
  engine clamps the model's answer into this range.

### Runtime state

Two places, both disposable-save-safe (CLAUDE.md: saves may change with a version bump during the
overhaul; **bump `CURRENT_SCHEMA_VERSION`** in `state_store.py`, which today is 2, and let old saves
be refused at load):

- `plot.endgame` gains `"chapter": None` while idle, or `{"waypoints": [...], "final_arc": {...},
  "cycle": n}` while a chapter approach is in progress, and `"chapters": []` (archived climaxes:
  `{kind: "chapter", title, description, cycle, concluded_turn}`, bounded by `CHAPTER_HISTORY_LIMIT = 6`; only titles ever reach a
  prompt, and only the planner's). These exist only for a story that authors `endless` (P-2).
- `mechanics.endings` bucket (the funnel's own) gains `"acts_since_chapter": 0` and
  `"chapter_cooldown": 0`. **The funnel's committed/pruned/waypoints/scores state is untouched by a
  chapter**: a chapter never calls `record_commit`, so `committed` stays null and the funnel keeps
  running across cycles.

### Engine surface (`backend/mechanics/endings.py`, pure)

Added to `EndingFunnel`, mirroring `commit_due` / `forced_due`:

- `endless(cfg)` → the authored object or `None`.
- `chapter_due(cfg, ctx)` → true only when **all** hold: endless authored; `endgame.requested` is
  false; no chapter approach in progress; no authored act is still waiting (the next authored act
  is consumed first, see below); `acts_since_chapter >= acts_between`; `phase == "open"` (see
  *Interaction with the funnel*); not within `chapter_cooldown`; funnel not committed; **and no destination is currently `ready`** (an authored ending within reach takes the floor, so a chapter never starts against one).
- `chapter_arc(cfg, plan)` → clamps/validates the planner's answer and returns a normalized
  `{final_arc, waypoints}`, or `None`. Pure; the call itself is not here.
- `record_chapter_plan`, `record_chapter_concluded`, `record_act_completed_for_chapter` — state
  writes go through this module, as for every `record_*` already there.
- Effects stay absolute and `resolve()` stays pure (registry invariant); the counter writes go
  through `register_effect` like `endings.update`.

`prompt_budget` stays `0`: a chapter adds no narrator text of its own. The planner prompt is not a
narration prompt. What the narrator eventually sees is the generated `final_arc` (as for an
authored arc, via the finale act) and the generated waypoint titles/descriptions as the next
generated act (as for any generated act).

### The planner call (`story_engine.plan_chapter(ctx)`)

Called from `check_and_advance_act` right after an act advances, when `chapter_due`. A plain
function like `_judge_commit`: the engine says *whether*, `story_engine` makes the call.

- **Tier.** The registry default is Tier C unless the module records why not. This is a generative,
  multi-sentence plan, not a classification, and it fires roughly once per several acts, so it
  matches the act-advancement call: Tier B, `reasoning=True`. **Record that reasoning in the
  `endings.py` module docstring** (the invariant requires it); revisit if the first measured runs say
  Tier C is as consistent.
- **Inputs:** `endless.lore`; `world.rules`; the main thread; `compressed_summary`; the archived
  chapters' titles (so it does not repeat one); `cycle`. **Never** ending names, criteria, hints,
  `detect` text or any authored ending's `arc`: those are the authored funnel's judge-only/secret
  material and the planner has no need of them. (The planner can still be surprised by what an
  authored ending needs; that is the funnel's concern, see below.)
- **Output:** `{"ready": bool, "reason": str, "final_arc": {"title","description"}, "waypoints":
  [{"title","description","completion_signals"}]}`. `ready: false` is a normal answer: no state
  change except that the next act advance asks again (same pattern as the act check).
- **Leak control.** The output becomes narrator-facing, so the prompt instructs the planner to
  write `final_arc` and waypoint text *as events on the page, never as an explanation of the lore*,
  and a CR-03 test asserts that a sentinel placed in `endless.lore` never reaches a narration prompt
  even after a chapter has been planned (the sentinel must not be *quoted* by the planner; the test
  uses a fake planner that echoes only what the prompt allows).
- `STATUS_LABELS` and `DEFAULT_STEP_ESTIMATE_SECONDS` gain a `chapter_plan` entry; the
  `_timed()` call site and the mirror test (`test_status_labels.py`) must change together.

### Waypoint consumption (`check_and_advance_act`)

The AppV3 function consumes the next **authored** act in order before it ever generates one, and
already carries a `PLANT` block. The chapter logic touches only the *generated-act branch*:

1. **No planning while authored acts remain.** `chapter_due` is false until the story has run out
   of authored acts, so a story authoring acts 1–3 plays all three before any chapter begins.
2. When `chapter.waypoints` is non-empty and the director says `ready: true`, pop the next waypoint
   and use its title / description / `completion_signals` for the generated act, instead of the
   model's freshly invented `next_act_*`. *When* a waypoint resolves stays emergent; only *what comes
   next* is pre-planned ("tight rails, loose paint").
3. When that pop empties the queue, call
   `_begin_endgame(ctx, chapter["final_arc"], cause="chapter")` in place of appending a normal act.
   This is the same single entry the funnel uses, so `endgame.requested`, `requested_turn`, the
   `is_finale` act and `current_act` are all set in one place.
4. `engine_trace` gets `chapter_plan` and `chapter_end` events beside the existing `act_check` /
   `endgame` ones. The existing `endgame` event already carries `cause`.

The funnel's `PLANT` block continues to appear on the generated act's prompt (authored
steering is unaffected). When both a chapter waypoint and authored plants apply, the chapter
waypoint decides the act and the authored plants still ride along (cap `MAX_PLANTS`).

### `generate_new_subplot` and friends

`generate_new_subplot` already no-ops on `endgame.requested`. Extend the guard to
`or endgame.get("chapter")` so no new threads open while a chapter is being approached. Everything
else that reads `endgame.requested` (`activate_carriers_early`, `check_ending_funnel`,
`plant_candidates`, `nudge_plan`, `observations`, `settle`, `clock.advance(finale=...)`,
`_section_footer`) behaves as it does for any finale during the chapter's finale turns, which is
the correct behaviour: the funnel is quiet while a climax plays out.

### The chapter finale and the cycle

**Markers.** Today a finale ends on the literal line `THE END` and conclusion is detected by
`"THE END" in ai_response` (`_generate_and_apply_turn`). Add two constants:

```python
FINAL_END_MARKER = "THE END"
CHAPTER_END_MARKER = "END OF THIS CHAPTER"
```

- `_section_pacing_or_endgame` and `_finale_pace` both emit the `"THE END"` instruction today; make
  both pick the marker from `endgame["cause"]` (`"chapter"` → chapter marker, everything else
  unchanged). `finale_turns` (authored min/max scenes) applies to a chapter finale too; the
  `max` clause asks for the cause's own marker.
- The conclusion check becomes cause-aware: a chapter finale concludes only on the chapter marker, a
  funnel finale only on `THE END`. (The two strings do not contain each other, but a model that
  writes "the end" in prose must not conclude the wrong kind.)

**Cycle reset, before the save.** After the conclusion check and before
`state_store.save_state`, a concluded `cause == "chapter"` finale:
- appends `{kind: "chapter", title, description, cycle, concluded_turn}` to `chapters` (bounded);
- clears `requested`, `requested_turn`, `final_arc`, `concluded`, `cause`, and `chapter`;
- bumps `cycle`, zeroes `acts_since_chapter`, sets `chapter_cooldown` so a new approach cannot begin
  before `acts_between` more acts *and* at least `check_every` story-clock turns have elapsed;
- emits a `chapter_end` trace event.

The save written for the climax turn therefore already reads `concluded: False`. App routes need no
change for the mode switch: `app.py` computes `mode = "concluded" if endgame["concluded"] else
"playing"` from freshly loaded state at lines 174, 200 and 413, so the next request reads
"playing". `take_turn`/`_generate_and_apply_turn` return the post-reset `concluded` value, so the
CLI loop (`story_engine.py` `if take_turn(...)`) and the web job both treat a chapter end as a
normal turn. The player reads the climax prose ending in `END OF THIS CHAPTER`, with no options
block (finale turns suppress it), and the very next turn is a normal one with options.

**Regenerate.** `pending_regenerate` is a whole-state snapshot taken before the turn; regenerating
the climax turn restores the pre-reset finale state and re-rolls it, which is the correct outcome.
A test covers it.

### Interaction with the funnel

This is the part the first draft could not know about, and the main risk.

- **Budget phases.** The funnel's `open_until` / `narrow_until` / `commit_by` are absolute story-clock
  turns and `commit_by` forces a *permanent* commit. A chapter climax is only planned in the
  funnel's `open` phase (`phase(cfg, ctx) == "open"`); from `narrow` on, no new chapter begins (one
  already approaching continues to its finale, since cancelling mid-approach would strand its
  waypoints). A story that wants to cycle indefinitely simply authors **no `commit_by`**, which the
  schema already allows: no forced commit ever happens, authored destinations can still commit by
  `ready_when` (the author's explicit "true ending"), and the catch-all requirement is unaffected.
  Lint warns when `endless` and `budget.commit_by` are both authored (the cycle will stop at that
  turn by design; probably not what the author meant, but it is legal).
- **Two steering systems.** The funnel steers toward authored destinations through `plant`; a
  chapter steers toward a generated climax through generated waypoints. They share the act
  generator (see above) and the pacing nudge. **A chapter yields to an authored
  destination (decided: abandon).** The funnel's commit is never suppressed by a chapter *approach*.
  When the funnel commits (any cause: `committed`, `forced`, `terminal`), `_begin_endgame` clears
  `endgame.chapter`: its waypoints are dropped, no climax is played, nothing is archived to
  `chapters` (it was not reached), and `chapter_cooldown` is irrelevant because the story is ending.
  Symmetrically, when the last waypoint is about to enter the chapter finale and a destination is
  `ready`, the chapter is abandoned instead (cleared, cooldown set, the generated act is a normal one)
  so the funnel's next check can commit. A chapter **finale already underway** (`requested` true) is
  never abandoned: the funnel stands down for it and a ready destination waits until after the reset.
- **Funnel bookkeeping during a chapter finale.** Waypoint detection, scoring and re-steering stand
  down while `endgame.requested` (existing behaviour). They resume the turn after the reset.
  Waypoints planted during the chapter finale's turns are not detected, so a chapter slightly
  delays authored steering; acceptable and measurable via the trace.
- **Terminals** keep running every turn. A terminal tripping during a chapter approach or its finale
  commits as `cause="terminal"` and ends the story permanently, exactly as today. Because
  `_begin_endgame` overwrites `endgame.cause`, the in-progress `chapter` must be cleared at that
  point so it can never re-arm after a (hypothetical) restore. `_begin_endgame` does this when the
  new cause is not `"chapter"`.
- **Side threads (CR-11)** are not registered yet (`engine_side_threads` is schema-only), so there is
  nothing to change now. When that engine lands it must treat `cause == "chapter"` as "wrap up and
  stop spawning", and **resume spawning after the reset**, which is the opposite of the permanent
  stop CR-11 specifies for a committed ending. Recorded here so it is not rediscovered.
- **Subplot / act accounting.** `_mark_act_completed` already runs on every act advance; the engine's
  `record_act_completed_for_chapter` hooks the same call. Acts completed *inside* a chapter approach
  still count toward the next cycle's `acts_since_chapter` only after the reset (it is zeroed at the
  reset), so approaches are not double-counted.

### Authoring surface (storyboard-first)

Per the build order, the board writes the final template paths even before the engine reads them.
The engine work can follow in whatever order authoring needs it.

- **Schema** (`schema/template.v3.schema.json`, `engine_endings`): add `endless` with the four fields
  above. Visibilities: `lore` `judge` + `x-secret`; the three numbers `author`.
- **Board** (`author_model.py` `endings_settings`, `frontend/author_board.html`): an **Endless**
  section in the Ending funnel settings panel (Story health) and the matching Forms → Pacing group,
  the same object, per the existing pattern for the budget and idle-turn settings. A story with no
  `endless` round-trips with none (no empty object written).
- **Lint** (`author_lint.py`, new rule; number follows the next free L-number):
  - error: `endless` authored with empty `lore`;
  - error: `endless.lore` over the size cap, **200,000 tokens** (decided as the initial cap, counted with
    the same estimator L17 uses; it bounds the planner call's input, not the narration prompt, and is
    a constant to revisit once a real planner call has been measured, since 200k is far above anything
    the narrator side allows);
  - warning: `endless.lore` text that appears (substring, 40+ chars) in any narrator-visible field
    (the CR-03 check, extended to the new secret);
  - warning: `endless` with `budget.commit_by`;
  - warning: `acts_between` greater than the story's authored act count when no acts can be
    generated (true once `max_acts` is reached; it is enforced now).
- **`mechanics.validate()`**: the existing "an authored block with no engine loads inert" warnings
  already cover a misplaced `endless` (it is inside `mechanics.endings`, which is declared). Add one
  check: `endless` present and `mechanics.endings` has no `engine` → raise, since `endless` would
  bind nothing.
- **Docs:** `Authoring_Tool_Spec.md` (settings section), `Agent_Authoring_Manual.md` (field reference,
  worked example, "when not to use it"), `Assistant_CoAuthor_Manual.md` if it lists settings,
  `How_Threads_Work.md` (a short note that a chapter climax is not a thread ending),
  `AUTHORING_TOOL_PHASES.md` (Phase S5: this is a new demand-driven slice, plus the D8 text), and
  `CLAUDE.md` per the amendments above. The pre-overhaul docs under `docs/Pre-V3 docs/` are history
  and are **not** edited (the first draft told you to edit `ARCHITECTURE.md`,
  `SCHEMA_V2_SPEC.md` and `Narrative_Engine_Spec.md`; those files are frozen by the overhaul).

## Files to change

- `schema/template.v3.schema.json` — `endless` under `engine_endings`.
- `backend/mechanics/endings.py` — `endless()`, `chapter_due()`, `chapter_arc()`, `record_*`, state
  keys in `init_state`, module docstring recording the Tier B reasoning.
- `backend/story_engine.py` — `plan_chapter`, `check_and_advance_act` (generated-branch consumption),
  `generate_new_subplot` guard, `_begin_endgame` (clear `chapter` on a non-chapter cause),
  `_section_pacing_or_endgame` / `_finale_pace` (cause-aware marker), `_generate_and_apply_turn`
  (cause-aware conclusion plus the reset), `STATUS_LABELS` / `DEFAULT_STEP_ESTIMATE_SECONDS`,
  `_trace_turn_fields`, new constants.
- `backend/state_store.py` — `plot.endgame` keys, `CURRENT_SCHEMA_VERSION` bump.
- `backend/author_model.py`, `backend/author_lint.py`, `frontend/author_board.html` — the authoring
  surface above.
- `backend/mechanics/__init__.py` — the one `validate()` check.
- `backend/app.py` — no change expected; confirmed by the route test below.
- Docs listed above.
- `test/fixtures/` — author `endless` on one fixture (`survival.json` fits) and add the marker
  entries for it to the written-out lists in `test_genre_conformance.py`, asserting in both
  directions: authored → the planner prompt contains the lore; **never** in `build_system_prompt()`.
  The other two fixtures stay endless-free (a fixture that omits it is mandatory for a new optional
  module).
- New `test/test_endless_mode.py` — `chapter_due` gating (opt-out costs zero, authored acts first,
  phase, cooldown, committed, requested), planner parse/clamp, waypoint consumption in the generated
  branch only, the `_begin_endgame` hand-off, cause-aware markers, the cycle reset (and that the
  funnel bucket is untouched), terminal-during-chapter, regenerate-of-the-climax, and a no-`endless`
  story byte-identical to today.
- `test/test_full_transcript.py` (CR-03 leak sentinel) — extend to `endless.lore`.
- `test/test_status_labels.py` — picks up the new label via the existing mirror assertion.
- `test/test_app_routes.py` — after a chapter conclusion, the polled result renders playable controls
  (use `wait_for_idle` before asserting, as the file's other turn tests do).

## Verification

- `python test/run_all.py`, including the extended CR-03 sentinel and genre-conformance tests.
- Run `scripts/lint_template.py` on a story with `endless` authored: zero errors; and on one with
  an empty `lore`: the new error.
- `scripts/measure_baseline.py`: the budget is `core + 7` observation fields and `endless` adds none;
  confirm the number does not move, and that `endless` adds no narration-prompt tokens (lint L17 and
  the baseline both).
- Drive an `endless` fixture through `take_turn` with a stubbed planner to a full cycle, and confirm:
  the lore never appears in any narration prompt; the climax turn ends with `END OF THIS CHAPTER`
  and no options block; the next turn is normal with `endgame` reset, `chapters` and `cycle`
  populated; the funnel bucket is unchanged.
- Start the dev server and play it in the browser through one chapter, confirm the controls never
  show the dead-end screen after a chapter end, and that a funnel commit or a terminal still does.
  (Per the repo guidance, say so if this could not be exercised in a browser.)
- Run a real playtest with `engine_trace` on and read the `chapter_plan` / `chapter_end` / `act_check`
  events before trusting the cadence defaults.

## Decisions on the open questions (author, 2026-10-03)

1. **Option A** is the resolution (see above). CR-05 was for AppV3; this moves to a new version.
2. **Abandon the chapter** when an authored destination becomes ready or commits (see *Interaction
   with the funnel*). Tests: approach abandoned on funnel commit; approach abandoned when the final
   waypoint would start a finale against a ready destination; a finale in progress is not abandoned.
3. **The chapter finale honours `finale_turns`** for now, with the chapter marker. A separate
   `endless.finale_turns` is deferred.
4. **Ending collections may expose chapter endings.** `endgame.chapters` entries are therefore
   written as `{kind: "chapter", title, description, cycle, concluded_turn}` so a later ending-collection
   feature (Story_Mechanics_Update.md open question 4) can list them next to funnel endings without a
   schema change. No UI is built here.
5. **Lore size cap: 200,000 tokens** for now (lint error above; see Authoring surface).

Still open for later: nothing blocking implementation. The six open questions in
`Story_Mechanics_Update.md` are being answered in a separate session and may affect the ending
collection (item 4) and the funnel's judge tier; re-check this plan against them afterwards.
