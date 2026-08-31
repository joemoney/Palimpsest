"""Regression test for multi-act subplots: a "span" field lets some subplots take
meaningfully longer to resolve (a higher completion_threshold, see
story_engine.insert_subplot), and check_and_advance_act no longer requires a subplot to
have completed before an act can even be considered for advancement - it also fires on a
periodic turn-count cadence, mirroring generate_pacing_nudge's existing
pacing_nudge_frequency/turns_since_last_pacing_nudge pattern, so a deliberately long-running
subplot no longer stalls the whole story from ever moving to the next act.

Run directly: python3 test/test_multi_act_subplots.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine, CannedResponses, RecordingLLM  # noqa: E402

se = load_story_engine()

state = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)

# --- insert_subplot: span decides completion_threshold ---
single_id = se.insert_subplot(state, "Short Errand", "A quick thing.")
assert state["plot"]["subplots"][single_id]["completion_threshold"] == 100
assert state["plot"]["subplots"][single_id]["span"] == "single_act"

multi_id = se.insert_subplot(state, "Long Con", "A slow-burn thread.", span="multi_act")
assert state["plot"]["subplots"][multi_id]["completion_threshold"] == se.MULTI_ACT_SUBPLOT_THRESHOLD
assert state["plot"]["subplots"][multi_id]["span"] == "multi_act"
print("OK: insert_subplot ties completion_threshold to span "
      f"(single_act=100, multi_act={se.MULTI_ACT_SUBPLOT_THRESHOLD})")

# --- generate_new_subplot: respects a returned span, defaults to single_act if omitted/bad ---
state["plot"]["subplots"] = {}
state["plot"]["pacing"]["max_parallel_subplots"] = 5

se.call_llm_json = CannedResponses([
    {"title": "Epic Thread", "description": "d", "priority": "high",
     "ties_to_main_plot": "t", "span": "multi_act"},
])
new_id = se.generate_new_subplot(state)
assert state["plot"]["subplots"][new_id]["span"] == "multi_act"
assert state["plot"]["subplots"][new_id]["completion_threshold"] == se.MULTI_ACT_SUBPLOT_THRESHOLD
print("OK: generate_new_subplot honors an LLM-returned multi_act span")

se.call_llm_json = CannedResponses([
    {"title": "Normal Thread", "description": "d", "priority": "medium", "ties_to_main_plot": "t"},
])
new_id2 = se.generate_new_subplot(state)
assert state["plot"]["subplots"][new_id2]["span"] == "single_act"
assert state["plot"]["subplots"][new_id2]["completion_threshold"] == 100
print("OK: generate_new_subplot defaults to single_act when the LLM omits span")

se.call_llm_json = CannedResponses([
    {"title": "Bad Span Thread", "description": "d", "priority": "low",
     "ties_to_main_plot": "t", "span": "eventually"},
])
new_id3 = se.generate_new_subplot(state)
assert state["plot"]["subplots"][new_id3]["span"] == "single_act"
print("OK: generate_new_subplot falls back to single_act on an unrecognized span value")

# --- check_and_advance_act: no longer hard-gated on a completed subplot ---
state = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
plot = state["plot"]
plot["pacing"]["subplots_completed_this_act"] = 0
plot["pacing"]["act_check_frequency"] = 5
plot["pacing"]["turns_since_last_act_check"] = 4  # one short of the threshold

recorder = RecordingLLM(lambda prompt: {"ready": False, "reason": "not yet"})
se.call_llm_json = recorder

assert se.check_and_advance_act(state) is None
assert recorder.prompts == [], "expected no LLM call before the periodic threshold is reached"
print("OK: check_and_advance_act stays a no-op below act_check_frequency with zero completions")

# One more turn's worth of the counter (as update_state_after_turn would apply) crosses the
# threshold - the check should now actually run, even though no subplot has completed.
plot["pacing"]["turns_since_last_act_check"] = 5
# Mark one subplot as an ongoing multi-act thread so we can also confirm the prompt mentions it.
first_sid = next(iter(plot["subplots"]))
plot["subplots"][first_sid]["span"] = "multi_act"
plot["subplots"][first_sid]["status"] = "active"
plot["subplots"][first_sid]["active"] = True

result = se.check_and_advance_act(state)
assert result is None  # director said not ready, so no new act - but it WAS asked
assert len(recorder.prompts) == 1, "expected exactly one LLM call once the periodic threshold was reached"
assert plot["subplots"][first_sid]["title"] in recorder.prompts[0]
assert "ONGOING MULTI-ACT SUBPLOTS" in recorder.prompts[0]
assert plot["pacing"]["turns_since_last_act_check"] == 0, "counter should reset once the check runs"
print("OK: check_and_advance_act fires on the periodic cadence alone, mentions the ongoing "
      "multi-act subplot, and resets its counter after running")

# --- the original fast path (a subplot actually completed) still works unchanged ---
recorder.prompts.clear()
plot["pacing"]["subplots_completed_this_act"] = 1
plot["pacing"]["turns_since_last_act_check"] = 0  # nowhere near due on its own
plot["completed_subplots"] = [first_sid]
result2 = se.check_and_advance_act(state)
assert len(recorder.prompts) == 1, "a completed subplot should still trigger the check immediately"
print("OK: a subplot completing this act still triggers the check right away (fast path preserved)")

print("\nALL CHECKS PASSED: test_multi_act_subplots")
