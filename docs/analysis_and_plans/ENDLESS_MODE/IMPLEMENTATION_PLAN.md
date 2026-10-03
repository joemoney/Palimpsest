# Endless mode: cyclical, LLM-generated endings

**Status: planned, not yet implemented.** This is a design document, not a record of
what's built. See the top-level `CLAUDE.md` / `docs/ARCHITECTURE.md` for what the engine
actually does today.

## Context

Today a story only ever ends two ways: the player types an end-phrase
(`handle_end_story_request`), or an authored `failure_conditions` condition fires
(`backend/mechanics/failure.py`). Both converge on `story_engine._begin_endgame`, which
sets `plot.endgame.requested = True`, appends a finale act, and switches the per-turn
narration prompt into an ENDGAME instruction until the model writes the literal line
`THE END`. From that point on `generate_new_subplot`/`check_and_advance_act` permanently
no-op and the web UI (`_controls.html`, gated on `mode == "concluded"`) shows a dead end.
There is no path where the story itself decides, from its own world rules and lore, that
it's time to build toward a climax — and no such thing as a climax that the story
survives.

The request: let a story opt into generating its own endings periodically — using world
rules plus a new pool of lore authored *only* for this purpose (never sent to the
per-turn narration prompt, so it costs nothing on the 8-12k token input budget every
other turn costs) — and have the engine plan the handful of waypoint acts needed to get
there. Unlike the existing two paths, this one doesn't stop the story: once a
director-generated climax concludes, the world archives it and keeps going, eventually
generating the next one. Confirmed with the user: this is opt-in via an explicit
template flag (not inferred from content, so authoring mistakes can be linted); the
existing player-request and failure-condition endings stay true, permanent stops even in
an endless story — only the new autonomous path cycles.

## Design

### Two new template fields (authored, optional)

- **`meta.endless: true`** — the explicit opt-in. Absent/false = today's behavior,
  byte-for-byte (every new function below no-ops on its first line if this isn't set).
- **`world.ending_lore: ["...", "..."]`** — same shape as `world.rules`, but read by
  exactly one place: the new planning call below. Never reaches `build_system_prompt` /
  any `_section_*`. This is the concrete mechanism for "won't overspend narration
  budget" — it's stored like `world.rules` but treated like `history.full_transcript`
  (write-mostly, read by one specific consumer, never the per-turn prompt).
- **`plot.pacing.acts_between_endings`** (optional int, default `DEFAULT_ACTS_BETWEEN_ENDINGS
  = 4`) — same family as `nudge_frequency`/`act_check_frequency`/`max_parallel_subplots`.

### Runtime state (`backend/state_store.py` `new_save_state`)

Extend the existing `plot.endgame` dict (currently `requested`, `requested_turn`,
`final_arc`, `concluded`, `cause`) with three keys, present on every save the same way
`endgame` itself already is:

```python
"endgame": {
    "requested": False, "requested_turn": None,
    "final_arc": None, "concluded": False, "cause": None,
    "waypoints": [], "cycle": 0, "history": [],
},
```

Extend `pacing` with `"acts_since_last_ending": 0`, incremented wherever
`_mark_act_completed` already runs.

### New director function: `plan_ending(ctx)` (`backend/story_engine.py`, beside `check_and_advance_act`)

Same shape as `check_and_advance_act`/`generate_new_subplot` — a plain function called
from the turn pipeline, not a mechanic-registry engine (this isn't an observation-field
mechanic like `stats`/`revelations`; it's a periodic Tier B planning call that mutates
plot structure directly, the same category as act-advancement and subplot generation,
which also live as plain functions here rather than in `backend/mechanics/`).

- No-ops immediately if `not ctx["story"].get("meta", {}).get("endless")` — zero cost for
  every story that doesn't opt in.
- No-ops if already ending (`endgame["requested"]`) or already approaching one
  (`endgame["waypoints"]`).
- Cadence-gated on `pacing["acts_since_last_ending"] >= acts_between_endings`, called
  right after `check_and_advance_act` succeeds (a new act beginning is the natural seam
  to ask "should this be the arc that climaxes").
- One Tier B call (`reasoning=True`), prompt built from `world.rules` + `world.ending_lore`
  + main thread + `endgame.history` titles (so it doesn't repeat a prior climax) +
  `endgame.cycle`. This is the *only* call site that ever reads `ending_lore`.
- Asks for `{"ready": bool, "reason": str, "final_arc": {"title","description"},
  "waypoints": [{"title","description","completion_signals"}, ...]}` (1-4 waypoints,
  same "qualitative judgment, not a checklist" framing as the act-advancement prompt).
- If not ready: no state change; `acts_since_last_ending` keeps growing and the next
  act-check re-asks (same pattern as the existing act/nudge cadences).
- If ready: writes `endgame["waypoints"]` and `endgame["final_arc"]` (pre-populated early;
  harmless since nothing reads `final_arc` until `requested` is True), resets
  `acts_since_last_ending = 0`.

### Waypoint consumption: extend `check_and_advance_act`

When `endgame["waypoints"]` is non-empty, the existing "ready?" Tier B judgment call is
unchanged (still organic — *when* a waypoint resolves stays emergent, only *what comes
next* is pre-planned, consistent with "tight rails, loose paint"). On `ready: true`:
instead of using the model's freshly-invented `next_act_title`/`next_act_description`,
pop the next entry off `waypoints` and use its title/description/completion_signals for
the new generated act. When that pop empties the queue, call
`_begin_endgame(ctx, endgame["final_arc"], cause="endgame_director")` instead of
appending a normal act — i.e. the last waypoint's resolution *is* what enters the
existing finale-act machinery, unchanged.

### `generate_new_subplot`: extend the existing no-op guard

```python
if ctx["state"]["plot"]["endgame"]["requested"] or ctx["state"]["plot"]["endgame"]["waypoints"]:
    return None
```
No new open threads while approaching a climax; existing live subplots still resolve
normally (nothing here touches `check_subplot_status`/`update_progress_from_turn`).

### Cycle-then-continue: extend `_generate_and_apply_turn`

Two closing markers instead of one, so the player isn't told "THE END" for a climax the
story survives:
```python
FINAL_END_MARKER = "THE END"
CHAPTER_END_MARKER = "END OF THIS CHAPTER"
```
`_section_pacing_or_endgame`'s ENDGAME instruction asks for `CHAPTER_END_MARKER` when
`endgame["cause"] == "endgame_director"`, `FINAL_END_MARKER` otherwise (unchanged
wording/behavior for `player_request` and failure-condition causes).

After the existing conclusion check, add the cycle:
```python
endgame = ctx["state"]["plot"]["endgame"]
if (endgame["concluded"] and endgame["cause"] == "endgame_director"
        and ctx["story"].get("meta", {}).get("endless")):
    endgame["history"].append({
        "title": endgame["final_arc"]["title"],
        "description": endgame["final_arc"]["description"],
        "cycle": endgame["cycle"],
        "concluded_turn": ctx["state"]["pacing"]["turn_count"],
    })
    endgame["history"] = endgame["history"][-ENDING_HISTORY_LIMIT:]  # bounded, like subplot title history
    endgame.update({"requested": False, "requested_turn": None, "final_arc": None,
                     "concluded": False, "cause": None, "waypoints": []})
    ctx["state"]["pacing"]["acts_since_last_ending"] = 0
```
This runs *before* `state_store.save_state`, so the save written for this turn already
reads `concluded: False`. **No `app.py`/`_controls.html` changes are needed at all** —
every `mode = "concluded" if endgame["concluded"] else "playing"` site (`app.py:136, 162,
344`) already recomputes from freshly-loaded state, so it naturally reads "playing" on
the very next request. The player reads the climax prose ending in "END OF THIS CHAPTER"
(no options block, same as today's endgame turns) and can immediately type their next
action into the still-present free-text form; player-request and failure-condition
endings are completely unaffected (the `and cause == "endgame_director"` guard excludes
them) and stay permanent exactly as today.

### Validation (`backend/mechanics/__init__.py` `validate(story)`)

Two checks alongside the existing "authored but not declared" warnings (not a
declare-to-bind/registry concern — `endless` isn't an engine — but the same collection
point, for one place authors check):
```python
endless = story.get("meta", {}).get("endless")
ending_lore = story.get("world", {}).get("ending_lore")
if endless and not ending_lore:
    raise ValueError("meta.endless is true but world.ending_lore is empty - the endless "
                      "director has nothing to draw on for endings.")
if ending_lore and not endless:
    print("WARNING: this story authors world.ending_lore but meta.endless is not set - "
          "it will never be read.")
```
(Confirmed with the user: `mechanics.failure_conditions` is explicitly *exempt* from any
conflict check — a story can be `endless` and still author static failure/death endings;
those just stay permanent per the cycle logic above.)

## Files to change

- `backend/state_store.py` — `new_save_state`: extend `plot.endgame`, `pacing`.
- `backend/story_engine.py` — new `plan_ending`; extend `check_and_advance_act`,
  `generate_new_subplot`, `_section_pacing_or_endgame`, `_generate_and_apply_turn`; new
  `DEFAULT_ACTS_BETWEEN_ENDINGS`, `ENDING_HISTORY_LIMIT`, `FINAL_END_MARKER`,
  `CHAPTER_END_MARKER` constants.
- `backend/mechanics/__init__.py` — two new checks in `validate()`.
- `docs/SCHEMA_V2_SPEC.md` — `meta.endless`, `world.ending_lore` (§3.4-adjacent),
  `plot.pacing.acts_between_endings` (§3.8), extended `plot.endgame` runtime shape (§4).
- `docs/ARCHITECTURE.md` — rewrite the "story only ends when the player asks" bullet
  under *Continuous / Long-Running Structure*; add the endless-mode cycle as a new bullet
  in the same section, following the existing multi_act-subplot bullet's style.
- `docs/Narrative_Engine_Spec.md` — add `world.ending_lore` and `plot.endgame.history` to
  the "stored but never prompted" table (required by P-5).
- `CLAUDE.md` — amend the "story ends only when the player asks" invariant to note the
  endless-mode exception, pointing at `docs/ARCHITECTURE.md`.
- `test/fixtures/` — author `meta.endless`/`world.ending_lore` on one existing fixture
  (survival.json fits thematically); add `ending_lore` to the other two fixtures'
  "modules absent" lists in `test_genre_conformance.py`, asserting it never appears in
  `build_system_prompt()` output even when authored (the direct test of the budget
  claim).
- New `test/test_endless_mode.py` — `plan_ending` gating/cadence, waypoint generation and
  consumption, `generate_new_subplot`'s extended guard, end-to-end cycle-reset (concluded
  → archived to history → cycle bumped → state reset → next turn's pacing resumes
  normally), `validate()`'s new raise/warning.
- `test/test_app_routes.py` — one test confirming that after a director-caused conclusion
  the next polled result renders playable controls, not the dead-end template.

## Verification

- `python test/run_all.py` — full offline suite, including the new/extended tests above.
- Manually drive a short endless-flagged save through `story_engine.take_turn` in a
  Python shell (or via the CLI) far enough to trigger `plan_ending` and a full waypoint
  cycle, confirming: `world.ending_lore` never appears in `build_system_prompt(ctx)`
  output at any point; the climax turn ends with `END OF THIS CHAPTER` and no options
  block; the very next turn shows normal pacing/options again with `endgame` reset and
  `history`/`cycle` populated.
- Start the dev server and play an endless-flagged story through a climax in the browser
  to confirm `_controls.html` never shows the dead-end screen for a director-caused
  ending (per the "no app.py changes needed" claim above) while a manual "end story" or a
  failure-condition death still does.
