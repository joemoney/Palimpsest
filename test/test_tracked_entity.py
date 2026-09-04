"""Regression test for CR-07: "the Architect" used to be hardcoded into two prompts
regardless of which story was running, asking stories/example's cozy mystery about an
entity that doesn't exist in its template. mechanics.tracked_entity now gates both the
state-update schema's entity_interaction field and check_and_advance_act's encounters line,
parameterized by whatever name/description the story configures. Also covers 5.5/CR-16:
entity_contact_count and pacing_note now reach the narration prompt too, so the narrator can
pace appearances against prior contact instead of treating every one as the first.

Run directly: python3 test/test_tracked_entity.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()

# --- a story with no tracked_entity (stories/example) gets no entity_interaction field, no
# encounters line, and no TRACKED ENTITY narration block ---
ctx = se.state_store.load_state("trackedentitytest", se.state_store.DEFAULT_STORY_SLUG)
assert "tracked_entity" not in ctx["story"].get("mechanics", {})
assert "TRACKED ENTITY" not in se.build_system_prompt(ctx)
print("OK: no tracked_entity -> no TRACKED ENTITY block in the narration prompt")

recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
})
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "look around", "narration text")
assert "entity_interaction" not in recorder.prompts[-1]
print("OK: no tracked_entity -> state-update schema has no entity_interaction field")

ctx["state"]["pacing"]["subplots_completed_this_act"] = 1
recorder = RecordingLLM(lambda p: {"ready": False, "reason": "not yet"})
se.call_llm_json = recorder
se.check_and_advance_act(ctx)
assert "ENCOUNTERS" not in recorder.prompts[-1]
print("OK: no tracked_entity -> act-check prompt has no encounters line")

# --- a story with a configured tracked_entity gets both, using its name ---
story_dict = se.state_store.thaw(ctx["story"])
story_dict["mechanics"]["tracked_entity"] = {
    "name": "The Warden", "description": "A watching presence.",
    "pacing_note": "Never appears twice in the same location.",
}
ctx["story"] = se.state_store.freeze(story_dict)

ctx["state"]["plot"]["entity_contact_count"] = 2
narration_prompt = se.build_system_prompt(ctx)
assert "TRACKED ENTITY: The Warden - A watching presence." in narration_prompt
assert "Pacing note: Never appears twice in the same location." in narration_prompt
assert "Prior contact this playthrough: 2 times." in narration_prompt
print("OK: a configured tracked_entity's name/description/pacing_note/contact count all "
      "reach the narration prompt")
recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
})
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "look around", "narration text")
assert '"entity_interaction"' in recorder.prompts[-1]
assert "The Warden" in recorder.prompts[-1]
print("OK: a configured tracked_entity adds entity_interaction, using its name")

ctx["state"]["pacing"]["subplots_completed_this_act"] = 1
ctx["state"]["plot"]["entity_contact_count"] = 3
recorder = RecordingLLM(lambda p: {"ready": False, "reason": "not yet"})
se.call_llm_json = recorder
se.check_and_advance_act(ctx)
assert "The Warden ENCOUNTERS: 3" in recorder.prompts[-1]
print("OK: a configured tracked_entity adds the encounters line, using its name and count")

# --- a save/template without tracked_entity at all doesn't crash either path ---
old_ctx = se.state_store.load_state("trackedentitytest2", se.state_store.DEFAULT_STORY_SLUG)
se.call_llm_json = lambda p, **kw: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
}
se.update_progress_from_turn(old_ctx, "do something", "narration text")
print("OK: a save/template without tracked_entity at all doesn't crash")

print("\nALL CHECKS PASSED: test_tracked_entity")
