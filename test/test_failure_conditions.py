"""Regression test for 5.7: mechanics.failure_conditions - a new capability closing the
genre gap where v1's only ending was the player typing "end story". Evaluated in the
state-update pass alongside revelations (same authored-trigger shape, different effect):
firing one sets endgame.requested/cause/final_arc and routes into the same ending machinery
handle_end_story_request uses, with no new code path and no LLM call for the closing text
(it's the condition's own authored ending_prompt).

Run directly: python3 test/test_failure_conditions.py
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


# --- a story with no failure_conditions gets no failure_triggered field and no FAILURE
# CONDITIONS line ---
ctx = se.state_store.load_state("failuretest", se.state_store.DEFAULT_STORY_SLUG)
assert "failure_conditions" not in ctx["story"].get("mechanics", {})
recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
})
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "look around", "narration text")
assert "failure_triggered" not in recorder.prompts[-1]
assert "FAILURE CONDITIONS" not in recorder.prompts[-1]
print("OK: no mechanics.failure_conditions -> no failure_triggered field, no prompt line")

# --- a configured failure condition is offered, and firing it locks in the ending ---
with_story(ctx, lambda s: s.setdefault("mechanics", {}).update(failure_conditions=[
    {"id": "fail_ferry", "trigger": "the player boards the ferry without learning what the "
     "lighthouse is", "title": "Gone Before You Knew", "ending_prompt": "Close on departure - "
     "safe, intact, and permanently unsatisfied."},
]))
recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
    "failure_triggered": "fail_ferry",
})
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "board the ferry and leave", "narration text")
assert "FAILURE CONDITIONS" in recorder.prompts[-1]
assert "the player boards the ferry" in recorder.prompts[-1]

endgame = ctx["state"]["plot"]["endgame"]
assert endgame["requested"] is True
assert endgame["cause"] == "fail_ferry", "cause should record the condition id, not 'player_request'"
assert endgame["final_arc"]["title"] == "Gone Before You Knew"
assert endgame["final_arc"]["description"] == "Close on departure - safe, intact, and permanently unsatisfied."
assert se._all_acts(ctx)[-1]["is_finale"] is True
print("OK: a fired failure condition locks in endgame with cause=<condition id> and the "
      "condition's own ending_prompt as the final arc, no LLM call needed for the text")

# --- once ending, no more failure conditions are offered or evaluated ---
recorder = RecordingLLM(lambda p: {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": [],
})
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "keep narrating the ending", "narration text")
assert "FAILURE CONDITIONS" not in recorder.prompts[-1]
print("OK: once the story is ending, failure conditions are no longer offered")

# --- a player-requested ending still records cause="player_request", distinguishing it
# from a failure-triggered one ---
ctx2 = se.state_store.load_state("failuretest2", se.state_store.DEFAULT_STORY_SLUG)
se.call_llm_json = CannedResponses([{"title": "The Reckoning", "description": "It ends well."}])
se.handle_end_story_request(ctx2)
assert ctx2["state"]["plot"]["endgame"]["cause"] == "player_request"
print("OK: handle_end_story_request still records cause='player_request', via the same "
      "shared endgame-entry helper")

print("\nALL CHECKS PASSED: test_failure_conditions")
