"""The `failure_conditions` / `triggered_ending` engine - engine v2 phase 4, port 4.

test_failure_conditions.py still owns the behaviour this module inherited, unchanged: a
fired condition sets `endgame.requested`, records `cause`, builds `final_arc` from the
authored `ending_prompt`, appends a finale act, and costs no LLM call. §7.6's requirement is
that the *effect* does not change, so that file passing is most of this port's gate.

What this file covers is what the port added or made explicit:

  - **`resolve_order = 90` replaced "it's the last block in the function".** v2 applied
    failure conditions after everything else, deliberately, so a failing turn's items,
    standing and progress all land first. That was a comment plus a statement position;
    §6.2 exists to make it declared data, and this asserts the declaration actually holds.
  - **The cadence is the engine's own decision now**, made from state rather than by the
    call site emptying a list before the prompt is built.
  - **The effect handler lives in story_engine**, on purpose - see the module docstring in
    backend/mechanics/failure.py. That seam is worth a test, because "the engine emits an
    effect nobody registered" fails loudly at apply time and silently in review.

Run directly: python3 test/test_triggered_ending.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics
ENGINE = mechanics.failure.ENGINE

EMPTY_DIFF = {
    "subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
    "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
}

CFG = {
    "engine": "triggered_ending",
    "conditions": [
        {"id": "fail_cold", "trigger": "the cold wins", "title": "Windward",
         "ending_prompt": "Close on the cold winning."},
        {"id": "fail_dark", "trigger": "the last light fails", "title": "No Fixed Point",
         "ending_prompt": "Close in the dark."},
    ],
}


def fake_ctx(cfg, ending=False):
    return {"story": {"mechanics": {"failure_conditions": cfg}},
            "state": {"plot": {"endgame": {"requested": ending}}, "pacing": {"turn_count": 4}}}


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


# =====================================================================================
# 1. Pure engine rules - no stubs
# =====================================================================================

# --- the cadence is decided from state, not by the caller ----------------------------
assert ENGINE.observations(CFG, fake_ctx(CFG)) is not None
assert ENGINE.observations(CFG, fake_ctx(CFG, ending=True)) is None, \
    "nothing left to fail into once the story is already ending"
print("OK: the engine decides for itself that an ending story has nothing to ask")

# --- the field carries every live trigger, and nothing else --------------------------
field = ENGINE.observations(CFG, fake_ctx(CFG))[0]
assert field.name == "failure_triggered"
assert "the cold wins" in field.context and "the last light fails" in field.context
assert "Close on the cold winning." not in field.context, \
    "the authored ending prose is for the ending, never for the pass that detects it"
print("OK: live triggers reach the observation pass; the authored ending prose never does")

# --- an id outside the authored set, or any id at all once ending, produces no event --
ctx = fake_ctx(CFG)
assert ENGINE.events(CFG, ctx, {"failure_triggered": "fail_invented"}) == []
assert ENGINE.events(CFG, ctx, {"failure_triggered": None}) == []
assert ENGINE.events(CFG, ctx, {}) == []
assert ENGINE.events(CFG, fake_ctx(CFG, ending=True), {"failure_triggered": "fail_cold"}) == []
assert ENGINE.events(CFG, ctx, {"failure_triggered": "fail_cold"}) == [
    {"type": "failure_triggered", "id": "fail_cold"}]
print("OK: an unknown id, a null, or any id at all once ending produces no event")

# --- resolve builds the arc from the condition's own authored text --------------------
effects = ENGINE.resolve(CFG, ctx, [{"type": "failure_triggered", "id": "fail_dark"}], [])
assert [e.kind for e in effects] == ["failure.trigger"]
assert effects[0].payload["cause"] == "fail_dark"
assert effects[0].payload["final_arc"] == {"title": "No Fixed Point",
                                           "description": "Close in the dark."}
assert effects[0].reason == "condition:fail_dark"
print("OK: the effect carries the condition's authored title and ending_prompt")

# --- a condition with no title falls back rather than producing an untitled act -------
untitled = {"engine": "triggered_ending",
            "conditions": [{"id": "f", "trigger": "t", "ending_prompt": "p"}]}
effect = ENGINE.resolve(untitled, fake_ctx(untitled), [{"type": "failure_triggered", "id": "f"}], [])[0]
assert effect.payload["final_arc"]["title"] == "The Ending"
print("OK: a condition with no authored title still produces a titled finale act")

# --- declaring the engine with no conditions raises rather than going inert -----------
try:
    ENGINE.live({"engine": "triggered_ending"}, fake_ctx({}))
    raise AssertionError("a triggered_ending with no conditions must not silently do nothing")
except ValueError as exc:
    assert "conditions" in str(exc), exc
print("OK: declaring triggered_ending without `conditions` raises rather than going inert")

# --- §6.2: failure resolves last, and that is now declared rather than positional -----
orders = {b_slot: engine.resolve_order
          for (b_slot, _), engine in mechanics.registered_engines().items()}
assert orders["failure_conditions"] == max(orders.values()), orders
assert all(orders["failure_conditions"] > o
           for slot, o in orders.items() if slot != "failure_conditions"), orders
print("OK: triggered_ending resolves after every other engine, by declared resolve_order")

# --- the effect handler is registered, by story_engine rather than by the engine ------
assert "failure.trigger" in mechanics._EFFECT_HANDLERS, \
    "the engine emits failure.trigger; something has to apply it or apply_effects raises"
assert mechanics._EFFECT_HANDLERS["failure.trigger"] is se._apply_failure_effect, \
    "the handler belongs to story_engine, which owns _begin_endgame - see failure.py's docstring"
print("OK: failure.trigger is applied by story_engine, which owns the one endgame path")


# =====================================================================================
# 2. Through a real turn
# =====================================================================================

# --- a failing turn's other effects still land before the ending machinery takes over.
# This is the ordering v2 encoded as "the last block in update_progress_from_turn" and is
# now triggered_ending's resolve_order; if it regressed, the items and standing from the
# turn that killed you would be missing from the save you died in. ---
ctx = se.state_store.load_state("triggeredendingtest", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["protagonist"]["inventory"] = []
with_story(ctx, lambda s: s["mechanics"].__setitem__("failure_conditions", CFG))
se.call_llm_json = CannedResponses([{
    **EMPTY_DIFF,
    "inventory": {"gained": [{"label": "a dead torch", "tags": ["tool"]}], "used": []},
    "social": [{"target": "Mrs. Abbott", "register": "went_behind_their_back",
                "reciprocated": False}],
    "flags_set": {"went_out_alone": {"value": True, "pinned": True}},
    "failure_triggered": "fail_dark",
}])
se.update_progress_from_turn(ctx, "walk out onto the shelf", "narration text")

assert [r["label"] for r in se.mechanics.items.ENGINE.records(ctx)] == ["a dead torch"], \
    "the failing turn's acquisition must still be in the save"
assert ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] == -9
assert ctx["state"]["protagonist"]["flags"]["active"]["went_out_alone"] is True
endgame = ctx["state"]["plot"]["endgame"]
assert endgame["requested"] is True and endgame["cause"] == "fail_dark"
assert endgame["final_arc"]["title"] == "No Fixed Point"
assert se._all_acts(ctx)[-1]["is_finale"] is True
print("OK: a failing turn's items, standing and flags all land before the ending is entered")

# --- the event log records the raw observation, and the field stops being asked -------
events = [e for e in ctx["state"].get("events", []) if e["type"] == "failure_triggered"]
assert events == [{"turn": ctx["state"]["pacing"]["turn_count"], "type": "failure_triggered",
                   "id": "fail_dark"}], events
recorder = RecordingLLM(lambda p: dict(EMPTY_DIFF))
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "keep narrating the ending", "narration text")
assert "FAILURE CONDITIONS" not in recorder.prompts[-1]
assert "failure_triggered" not in recorder.prompts[-1]
print("OK: the log records what was observed, and the field is not asked again while ending")

# --- a second condition firing during the ending cannot overwrite the first -----------
se.call_llm_json = CannedResponses([{**EMPTY_DIFF, "failure_triggered": "fail_cold"}])
se.update_progress_from_turn(ctx, "and now the cold", "narration text")
assert ctx["state"]["plot"]["endgame"]["cause"] == "fail_dark", \
    "whichever ending got there first wins - a second cannot rewrite it"
print("OK: a second condition cannot overwrite an ending already under way")

print("\nALL CHECKS PASSED: test_triggered_ending")
