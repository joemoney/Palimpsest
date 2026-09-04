"""Regression test for prompt-size bounding: simulates a long-running game (30
completed subplots) and inspects the raw prompt text sent to the LLM to confirm
generate_new_subplot() and check_and_advance_act() only feed in a bounded window
instead of the whole game's history.

Run directly: python3 test/test_context_bounding.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine, RecordingLLM  # noqa: E402

se = load_story_engine()


def title_present(text, i):
    # word-boundary match so "Old Subplot 1" doesn't false-positive on "Old Subplot 10"
    return re.search(rf"Old Subplot {i}\b", text) is not None


ctx = se.state_store.load_state("boundingtest", se.state_store.DEFAULT_STORY_SLUG)
plot_state = ctx["state"]["plot"]

# Simulate 30 completed subplots accumulated over a long game (only 3 completed
# "this act" per the pacing counter - the rest belong to earlier acts). All fully
# self-contained (no template counterpart) since they were all invented during play.
plot_state["subplots"] = {}
plot_state["completed_subplots"] = []
for i in range(1, 31):
    sid = f"subplot_{i:03d}"
    plot_state["subplots"][sid] = {
        "title": f"Old Subplot {i}", "description": "d", "priority": "medium",
        "status": "completed", "progress": 100, "completion_threshold": 100,
        "ties_to_main_plot": "", "active": False, "span": "single_act",
    }
    plot_state["completed_subplots"].append(sid)

# live_count is 0 (everything above is completed), well under whatever max_parallel_subplots
# the template authors - no need to override that authored/frozen value.
ctx["state"]["pacing"]["subplots_completed_this_act"] = 3  # only the last 3 belong to current act


def respond(prompt):
    if "Invent a new subplot" in prompt:
        return {"title": "New One", "description": "d", "priority": "medium", "ties_to_main_plot": "t"}
    return {"ready": False, "reason": "not yet"}


recorder = RecordingLLM(respond)
se.call_llm_json = recorder

# --- generate_new_subplot: dedup list must not include all 30 completed titles ---
se.generate_new_subplot(ctx)
gen_prompt = recorder.prompts[-1]
old_titles_present = sum(1 for i in range(1, 31) if title_present(gen_prompt, i))
assert old_titles_present <= se.SUBPLOT_TITLE_HISTORY_LIMIT, \
    f"expected at most {se.SUBPLOT_TITLE_HISTORY_LIMIT} old titles in prompt, found {old_titles_present}"
assert old_titles_present > 0, "expected at least the most recent old titles to still be present"
assert title_present(gen_prompt, 30)  # most recent kept
assert not title_present(gen_prompt, 1)  # oldest dropped
print(f"OK: generate_new_subplot prompt bounded to {old_titles_present} recent titles "
      f"(cap={se.SUBPLOT_TITLE_HISTORY_LIMIT}), oldest ones dropped")

# --- check_and_advance_act: completed_titles must be scoped to current act only ---
recorder.prompts.clear()
se.check_and_advance_act(ctx)
act_prompt = recorder.prompts[-1]
old_titles_present = sum(1 for i in range(1, 31) if title_present(act_prompt, i))
assert old_titles_present == 3, f"expected exactly 3 (this act's), found {old_titles_present}"
assert title_present(act_prompt, 28) and title_present(act_prompt, 29) and title_present(act_prompt, 30)
assert not title_present(act_prompt, 27)
print("OK: check_and_advance_act prompt scoped to just this act's 3 completions, not all 30")

print("\nALL CHECKS PASSED: test_context_bounding")
