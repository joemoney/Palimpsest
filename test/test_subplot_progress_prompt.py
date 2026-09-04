"""Regression test for CR-08: the state-update prompt used to send active subplots as bare
{id: title}, giving the scoring model no sense of where a subplot currently stood - it
couldn't tell "this beat should finish the thread" from "this nudges it." The prompt now
includes each active subplot's description, current progress, and completion_threshold.

Run directly: python3 test/test_subplot_progress_prompt.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()

ctx = se.state_store.load_state("subplotprogresstest", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["plot"]["subplots"]["subplot_001"]["progress"] = 40

recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
})
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "chat with the innkeeper", "narration text")
prompt = recorder.prompts[-1]

assert "subplot_001: Settling In - Get to know the Harborlight Inn" in prompt, prompt
assert "[40/100]" in prompt, prompt
print("OK: active subplot's description, progress, and threshold appear in the state-update prompt")

# --- clamping behaviour is unchanged: a delta is added to current progress, clamped to
# [0, completion_threshold] ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {"subplot_001": 90}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "make real progress", "narration text")
assert se._subplot_view(ctx, "subplot_001")["progress"] == 100, \
    "40 + 90 should clamp at completion_threshold (100), not overshoot"
print("OK: subplot progress delta still clamps to [0, completion_threshold]")

print("\nALL CHECKS PASSED: test_subplot_progress_prompt")
