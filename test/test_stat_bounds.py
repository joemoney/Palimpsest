"""Regression test for 5.2: mechanics.stats.floor/.ceiling replaces the old global
STAT_FLOOR=0 constant with a per-story dial - a story with no mechanics.stats at all falls
back to floor=0/unbounded ceiling (the old default behaviour); one that authors a negative
floor or an explicit ceiling gets both enforced.

Run directly: python3 test/test_stat_bounds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402

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
    {"subplot_progress": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
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
    {"subplot_progress": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
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
    {"subplot_progress": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
     "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
     "stat_changes": {"days_remaining": 30}},
])
se.update_progress_from_turn(ctx, "a windfall of time", "narration text")
assert ctx["state"]["protagonist"]["stats"]["days_remaining"] == 7, \
    "should clamp at the authored ceiling of 7 - previously unbounded upward"
print("OK: an authored ceiling (mechanics.stats.ceiling) caps upward growth")

print("\nALL CHECKS PASSED: test_stat_bounds")
