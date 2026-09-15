"""Regression test for 5.2: mechanics.stats.floor/.ceiling replaces the old global
STAT_FLOOR=0 constant with a per-story dial - a story with no mechanics.stats at all falls
back to floor=0/unbounded ceiling (the old default behaviour); one that authors a negative
floor or an explicit ceiling gets both enforced.

Also covers §8.1's priced axes, where the model names an event from the story's own
vocabulary and the engine does the arithmetic. Which of the two shapes a story gets is
decided by whether it authors `costs` - the same declare-to-opt-in the registry uses one
level up - so both directions are asserted here, along with per-axis bounds overriding the
block-level ones and `per_turn` drift ticking on a turn that observed nothing at all.

Run directly: python3 test/test_stat_bounds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


# --- no mechanics.stats at all: the mechanic does not exist (engine v2 declare-to-bind) ---
# This used to assert a fallback to a hardcoded floor of 0. That implicit default was a P-3
# violation - an engine constant deciding a creative question for any story that stayed
# quiet - and SCHEMA_V2_SPEC §3.6 already claimed mechanics.stats had replaced it when it
# had only shadowed it. A story that wants a floor of 0 now declares one; a story that
# declares no engine gets no stat mechanic at all, per P-2.
ctx = se.state_store.load_state("statboundstest", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["protagonist"]["stats"] = {"health": 2}
se.call_llm_json = CannedResponses([
    {"subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
     "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
     "stat_changes": {"health": -10}},
])
se.update_progress_from_turn(ctx, "get hurt badly", "narration text")
assert ctx["state"]["protagonist"]["stats"]["health"] == 2, \
    "an undeclared stats mechanic must be inert - no clamping, and no delta applied"
assert "stat_changes" not in se.build_system_prompt(ctx)
print("OK: with no mechanics.stats.engine the mechanic does not exist at all (P-2)")

# --- an authored negative floor is respected ---
with_story(ctx, lambda s: s.setdefault("mechanics", {}).update(stats={"engine": "bounded_counter", "floor": -10, "ceiling": None}))
ctx["state"]["protagonist"]["stats"] = {"days_remaining": -5}
se.call_llm_json = CannedResponses([
    {"subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
     "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
     "stat_changes": {"days_remaining": -20}},
])
se.update_progress_from_turn(ctx, "time runs out", "narration text")
assert ctx["state"]["protagonist"]["stats"]["days_remaining"] == -10, \
    "should clamp at the authored floor of -10, not the old hardcoded 0"
print("OK: an authored negative floor (mechanics.stats.floor) is respected")

# --- an authored ceiling caps upward growth ---
ctx["state"]["protagonist"]["stats"]["days_remaining"] = -8
with_story(ctx, lambda s: s["mechanics"].update(stats={"engine": "bounded_counter", "floor": -10, "ceiling": 7}))
se.call_llm_json = CannedResponses([
    {"subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
     "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
     "stat_changes": {"days_remaining": 30}},
])
se.update_progress_from_turn(ctx, "a windfall of time", "narration text")
assert ctx["state"]["protagonist"]["stats"]["days_remaining"] == 7, \
    "should clamp at the authored ceiling of 7 - previously unbounded upward"
print("OK: an authored ceiling (mechanics.stats.ceiling) caps upward growth")

# =====================================================================================
# §8.1 - priced axes: the model names what happened, the engine prices it
# =====================================================================================

PRICED = {
    "engine": "bounded_counter", "floor": 0, "ceiling": 20,
    "axes": {
        "warmth": {"costs": {"shelter.made": 3, "exposure.long": -3}},
        "supplies": {"costs": {"forage.good": 3, "meal.eaten": -1}, "floor": -5},
        "days_out": {"per_turn": 1},
    },
}


def priced_ctx(stats):
    ctx = se.state_store.load_state("pricedstats", se.state_store.DEFAULT_STORY_SLUG)
    with_story(ctx, lambda s: s.setdefault("mechanics", {}).update(stats=dict(PRICED)))
    ctx["state"]["protagonist"]["stats"] = dict(stats)
    return ctx


def report(ctx, **fields):
    se.call_llm_json = CannedResponses([{
        "subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
        "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [], **fields,
    }])
    se.update_progress_from_turn(ctx, "do something", "narration text")


# --- the authored vocabulary is what the model is asked for, and never a number ---------
ctx = priced_ctx({"warmth": 5, "supplies": 5, "days_out": 0})
recorder = se.call_llm_json = RecordingLLM(lambda p: {})
se.update_progress_from_turn(ctx, "look", "narration text")
prompt = recorder.prompts[-1]
assert '"stat_events"' in prompt and '"stat_changes"' not in prompt
assert "shelter.made" in prompt and "exposure.long" in prompt and "forage.good" in prompt
print("OK: a story authoring costs is asked for stat_events from its own vocabulary, never "
      "for a delta map")

# --- an event moves every axis its tables price, by the authored magnitude ---------------
ctx = priced_ctx({"warmth": 5, "supplies": 5, "days_out": 0})
report(ctx, stat_events=["shelter.made", "meal.eaten"])
stats = ctx["state"]["protagonist"]["stats"]
assert stats["warmth"] == 8, stats          # +3, authored
assert stats["supplies"] == 4, stats        # -1, authored
print("OK: each event costs exactly what the story priced it at, on every axis it names")

# --- the same event twice costs twice, and projects rather than overwriting --------------
ctx = priced_ctx({"warmth": 5, "supplies": 5, "days_out": 0})
report(ctx, stat_events=["shelter.made", "shelter.made"])
assert ctx["state"]["protagonist"]["stats"]["warmth"] == 11, \
    "two occurrences must price twice - the second effect has to see the first"
print("OK: repeated events accumulate, because resolve prices against a projection")

# --- an event outside the vocabulary is dropped, not guessed at --------------------------
ctx = priced_ctx({"warmth": 5, "supplies": 5, "days_out": 0})
report(ctx, stat_events=["not.a.real.event", 7, None])
assert ctx["state"]["protagonist"]["stats"]["warmth"] == 5
print("OK: an event outside the authored vocabulary moves nothing")

# --- a per-axis floor overrides the block-level one --------------------------------------
ctx = priced_ctx({"warmth": 5, "supplies": 0, "days_out": 0})
report(ctx, stat_events=["meal.eaten", "meal.eaten", "meal.eaten"])
assert ctx["state"]["protagonist"]["stats"]["supplies"] == -3, \
    "supplies authors floor -5, which must win over the block-level 0"
report(ctx, stat_events=["exposure.long", "exposure.long", "exposure.long"])
assert ctx["state"]["protagonist"]["stats"]["warmth"] == 0, \
    "warmth authors no floor, so the block-level 0 applies"
print("OK: a per-axis floor overrides the block-level one; an axis without one inherits it")

# --- per_turn drift ticks ONCE per turn, including one that observed nothing -------------
# The count is the assertion, not just the movement. update_state_after_turn used to run the
# engine pipeline a second time after update_progress_from_turn had already run it - a phase 1
# placeholder that was provably a no-op while every engine needed an observation to produce an
# effect. Drift is the first thing that does not, so it ticked twice a turn and nothing caught
# it until a real save was played forward.
ctx = priced_ctx({"warmth": 5, "supplies": 5, "days_out": 0})
report(ctx)
assert ctx["state"]["protagonist"]["stats"]["days_out"] == 1, \
    "drift must arrive whether or not the model reported anything (§7.1), and exactly once"
report(ctx, stat_events=["shelter.made"])
assert ctx["state"]["protagonist"]["stats"]["days_out"] == 2, \
    "a turn that DID observe something must still drift exactly once"
report(ctx, stat_events=["shelter.made", "exposure.long"])
assert ctx["state"]["protagonist"]["stats"]["days_out"] == 3, \
    "drift is per turn, not per observed event"
print("OK: per_turn drift ticks exactly once a turn - with no events, with one, and with two")

# --- a story with stats but no costs keeps the delta map ---------------------------------
ctx = se.state_store.load_state("unpricedstats", se.state_store.DEFAULT_STORY_SLUG)
with_story(ctx, lambda s: s.setdefault("mechanics", {}).update(
    stats={"engine": "bounded_counter", "floor": 0, "ceiling": None}))
ctx["state"]["protagonist"]["stats"] = {"health": 5}
recorder = se.call_llm_json = RecordingLLM(lambda p: {})
se.update_progress_from_turn(ctx, "look", "narration text")
assert '"stat_changes"' in recorder.prompts[-1] and '"stat_events"' not in recorder.prompts[-1]
report(ctx, stat_changes={"health": -2})
assert ctx["state"]["protagonist"]["stats"]["health"] == 3
print("OK: authoring no costs keeps the v2 delta map, and it still applies")

print("\nALL CHECKS PASSED: test_stat_bounds")
