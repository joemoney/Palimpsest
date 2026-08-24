"""Regression test for story_engine.regenerate_last_turn(): re-rolling the latest scene
should restore state to exactly before that turn (undoing its subplot progress, flags, and
pacing counter) and then re-run the same player action through a fresh LLM call - not replay
or re-detect anything about the original action itself.

Run directly: python3 test/test_regenerate.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()

state_holder = {"state": se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)}
se.state_store.load_state = lambda *a, **k: state_holder["state"]
se.state_store.save_state = lambda s, *a, **k: state_holder.update(state=s)

# --- regenerating with no prior turn (e.g. right after the opening) is a safe no-op ---
assert se.regenerate_last_turn() is False
print("OK: regenerate_last_turn() with nothing pending is a no-op that returns False")

# --- take a real turn: narrates, and completes subplot_001 for a visible state change ---
se.call_llm = CannedResponses(["First narration.\n\nOPTIONS:\n1. a || a\n2. b || b\n3. c || c"])
se.call_llm_json = CannedResponses([
    {"subplot_progress": {"subplot_001": 100}, "flags_set": {"test_flag": {"value": True, "pinned": False}},
     "memory_fragments_revealed": [], "entity_interaction": False},
    # subplot_001 completing triggers a replacement via generate_new_subplot
    {"title": "Replacement Subplot", "description": "d", "priority": "medium", "ties_to_main_plot": "t"},
    # ...and also a check_and_advance_act pacing checkpoint, since a subplot completed this act
    {"ready": False, "reason": "not yet"},
])
se.take_turn("do the first thing")

state = state_holder["state"]
turn_count_after_first = state["plot"]["pacing"]["turn_count"]
recent_turns_len_after_first = len(state["history_log"]["recent_turns"])
assert turn_count_after_first == 1
assert state["plot"]["subplots"]["subplot_001"]["progress"] == 100
assert state["player"]["flags_active"]["test_flag"] is True
assert "First narration." in state["history_log"]["recent_turns"][-1]
pending = state["history_log"]["pending_regenerate"]
assert pending["player_action"] == "do the first thing"
print("OK: take_turn() applies state changes and stashes a pre-turn snapshot")

# --- regenerate: state should roll back to pre-turn, then re-apply based on the NEW response ---
se.call_llm = CannedResponses(["Second narration (regenerated).\n\nOPTIONS:\n1. x || x\n2. y || y\n3. z || z"])
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False},
])
result = se.regenerate_last_turn()
assert result is False

state = state_holder["state"]
assert state["plot"]["pacing"]["turn_count"] == turn_count_after_first, \
    "turn_count should net out the same (rolled back, then re-incremented), not double-counted"
assert len(state["history_log"]["recent_turns"]) == recent_turns_len_after_first, \
    "the regenerated turn should replace the last entry, not add a second one"
assert "Second narration (regenerated)." in state["history_log"]["recent_turns"][-1]
assert "First narration." not in state["history_log"]["recent_turns"][-1]
assert state["plot"]["subplots"]["subplot_001"]["progress"] == 0, \
    "subplot progress from the discarded original turn should be rolled back, not kept"
assert "test_flag" not in state["player"]["flags_active"], \
    "flags set by the discarded original turn should be rolled back, not kept"
print("OK: regenerate_last_turn() restores pre-turn state, then applies the fresh response")

# --- regenerating again re-rolls from the SAME original pre-turn point, not the last reroll ---
recorder = RecordingLLM(lambda prompt: "Third narration.\n\nOPTIONS:\n1. p || p\n2. q || q\n3. r || r")
se.call_llm = recorder
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False},
])
se.regenerate_last_turn()
state = state_holder["state"]
assert state["plot"]["pacing"]["turn_count"] == turn_count_after_first
assert len(state["history_log"]["recent_turns"]) == recent_turns_len_after_first
assert "do the first thing" in recorder.prompts[-1], \
    "regenerating should always replay the ORIGINAL player action, not drift"
print("OK: regenerating repeatedly keeps re-rolling from the same original pre-turn point")

print("\nALL CHECKS PASSED: test_regenerate")
