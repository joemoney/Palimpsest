"""End-to-end regression test for the continuous subplot/act/endgame machinery
added to story_engine.py: a subplot completing should auto-generate a
replacement, an act should be able to advance past the old fixed 3-act
ceiling, and requesting an ending should lock out further auto-generation.

Run directly: python3 test/test_subplot_act_endgame.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine, CannedResponses  # noqa: E402

se = load_story_engine()

responses = CannedResponses([
    # 1) update_progress_from_turn: push subplot_001 to completion
    {"subplot_progress": {"subplot_001": 100}, "flags_set": {"learned_basic_computation": True},
     "memory_fragments_revealed": ["frag_0001"], "entity_interaction": False},
    # 2) generate_new_subplot (replacement for subplot_001)
    {"title": "Test New Subplot", "description": "A freshly invented thread.",
     "priority": "high", "ties_to_main_plot": "ties in somehow"},
    # 3) check_and_advance_act: judge ready, hand back Act 2
    {"ready": True, "reason": "Enough has resolved.", "next_act_title": "Act 2 Test",
     "next_act_description": "The investigation deepens."},
    # 4) handle_end_story_request: final arc
    {"title": "The Reckoning (test)", "description": "Everything converges."},
])
se.call_llm_json = responses

state = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
assert state["plot"]["subplots"]["subplot_001"]["progress"] == 0

# Not every story uses the memory-fragment mechanic (it's optional per-story flavor, not a
# required feature) - seed one directly so this test can exercise the reveal path regardless
# of whether DEFAULT_STORY_SLUG's own template happens to define any.
state["player"]["origin"]["memory_fragments"] = [
    {"id": "frag_0001", "trigger": "test trigger", "content": "test content", "revealed": False},
]

# 1+2: one turn worth of progress -> completes subplot_001 -> auto-generates a replacement.
# The next id follows the template's own existing subplot numbering, whatever it is - not
# hardcoded, since this test should hold regardless of which story DEFAULT_STORY_SLUG points to.
existing_numbers = [int(sid.rsplit("_", 1)[-1]) for sid in state["plot"]["subplots"]]
expected_new_id = f"subplot_{max(existing_numbers) + 1:03d}"

se.update_progress_from_turn(state, "do the thing", "narration text")
status = se.check_subplot_status(state)
assert status["completed"] == ["subplot_001"], status
assert state["plot"]["pacing"]["subplots_completed_this_act"] == 1
for _ in status["completed"]:
    se.generate_new_subplot(state)
subplot_ids = list(state["plot"]["subplots"].keys())
assert expected_new_id in subplot_ids, subplot_ids
assert state["plot"]["subplots"][expected_new_id]["title"] == "Test New Subplot"
assert state["player"]["flags_active"]["learned_basic_computation"] is True
assert any(f["id"] == "frag_0001" and f["revealed"] for f in state["player"]["origin"]["memory_fragments"])
print("OK: subplot completion + auto-regeneration + progress diff applied")

# 3: act advancement judged ready -> Act 2 generated, endless (no ceiling)
new_act_num = se.check_and_advance_act(state)
assert new_act_num == 2, new_act_num
assert state["plot"]["main_thread"]["current_act"] == 2
assert state["plot"]["main_thread"]["acts"][1]["title"] == "Act 2 Test"
assert state["plot"]["main_thread"]["acts"][0]["completed"] is True
assert state["plot"]["pacing"]["subplots_completed_this_act"] == 0
assert len(state["plot"]["main_thread"]["act_history"]) == 1
print("OK: act auto-advanced past the old fixed Act-3 ceiling")

# 4: player asks to end the story -> finale act appended, endgame locked in
final_arc = se.handle_end_story_request(state)
assert final_arc["title"] == "The Reckoning (test)"
assert state["plot"]["endgame"]["requested"] is True
assert state["plot"]["main_thread"]["acts"][-1]["is_finale"] is True
assert state["plot"]["main_thread"]["current_act"] == 3
print("OK: endgame request generates a locked-in finale act")

# after endgame: no more auto-generation of subplots or acts
assert se.generate_new_subplot(state) is None
state["plot"]["pacing"]["subplots_completed_this_act"] = 5  # would normally trigger a check
assert se.check_and_advance_act(state) is None
assert responses.remaining() == 0, f"unused canned responses left over: {responses.remaining()}"
print("OK: endgame disables further subplot/act auto-generation")

print("\nALL CHECKS PASSED: test_subplot_act_endgame")
