"""`mechanics.flags.declared`: the state-update pass is told what each unset declared flag means.

Before this the pass was never shown a declared flag's `detect` text, so a flag was set only if the
model happened to use its exact id - and every thread, act or ending gated on a flag waited on
that. Covers: the prompt lists only declared flags that have an event description and are not yet
set (active OR archive, the reading conditions use), tells the model to evaluate them, and is
silent when there is nothing to ask; a flag set through `flags_set` opens the condition that names
it; a declared flag reported `false` is never set (it would read as set); undeclared flags stay
free-form; and the list is bounded (it shrinks as flags are set).

Run directly: python3 test/test_declared_flags.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
import conditions  # noqa: E402

EMPTY = {"subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
         "inventory": {"gained": [], "used": []}, "new_characters": [],
         "scene_update": {"location": "", "summary": "unchanged", "present_npcs": []}}

STORY = {
    "schema_version": 3, "story_version": "2024-01-01.1", "meta": {"title": "T"},
    "narration": {"pov": "second-person"},
    "world": {"setting_summary": "s", "rules": ["r"]},
    "protagonist": {"default_name": "Traveller"},
    "mechanics": {"flags": {"declared": [
        {"id": "lark_departed", "detect": "Lark has left the story for good"},
        {"id": "ship_seized", "detect": "The ship was impounded, requisitioned or taken as collateral"},
        {"id": "no_detect_text"},
        {"id": "blank_detect", "detect": "   "},
    ]}},
    "plot": {"main_thread": {"title": "M", "description": "d", "acts": [{"act_number": 1, "title": "A", "description": "d"}]},
             "initial_scene": {"location": "start", "summary": "s"},
             "opening_scene": {"narration_before_name": "b", "narration_after_name": "a"}},
}


def new_ctx(story=STORY):
    return {"story": se.state_store.freeze(story), "state": se.state_store.new_save_state(story, "x")}


def run_turn(ctx, diff=None):
    recorder = RecordingLLM(lambda p: {**copy.deepcopy(EMPTY), **(diff or {})})
    original = se.call_llm_json
    se.call_llm_json = recorder
    try:
        se.update_progress_from_turn(ctx, "I act", "The scene unfolds.")
    finally:
        se.call_llm_json = original
    return recorder.prompts[-1]


# --- what is asked about ------------------------------------------------------------------------
ctx = new_ctx()
assert se._pending_declared_flags(ctx) == [("lark_departed", "Lark has left the story for good"),
                                            ("ship_seized", "The ship was impounded, requisitioned or taken as collateral")]
prompt = run_turn(ctx)
assert "DECLARED FLAGS not yet set (id: event): lark_departed: Lark has left the story for good; " \
       "ship_seized: The ship was impounded" in prompt, prompt
assert "no_detect_text" not in prompt and "blank_detect" not in prompt, "a flag with no event text cannot be asked about"
assert "using exactly that id" in prompt and "Never force a match" in prompt
assert "not by whether the narration reuses" in prompt, "evaluate by events, not wording (the revelations lesson)"
print("OK: the prompt lists each unset declared flag with its event text and says to evaluate it")

# --- a flag the model reports opens the condition that names it -----------------------------------
cond = {"flag": "lark_departed"}
assert not conditions.satisfied(cond, ctx, conditions.CLOSED), "unset before the turn"
run_turn(ctx, {"flags_set": {"lark_departed": {"value": True, "pinned": False}}})
assert conditions.satisfied(cond, ctx, conditions.CLOSED), "set by the turn"
assert ctx["state"]["protagonist"]["flags"]["meta"]["lark_departed"]["turn_set"] == ctx["state"]["pacing"]["turn_count"]
print("OK: a declared flag reported through flags_set is set, and the condition naming it holds")

# --- and stops being asked about; the list shrinks, in active or archive ---------------------------
assert [f for f, _ in se._pending_declared_flags(ctx)] == ["ship_seized"]
prompt = run_turn(ctx)
assert "lark_departed: Lark has left" not in prompt.split("CURRENT FLAGS")[0], "a set flag is not asked about again"
ctx["state"]["protagonist"]["flags"]["archive"]["ship_seized"] = True
assert se._pending_declared_flags(ctx) == [], "a flag retired to the archive still counts as set"
prompt = run_turn(ctx)
assert "DECLARED FLAGS" not in prompt and "using exactly that id" not in prompt, "nothing pending, nothing said"
print("OK: set flags (active or archived) drop out of the list; with none left the prompt says nothing")

# --- precision: a false is never a set ---------------------------------------------------------------
ctx = new_ctx()
run_turn(ctx, {"flags_set": {"lark_departed": {"value": False}, "ship_seized": False}})
assert not conditions.satisfied({"flag": "lark_departed"}, ctx, conditions.CLOSED) and not conditions.satisfied({"flag": "ship_seized"}, ctx, conditions.CLOSED)
assert "lark_departed" not in ctx["state"]["protagonist"]["flags"]["active"]
print("OK: a declared flag reported false is dropped, not stored (a stored false would read as set)")

# --- undeclared flags stay free-form ---------------------------------------------------------------------
ctx = new_ctx()
run_turn(ctx, {"flags_set": {"met_the_clerk": {"value": True, "pinned": True}, "cold_snap": {"value": False}}})
active = ctx["state"]["protagonist"]["flags"]["active"]
assert active == {"met_the_clerk": True, "cold_snap": False}, active
print("OK: undeclared flags behave exactly as before, false included")

# --- P-2: no flags block, no section ----------------------------------------------------------------------
bare = copy.deepcopy(STORY)
del bare["mechanics"]
ctx = new_ctx(bare)
assert se._pending_declared_flags(ctx) == [] and se._declared_flags(ctx) == {}
assert "DECLARED FLAGS" not in run_turn(ctx)
print("OK: a story that declares no flags gets no section and no instruction")

print("\nALL CHECKS PASSED: test_declared_flags")
