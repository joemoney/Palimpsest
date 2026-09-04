"""Regression test for CR-01: scene (ctx["state"]["scene"]) previously had no writer
anywhere in the codebase and was frozen at whatever the template seeded it to for the life
of the playthrough. update_progress_from_turn's scene_update field is now the only writer.

Run directly: python3 test/test_scene_update.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402

se = load_story_engine()

ctx = se.state_store.load_state("sceneupdatetest", se.state_store.DEFAULT_STORY_SLUG)
assert ctx["state"]["scene"]["location"] == "loc_dock"

# --- a valid location id, summary, and present_npcs all round-trip ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "scene_update": {
         "location": "loc_inn", "summary": "Now warming up by the fire at the Harborlight.",
         "present_npcs": ["Mrs. Abbott"],
     },
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "follow her inside", "narration text")
scene = ctx["state"]["scene"]
assert scene["location"] == "loc_inn", scene
assert scene["summary"] == "Now warming up by the fire at the Harborlight.", scene
assert scene["present"] == ["Mrs. Abbott"], scene
print("OK: a valid scene_update writes location, summary, and present")

# --- an unknown/invalid location id is rejected; the previous value survives ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "scene_update": {"location": "loc_not_a_real_place", "summary": "Should still apply.",
                       "present_npcs": []},
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "wander off", "narration text")
assert ctx["state"]["scene"]["location"] == "loc_inn", "invalid id must not overwrite"
assert ctx["state"]["scene"]["summary"] == "Should still apply.", \
    "summary/present apply independently of whether location was accepted"
print("OK: an invalid location id is rejected, keeping the previous value, while summary still applies")

# --- an empty/missing summary keeps the previous value instead of blanking it ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "scene_update": {"location": "loc_inn", "summary": "", "present_npcs": []},
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "stay quiet", "narration text")
assert ctx["state"]["scene"]["summary"] == "Should still apply.", \
    "an empty summary must not blank the previous one"
print("OK: an empty summary is a no-op, not a blank-out")

# --- a missing scene_update key entirely is a no-op, not a crash ---
before = dict(ctx["state"]["scene"])
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "do nothing in particular", "narration text")
assert ctx["state"]["scene"] == before
print("OK: a missing scene_update key is a no-op")

# --- a story with no world.locations table accepts any free-text location string ---
free_ctx = se.state_store.load_state("sceneupdatetest2", se.state_store.DEFAULT_STORY_SLUG)
story_dict = se.state_store.thaw(free_ctx["story"])
story_dict["world"]["locations"] = {}
free_ctx["story"] = se.state_store.freeze(story_dict)
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "scene_update": {"location": "the rooftop, somewhere improvised", "summary": "s",
                       "present_npcs": []},
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
se.update_progress_from_turn(free_ctx, "climb up", "narration text")
assert free_ctx["state"]["scene"]["location"] == "the rooftop, somewhere improvised"
print("OK: an empty world.locations table accepts any free-text location string")

print("\nALL CHECKS PASSED: test_scene_update")
