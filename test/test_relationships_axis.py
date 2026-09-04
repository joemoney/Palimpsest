"""Regression test for 5.3: mechanics.relationships.axis replaces the hardcoded "-100
hostile to +100 devoted" / "trust and warmth built" instruction text in both prompts, and
an absent mechanics.relationships block means the story tracks no relationship scores at
all - the field vanishes from the state-update schema, the CURRENT RELATIONSHIPS line, and
the narration PLAYER line, while character discovery (new_characters) keeps working
independently of whether scores are tracked.

Run directly: python3 test/test_relationships_axis.py
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


# --- both templates author custom axis labels, interpolated into the state-update prompt ---
ctx = se.state_store.load_state("relaxistest", se.state_store.DEFAULT_STORY_SLUG)
recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
})
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "look around", "narration text")
prompt = recorder.prompts[-1]
assert "-100 hostile to +100 devoted" in prompt
assert "trust and warmth built" in prompt
print("OK: mechanics.relationships.axis interpolated into the state-update prompt")

# --- a custom axis (e.g. a regency-style disregard/devotion scale) renders correctly ---
with_story(ctx, lambda s: s["mechanics"]["relationships"].update(
    axis={"negative": "disregard", "positive": "devotion", "description": "esteem earned"}
))
recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
})
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "look around", "narration text")
prompt = recorder.prompts[-1]
assert "-100 disregard to +100 devotion" in prompt
assert "esteem earned" in prompt
print("OK: a custom relationship axis renders correctly in the state-update prompt")

# --- and in the narration prompt's PLAYER line ---
ctx["state"]["characters"] = {"Someone": {"relationship": 5, "first_seen_turn": 0, "introduced": True}}
narration_prompt = se.build_system_prompt(ctx)
assert "Relationships: {'Someone': 5}" in narration_prompt or 'Relationships: {"Someone": 5}' in narration_prompt
print("OK: relationship scores render in the narration prompt's PLAYER line")

# --- absent mechanics.relationships: no schema field, no CURRENT RELATIONSHIPS line, no
# PLAYER-line Relationships, and a stray relationship_changes key from the model is ignored
# rather than starting to track scores anyway ---
no_rel_ctx = se.state_store.load_state("relaxistest2", se.state_store.DEFAULT_STORY_SLUG)
with_story(no_rel_ctx, lambda s: s["mechanics"].pop("relationships", None))
recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {"Sneaky": 99}, "new_characters": [],
})
se.call_llm_json = recorder
se.update_progress_from_turn(no_rel_ctx, "look around", "narration text")
prompt = recorder.prompts[-1]
assert "relationship_changes" not in prompt
assert "CURRENT RELATIONSHIPS" not in prompt
assert "Sneaky" not in no_rel_ctx["state"]["characters"], \
    "a stray relationship_changes key should be ignored when the story opted out of score tracking"

narration_prompt = se.build_system_prompt(no_rel_ctx)
assert "Relationships:" not in narration_prompt
print("OK: absent mechanics.relationships removes the field/line from both prompts and "
      "ignores a stray relationship_changes key")

# --- character discovery (new_characters) still works without score tracking ---
recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {},
    "new_characters": [{"name": "Odette", "description": "a fence", "hook": "runs a stall"}],
})
se.call_llm_json = recorder
se.update_progress_from_turn(no_rel_ctx, "meet Odette", "narration text")
assert "Odette" in no_rel_ctx["state"]["characters"]
assert no_rel_ctx["state"]["characters"]["Odette"]["description"] == "a fence"
print("OK: character discovery (new_characters) works independently of score tracking")

# --- mechanics.relationships.limit overrides the global RELATIONSHIPS_LIMIT default ---
limited_ctx = se.state_store.load_state("relaxistest3", se.state_store.DEFAULT_STORY_SLUG)
with_story(limited_ctx, lambda s: s["mechanics"]["relationships"].update(limit=2))
limited_ctx["state"]["characters"] = {
    "A": {"relationship": 1, "first_seen_turn": 0, "introduced": False},
    "B": {"relationship": 2, "first_seen_turn": 0, "introduced": False},
}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {"C": 50}, "new_characters": []},
])
se.update_progress_from_turn(limited_ctx, "meet someone new", "narration text")
assert len(limited_ctx["state"]["characters"]) == 2, limited_ctx["state"]["characters"]
assert "A" not in limited_ctx["state"]["characters"], "closest-to-neutral should be evicted first"
assert "C" in limited_ctx["state"]["characters"]
print("OK: mechanics.relationships.limit overrides the global RELATIONSHIPS_LIMIT default")

print("\nALL CHECKS PASSED: test_relationships_axis")
