"""Regression test for CR-03: a revealed memory fragment's content previously never reached
the narrator - the reveal path only ever flipped a frag["revealed"] boolean, with nothing
downstream except a count. build_system_prompt now surfaces revealed content (capped and
ordered by recency, via ctx["state"]["plot"]["revelations_revealed"]); update_progress_from_turn
continues to see only unrevealed triggers, from the authored ctx["story"]["mechanics"]["revelations"]
list.

Run directly: python3 test/test_revealed_memories.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402


def content_present(text, i):
    # word-boundary match so "content 1" doesn't false-positive on "content 10"/"content 19"
    return re.search(rf"content {i}\b", text) is not None


se = load_story_engine()

ctx = se.state_store.load_state("revealedmemtest", se.state_store.DEFAULT_STORY_SLUG)
story_dict = se.state_store.thaw(ctx["story"])
story_dict["mechanics"]["revelations"] = [
    {"id": "frag_1", "trigger": "trigger one", "content": "content one"},
    {"id": "frag_2", "trigger": "trigger two", "content": "content two"},
]
ctx["story"] = se.state_store.freeze(story_dict)

# --- a story with zero fragments produces no REVEALED MEMORIES header at all ---
prompt = se.build_system_prompt(ctx)
assert "REVEALED MEMORIES" not in prompt
print("OK: no revealed fragments -> no REVEALED MEMORIES header")

# --- revealing one via the state-update pass records its turn, and its content (but not the
# still-unrevealed one's) shows up in the narration prompt; the state-update prompt itself
# only ever sees unrevealed triggers, never revealed content ---
ctx["state"]["pacing"]["turn_count"] = 5
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": ["frag_1"],
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "attempt a proof", "narration text")

assert "frag_1" in ctx["state"]["plot"]["revelations_revealed"]
assert ctx["state"]["plot"]["revelations_revealed"]["frag_1"]["turn"] == 5

prompt = se.build_system_prompt(ctx)
assert "content one" in prompt
assert "content two" not in prompt, "unrevealed fragment content must never reach narration"
assert "REVEALED MEMORIES" in prompt
print("OK: revealing a fragment records its turn and surfaces its content, not the unrevealed one's")

# --- the state-update prompt continues to see only unrevealed triggers, never revealed content ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
captured = {}
original_json = se.call_llm_json


def spy(prompt, **kw):
    captured["prompt"] = prompt
    return original_json(prompt, **kw)


se.call_llm_json = spy
se.update_progress_from_turn(ctx, "look around", "narration text")
assert "trigger two" in captured["prompt"]
assert "content one" not in captured["prompt"], "revealed content must never reach the state-update pass"
print("OK: state-update prompt sees only unrevealed triggers, never revealed content")

# --- capped to the most recently revealed MEMORY_FRAGMENT_PROMPT_LIMIT ---
many_ctx = se.state_store.load_state("revealedmemtest2", se.state_store.DEFAULT_STORY_SLUG)
many_story = se.state_store.thaw(many_ctx["story"])
many_story["mechanics"]["revelations"] = [
    {"id": f"frag_{i}", "trigger": "t", "content": f"content {i}"}
    for i in range(1, se.MEMORY_FRAGMENT_PROMPT_LIMIT + 5)
]
many_ctx["story"] = se.state_store.freeze(many_story)
many_ctx["state"]["plot"]["revelations_revealed"] = {
    f"frag_{i}": {"turn": i} for i in range(1, se.MEMORY_FRAGMENT_PROMPT_LIMIT + 5)
}
prompt = se.build_system_prompt(many_ctx)
present = sum(1 for i in range(1, se.MEMORY_FRAGMENT_PROMPT_LIMIT + 5) if content_present(prompt, i))
assert present == se.MEMORY_FRAGMENT_PROMPT_LIMIT, present
last_i = se.MEMORY_FRAGMENT_PROMPT_LIMIT + 4
assert content_present(prompt, last_i), "most recently revealed must survive the cap"
assert not content_present(prompt, 1), "oldest revealed fragment must be dropped past the cap"
print(f"OK: revealed-fragment block capped to {se.MEMORY_FRAGMENT_PROMPT_LIMIT}, most recent kept")

# --- with unrevealed fragments present, the state-update prompt instructs evaluation ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
captured = {}
original_json = se.call_llm_json


def spy(prompt, **kw):
    captured["prompt"] = prompt
    return original_json(prompt, **kw)


se.call_llm_json = spy
se.update_progress_from_turn(ctx, "look around", "narration text")
assert "never force a match" in captured["prompt"]
assert "that the narration satisfies this turn" in captured["prompt"]
print("OK: unrevealed fragments present -> state-update prompt instructs evaluation")

# --- with all fragments already revealed, no evaluation instruction is emitted ---
all_revealed_ctx = se.state_store.load_state("revealedmemtest3", se.state_store.DEFAULT_STORY_SLUG)
all_revealed_story = se.state_store.thaw(all_revealed_ctx["story"])
all_revealed_story["mechanics"]["revelations"] = [
    {"id": "frag_1", "trigger": "trigger one", "content": "content one"},
]
all_revealed_ctx["story"] = se.state_store.freeze(all_revealed_story)
all_revealed_ctx["state"]["plot"]["revelations_revealed"] = {"frag_1": {"turn": 1}}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
captured = {}
original_json = se.call_llm_json
se.call_llm_json = spy
se.update_progress_from_turn(all_revealed_ctx, "look around", "narration text")
assert "never force a match" not in captured["prompt"]
print("OK: all fragments already revealed -> no evaluation instruction emitted")

# --- a story with no mechanics.revelations at all does not raise and emits no instruction ---
no_rev_ctx = se.state_store.load_state("revealedmemtest4", se.state_store.DEFAULT_STORY_SLUG)
no_rev_story = se.state_store.thaw(no_rev_ctx["story"])
no_rev_story["mechanics"]["revelations"] = []
no_rev_ctx["story"] = se.state_store.freeze(no_rev_story)
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
captured = {}
original_json = se.call_llm_json
se.call_llm_json = spy
se.update_progress_from_turn(no_rev_ctx, "look around", "narration text")
assert "never force a match" not in captured["prompt"]
print("OK: no mechanics.revelations -> no raise, no evaluation instruction")

print("\nALL CHECKS PASSED: test_revealed_memories")
