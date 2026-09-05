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

# --- _enforce_word_cap: under-cap input is returned unchanged ---
short_text = "one two three four five."
assert se._enforce_word_cap(short_text, 10) == short_text
print("OK: _enforce_word_cap leaves under-cap text unchanged")

# --- _enforce_word_cap: over-cap input comes back at or under the cap ---
over_cap = " ".join(f"word{i}." for i in range(20))
result = se._enforce_word_cap(over_cap, 10)
assert len(result.split()) <= 10
print("OK: _enforce_word_cap trims over-cap text to at or under the cap")

# --- _enforce_word_cap: trims back to end on sentence punctuation ---
mid_sentence = "First sentence here. Second sentence continues without stopping and just keeps going"
result = se._enforce_word_cap(mid_sentence, 6)
assert result.endswith((".", "!", "?")), result
assert result == "First sentence here."
print("OK: _enforce_word_cap trims back to a sentence boundary")

# --- _enforce_word_cap: no sentence punctuation at all still returns at or under cap ---
no_punct = " ".join(f"word{i}" for i in range(20))
result = se._enforce_word_cap(no_punct, 10)
assert len(result.split()) <= 10
print("OK: _enforce_word_cap falls back to a hard cut when there's no sentence punctuation")

# --- end-to-end: rollover clamps the saved compressed_summary to the cap ---
e2e_ctx = se.state_store.load_state("boundingtest2", se.state_store.DEFAULT_STORY_SLUG)
e2e_ctx["state"]["history"]["compressed_summary"] = ""
se.call_llm_json = lambda prompt, **kw: {}
over_cap_summary = " ".join(f"word{i}." for i in range(se.SUMMARY_MAX_WORDS + 500))
se.call_llm = lambda prompt, **kw: over_cap_summary
for _ in range(se.RECENT_TURN_LIMIT + se.ROLLOVER_BATCH_TURNS):
    se.update_state_after_turn(e2e_ctx, "do something", "narration text")
# Non-empty first: with batched rollover, too few turns means no rollover fires at all and
# the cap assertion below would pass trivially against an empty string.
assert e2e_ctx["state"]["history"]["compressed_summary"], "expected a rollover to have fired"
assert len(e2e_ctx["state"]["history"]["compressed_summary"].split()) <= se.SUMMARY_MAX_WORDS
print("OK: end-to-end rollover keeps compressed_summary within SUMMARY_MAX_WORDS")

# --- rollover fires once per batch, not once per turn ---
# The bug this guards: the trigger used to be `> RECENT_TURN_LIMIT`, and since each turn
# appends exactly one and the trim put the list straight back on the boundary, every turn
# past the tenth paid for a full summary call and re-compressed the summary again.
batch_ctx = se.state_store.load_state("boundingtest3", se.state_store.DEFAULT_STORY_SLUG)
batch_ctx["state"]["history"]["compressed_summary"] = ""
rollovers = []
se.call_llm_json = lambda prompt, **kw: {}
se.call_llm = lambda prompt, **kw: (rollovers.append(1), "a summary.")[1]
turns = 3 * se.ROLLOVER_BATCH_TURNS + 5  # deliberately off a batch boundary, so the
# assertions below see storage genuinely running ahead of the prompt window mid-batch
for _ in range(turns):
    se.update_state_after_turn(batch_ctx, "do something", "narration text")

expected = (turns - se.RECENT_TURN_LIMIT) // se.ROLLOVER_BATCH_TURNS
assert len(rollovers) == expected, f"expected {expected} rollovers over {turns} turns, got {len(rollovers)}"
assert len(rollovers) < turns, "rollover is still firing every turn"
print(f"OK: {turns} turns triggered {len(rollovers)} rollovers, not one per turn")

# --- the prompt window is unchanged by the deeper stored list ---
stored = len(batch_ctx["state"]["history"]["recent_turns"])
assert stored > se.RECENT_TURN_LIMIT, "expected recent_turns to run ahead of the prompt window"
assert stored < se.RECENT_TURN_LIMIT + se.ROLLOVER_BATCH_TURNS
prompt = se.build_system_prompt(batch_ctx)
assert prompt.count("narration text") <= se.RECENT_TURN_LIMIT, (
    "build_system_prompt must still only see RECENT_TURN_LIMIT turns, however deep storage runs"
)
print(f"OK: {stored} turns stored, but the narration prompt still sees at most "
      f"{se.RECENT_TURN_LIMIT}")

# --- nothing is lost: every rolled turn lands in full_transcript ---
archived = len(batch_ctx["state"]["history"]["full_transcript"])
assert archived + stored == turns, (
    f"turn accounting: {archived} archived + {stored} recent != {turns} played"
)
print("OK: batching loses no turns - full_transcript plus recent_turns accounts for all of them")

print("\nALL CHECKS PASSED: test_context_bounding")
